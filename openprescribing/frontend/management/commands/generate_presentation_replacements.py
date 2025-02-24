"""This command deals with the fact that the NHS mutates its
prescribing identifiers periodically, making tracking changes through
time very difficult.

As of 2017 (but this is expected to change within the next year), NHS
England uses a derivative of the BNF (British National Formulary)
codes to identify each presentation dispensed, called the NHS Pseudo
Classification.

Unfortunately, both the BNF and the NHS make changes to codes
periodically. Sometimes a chemical gets a new code, or sometimes it
moves to a new section. Because the BNF code includes the section in
the first few characters of the code, just reclassifying a drug means
its unique identifier has changed.  This makes tracking that drug
through time impossible.

The situation is further complicated that the BNF no longer maintains
its old classification, so the Pseudo codes now used by the NHS no
longer necessarily correspond with official BNF codes at all.

The situation is expected to improve with the introduction of ePACT2
and the moving of prescribing data to use SNOMED CT codes as per dm+d.

For the being, this method aims to normalise all codes in our dataset
so that prescribing is always indexed by the most recent version of
the Pseudo BNF Code.

We achieve this by applying a mapping of old code to new code which
has been applied annualy by NHSBSA to create their Pseduo code list.
This mapping has been supplied to us in private correspondence with
the NHS BSA, and is reproduced (with some corrections to obvious
typos, etc) in the files accompanying this module.

The normalisation process is as follows:

For each old code -> new code mapping, in reverse order of date
(i.e. starting with the most recent mappings):

* If the code is at the section, paragraph, chemical or product level,
  mark our internal corresponding model for that classification as no
  longer current

* Find every presentation matching the new code (or classification),
  and ensure a presentation exists matching the old code.  Create a
  reference to the new presentation code from the old one.

* Create a table of mappings from old codes to the most recent current
  code (taking into account multlple code changes)

* Replace all the codes that have new normalised versions in all local
  version of the prescribing data.  (If this command ever needs
  running again, some time could be saved by applying this only to
  prescribing data downloaded since the last this command was run)

* Iterate over all known BNF codes, sections, paragraphs etc, looking
  for codes which have never been prescribed, and mark these as not
  current.  This is necessary because sometimes our mappings involve a
  chemical-level change without making this explicit (i.e. a 15
  character BNF code mapping has been supplied, but in fact it's the
  Chemical part of the code that has changed).  In these cases, we
  can't tell if the Chemical is now redundant without checking to see
  if there is any other prescribing under that code.  This process
  also has the useful side effect of removing the (many thousands of)
  codes that have never actually been prescribed, and are therefore
  unhelpful noise in our user interface.

  * The problem with this approach is that recently added codes may
    not yet have prescribing, but may do so at some point in the
    future. Therefore, there is a `refresh_class_currency` method
    within the `import_hscic_prescribing` management command, which
    iterates over all sections, paragraphs, chemicals and products
    currently listed as not current, and checks to see if there has
    been any prescribing this month.

This command should in theory only have to be run once a year, as
we've been told mappings only happen this frequently.  And in theory,
2017 is the last year of using BNF codes.

"""

import csv
import glob
import logging
import re
import tempfile

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from frontend import bq_schemas as schemas
from frontend.models import Chemical, Presentation, Product, Section
from gcutils.bigquery import Client, TableExporter

logger = logging.getLogger(__name__)


def create_code_mapping(filenames):
    """Given a list of filenames containing tab-delimited old->new BNF
    code changes:

      * find the matching entity in our local database (e.g. Section
        or Presentation, etc); mark the old version as
      * no-longer-current add a reference (by BNF code) to its
      * replacement

    """
    bnf_map = []
    removed_mappings = []
    for f in filenames:
        for line in open(f, "r"):
            line = line.strip()
            if not line:
                continue  # skip blank lines
            elif line.startswith("#"):
                continue  # skip comments
            elif "\t" not in line:
                raise CommandError("Input lines must be tab delimited: %s" % line)
            prev_code, next_code = line.split("\t")
            prev_code = prev_code.strip()
            next_code = next_code.strip()
            if next_code.upper() == "REMOVED":
                removed_mappings.append(prev_code)
            elif re.match(r"^[0-9A-Z]+$", next_code):
                bnf_map.append((prev_code, next_code))

    with transaction.atomic():
        with connection.cursor() as cursor:
            # Perform the delete manually to avoid spurious integrity complaints from
            # Django (some deleted rows are referred to by other rows – but they are
            # also being deleted)
            cursor.execute(
                f"DELETE FROM {Presentation._meta.db_table} WHERE replaced_by_id IS NOT NULL"
            )
        for prev_code, next_code in bnf_map:
            if len(prev_code) <= 7:  # section, subsection, paragraph, subparagraph
                Section.objects.filter(bnf_id__startswith=prev_code).update(
                    is_current=False
                )
            elif len(prev_code) == 9:
                Chemical.objects.filter(bnf_code=prev_code).update(is_current=False)
            elif len(prev_code) == 11:
                Product.objects.filter(bnf_code=prev_code).update(is_current=False)
            matches = Presentation.objects.filter(bnf_code__startswith=next_code)
            for row in matches:
                replaced_by_id = row.pk
                old_bnf_code = prev_code + replaced_by_id[len(prev_code) :]
                try:
                    old_version = Presentation.objects.get(pk=old_bnf_code)
                except Presentation.DoesNotExist:
                    old_version = row
                    old_version.pk = None  # allows us to clone
                    old_version.bnf_code = old_bnf_code
                old_version.replaced_by_id = replaced_by_id
                old_version.save()
        # Remove any mappings which are marked for removal
        Presentation.objects.filter(bnf_code__in=removed_mappings).update(
            replaced_by=None
        )


