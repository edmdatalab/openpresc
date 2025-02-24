"""
Download prescribing data from BigQuery to gzipped CSV files in the
`settings.MATRIXSTORE_IMPORT_DIR` directory
"""

import glob
import logging
import os

from django.conf import settings

from gcutils.bigquery import Client, StorageClient

from .common import (
    get_prescribing_filename,
    get_filename_for_download,
    get_temp_filename,
)
from .dates import generate_dates
from .sort_and_merge_gzipped_csv_files import sort_and_merge_gzipped_csv_files


logger = logging.getLogger(__name__)


# We need a way to indicate that an export from a BigQuery table has completed
# (sadly BigQuery doesn't do this for us) so we create a "sentinel" file once
# the export has finished using the suffix below
SENTINEL_SUFFIX = "done"


def download_prescribing(end_date, months=None):
    # Getting a local copy of prescribing data for a given month is a
    # multi-stage process:
    #
    # 1. Extract the data for that month from the main prescribing table into
    #    its own table
    # 2. Export that table to Google Cloud Storage (which will end up sharded
    #    into multiple files)
    # 3. Download those shard files
    # 4. Consolidate the shards into a single file, sorted by BNF code
    bq_client = Client("prescribing_export")
    bucket = StorageClient().bucket()
    # To determine what steps to execute we need to work backwards through this
    # process. For instance, if we already have data downloaded for a given
    # date then there is no point checking whether the corresponding files
    # exist in Google Cloud Storage (they may have been deleted as part of a
    # cleanup in any case). So we start with all the dates we want and then filter
    # out anyting we've already got.
    dates = generate_dates(end_date, months=months)
    dates_to_consolidate = filter_dates_to_consolidate(dates)
    dates_to_download = filter_dates_to_download(dates_to_consolidate)
    dates_to_export = filter_dates_to_export(dates_to_download, bucket)
    dates_to_extract = filter_dates_to_extract(dates_to_export, bq_client)
    # Once we know what needs doing we loop over the required dates and carry
    # out the tasks. There's obvious scope for speeding this up by doing things
    # in parallel, but we keep it simple for now.
    for date in dates:
        if date in dates_to_extract:
            extract_data_for_date(date, bq_client)
        if date in dates_to_export:
            export_data_for_date(date, bq_client, bucket)
        if date in dates_to_download:
            download_data_for_date(date, bucket)
        if date in dates_to_consolidate:
            consolidate_data_for_date(date)
        clean_up_downloaded_files(date)


def filter_dates_to_consolidate(dates):
    """
    Return only those dates for which a consolidated prescribing file (i.e. a
    single gzipped CSV file containing all prescribing for a given month) does
    not exist
    """
    return [
        date for date in dates if not os.path.exists(get_prescribing_filename(date))
    ]


def filter_dates_to_download(dates):
    """
    Return only those dates for which a complete set of prescribing data has
    not yet been downloaded from Google Cloud Storage
    """
    return [date for date in dates if not download_is_complete(date)]


def download_is_complete(date):
    """
    Has the process of downloading prescribing data for this date finished
    successfully?
    """
    return os.path.exists(local_storage_prefix_for_date(date) + SENTINEL_SUFFIX)


def filter_dates_to_export(dates, bucket):
    """
    Return only those dates for which prescribing data has not yet been
    exported from BigQuery into Google Cloud Storage
    """
    missing_dates = []
    for date in dates:
        sentinel_file = remote_storage_prefix_for_date(date) + SENTINEL_SUFFIX
        if not bucket.blob(sentinel_file).exists():
            missing_dates.append(date)
    return missing_dates


def filter_dates_to_extract(dates, bq_client):
    """
    Return only those dates for which prescribing data has not yet been
    extracted from the main prescribing table into a single one-month-only
    table on BigQuery
    """
    table_ids = set([t.table_id for t in bq_client.list_tables()])
    return [date for date in dates if table_id_for_date(date) not in table_ids]


def extract_data_for_date(date, bq_client):
    """
    Extract prescribing data for the given month into its own table on BigQuery
    """
    check_prescribing_data_in_bigquery(date, bq_client)
    table_id = table_id_for_date(date)
    logger.info("Extracting data for %s into table %s", date, table_id)
    table = bq_client.get_table(table_id)
    table.insert_rows_from_query(
        """
        SELECT
          bnf_code, practice, month, items, quantity, net_cost, actual_cost
        FROM
          {hscic}.prescribing_v2
        WHERE
          month = TIMESTAMP("{month}")
        """,
        substitutions={"month": date},
    )


