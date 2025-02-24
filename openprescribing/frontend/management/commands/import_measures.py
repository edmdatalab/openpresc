"""Calculate and store measures based on definitions in
`measures/definitions/` folder.

We use BigQuery to compute measures. This is considerably cheaper than
the alternative, which is looping over thousands of practices
individually with a custom SQL query. However, the tradeoff is that
most of the logic now lives in SQL which is harder to read and test
clearly.
"""

import csv
import glob
import json
import logging
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from common import utils
from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.core.management import BaseCommand
from django.db import connection, transaction
from django.urls import reverse
from frontend.models import ImportLog, Measure, MeasureGlobal, MeasureValue
from frontend.utils.bnf_hierarchy import get_all_bnf_codes, simplify_bnf_codes
from gcutils.bigquery import Client
from google.api_core.exceptions import BadRequest

logger = logging.getLogger(__name__)

CENTILES = [10, 20, 30, 40, 50, 60, 70, 80, 90]

MEASURE_FIELDNAMES = [
    "measure_id",
    "regional_team_id",
    "stp_id",
    "pct_id",
    "pcn_id",
    "practice_id",
    "month",
    "numerator",
    "denominator",
    "calc_value",
    "percentile",
    "cost_savings",
]


class Command(BaseCommand):
    """
    Specify a measure with a single string argument to `--measure`,
    and more than one with a comma-delimited list.
    """

    def check_definitions(self, measure_defs, start_date, end_date, verbose):
        """Checks SQL definitions for measures."""

        # We don't validate JSON here, as this is already done as a
        # side-effect of parsing the command options.
        errors = []
        for measure_def in measure_defs:
            measure_id = measure_def["id"]
            try:
                measure = create_or_update_measure(measure_def, end_date)
                calculation = MeasureCalculation(
                    measure, start_date=start_date, end_date=end_date, verbose=verbose
                )
                calculation.check_definition()
            except BadRequest as e:
                errors.append("* SQL error in `{}`: {}".format(measure_id, e.args[0]))
            except TypeError as e:
                errors.append("* JSON error in `{}`: {}".format(measure_id, e.args[0]))
        if errors:
            raise BadRequest("\n".join(errors))

    def build_measures(self, measure_defs, start_date, end_date, verbose, options):
        upload_supplementary_tables()
        # This is an optimisation that only makes sense when we're updating the
        # entire table, any of these supplied options mean we're doing
        # something else
        drop_and_rebuild_indices = bool(
            not options["measure"]
            and not options["definitions_only"]
            and not options["bigquery_only"]
        )
        with conditional_constraint_and_index_reconstructor(drop_and_rebuild_indices):
            for measure_def in measure_defs:
                measure_id = measure_def["id"]
                logger.info("Updating measure: %s" % measure_id)
                measure_start = datetime.now()

                with transaction.atomic():
                    measure = create_or_update_measure(measure_def, end_date)

                    if options["definitions_only"]:
                        continue

                    calcuation = MeasureCalculation(
                        measure,
                        start_date=start_date,
                        end_date=end_date,
                        verbose=verbose,
                    )

                    if not options["bigquery_only"]:
                        MeasureValue.objects.filter(measure=measure).delete()
                        MeasureGlobal.objects.filter(measure=measure).delete()

                    # Compute the measures
                    calcuation.calculate(options["bigquery_only"])

                elapsed = datetime.now() - measure_start
                logger.warning(
                    "Elapsed time for %s: %s seconds" % (measure_id, elapsed.seconds)
                )

    def handle(self, *args, **options):
        start = datetime.now()

        if options["measure"]:
            measure_ids = options["measure"].split(",")
            measure_defs = load_measure_defs(measure_ids)
        else:
            measure_defs = load_measure_defs()

        end_date = ImportLog.objects.latest_in_category("prescribing").current_at
        start_date = end_date - relativedelta(years=5)

        verbose = options["verbosity"] > 1
        if options["check"]:
            self.check_definitions(measure_defs, start_date, end_date, verbose)
        else:
            self.build_measures(measure_defs, start_date, end_date, verbose, options)

        if options["print_confirmation"]:
            if options["definitions_only"]:
                target = "definitions (but not values)"
            else:
                target = "definitions and values"
            measure_urls = "\n".join(
                f"https://openprescribing.net/measure/{measure_id}/"
                for measure_id in measure_ids
            )
            print(f"Imported measure {target} for:\n{measure_urls}")

        logger.warning("Total elapsed time: %s" % (datetime.now() - start))

    def add_arguments(self, parser):
        parser.add_argument("--measure")
        parser.add_argument("--definitions_only", action="store_true")
        parser.add_argument("--bigquery_only", action="store_true")
        parser.add_argument("--check", action="store_true")
        parser.add_argument("--print-confirmation", action="store_true")