def create_bigquery_table():
    """Create a table in bigquery of all BNF codes for presentations that
    are no longer current, along with the BNF code of their latest
    incarnation

    """
    # output a row for each presentation and its ultimate replacement
    with tempfile.NamedTemporaryFile(mode="r+") as csv_file:
        writer = csv.writer(csv_file)
        for p in Presentation.objects.filter(replaced_by__isnull=False):
            writer.writerow([p.bnf_code, p.current_version.bnf_code])
        csv_file.seek(0)
        client = Client("hscic")
        table = client.get_or_create_table("bnf_map", schemas.BNF_MAP_SCHEMA)
        table.insert_rows_from_csv(csv_file.name, schemas.BNF_MAP_SCHEMA)


def write_zero_prescribing_codes_table(level):
    """Given a BNF `level` (`section`, `chapter`, `paragraph`, etc), write
    a table in bigquery listing all such levels that have zero prescribing.

    Returns a bigquery Table.

    """
    logger.info("Scanning %s to see if it has zero prescribing" % level)
    sql = """
    SELECT
      bnf.%s
    FROM
      {hscic}.normalised_prescribing AS prescribing
    RIGHT JOIN
      {hscic}.bnf bnf
    ON
      prescribing.bnf_code = bnf.presentation_code
    WHERE (
        bnf.presentation_code NOT LIKE '2%%'  -- appliances, etc
    )
    GROUP BY
      bnf.%s
    HAVING
      COUNT(prescribing.bnf_code) = 0
    """ % (
        level,
        level,
    )
    client = Client("tmp_eu")
    table = client.get_table("unused_codes_%s" % level)
    table.insert_rows_from_query(sql)
    return table


def get_csv_of_empty_classes_for_level(level):
    """Using BigQuery, make a CSV of BNF codes at the given level
    (e.g. `section`, `paragraph`) that have never had any prescribing.

    Returns a path to the CSV

    """
    temp_table = write_zero_prescribing_codes_table(level)
    storage_prefix = "tmp/{}".format(temp_table.table_id)
    exporter = TableExporter(temp_table, storage_prefix)

    logger.info("Copying %s to %s" % (temp_table.table_id, storage_prefix))
    exporter.export_to_storage()

    path = "/%s/%s.csv" % (tempfile.gettempdir(), temp_table.table_id)
    logger.info("Downloading %s to %s" % (storage_prefix, path))
    with open(path, "w") as f:
        exporter.download_from_storage_and_unzip(f)
    return path


def cleanup_empty_classes():
    """In BigQuery, find all BNF classes/levels (e.g. `section`,
    `paragraph`) that have never had any prescribing, and mark their
    corresponding entities in our local database as not current.

    """
    classes = [
        ("section_code", Section, "bnf_id"),
        ("para_code", Section, "bnf_id"),
        ("subpara_code", Section, "bnf_id"),
        ("chemical_code", Chemical, "bnf_code"),
        ("product_code", Product, "bnf_code"),
        ("presentation_code", Presentation, "bnf_code"),
    ]
    for class_column, model, bnf_field in classes:
        csv_path = get_csv_of_empty_classes_for_level(class_column)
        logger.info("Marking all classes in %s as not current" % csv_path)
        with transaction.atomic():
            with open(csv_path, "r") as f:
                reader = csv.reader(f)
                next(reader)  # skip header
                for row in reader:
                    code = row[0]
                    kwargs = {bnf_field: code}
                    try:
                        obj = model.objects.get(**kwargs)
                        obj.is_current = False
                        obj.save()
                    except model.DoesNotExist:
                        #  Reasons this might happen without cause for alarm:
                        #
                        #  * We don't create paragraphs ending in
                        #    zero, as these data properly belong with
                        #   their section;
                        #
                        #  * We don't currently import appliances and similar
                        logger.warn("Couldn't find %s(pk=%s)" % (model.__name__, code))


def update_existing_prescribing():
    """For every child table of the prescribing table, update all the data
    so that the BNF codes are always normalised to the current BNF
    code.

    """
    update_sql = """
        UPDATE %s
        SET presentation_code = '%s'
        WHERE presentation_code = '%s'"""
    tables_sql = """
        SELECT
          c.relname AS child
        FROM
          pg_inherits
        JOIN pg_class AS c
          ON (inhrelid=c.oid)
        JOIN pg_class AS p
          ON (inhparent=p.oid)
         WHERE p.relname = 'frontend_prescription'"""

    with connection.cursor() as cursor:
        cursor.execute(tables_sql)
        for row in cursor.fetchall():
            table_name = row[0]
            with transaction.atomic():
                for p in Presentation.objects.filter(replaced_by__isnull=False):
                    cursor.execute(
                        update_sql
                        % (table_name, p.current_version.bnf_code, p.bnf_code)
                    )


class Command(BaseCommand):
    args = ""
    help = "Imports presentation replacements."

    def add_arguments(self, parser):
        parser.add_argument(
            "filenames",
            nargs="*",
            help="This argument only exists for tests. Normally the command "
            "is expected to work on the contents of `presentation_commands/`",
        )

    def handle(self, *args, **options):
        if options["filenames"]:
            filenames = reversed(sorted(options["filenames"]))
        else:
            filenames = reversed(
                sorted(
                    glob.glob(
                        "frontend/management/commands/presentation_replacements/*.txt"
                    )
                )
            )
        create_code_mapping(filenames)
        create_bigquery_table()
        update_existing_prescribing()
        cleanup_empty_classes()
