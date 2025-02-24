"""
Import practice statistics from downloaded CSV files into SQLite
"""

import csv
import gzip
import json
import logging
import os.path
import sqlite3

from matrixstore.matrix_ops import sparse_matrix, finalise_matrix
from matrixstore.serializer import serialize_compressed

from .common import get_practice_stats_filename


logger = logging.getLogger(__name__)


class MissingHeaderError(Exception):
    pass


def import_practice_stats(sqlite_path):
    if not os.path.exists(sqlite_path):
        raise RuntimeError("No SQLite file at: {}".format(sqlite_path))
    connection = sqlite3.connect(sqlite_path)
    dates = [date for (date,) in connection.execute("SELECT date FROM date")]
    practice_statistics = get_practice_statistics_for_dates(dates)
    write_practice_stats(connection, practice_statistics)
    connection.commit()
    connection.close()


def write_practice_stats(connection, practice_statistics):
    cursor = connection.cursor()
    # Map practice codes and date strings to their corresponding row/column in
    # the matrix
    practices = dict(cursor.execute("SELECT code, offset FROM practice"))
    dates = dict(cursor.execute("SELECT date, offset FROM date"))
    matrices = build_matrices(practice_statistics, practices, dates)
    for statistic_name, matrix in matrices:
        # Once we can use SQLite v3.24.0 which has proper UPSERT support we
        # won't need to do this
        cursor.execute(
            "INSERT OR IGNORE INTO practice_statistic (name) VALUES (?)",
            [statistic_name],
        )
        cursor.execute(
            """
            UPDATE practice_statistic SET value=? WHERE name=?
            """,
            [serialize_compressed(matrix), statistic_name],
        )


def get_practice_statistics_for_dates(dates):
    """
    Yield all practice statistics for the given dates as tuples of the form:

        statistic_name, practice_code, date, statistic_value
    """
    dates = sorted(dates)
    filenames = [get_practice_stats_filename(date) for date in dates]
    missing_files = [f for f in filenames if not os.path.exists(f)]
    if missing_files:
        raise RuntimeError(
            "Some required CSV files were missing:\n  {}".format(
                "\n  ".join(missing_files)
            )
        )
    for filename in filenames:
        logger.info("Reading practice statistics from %s", filename)
        with gzip.open(filename, "rt") as f:
            for row in parse_practice_statistics_csv(f):
                yield row


def parse_practice_statistics_csv(input_stream):
    """
    Accepts a stream of CSV and yields practice stastics as tuples of the form:

        statistic_name, practice_code, date, statistic_value
    """
    reader = csv.reader(input_stream)
    headers = next(reader)
    try:
        date_col = headers.index("month")
        practice_col = headers.index("practice")
        star_pu_col = headers.index("star_pu")
    except ValueError as e:
        raise MissingHeaderError(str(e))
    other_headers = [
        (i, header)
        for (i, header) in enumerate(headers)
        if i not in (date_col, practice_col, star_pu_col) and header != "pct_id"
    ]
    for row in reader:
        # We only need the YYYY-MM-DD part of the date
        date = row[date_col][:10]
        # These sometimes have trailing spaces in the CSV
        practice = row[practice_col].strip()
        for i, statistic_name in other_headers:
            value_str = row[i]
            value = float(value_str) if "." in value_str else int(value_str)
            yield statistic_name, practice, date, value
        star_pu = json.loads(row[star_pu_col])
        for star_pu_name, value in star_pu.items():
            yield "star_pu." + star_pu_name, practice, date, value


def build_matrices(practice_statistics, practices, dates):
    """
    Accepts an iterable of practice statistics, plus mappings of pratice codes
    and date strings to their respective row/column offsets. Yields pairs of
    the form:

        statistic_name, matrix

    Where the matrix contains the values for that statistic for each practice
    and date.
    """
    max_row = max(practices.values())
    max_col = max(dates.values())
    shape = (max_row + 1, max_col + 1)
    matrices = {}
    for statistic_name, practice, date, value in practice_statistics:
        try:
            practice_offset = practices[practice]
        except KeyError:
            # Because we download all practice statistics for a given date
            # range we end up including practices which have not prescribed at
            # all during this period and hence which aren't included in our
            # list of known practices. We just want to ignore these.
            continue
        date_offset = dates[date]
        try:
            matrix = matrices[statistic_name]
        except KeyError:
            matrix = sparse_matrix(shape, integer=isinstance(value, int))
            matrices[statistic_name] = matrix
        matrix[practice_offset, date_offset] = value
    logger.info("Writing %s practice statistics matrices to SQLite", len(matrices))
    for statistic_name, matrix in sorted(matrices.items()):
        yield statistic_name, finalise_matrix(matrix)
