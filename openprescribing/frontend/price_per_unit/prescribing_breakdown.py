import numpy
from frontend.models import Presentation
from matrixstore.db import get_db, get_row_grouper
from matrixstore.matrix_ops import get_submatrix

from .substitution_sets import get_substitution_sets


def get_prescribing(generic_code, date):
    """
    For a given set of substitutable presentations (identified by
    `generic_code`) get all prescribing of those presentations on the given
    date

    Return value is a dict of the form:
        {
            ...
            bnf_code: (quantity_matrix, net_cost_matrix)
            ...
        }
    """
    # If the supplied code doesn't represent a substitution set just show
    # prescribing for that single BNF code
    try:
        bnf_codes = get_substitution_sets()[generic_code].presentations
    except KeyError:
        bnf_codes = [generic_code]

    db = get_db()
    try:
        date_column = db.date_offsets[date]
    except KeyError:
        return {}
    date_slice = slice(date_column, date_column + 1)

    results = db.query(
        """
        SELECT
          bnf_code, quantity, net_cost
        FROM
          presentation
        WHERE
          bnf_code IN ({})
        """.format(",".join("?" * len(bnf_codes))),
        bnf_codes,
    )
    return {
        bnf_code: (
            get_submatrix(quantity, cols=date_slice),
            get_submatrix(net_cost, cols=date_slice),
        )
        for bnf_code, quantity, net_cost in results
    }


def get_ppu_breakdown(prescribing, org_type, org_id):
    """
    Given a prescribing dict (see `get_prescribing` above) return a breakdown
    of how much of each presentation was prescribed at each price-per-unit by
    the given org.

    Note that we round PPUs to the nearest pence, so that if 10 units were
    prescribed at 9.9p each and 5 units at 10.1p this function will say that 15
    units were prescribed at 10p each.
    """
    group_by_org = get_row_grouper(org_type)
    names = Presentation.names_for_bnf_codes(prescribing.keys())
    presentations = []
    for bnf_code, (quantities, net_costs) in prescribing.items():
        quantities = group_by_org.get_group(quantities, org_id)[:, 0]
        net_costs = group_by_org.get_group(net_costs, org_id)[:, 0]
        ppu = net_costs / quantities
        rounded_ppu = numpy.rint(ppu)
        ppu_values = numpy.unique(rounded_ppu)
        ppu_values = ppu_values[numpy.isfinite(ppu_values)]
        if len(ppu_values):
            presentations.append(
                {
                    "name": names.get(bnf_code, "{} (unknown)".format(bnf_code)),
                    "mean_ppu": net_costs.sum() / quantities.sum(),
                    "is_generic": bnf_code[9:11] == "AA",
                    "quantity_at_each_ppu": [
                        (ppu_value, quantities[rounded_ppu == ppu_value].sum())
                        for ppu_value in ppu_values
                    ],
                }
            )
    presentations.sort(key=lambda i: (i["mean_ppu"], i["name"]))
    return presentations


def get_mean_ppu(prescribing, org_type, org_id):
    """
    Given a prescribing dict (see `get_prescribing` above) return the mean
    price-per-unit achieved by the given org over all included presentations
    """
    group_by_org = get_row_grouper(org_type)
    total_quantity = 0
    total_net_cost = 0
    for quantities, net_costs in prescribing.values():
        total_quantity += group_by_org.sum_one_group(quantities, org_id)[0]
        total_net_cost += group_by_org.sum_one_group(net_costs, org_id)[0]
    if total_quantity > 0:
        return total_net_cost / total_quantity
    else:
        return None