def check_prescribing_data_in_bigquery(date, bq_client):
    """
    Assert that prescribing data for date is in BigQuery.
    """
    if not settings.CHECK_DATA_IN_BQ:
        return
    results = bq_client.query(
        """
        SELECT COUNT(*)
        FROM {hscic}.prescribing_v2
        WHERE month = TIMESTAMP("%s")
        """
        % (date,)
    )
    assert results.rows[0][0] > 0


def export_data_for_date(date, bq_client, bucket):
    """
    Export the one-month-only prescribing table for the given date to Google
    Cloud Storage as gzipped CSV
    """
    table_id = table_id_for_date(date)
    prefix = remote_storage_prefix_for_date(date)
    logger.info("Exporting data for %s into gs://%s/%s*", date, bucket.name, prefix)
    table = bq_client.get_table(table_id)
    table.export_to_storage(prefix)
    sentinel_file = prefix + SENTINEL_SUFFIX
    bucket.blob(sentinel_file).upload_from_string("done")


def download_data_for_date(date, bucket):
    """
    Download exported prescribing data for the given date from Google Cloud
    Storage
    """
    prefix = remote_storage_prefix_for_date(date)
    blobs = bucket.list_blobs(prefix=prefix)
    # Sort the files so we always download the sentinel file last
    blobs = sorted(blobs, key=lambda blob: blob.name)
    logger.info(
        "Downloading %s files from gs://%s/%s*", len(blobs), bucket.name, prefix
    )
    for blob in blobs:
        local_name = get_filename_for_download(blob.name)
        if not os.path.exists(local_name):
            temp_name = get_temp_filename(local_name)
            blob.download_to_filename(temp_name)
            os.rename(temp_name, local_name)
            logger.info("Downloaded %s", blob.name)
    if not download_is_complete(date):
        raise RuntimeError(
            "Export for {date} looks incomplete (no sentinel file)".format(date=date)
        )


def consolidate_data_for_date(date):
    """
    Consolidate downloaded prescribing data for the given date into a single
    gzipped CSV file, sorted by (bnf_code, practice, month)
    """
    pattern = "{}*.csv.gz".format(local_storage_prefix_for_date(date))
    input_files = glob.glob(pattern)
    target_file = get_prescribing_filename(date)
    temp_file = get_temp_filename(target_file)
    logger.info("Consolidating %s data files into %s", len(input_files), target_file)
    sort_and_merge_gzipped_csv_files(
        input_files, temp_file, ("bnf_code", "practice", "month")
    )
    os.rename(temp_file, target_file)


def clean_up_downloaded_files(date):
    """
    Delete downloaded export shards; now that we have the consolidated data we
    no longer need these
    """
    pattern = local_storage_prefix_for_date(date) + "*"
    # Delete in reverse order so that the sentinel file gets deleted first
    files = sorted(glob.glob(pattern), reverse=True)
    for filename in files:
        os.unlink(filename)


def table_id_for_date(date):
    """
    Return BigQuery table name to hold the extracted prescribing data for the
    given date
    """
    return "prescribing_{}".format(date[:7].replace("-", "_"))


def remote_storage_prefix_for_date(date):
    """
    Return the path prefix on Google Cloud Storage for exporting prescribing
    data for this date

    BigQuery will split the export over multiple files so this is a prefix,
    rather than a filename
    """
    # The BQ_NONCE setting is only defined during tests so that each test run
    # writes its files to a unique location in GCS
    bq_nonce = getattr(settings, "BQ_NONCE", None)
    suffix = "_{}".format(bq_nonce) if bq_nonce else ""
    return "prescribing_exports{}/{}".format(suffix, filename_prefix_for_date(date))


def local_storage_prefix_for_date(date):
    """
    Return the path prefix on the local filesystem where prescribing data for
    this date will be exported to
    """
    return get_filename_for_download(filename_prefix_for_date(date))


def filename_prefix_for_date(date):
    return "prescribing_{}_".format(date[:7].replace("-", "_"))
