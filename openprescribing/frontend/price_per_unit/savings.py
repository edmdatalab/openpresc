import numpy
from django.db import connection
from frontend.views.spending_utils import (
    APPLIANCE_DISCOUNT_PERCENTAGE,
    BRAND_DISCOUNT_PERCENTAGE,
    GENERIC_DISCOUNT_PERCENTAGE,
)
from matrixstore.cachelib import memoize
from matrixstore.db import get_db, get_row_grouper
from matrixstore.matrix_ops import get_submatrix, zeros_like
from matrixstore.sql_functions import MatrixSum

from .substitution_sets import get_substitution_sets

# Defines how we determine the target PPU against which savings are calculated.
# We want to know which set of practices we are comparing with and which
# centile over those practices we are targeting.
CONFIG_TARGET_CENTILE = 10
# Note this is "standard_practice" rather than "practice" as we only want to
# include setting 4 practices (i.e. ordinary GP practices)
CONFIG_TARGET_PEER_GROUP = "standard_practice"
# Any savings below these limits are ignored when calculating the total
# available savings for an organisation. This is to avoid including savings
# that can only be achieved by making lots of tiny savings over a large number
# of presentations. (Note: all values in pence.)
CONFIG_MIN_SAVINGS_FOR_ORG_TYPE = {
    "practice": 10 * 100,
    "ccg": 200 * 100,
    # This is the limit for All England savings, picked somewhat arbitrarily
    # based on the CCG limit
    "all_standard_practices": 50000 * 100,
}


def get_all_savings_for_orgs(date, org_type, org_ids):
    """
    Get all available savings through presentation switches for the given orgs
    """
    min_saving = CONFIG_MIN_SAVINGS_FOR_ORG_TYPE[org_type]
    results = []
    for generic_code in get_substitution_sets().keys():
        savings = get_savings_for_orgs(
            generic_code, date, org_type, org_ids, min_saving=min_saving
        )
        results.extend(savings)
    results.sort(key=lambda i: i["possible_savings"], reverse=True)
    return results


def get_savings_for_orgs(generic_code, date, org_type, org_ids, min_saving=1):
    """
    Get available savings for the given orgs within a particular class of
    substitutable presentations
    """
    try:
        substitution_set = get_substitution_sets()[generic_code]
    # Gracefully handle being asked for the savings for a code with no
    # substitutions (to which the answer is always: no savings)
    except KeyError:
        return []

    quantities, net_costs = get_quantities_and_net_costs_at_date(
        get_db(), substitution_set, date
    )

    group_by_org = get_row_grouper(org_type)
    quantities_for_orgs = group_by_org.sum(quantities, org_ids)
    # Bail early if none of the orgs have any relevant prescribing
    if not numpy.any(quantities_for_orgs):
        return []
    net_costs_for_orgs = group_by_org.sum(net_costs, org_ids)
    ppu_for_orgs = net_costs_for_orgs / quantities_for_orgs

    target_ppu = get_target_ppu(
        quantities,
        net_costs,
        group_by_org=get_row_grouper(CONFIG_TARGET_PEER_GROUP),
        target_centile=CONFIG_TARGET_CENTILE,
    )
    practice_savings = get_savings(quantities, net_costs, target_ppu)

    savings_for_orgs = group_by_org.sum(practice_savings, org_ids)

    results = [
        {
            "date": date,
            "org_id": org_id,
            "price_per_unit": ppu_for_orgs[offset, 0] / 100,
            "possible_savings": savings_for_orgs[offset, 0] / 100,
            "quantity": quantities_for_orgs[offset, 0],
            "lowest_decile": target_ppu[0] / 100,
            "presentation": substitution_set.id,
            "formulation_swap": substitution_set.formulation_swaps,
            "name": substitution_set.name,
        }
        for offset, org_id in enumerate(org_ids)
        if savings_for_orgs[offset, 0] >= min_saving
    ]

    results.sort(key=lambda i: i["possible_savings"], reverse=True)
    return results


def get_total_savings_for_org(date, org_type, org_id):
    """
    Get total available savings through presentation switches for the given org
    """
    group_by_org = get_row_grouper(org_type)
    substitution_sets = get_substitution_sets()
    # This only happens during testing where a test case might not have enough
    # different presentations to generate any substitutions. If this is the
    # case then their are, obviously, zero savings available.
    if not substitution_sets:
        return 0.0
    totals = get_total_savings_for_org_type(
        db=get_db(),
        substitution_sets=substitution_sets,
        date=date,
        group_by_org=group_by_org,
        min_saving=CONFIG_MIN_SAVINGS_FOR_ORG_TYPE[org_type],
        practice_group_by_org=get_row_grouper(CONFIG_TARGET_PEER_GROUP),
        target_centile=CONFIG_TARGET_CENTILE,
    )
    offset = group_by_org.offsets[org_id]
    return totals[offset, 0] / 100


