"""
Drugs prescribed generically should always be dispensed at the tariff price.
However, it turns out it's possible to prescribe a generic drug but specify the
manufacturer. This extra bit of specification doesn't show up in our data as
it's not sufficiently granular so it just looks like an ordinary generic. But
the price paid can be much greater than the tariff price. We call these Ghost
Branded Generics.

A further complication is that we don't always know what the correct tariff
price is; the BSA's internal version doesn't always match the published
version. So we attempt to infer the correct price by looking at all prescribing
of that drug and finding the median price.
"""

import hashlib

import numpy
from django.db import connection
from frontend.models import Presentation
from matrixstore.cachelib import memoize
from matrixstore.db import get_db, get_row_grouper
from matrixstore.matrix_ops import get_submatrix

# Minimum difference (positive or negative) between a practice's net costs for
# a drug and our calculated tariff costs. Any differences below this level we
# ignore. Value in pence.
MIN_GHOST_GENERIC_DELTA = 200


class SetWithCacheKey(set):
    """
    Set subclass which adds a `cache_key` attribute which is just the hash of
    the of its values
    """

    cache_key = None

    def __new__(cls, items):
        instance = set.__new__(cls, items)
        hashobj = hashlib.md5(str(sorted(items)).encode("utf8"))
        instance.cache_key = hashobj.digest()
        return instance


PRESENTATIONS_TO_IGNORE = SetWithCacheKey(
    [
        # These can be prescribed fractionally, but BSA round quantity down,
        # making quantity unreliable. See #1764
        "1106000L0AAAAAA",  # latanoprost
        "1308010Z0AAABAB",  # Ingenol Mebutate_Gel
        # These are sometimes recorded by dose, and sometimes by pack (of 8) see #937
        "0407020A0AABPBP",  # Fentanyl 400micrograms/dose nasal spray
        # These have two pack sizes, which don't appear to be separated in BA data
        "0302000U0AAABAB",  # Ciclesonide 160micrograms/dose inhaler CFC free
        # These are inconsistently recorded as doses or packs by NHSBSA
        "0604011G0AABTBT",  # Estradiol 1.53mg/dose transdermal spray
        # These are sometimes recorded as bottles, sometimes in litres
        "0902021S0AAAXAX",  # Sodium chloride 0.9% infusion 1litre polyethylene bottles
    ]
)


def get_ghost_branded_generic_spending(date, org_type, org_ids):
    """
    Return all spending on generics (by these orgs and in this month) which
    differs significantly from the tariff price
    """
    db = get_db()
    prices = get_inferred_tariff_prices(db, date, PRESENTATIONS_TO_IGNORE)
    bnf_codes = list(prices.keys())
    prescribing = get_prescribing_for_orgs(db, bnf_codes, date, org_type, org_ids)
    results = []
    bnf_codes_used = set()
    for org_id, bnf_code, quantities, net_costs in prescribing:
        tariff_price = prices[bnf_code]
        tariff_costs = quantities * tariff_price
        possible_savings = net_costs - tariff_costs
        savings_above_threshold = (
            numpy.absolute(possible_savings) >= MIN_GHOST_GENERIC_DELTA
        )
        total_savings = possible_savings.sum(where=savings_above_threshold)
        if total_savings != 0:
            bnf_codes_used.add(bnf_code)
            total_net_cost = net_costs.sum()
            total_quantity = quantities.sum()
            results.append(
                {
                    "date": date,
                    "org_type": org_type,
                    "org_id": org_id,
                    "bnf_code": bnf_code,
                    "median_ppu": tariff_price / 100,
                    "price_per_unit": total_net_cost / total_quantity / 100,
                    "quantity": total_quantity,
                    "possible_savings": total_savings / 100,
                }
            )
    names = Presentation.names_for_bnf_codes(bnf_codes_used)
    for result in results:
        result["product_name"] = names.get(result["bnf_code"], "unknown")
    results.sort(key=lambda i: i["possible_savings"], reverse=True)
    return results


def get_total_ghost_branded_generic_spending(date, org_type, org_id):
    """
    Get the total spend on generics (by this org and in this month) over and
    above the price set by the Drug Tariff
    """
    db = get_db()
    practice_spending = get_total_ghost_branded_generic_spending_per_practice(
        db, date, PRESENTATIONS_TO_IGNORE, MIN_GHOST_GENERIC_DELTA
    )
    group_by_org = get_row_grouper(org_type)
    return group_by_org.sum_one_group(practice_spending, org_id)[0] / 100


@memoize()
def get_total_ghost_branded_generic_spending_per_practice(
    db, date, presentations_to_ignore, min_delta
):
    """
    Get the total spend on generics by all practices in this month over and
    above the price set by the Drug Tariff.

    This gives us a single, cacheable matrix from which we can calculate totals
    for any organisation.
    """
    prices = get_inferred_tariff_prices(db, date, presentations_to_ignore)
    bnf_codes = list(prices.keys())
    totals = None
    for bnf_code, quantities, net_costs in get_prescribing(db, bnf_codes, date):
        if not isinstance(quantities, numpy.ndarray):
            quantities = quantities.toarray()
        if not isinstance(net_costs, numpy.ndarray):
            net_costs = net_costs.toarray()
        tariff_price = prices[bnf_code]
        tariff_costs = quantities * tariff_price
        possible_savings = net_costs - tariff_costs
        savings_above_threshold = numpy.absolute(possible_savings) >= min_delta
        if totals is None:
            totals = numpy.zeros_like(possible_savings)
        numpy.add(totals, possible_savings, out=totals, where=savings_above_threshold)
    assert totals is not None
    return totals