def load_measure_defs(measure_ids=None):
    """Load measure definitions from JSON files.

    Since the lpzomnibus measure depends on other LP measures having already been
    calculated, it is important that the measures are returned in alphabetical order.
    (This is a bit of a hack...)
    """
    measures = []
    errors = []

    glob_path = os.path.join(settings.MEASURE_DEFINITIONS_PATH, "*.json")
    for path in sorted(glob.glob(glob_path)):
        measure_id = os.path.basename(path).split(".")[0]
        if measure_id.startswith(settings.MEASURE_PREVIEW_PREFIX):
            errors.append(
                f"'{measure_id}' starts with the prefix reserved for previews"
            )
            continue

        with open(path) as f:
            try:
                measure_def = json.load(f)
            except ValueError as e:
                # Add the measure_id to the exception
                errors.append("* {}: {}".format(measure_id, e.args[0]))
                continue

            if measure_ids is None:
                if "skip" in measure_def:
                    continue
            else:
                if measure_id not in measure_ids:
                    continue

            measure_def["id"] = measure_id
            measures.append(measure_def)

    if errors:
        raise ValueError("Problems parsing JSON:\n" + "\n".join(errors))
    return measures


# Utility methods


def float_or_null(v):
    """Return a value coerced to a float, unless it's a None."""
    if v is not None:
        v = float(v)
    return v


def float_or_zero(v):
    """Return a value coerced to a float; Nones become zero."""
    v = float_or_null(v)
    if v is None:
        v = 0.0
    return v


def convertSavingsToDict(datum, prefix=None):
    """Convert flat list of savings into a dict with centiles as
    keys

    """
    data = {}
    for centile in CENTILES:
        key = "cost_savings_%s" % centile
        if prefix:
            key = "%s_%s" % (prefix, key)
        data[str(centile)] = float_or_zero(datum.pop(key))
    return data


def convertDecilesToDict(datum, prefix=None):
    """Convert flat list of deciles into a dict with centiles as
    keys

    """
    assert prefix
    data = {}
    for centile in CENTILES:
        key = "%s_%sth" % (prefix, centile)
        data[str(centile)] = float_or_null(datum.pop(key))
    return data


def normalisePercentile(percentile):
    """Given a percentile between 0 and 1, or None, return a normalised
    version between 0 and 100, or None.

    """
    percentile = float_or_null(percentile)
    if percentile:
        percentile = percentile * 100
    return percentile


def arrays_to_strings(measure_def):
    """To facilitate readability via newlines, we express some JSON
    strings as arrays, but store them as strings.

    Returns the json with such fields converted to strings.

    """
    fields_to_convert = [
        "title",
        "description",
        "why_it_matters",
        "numerator_columns",
        "numerator_from",
        "numerator_where",
        "numerator_bnf_codes_query",
        "denominator_columns",
        "denominator_where",
        "denominator_from",
        "denominator_bnf_codes_query",
    ]

    for field in fields_to_convert:
        if field not in measure_def:
            continue
        if isinstance(measure_def[field], list):
            measure_def[field] = " ".join(measure_def[field])
    return measure_def


