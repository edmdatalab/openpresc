import re

from frontend.measure_tags import MEASURE_TAGS
from frontend.models import Measure, MeasureGlobal, MeasureValue, Presentation
from matrixstore.db import get_db, get_row_grouper
from rest_framework.decorators import api_view, renderer_classes
from rest_framework.exceptions import APIException
from rest_framework.renderers import BrowsableAPIRenderer, JSONRenderer
from rest_framework.response import Response
from rest_framework_csv.renderers import CSVRenderer

from . import view_utils as utils


class MissingParameter(APIException):
    status_code = 400
    default_detail = "You are missing a required parameter."


class InvalidMultiParameter(APIException):
    status_code = 400
    default_detail = (
        "You can specify one org and many measures, "
        "or one measure and many orgs, but not many of both"
    )


@api_view(["GET"])
def measure_global(request, format=None):
    measures = utils.param_to_list(request.query_params.get("measure", None))
    tags = utils.param_to_list(request.query_params.get("tags", None))
    qs = MeasureGlobal.objects.prefetch_related("measure")
    if measures:
        qs = qs.filter(measure_id__in=measures)
    if tags:
        qs = qs.filter(measure__tags__overlap=tags)
    qs = qs.order_by("measure_id", "month")
    rolled = {}
    for mg in qs:
        id = mg.measure_id
        d_copy = {
            "date": mg.month,
            "numerator": mg.numerator,
            "denominator": mg.denominator,
            "calc_value": mg.calc_value,
            "percentiles": mg.percentiles,
            "cost_savings": mg.cost_savings,
        }
        if id in rolled:
            rolled[id]["data"].append(d_copy)
        else:
            measure = mg.measure
            if measure.tags_focus:
                tags_focus = ",".join(measure.tags_focus)
            else:
                tags_focus = ""
            rolled[id] = {
                "id": id,
                "name": measure.name,
                "title": measure.title,
                "description": measure.description,
                "why_it_matters": measure.why_it_matters,
                "numerator_short": measure.numerator_short,
                "denominator_short": measure.denominator_short,
                "url": measure.url,
                "is_cost_based": measure.is_cost_based,
                "is_percentage": measure.is_percentage,
                "low_is_good": measure.low_is_good,
                "radar_include": not measure.radar_exclude,
                "tags_focus": tags_focus,
                "numerator_is_list_of_bnf_codes": measure.numerator_is_list_of_bnf_codes,
                "analyse_url": measure.analyse_url,
                "tags": _hydrate_tags(measure.tags),
                "data": [d_copy],
            }
    d = {"measures": [rolled[k] for k in rolled]}

    return Response(d)


def _get_org_type_and_id_from_request(request):
    """Return an (org_type, org_id) tuple from the request, normalised
    for various backward-compatibilities.
    """
    org_id = utils.param_to_list(request.query_params.get("org", []))
    org_id = org_id and org_id[0]
    org_type = request.query_params.get("org_type")
    if org_type == "pct":
        org_type = "ccg"
    if org_id and not org_type:
        # This is here for backwards compatibility, in case anybody else is
        # using the API.  Now we have measures for regional teams, we cannot
        # guess the type of an org by the length of its code, as both CCGs and
        # regional teams have codes of length 3.
        if len(org_id) in [3, 5]:
            org_type = "ccg"
        elif len(org_id) == 6:
            org_type = "practice"
        else:
            raise ValueError("Unexpected org: {}".format(org_id))
    if not org_id:
        org_type = "all_practices"
        org_id = None
    return org_type, org_id


