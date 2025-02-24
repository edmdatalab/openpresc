"""
Sets up a SQLite database ready to have prescribing data and practice
statistics imported into it.

Data on practices and presentations is obtained by connecting to BigQuery.
"""

import logging
import os
import sqlite3

from gcutils.bigquery import Client

from .common import get_temp_filename
from .dates import generate_dates


logger = logging.getLogger(__name__)


SCHEMA_SQL = """
    CREATE TABLE presentation (
        bnf_code TEXT,
        -- The below columns will contain the actual prescribing data as
        -- serialized matrices of shape (number of practices, number of months)
        items BLOB,
        quantity BLOB,
        actual_cost BLOB,
        net_cost BLOB,

        PRIMARY KEY (bnf_code)
    );

   -- This table will contain a single row giving totals pre-calculated from
   -- the above data as these are slightly too expensive to calculate at
   -- runtime
    CREATE TABLE all_presentations (
        -- The below columns will contain total prescribing over all
        -- presentations as serialized matrices of shape (number of practices,
        -- number of months)
        items BLOB,
        quantity BLOB,
        actual_cost BLOB,
        net_cost BLOB
    );

    CREATE TABLE practice_statistic (
        name TEXT,
        -- The "value" column will contain the actual statistics as serialized
        -- matrices of shape (number of practices, number of months)
        value BLOB,

        PRIMARY KEY (name)
    );

    -- Maps each practice code to its corresponding row offset in the data matrix
    CREATE TABLE practice (
        offset INTEGER,
        code TEXT UNIQUE,

        PRIMARY KEY (offset)
    );

    -- Maps each date to its corresponding column offset in the data matrix
    CREATE TABLE date (
        offset INTEGER,
        date TEXT UNIQUE,

        PRIMARY KEY (offset)
    );
"""


def init_db(end_date, sqlite_path, months=None):
    if os.path.exists(sqlite_path):
        raise RuntimeError("File already exists at: " + sqlite_path)
    logger.info("Initialising SQLite database at %s", sqlite_path)
    sqlite_path = os.path.abspath(sqlite_path)
    temp_filename = get_temp_filename(sqlite_path)
    sqlite_conn = sqlite3.connect(temp_filename)
    bq_conn = Client("hscic")
    sqlite_conn.executescript(SCHEMA_SQL)
    dates = generate_dates(end_date, months=months)
    import_dates(sqlite_conn, dates)
    import_practices(bq_conn, sqlite_conn, dates)
    sqlite_conn.commit()
    sqlite_conn.close()
    os.rename(temp_filename, sqlite_path)


def import_dates(sqlite_conn, dates):
    sqlite_conn.executemany(
        "INSERT INTO date (offset, date) VALUES (?, ?)", enumerate(dates)
    )


def import_practices(bq_conn, sqlite_conn, dates):
    """
    Query BigQuery for the list of active practice codes and write them to
    SQLite

    Active in this context just means that we have data for them in the
    relevant period, either prescribing data or practice statistics; there's no
    sense in having rows in the matrices which will never contain data.
    """
    date_start = min(dates)
    date_end = max(dates)
    logger.info(
        "Querying for active practice codes between %s and %s", date_start, date_end
    )
    sql = """
        SELECT DISTINCT practice FROM {hscic}.prescribing_v2
          WHERE month BETWEEN TIMESTAMP('%(start)s') AND TIMESTAMP('%(end)s')
        UNION DISTINCT
        SELECT DISTINCT practice FROM {hscic}.practice_statistics_all_years
          WHERE month BETWEEN TIMESTAMP('%(start)s') AND TIMESTAMP('%(end)s')
        ORDER BY practice
        """
    result = bq_conn.query(sql % {"start": date_start, "end": date_end})
    practice_codes = [row[0] for row in result.rows]
    logger.info("Writing %s practice codes to SQLite", len(practice_codes))
    sqlite_conn.executemany(
        "INSERT INTO practice (offset, code) VALUES (?, ?)", enumerate(practice_codes)
    )