def create_or_update_measure(measure_def, end_date):
    """Create a measure object based on a measure definition"""
    measure_id = measure_def["id"]
    v = arrays_to_strings(measure_def)

    for k, val in v.items():
        if isinstance(val, str):
            v[k] = val.strip()

    try:
        m = Measure.objects.get(id=measure_id)
    except Measure.DoesNotExist:
        m = Measure(id=measure_id)

    m.title = v["title"]
    m.description = v["description"]
    m.why_it_matters = v["why_it_matters"]
    m.name = v["name"]
    m.tags = v["tags"]
    m.tags_focus = v.get("tags_focus", [])
    m.title = v["title"]
    m.description = v["description"]
    m.numerator_short = v["numerator_short"]
    m.denominator_short = v["denominator_short"]
    m.url = v["url"]
    m.is_cost_based = v["is_cost_based"]
    m.is_percentage = v["is_percentage"]
    m.low_is_good = v["low_is_good"]
    m.include_in_alerts = v.get("include_in_alerts", True)
    m.radar_exclude = v.get("radar_exclude", True)

    m.numerator_type = v["numerator_type"]

    if m.numerator_type == "custom":
        m.numerator_columns = v["numerator_columns"]
        m.numerator_from = v["numerator_from"]
        m.numerator_where = v["numerator_where"]
        m.numerator_bnf_codes_query = v.get("numerator_bnf_codes_query")
        m.numerator_is_list_of_bnf_codes = v.get("numerator_is_list_of_bnf_codes", True)
        if m.numerator_is_list_of_bnf_codes:
            m.numerator_bnf_codes = get_num_or_denom_bnf_codes(m, "numerator", end_date)

    else:
        if m.numerator_type == "bnf_items":
            m.numerator_columns = "SUM(items) AS numerator"
        elif m.numerator_type == "bnf_quantity":
            m.numerator_columns = "SUM(quantity) AS numerator"
        elif m.numerator_type == "bnf_cost":
            m.numerator_columns = "SUM(actual_cost) AS numerator"
        else:
            assert False, measure_id

        m.numerator_from = "{hscic}.normalised_prescribing"

        m.numerator_bnf_codes_filter = v.get("numerator_bnf_codes_filter")
        m.numerator_bnf_codes_query = v.get("numerator_bnf_codes_query")
        m.numerator_bnf_codes = get_bnf_codes(
            m.numerator_bnf_codes_query, m.numerator_bnf_codes_filter
        )

        m.numerator_where = build_where(m.numerator_bnf_codes)
        m.numerator_is_list_of_bnf_codes = True

    m.denominator_type = v["denominator_type"]

    if m.denominator_type == "custom":
        m.denominator_columns = v["denominator_columns"]
        m.denominator_from = v["denominator_from"]
        m.denominator_where = v["denominator_where"]
        m.denominator_bnf_codes_query = v.get("denominator_bnf_codes_query")
        if m.denominator_from and "normalised_prescribing" in m.denominator_from:
            m.denominator_is_list_of_bnf_codes = v.get(
                "denominator_is_list_of_bnf_codes", True
            )
        else:
            m.denominator_is_list_of_bnf_codes = False
        m.denominator_bnf_codes = get_num_or_denom_bnf_codes(m, "denominator", end_date)

    elif m.denominator_type == "list_size":
        m.denominator_columns = "SUM(total_list_size / 1000.0) AS denominator"
        m.denominator_from = "{hscic}.practice_statistics"
        m.denominator_where = "1 = 1"
        m.denominator_bnf_codes_query = None
        m.denominator_is_list_of_bnf_codes = False

    elif m.denominator_type == "star_pu_antibiotics":
        m.denominator_columns = "CAST(JSON_EXTRACT(MAX(star_pu), '$.oral_antibacterials_item') AS FLOAT64) AS denominator"
        m.denominator_from = "{hscic}.practice_statistics"
        m.denominator_where = "1 = 1"
        m.denominator_bnf_codes_query = None
        m.denominator_is_list_of_bnf_codes = False

    else:
        if m.denominator_type == "bnf_items":
            m.denominator_columns = "SUM(items) AS denominator"
        elif m.denominator_type == "bnf_quantity":
            m.denominator_columns = "SUM(quantity) AS denominator"
        elif m.denominator_type == "bnf_cost":
            m.denominator_columns = "SUM(actual_cost) AS denominator"
        else:
            assert False, measure_id

        m.denominator_from = "{hscic}.normalised_prescribing"

        m.denominator_bnf_codes_filter = v.get("denominator_bnf_codes_filter")
        m.denominator_bnf_codes_query = v.get("denominator_bnf_codes_query")
        m.denominator_bnf_codes = get_bnf_codes(
            m.denominator_bnf_codes_query, m.denominator_bnf_codes_filter
        )

        m.denominator_where = build_where(m.denominator_bnf_codes)
        m.denominator_is_list_of_bnf_codes = True

    if not v.get("no_analyse_url"):
        m.analyse_url = build_analyse_url(m)

    m.save()

    return m