@memoize()
def get_inferred_tariff_prices(db, date, presentations_to_ignore):
    """
    Return a dict mapping BNF codes for generics to (our best guess for) their
    Drug Tariff price.

    We exclude certain presentations where we know we can't do this accurately.
    """
    bnf_codes = get_bnf_codes_with_single_tariff_price(date)
    bnf_codes = [code for code in bnf_codes if code not in presentations_to_ignore]
    return infer_tariff_price_for_presentations(db, bnf_codes, date)


def get_bnf_codes_with_single_tariff_price(date):
    """
    Get all BNF codes where there is a consistent price-per-unit across
    different pack sizes. It's only for such products that we can estimate
    ghost-branded generic spend.
    """
    # Note: this was cribbed from the original SQL view. It can probably be
    # done through the ORM but I'm not exactly sure how to implement the HAVING
    # clause.
    sql = """
    SELECT DISTINCT vmp.bnf_code
    FROM dmd_vmp vmp
    INNER JOIN dmd_vmpp vmpp ON vmp.vpid = vmpp.vpid
    INNER JOIN frontend_tariffprice tp ON vmpp.vppid = tp.vmpp_id
    WHERE tp.date = %(date)s
    GROUP BY vmp.vpid
    HAVING stddev_pop(tp.price_pence / vmpp.qtyval) = 0;
    """
    with connection.cursor() as cursor:
        cursor.execute(sql, {"date": date})
        return [row[0] for row in cursor.fetchall()]


def infer_tariff_price_for_presentations(db, bnf_codes, date):
    """
    Although we have the Drug Tariff prices for each month in our database we
    aren't able to straightforwardly match these against prescribing due to
    issues with exactly *when* these prices apply (see #1318). We therefore
    attempt to infer the correct tariff price by looking at the median price
    paid (on the basis that if this *isn't* the tariff price then something has
    gone seriously wrong).

    Returns a dict mapping BNF codes to inferred tariff price
    """
    # Silence numpy warnings
    numpy_err = numpy.seterr(divide="ignore", invalid="ignore")
    prices = {}
    for bnf_code, quantity, net_cost in get_prescribing(db, list(bnf_codes), date):
        # Depending on the sparsity of the data we may get back either a numpy
        # ndarray or a scipy sparse matrix. Usually they behave similarly
        # enough that it doesn't matter, but we can't divide mixed types so we
        # need to make them consistent here.
        if not isinstance(quantity, numpy.ndarray):
            quantity = quantity.toarray()
        if not isinstance(net_cost, numpy.ndarray):
            net_cost = net_cost.toarray()
        ppu = net_cost / quantity
        # We occasionally get instances where a practice has a postive net cost
        # for a drug but a quantity of zero, resulting in an infinite PPU. This
        # is due to rounding issues (see #1373). In this case we really can't
        # trust the data enough to do a ghost-branded generic analysis so we
        # skip the drug entirely.
        if numpy.any(numpy.isinf(ppu)):
            continue
        # We use interpolation "lower" here (rather than the default "linear",
        # which we use elsewhere) because this matches the behaviour of
        # Postgres's PERCENTILE_DISC function on which this calculation was
        # originally based
        median_ppu = numpy.nanpercentile(ppu, axis=0, q=50, interpolation="lower")[0]
        prices[bnf_code] = median_ppu
    # Restore numpy warnings
    numpy.seterr(**numpy_err)
    return prices


def get_prescribing_for_orgs(db, bnf_codes, date, org_type, org_ids):
    """
    Get all prescribing for a given set of presentations by the given
    organisation on the given date.

    Results are returned as practice level matrices, but containing only the
    practices in the given organisations. Presentations for which there is no
    relevant prescribing are omitted.

    Yields tuples of the form:

        org_id, bnf_code, quantity_matrix, net_cost_matrix
    """
    group_by_org = get_row_grouper(org_type)
    for bnf_code, quantities, net_costs in get_prescribing(db, bnf_codes, date):
        for org_id in org_ids:
            quantities_for_org = group_by_org.get_group(quantities, org_id)
            if numpy.any(quantities_for_org):
                net_costs_for_org = group_by_org.get_group(net_costs, org_id)
                yield org_id, bnf_code, quantities_for_org, net_costs_for_org


def get_prescribing(db, bnf_codes, date):
    """
    Get all prescribing for a given set of presentations on the given date

    Yields tuples of the form: (bnf_code, quantity_matrix, net_cost_matrix)
    """
    date_column = db.date_offsets[date]
    date_slice = slice(date_column, date_column + 1)

    results = db.query(
        """
        SELECT bnf_code, quantity, net_cost FROM presentation WHERE bnf_code IN ({})
        """.format(",".join("?" * len(bnf_codes))),
        bnf_codes,
    )
    for bnf_code, quantity, net_cost in results:
        yield (
            bnf_code,
            get_submatrix(quantity, cols=date_slice),
            get_submatrix(net_cost, cols=date_slice),
        )