@api_view(["GET"])
def measure_numerators_by_org(request, format=None):
    measure_id = request.query_params.get("measure", None)
    measure = Measure.objects.get(pk=measure_id)
    org_type, org_id = _get_org_type_and_id_from_request(request)
    group_by_org = get_row_grouper(org_type)

    # Nested function which takes a prescribing matrix and returns the total
    # value for the current organisation over the last 3 months (where the
    # current organisation is defined by the `group_by_org` and `org_id`
    # variables)
    def get_total(matrix):
        latest_three_months = matrix[:, -3:]
        values_for_org = group_by_org.sum_one_group(latest_three_months, org_id)
        return values_for_org.sum()

    bnf_codes, sort_field = _get_bnf_codes_and_sort_field_for_measure(measure)
    prescribing = _get_prescribing_for_bnf_codes(bnf_codes)
    results = []
    for bnf_code, items_matrix, quantity_matrix, actual_cost_matrix in prescribing:
        items = get_total(items_matrix)
        if items == 0:
            continue
        quantity = get_total(quantity_matrix)
        actual_cost = get_total(actual_cost_matrix)
        results.append(
            {
                "bnf_code": bnf_code,
                "total_items": int(items),
                "quantity": int(quantity),
                # Pence to pounds
                "cost": actual_cost / 100.0,
            }
        )
    # Sort by whatever is the appropriate field for this measure
    results.sort(key=lambda i: i[sort_field], reverse=True)
    names = Presentation.names_for_bnf_codes([i["bnf_code"] for i in results])
    for item in results:
        # Occasional issues with BNF code updates mean we temporarily can't
        # recognise a BNF code until we get the latest copy of the code mapping
        # file.
        item["presentation_name"] = names.get(item["bnf_code"], "<Name unavailable>")
    response = Response(results)
    filename = "%s-%s-breakdown.csv" % (measure, org_id)
    if request.accepted_renderer.format == "csv":
        response["content-disposition"] = "attachment; filename=%s" % filename
    return response


def _get_bnf_codes_and_sort_field_for_measure(measure):
    """
    Return `(bnf_codes, sort_field)` for a measure where `bnf_codes` is a list
    of BNF codes in the measure numerator (which may be empty for measures
    where this is not supported) and `sort_field` is the field in the API
    response by which results should be sorted
    """
    # For measures whose numerator sums one of the columns in the prescribing
    # table, we order the presentations by that column.  For other measures,
    # the columns used to calculate the numerator is not available here (it's
    # in BQ) so we order by total_items, which is the best we can do.
    #
    # Because the columns in BQ don't match the field names in our API (for
    # historical reasons) we need to pass them through a translation
    # dictionary.
    match = re.match(
        r"SUM\((items|quantity|actual_cost)\) AS numerator", measure.numerator_columns
    )

    if match:
        sort_field = {
            "items": "total_items",
            "actual_cost": "cost",
            "quantity": "quantity",
        }[match.groups()[0]]
    else:
        sort_field = "total_items"
    if measure.numerator_is_list_of_bnf_codes:
        bnf_codes = measure.numerator_bnf_codes
    else:
        bnf_codes = []
    return bnf_codes, sort_field


def _get_prescribing_for_bnf_codes(bnf_codes):
    """
    Return the items, quantity and actual cost matrices for the given list of
    BNF codes
    """
    return get_db().query(
        """
        SELECT
          bnf_code, items, quantity, actual_cost
        FROM
          presentation
        WHERE
          bnf_code IN ({})
        """.format(",".join(["?"] * len(bnf_codes))),
        bnf_codes,
    )


class MeasureValueCSVRenderer(CSVRenderer):
    header = [
        "measure",
        "org_type",
        "org_id",
        "org_name",
        "date",
        "numerator",
        "denominator",
        "calc_value",
        "percentile",
    ]


@api_view(["GET"])
@renderer_classes([JSONRenderer, BrowsableAPIRenderer, MeasureValueCSVRenderer])
def measure_by_regional_team(request, format=None):
    return _measure_by_org(request, "regional_team")


@api_view(["GET"])
@renderer_classes([JSONRenderer, BrowsableAPIRenderer, MeasureValueCSVRenderer])
def measure_by_stp(request, format=None):
    return _measure_by_org(request, "stp")


@api_view(["GET"])
@renderer_classes([JSONRenderer, BrowsableAPIRenderer, MeasureValueCSVRenderer])
def measure_by_ccg(request, format=None):
    return _measure_by_org(request, "ccg")


@api_view(["GET"])
@renderer_classes([JSONRenderer, BrowsableAPIRenderer, MeasureValueCSVRenderer])
def measure_by_pcn(request, format=None):
    return _measure_by_org(request, "pcn")


@api_view(["GET"])
@renderer_classes([JSONRenderer, BrowsableAPIRenderer, MeasureValueCSVRenderer])
def measure_by_practice(request, format=None):
    return _measure_by_org(request, "practice")