def get_bnf_codes(base_query, filter_):
    """Return list of BNF codes used to caluclate measure numerator/denominator
    values.

    At least one of `base_query` and `filter_` must not be None.

    `base_query` is a query to be run against BigQuery.  It should return
    results with a single column called `bnf_code`.

    `filter_` is a list of BNF code filters, which have one of the following
    forms:

      * a BNF code (eg 0501015P0AAABAB)
      * a BNF code prefix (eg 0501015P0)
      * a string including a SQL wildcard (eg 0501015P0%AB)

    Each filter can be negated by being prefixed with a ~.

    If both `base_query` and `filter_` are not None, the results of
    `base_query` are filtered by the filters specified in `filter_`.

    If `base_query` is None, all BNF codes are filtered by the filters
    specified in `filter_`.

    If `filter_` is None, the results of `base_query` are not filtered.
    """

    assert base_query or filter_

    query = build_bnf_codes_query(base_query, filter_)
    results = Client().query(query)

    # Before 2017, the published prescribing data included trailing spaces in
    # certain BNF codes.  We strip those here.  See #2447.
    bnf_codes = {row[0].strip() for row in results.rows}

    return sorted(bnf_codes & get_all_bnf_codes())


def build_bnf_codes_query(base_query, filter_):
    if base_query is None:
        base_query = "SELECT bnf_code FROM {hscic}.presentation"

    if filter_ is None:
        query = base_query
    else:
        where_clause = build_bnf_codes_query_where(filter_)
        query = """
        WITH subquery AS ({})
        SELECT bnf_code
        FROM subquery
        WHERE {}
        """.format(base_query, where_clause)

    return query


def build_bnf_codes_query_where(filter_):
    includes = []
    excludes = []

    for element in filter_:
        element = element.split("#")[0].strip()
        if element[0] == "~":
            excludes.append(build_bnf_codes_query_fragment(element[1:]))
        else:
            includes.append(build_bnf_codes_query_fragment(element))

    where_clause = ""

    if includes:
        where_clause += "(" + " OR ".join(includes) + ")"
        if excludes:
            where_clause += " AND "

    if excludes:
        where_clause += "NOT (" + " OR ".join(excludes) + ")"

    return where_clause


def build_bnf_codes_query_fragment(element):
    if element[:2] <= "19":
        # this is a drug
        full_code_length = 15
    else:
        # this is an appliance
        full_code_length = 11

    if "%" in element:
        fragment = "bnf_code LIKE '{}'"
    elif len(element) == full_code_length:
        fragment = "bnf_code = '{}'"
    else:
        fragment = "bnf_code LIKE '{}%'"

    return fragment.format(element)


def build_where(bnf_codes):
    if not bnf_codes:
        # hackety hack: this is for the end-to-end tests, where we don't import
        # enough prescribing data for there to be prescribing for every measure.
        return "1 = 0"

    bnf_code_sql_fragment = ", ".join('"{}"'.format(bnf_code) for bnf_code in bnf_codes)
    return "bnf_code IN ({})".format(bnf_code_sql_fragment)


def build_analyse_url(measure):
    params = {"measure": measure.id}

    if measure.numerator_is_list_of_bnf_codes:
        if not measure.numerator_bnf_codes:
            return
        params["numIds"] = ",".join(simplify_bnf_codes(measure.numerator_bnf_codes))
    else:
        return

    if measure.denominator_is_list_of_bnf_codes:
        if not measure.denominator_bnf_codes:
            return
        params["denomIds"] = ",".join(simplify_bnf_codes(measure.denominator_bnf_codes))
    elif measure.denominator_type == "list_size":
        params["denom"] = "total_list_size"
    elif measure.denominator_type == "star_pu_antibiotics":
        params["denom"] = "star_pu.oral_antibacterials_item"
    else:
        return

    querystring = urlencode(params)
    url = "{}#{}".format(reverse("analyse"), querystring)

    if len(url) > 5000:
        # Anything longer than 5000 characters takes too long to load.  In
        # practice, as of 12/19, only one measure is affected (silver).
        return

    return url


