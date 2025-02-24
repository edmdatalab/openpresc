"""
Downloads practice statistics data from BigQuery into the
`settings.MATRIXSTORE_IMPORT_DIR` directory
"""

import csv
import gzip
import logging
import os

from django.conf import settings

from gcutils.bigquery import Client

from .common import get_practice_stats_filename, get_temp_filename
from .dates import generate_dates


logger = logging.getLogger(__name__)


def download_practice_stats(end_date, months=None):
    for date in generate_dates(end_date, months=months):
        ensure_stats_downloaded_for_date(date)


def ensure_stats_downloaded_for_date(date):
    """
    Download practice statistics for date, or do nothing if already downloaded
    """
    filename = get_practice_stats_filename(date)
    if os.path.exists(filename):
        return
    client = Client("hscic")
    check_stats_in_bigquery(date, client)
    logger.info("Downloading practice statistics for %s", date)
    temp_name = get_temp_filename(filename)
    result = client.query(
        """
        SELECT *
        FROM {hscic}.practice_statistics_all_years
        WHERE month = TIMESTAMP("%s")
        """
        % (date,)
    )

    with gzip.open(temp_name, "wt") as f:
        writer = csv.writer(f)
        writer.writerow(result.field_names)
        for row in result.rows:
            writer.writerow(row)
    os.rename(temp_name, filename)


def check_stats_in_bigquery(date, client):
    """
    Assert that practice statistics for date is in BigQuery.
    """
    if not settings.CHECK_DATA_IN_BQ:
        return
    results = client.query(
        """
        SELECT COUNT(*)
        FROM {hscic}.practice_statistics_all_years
        WHERE month = TIMESTAMP("%s")
        """
        % (date,)
    )
    assert results.rows[0][0] > 0