# Increment the version number if the logic of this function changes such that
# the same inputs no longer produce the same outputs
@memoize(version=1)
def get_total_savings_for_org_type(
    db,
    substitution_sets,
    date,
    group_by_org,
    min_saving,
    practice_group_by_org,
    target_centile,
):
    """
    Return a matrix giving total savings for all orgs of a given type

    This duplicates some of the logic in `get_savings_for_orgs` but it gives us
    much better caching behaviour to calculate savings for all orgs of a given
    type together in a single, cacheable matrix than it does to do them one by
    one.

    Because we want this function to be cacheable it needs to touch no global
    state or configuration and have eveything passed into it, hence the
    slightly convoluted call signature.
    """
    totals = None
    for substitution_set in substitution_sets.values():
        quantities, net_costs = get_quantities_and_net_costs_at_date(
            db, substitution_set, date
        )
        target_ppu = get_target_ppu(
            quantities,
            net_costs,
            group_by_org=practice_group_by_org,
            target_centile=target_centile,
        )
        practice_savings = get_savings(quantities, net_costs, target_ppu)
        savings_for_orgs = group_by_org.sum(practice_savings)
        savings_above_threshold = savings_for_orgs >= min_saving
        if totals is None:
            totals = zeros_like(savings_for_orgs)
        numpy.add(totals, savings_for_orgs, out=totals, where=savings_above_threshold)
    assert totals is not None
    return totals


def get_target_ppu(quantities, net_costs, group_by_org, target_centile):
    """
    Calculate the price-per-unit achieved by the organisation (as defined by
    `group_by_org`) at the target centile
    """
    quantities = group_by_org.sum(quantities)
    net_costs = group_by_org.sum(net_costs)
    ppu = net_costs / quantities
    target_ppu = numpy.nanpercentile(ppu, axis=0, q=target_centile)
    return target_ppu


def get_savings(quantities, net_costs, target_ppu):
    """
    For a given target price-per-unit calculate how much would be saved by each
    practice if they had achieved that price-per-unit
    """
    target_costs = quantities * target_ppu
    savings = net_costs - target_costs
    # Replace any negative savings (i.e. practices already performing better
    # than the target) with zero savings
    numpy.clip(savings, a_min=0, a_max=None, out=savings)
    return savings


# Increment the version number if the logic of this function changes such that
# the same inputs no longer produce the same outputs
@memoize(version=2)
def get_quantities_and_net_costs_at_date(db, substitution_set, date):
    """
    Sum quantities and net costs over the supplied list of BNF codes for just
    the specified date.
    """
    bnf_codes = substitution_set.presentations
    date_column = db.date_offsets[date]
    date_slice = slice(date_column, date_column + 1)

    discounts = get_discounts_at_date(bnf_codes, date)

    results = db.query(
        """
        SELECT
          bnf_code, quantity, net_cost
        FROM
          presentation
        WHERE
          bnf_code IN ({})
        """.format(
            ",".join("?" * len(bnf_codes))
        ),
        bnf_codes,
    )

    quantity_sum = MatrixSum()
    net_cost_sum = MatrixSum()
    for bnf_code, quantity, net_cost in results:
        quantity_sum.add(get_submatrix(quantity, cols=date_slice))
        net_cost_slice = get_submatrix(net_cost, cols=date_slice) * discounts[bnf_code]
        net_cost_sum.add(net_cost_slice)
    return quantity_sum.value(), net_cost_sum.value()


DISCOUNTS_FOR_CATEGORY = {
    category_id: (100 - discount) / 100.0
    for discount, categories in [
        (GENERIC_DISCOUNT_PERCENTAGE, [1, 11]),
        (APPLIANCE_DISCOUNT_PERCENTAGE, [5, 6, 7, 8, 10]),
    ]
    for category_id in categories
}


DEFAULT_DISCOUNT = (100 - BRAND_DISCOUNT_PERCENTAGE) / 100.0


def get_discounts_at_date(bnf_codes, date):
    """
    Return a dictionary mapping BNF codes to the appropriate discount for that
    presentation on the given date
    """
    category_ids = {}
    concessions = {}
    with connection.cursor() as cursor:
        # Given a list of BNF codes find their corresponding drug tariff category and
        # whether they have a price concession for this month. This query will only
        # find BNF codes corresponding to VMPPs, not AMPPs, but that's fine because we
        # don't need to know the category for AMPPs because they all have the default
        # discount anyone. And, necessarily, AMPPs can't be part of a price concession
        # so we don't need to worry about that either.
        cursor.execute(
            f"""
            SELECT
              vmpp.bnf_code AS bnf_code,
              tariff.tariff_category_id AS category_id,
              ncso.vmpp_id IS NOT NULL AS has_concession
            FROM
              dmd_vmpp AS vmpp
            JOIN
              frontend_tariffprice AS tariff
            ON
              vmpp.vppid = tariff.vmpp_id AND tariff.date = %s
            LEFT JOIN
              frontend_ncsoconcession AS ncso
            ON
              ncso.vmpp_id = vmpp.vppid AND ncso.date = %s
            WHERE
              vmpp.bnf_code IN ({", ".join(["%s"] * len(bnf_codes))})
            """,
            [date, date, *bnf_codes],
        )
        for bnf_code, category_id, has_concession in cursor.fetchall():
            category_ids[bnf_code] = category_id
            concessions[bnf_code] = has_concession
    return {
        bnf_code: (
            # As above, we expect to frequently get missing keys here and they should
            # all have the default discount applied
            DISCOUNTS_FOR_CATEGORY.get(category_ids.get(bnf_code), DEFAULT_DISCOUNT)
            if not concessions.get(bnf_code)
            # If there's a price concession for this presentation in this month then we
            # don't discount the price paid at all
            else 1.0
        )
        for bnf_code in bnf_codes
    }