def get_num_or_denom_bnf_codes(measure, num_or_denom, end_date):
    """Return list of BNF codes used in calculation of numerator or denominator.  For
    most measures, this can be computed by constructing a query using the
    [num_or_denom]_from and [num_or_denom]_where attributes of the measure.  In a
    handful of cases this cannot be done, and the creator of the measure must provide a
    query ([num_or_denom]_bnf_codes_query) that can be used for this.

    For the lpzomnibus query, numerator_bnf_codes_query was produced with help from:

    >>> for m in Measure.objects.filter(tags__contains=['lowpriority']):
    ...   print '"' + m.numerator_where.strip() + ' OR",'
    """

    def get_measure_attr(name):
        full_name = num_or_denom + "_" + name
        return getattr(measure, full_name)

    if not get_measure_attr("is_list_of_bnf_codes"):
        return []

    if get_measure_attr("bnf_codes_query") is not None:
        sql = get_measure_attr("bnf_codes_query")
        three_months_ago = end_date - relativedelta(months=2)
        substitutions = {
            "three_months_ago": three_months_ago.strftime("%Y-%m-01 00:00:00")
        }

    else:
        # It would be nice if we could do:
        #
        #     SELECT normalised_prescribing.bnf_code FROM ...
        #
        # but BQ doesn't let you refer to an aliased table by its original
        # name, so we have to mess around like this.
        if "{hscic}.normalised_prescribing p" in get_measure_attr("from"):
            col_name = "p.bnf_code"
        else:
            col_name = "bnf_code"

        sql = """
        SELECT DISTINCT {col_name}
        FROM {from_}
        WHERE {where}
        ORDER BY bnf_code
        """.format(
            col_name=col_name,
            from_=get_measure_attr("from"),
            where=get_measure_attr("where"),
        )
        substitutions = None

    results = Client().query(sql, substitutions=substitutions)

    # Before 2017, the published prescribing data included trailing spaces in
    # certain BNF codes.  We strip those here.  See #2447.
    return sorted({row[0].strip() for row in results.rows})