def _measure_by_org(request, org_type):
    measure_ids = utils.param_to_list(request.query_params.get("measure", None))
    tags = utils.param_to_list(request.query_params.get("tags", []))
    org_ids = utils.param_to_list(request.query_params.get("org", []))
    parent_org_type = request.query_params.get("parent_org_type", None)
    aggregate = bool(request.query_params.get("aggregate"))

    if org_type == "practice" and not (org_ids or aggregate):
        raise MissingParameter
    if len(org_ids) > 1 and len(measure_ids) > 1:
        raise InvalidMultiParameter

    if parent_org_type is None:
        if org_type == "practice" and org_ids:
            length = len(org_ids[0])

            if length in [3, 5]:
                parent_org_type = "pct"
            elif length == 6:
                parent_org_type = "practice"
            else:
                assert False, length
        else:
            parent_org_type = org_type

    measure_values = MeasureValue.objects.by_org(
        org_type, parent_org_type, org_ids, measure_ids, tags
    )

    # Because we access the `name` of the related org for each MeasureValue
    # during the roll-up process below we need to prefetch them to avoid doing
    # N+1 db queries
    org_field = org_type if org_type != "ccg" else "pct"
    measure_values = measure_values.prefetch_related(org_field)

    if aggregate:
        measure_values = measure_values.aggregate_by_measure_and_month()

    if request.accepted_renderer.format == "csv":
        data = [_measure_value_data(mv, org_type) for mv in measure_values]
        response = Response(data)
        response["content-disposition"] = "attachment; filename=measures.csv"
        return response

    else:
        rsp_data = {"measures": _roll_up_measure_values(measure_values, org_type)}
        return Response(rsp_data)


def _roll_up_measure_values(measure_values, org_type):
    rolled = {}

    for measure_value in measure_values:
        measure_id = measure_value.measure_id
        measure_value_data = _measure_value_data(measure_value, org_type)

        if measure_id in rolled:
            rolled[measure_id]["data"].append(measure_value_data)
        else:
            measure = measure_value.measure
            rolled[measure_id] = {
                "id": measure_id,
                "name": measure.name,
                "title": measure.title,
                "description": measure.description,
                "why_it_matters": measure.why_it_matters,
                "numerator_short": measure.numerator_short,
                "denominator_short": measure.denominator_short,
                "url": measure.url,
                "is_cost_based": measure.is_cost_based,
                "is_percentage": measure.is_percentage,
                "low_is_good": measure.low_is_good,
                "radar_include": not measure.radar_exclude,
                "tags": _hydrate_tags(measure.tags),
                "data": [measure_value_data],
            }

    return list(rolled.values())


def _measure_value_data(measure_value, org_type):
    measure_value_data = {
        "measure": measure_value.measure_id,
        "date": measure_value.month,
        "numerator": measure_value.numerator,
        "denominator": measure_value.denominator,
        "calc_value": measure_value.calc_value,
        "percentile": measure_value.percentile,
        "cost_savings": measure_value.cost_savings,
    }

    if org_type == "practice":
        if measure_value.practice_id:
            measure_value_data.update(
                {
                    "org_type": "practice",
                    "org_id": measure_value.practice_id,
                    "org_name": measure_value.practice.name,
                }
            )
    elif org_type == "pcn":
        if measure_value.pcn_id:
            measure_value_data.update(
                {
                    "org_type": "pcn",
                    "org_id": measure_value.pcn_id,
                    "org_name": measure_value.pcn.name,
                }
            )
    elif org_type == "ccg":
        if measure_value.pct_id:
            measure_value_data.update(
                {
                    "org_type": "ccg",
                    "org_id": measure_value.pct_id,
                    "org_name": measure_value.pct.name,
                }
            )
    elif org_type == "stp":
        if measure_value.stp_id:
            measure_value_data.update(
                {
                    "org_type": "stp",
                    "org_id": measure_value.stp_id,
                    "org_name": measure_value.stp.name,
                }
            )
    elif org_type == "regional_team":
        if measure_value.regional_team_id:
            measure_value_data.update(
                {
                    "org_type": "regional_team",
                    "org_id": measure_value.regional_team_id,
                    "org_name": measure_value.regional_team.name,
                }
            )
    else:
        assert False

    return measure_value_data


def _hydrate_tags(tag_ids):
    return [{"id": tag_id, "name": MEASURE_TAGS[tag_id]["name"]} for tag_id in tag_ids]