class MeasureCalculation(object):
    """Logic for measure calculations in BQ."""

    def __init__(self, measure, start_date=None, end_date=None, verbose=False):
        self.verbose = verbose
        self.fpath = os.path.dirname(__file__)
        self.measure = measure
        self.start_date = start_date
        self.end_date = end_date

    def table_name(self, org_type):
        return "{}_data_{}".format(org_type, self.measure.id)

    def check_definition(self):
        """Check that the measure definition would result in runnable
        practice-level SQL
        """
        self.calculate_practice_ratios(dry_run=True)

    def calculate(self, bigquery_only=False):
        self.calculate_practices(bigquery_only=bigquery_only)
        self.calculate_orgs("pcn", bigquery_only=bigquery_only)
        self.calculate_orgs("ccg", bigquery_only=bigquery_only)
        self.calculate_orgs("stp", bigquery_only=bigquery_only)
        self.calculate_orgs("regtm", bigquery_only=bigquery_only)  # Regional Team
        self.calculate_global(bigquery_only=bigquery_only)

    def calculate_practices(self, bigquery_only=False):
        """Calculate ratios, centiles and (optionally) cost savings at a
        practice level, and write these to the database.

        """
        self.calculate_practice_ratios()
        self.add_practice_percent_rank()
        self.calculate_global_centiles_for_practices()
        if self.measure.is_cost_based:
            self.calculate_cost_savings_for_practices()
        if not bigquery_only:
            self.write_practice_ratios_to_database()

    def calculate_practice_ratios(self, dry_run=False):
        """Given a measure defition, construct a BigQuery query which computes
        numerator/denominator ratios for practices.

        See also comments in SQL.

        """
        m = self.measure
        numerator_aliases = ""
        denominator_aliases = ""
        aliased_numerators = ""
        aliased_denominators = ""
        for col in self._get_col_aliases("denominator"):
            denominator_aliases += ", denom.%s AS denom_%s" % (col, col)
            aliased_denominators += ", denom_%s" % col
        for col in self._get_col_aliases("numerator"):
            numerator_aliases += ", num.%s AS num_%s" % (col, col)
            aliased_numerators += ", num_%s" % col

        context = {
            "numerator_from": m.numerator_from,
            "numerator_where": m.numerator_where,
            "numerator_columns": self._columns_for_select("numerator"),
            "denominator_columns": self._columns_for_select("denominator"),
            "denominator_from": m.denominator_from,
            "denominator_where": m.denominator_where,
            "numerator_aliases": numerator_aliases,
            "denominator_aliases": denominator_aliases,
            "aliased_denominators": aliased_denominators,
            "aliased_numerators": aliased_numerators,
            "start_date": self.start_date,
            "end_date": self.end_date,
        }

        self.insert_rows_from_query(
            "practice_ratios", self.table_name("practice"), context, dry_run=dry_run
        )

    def add_practice_percent_rank(self):
        """Add a percentile rank to the ratios table"""
        self.insert_rows_from_query(
            "practice_percent_rank", self.table_name("practice"), {}
        )

    def calculate_global_centiles_for_practices(self):
        """Compute overall sums and centiles for each practice."""
        extra_fields = []
        # Add prefixes to the select columns so we can reference the joined
        # tables (bigquery flattens columns names from subqueries using table
        # alias + underscore)
        for col in self._get_col_aliases("numerator"):
            extra_fields.append("num_" + col)
        for col in self._get_col_aliases("denominator"):
            extra_fields.append("denom_" + col)
        extra_select_sql = ""
        for f in extra_fields:
            extra_select_sql += ", SUM(%s) as %s" % (f, f)
        if self.measure.is_cost_based and self.measure.is_percentage:
            # Cost calculations for percentage measures require extra columns.
            extra_select_sql += (
                ", "
                "(SUM(denom_cost) - SUM(num_cost)) / (SUM(denom_quantity)"
                "- SUM(num_quantity)) AS cost_per_denom,"
                "SUM(num_cost) / SUM(num_quantity) as cost_per_num"
            )

        context = {"extra_select_sql": extra_select_sql}

        self.insert_rows_from_query(
            "global_deciles_practices", self.table_name("global"), context
        )

    def calculate_cost_savings_for_practices(self):
        """Append cost savings column to the Practice working table"""
        if self.measure.is_percentage:
            query_id = "practice_percentage_measure_cost_savings"
        else:
            query_id = "practice_list_size_measure_cost_savings"
        self.insert_rows_from_query(query_id, self.table_name("practice"), {})

    def write_practice_ratios_to_database(self):
        """Copy the bigquery ratios data to the local postgres database.

        Uses COPY command via a CSV file for performance as this can
        be a very large number, especially when computing many months'
        data at once.  We drop and then recreate indexes to improve
        load time performance.

        """
        f = tempfile.TemporaryFile(mode="r+")
        writer = csv.DictWriter(f, fieldnames=MEASURE_FIELDNAMES)
        # Write the data we want to load into a file
        for datum in self.get_rows_as_dicts(self.table_name("practice")):
            datum["measure_id"] = self.measure.id
            if self.measure.is_cost_based:
                datum["cost_savings"] = json.dumps(convertSavingsToDict(datum))
            datum["percentile"] = normalisePercentile(datum["percentile"])
            datum = {fn: datum[fn] for fn in MEASURE_FIELDNAMES if fn in datum}
            writer.writerow(datum)
        # load data
        copy_str = "COPY frontend_measurevalue(%s) FROM STDIN "
        copy_str += "WITH (FORMAT CSV)"
        self.log(copy_str % ", ".join(MEASURE_FIELDNAMES))
        f.seek(0)
        with connection.cursor() as cursor:
            cursor.copy_expert(copy_str % ", ".join(MEASURE_FIELDNAMES), f)
        f.close()

    def calculate_orgs(self, org_type, bigquery_only=False):
        """Calculate ratios, centiles and (optionally) cost savings at a
        organisation level, and write these to the database.

        """
        self.calculate_org_ratios(org_type)
        self.add_org_percent_rank(org_type)
        self.calculate_global_centiles_for_orgs(org_type)
        if self.measure.is_cost_based:
            self.calculate_cost_savings_for_orgs(org_type)
        if not bigquery_only:
            self.write_org_ratios_to_database(org_type)

    def calculate_org_ratios(self, org_type):
        """Sums all the fields in the per-practice table, grouped by
        organisation. Stores in a new table.

        """
        numerator_aliases = denominator_aliases = ""
        for col in self._get_col_aliases("denominator"):
            denominator_aliases += ", SUM(denom_%s) AS denom_%s" % (col, col)
        for col in self._get_col_aliases("numerator"):
            numerator_aliases += ", SUM(num_%s) AS num_%s" % (col, col)

        context = {
            "denominator_aliases": denominator_aliases,
            "numerator_aliases": numerator_aliases,
        }
        self.insert_rows_from_query(
            "{}_ratios".format(org_type), self.table_name(org_type), context
        )

    def add_org_percent_rank(self, org_type):
        """Add a percentile rank to the ratios table"""
        self.insert_rows_from_query(
            "{}_percent_rank".format(org_type), self.table_name(org_type), {}
        )

    def calculate_global_centiles_for_orgs(self, org_type):
        """Adds centiles to the already-existing centiles table"""
        extra_fields = []
        # Add prefixes to the select columns so we can reference the joined
        # tables (bigquery flattens columns names from subqueries using table
        # alias + underscore)
        for col in self._get_col_aliases("numerator"):
            extra_fields.append("num_" + col)
        for col in self._get_col_aliases("denominator"):
            extra_fields.append("denom_" + col)
        extra_select_sql = ""
        for f in extra_fields:
            extra_select_sql += ", global_deciles.%s as %s" % (f, f)
        if self.measure.is_cost_based and self.measure.is_percentage:
            # Cost calculations for percentage measures require extra columns.
            extra_select_sql += (
                ", global_deciles.cost_per_denom AS cost_per_denom"
                ", global_deciles.cost_per_num AS cost_per_num"
            )

        context = {"extra_select_sql": extra_select_sql}

        self.insert_rows_from_query(
            "global_deciles_{}s".format(org_type), self.table_name("global"), context
        )

    def calculate_cost_savings_for_orgs(self, org_type):
        """Appends cost savings column to the organisation ratios table"""
        if self.measure.is_percentage:
            query_id = "{}_percentage_measure_cost_savings".format(org_type)
        else:
            query_id = "{}_list_size_measure_cost_savings".format(org_type)
        self.insert_rows_from_query(query_id, self.table_name(org_type), {})

    def write_org_ratios_to_database(self, org_type):
        """Create measure values for organisation ratios."""
        for datum in self.get_rows_as_dicts(self.table_name(org_type)):
            datum["measure_id"] = self.measure.id
            if self.measure.is_cost_based:
                datum["cost_savings"] = convertSavingsToDict(datum)
            datum["percentile"] = normalisePercentile(datum["percentile"])
            datum = {fn: datum[fn] for fn in MEASURE_FIELDNAMES if fn in datum}
            MeasureValue.objects.create(**datum)

    def calculate_global(self, bigquery_only=False):
        if self.measure.is_cost_based:
            self.calculate_global_cost_savings()
        if not bigquery_only:
            self.write_global_centiles_to_database()

    def calculate_global_cost_savings(self):
        """Sum cost savings at practice and CCG levels.

        Reads from the existing global table and writes back to it again.
        """
        self.insert_rows_from_query(
            "global_cost_savings", self.table_name("global"), {}
        )

    def write_global_centiles_to_database(self):
        """Write the globals data from BigQuery to the local database"""
        self.log(
            "Writing global centiles from %s to database" % self.table_name("global")
        )
        for d in self.get_rows_as_dicts(self.table_name("global")):
            regtm_cost_savings = {}
            stp_cost_savings = {}
            ccg_cost_savings = {}
            pcn_cost_savings = {}
            practice_cost_savings = {}
            d["measure_id"] = self.measure.id
            # The cost-savings calculations prepend columns with
            # global_. There is probably a better way of constructing
            # the query so this clean-up doesn't have to happen...
            new_d = {}
            for attr, value in d.items():
                new_d[attr.replace("global_", "")] = value
            d = new_d

            mg, _ = MeasureGlobal.objects.get_or_create(
                measure_id=self.measure.id, month=d["month"]
            )

            # Coerce decile-based values into JSON objects
            if self.measure.is_cost_based:
                practice_cost_savings = convertSavingsToDict(d, prefix="practice")
                pcn_cost_savings = convertSavingsToDict(d, prefix="pcn")
                ccg_cost_savings = convertSavingsToDict(d, prefix="ccg")
                stp_cost_savings = convertSavingsToDict(d, prefix="stp")
                regtm_cost_savings = convertSavingsToDict(d, prefix="regtm")
                mg.cost_savings = {
                    "regional_team": regtm_cost_savings,
                    "stp": stp_cost_savings,
                    "ccg": ccg_cost_savings,
                    "pcn": pcn_cost_savings,
                    "practice": practice_cost_savings,
                }
            practice_deciles = convertDecilesToDict(d, prefix="practice")
            pcn_deciles = convertDecilesToDict(d, prefix="pcn")
            ccg_deciles = convertDecilesToDict(d, prefix="ccg")
            stp_deciles = convertDecilesToDict(d, prefix="stp")
            regtm_deciles = convertDecilesToDict(d, prefix="regtm")
            mg.percentiles = {
                "regional_team": regtm_deciles,
                "stp": stp_deciles,
                "ccg": ccg_deciles,
                "pcn": pcn_deciles,
                "practice": practice_deciles,
            }

            # Set the rest of the data returned from bigquery directly
            # on the model
            for attr, value in d.items():
                setattr(mg, attr, value)
            mg.save()

    def insert_rows_from_query(self, query_id, table_name, ctx, dry_run=False):
        """Interpolate values from ctx into SQL identified by query_id, and
        insert results into given table.
        """
        query_path = os.path.join(self.fpath, "measure_sql", query_id + ".sql")
        ctx["measure_id"] = self.measure.id

        with open(query_path) as f:
            sql = f.read()

        self.get_table(table_name).insert_rows_from_query(
            sql, substitutions=ctx, dry_run=dry_run
        )

    def get_rows_as_dicts(self, table_name):
        """Iterate over the specified bigquery table, returning a dict for
        each row of data.

        """
        return self.get_table(table_name).get_rows_as_dicts()

    def get_table(self, table_name):
        client = Client("measures")
        return client.get_table(table_name)

    def log(self, message):
        if self.verbose:
            logger.warning(message)
        else:
            logger.info(message)

    def _get_col_aliases(self, num_or_denom):
        """Return column names referred to in measure definitions for both
        numerator or denominator.

        When we use nested SELECTs, we need to know which column names
        have been aliased in the inner SELECT so we can select them
        explicitly by name in the outer SELECT.

        """
        assert num_or_denom in ["numerator", "denominator"]
        cols = []
        cols = self._columns_for_select(num_or_denom)
        aliases = re.findall(r"AS ([a-z0-9_]+)", cols)
        return [x for x in aliases if x not in num_or_denom]

    def _columns_for_select(self, num_or_denom):
        """Parse measures definition for SELECT columns; add
        cost-savings-related columns when necessary.

        """
        assert num_or_denom in ["numerator", "denominator"]
        fieldname = "%s_columns" % num_or_denom
        val = getattr(self.measure, fieldname)
        if self.measure.is_cost_based and self.measure.is_percentage:
            # Cost calculations for percentage measures require extra columns.
            # (Include newline in case previous line ends in a comment)
            val += (
                "\n    , "
                "SUM(items) AS items, "
                "SUM(actual_cost) AS cost, "
                "SUM(quantity) AS quantity "
            )
        return val


@contextmanager
def conditional_constraint_and_index_reconstructor(enabled):
    if not enabled:
        yield
    else:
        with utils.constraint_and_index_reconstructor("frontend_measurevalue"):
            yield


def upload_supplementary_tables():
    client = Client("measures")

    for path in (Path(settings.APPS_ROOT) / "measures" / "tables").glob("*.csv"):
        t = client.get_table(path.stem)
        t.insert_rows_from_csv(path)
