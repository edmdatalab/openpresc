"""
Import prescribing data from CSV files into SQLite
"""

from collections import namedtuple
import csv
from itertools import groupby
import logging
import os
import sqlite3
import gzip
import heapq

from matrixstore.matrix_ops import sparse_matrix, finalise_matrix
from matrixstore.serializer import serialize_compressed

from .common import get_prescribing_filename


logger = logging.getLogger(__name__)


MatrixRow = namedtuple("MatrixRow", "bnf_code items quantity actual_cost net_cost")


class MissingHeaderError(Exception):
    pass


def import_prescribing(filename):
    if not os.path.exists(filename):
        raise RuntimeError("No SQLite file at: {}".format(filename))
    connection = sqlite3.connect(filename)
    # Trade crash-safety for insert speed
    connection.execute("PRAGMA synchronous=OFF")
    dates = [date for (date,) in connection.execute("SELECT date FROM date")]
    prescriptions = get_prescriptions_for_dates(dates)
    write_prescribing(connection, prescriptions)
    connection.commit()
    connection.close()


def write_prescribing(connection, prescriptions):
    cursor = connection.cursor()
    # Map practice codes and date strings to their corresponding row/column
    # offset in the matrix
    practices = dict(cursor.execute("SELECT code, offset FROM practice"))
    dates = dict(cursor.execute("SELECT date, offset FROM date"))
    matrices = build_matrices(prescriptions, practices, dates)
    rows = format_as_sql_rows(matrices, connection)
    cursor.executemany(
        """
        UPDATE presentation SET items=?, quantity=?, actual_cost=?, net_cost=?
        WHERE bnf_code=?
        """,
        rows,
    )


def get_prescriptions_for_dates(dates):
    """
    Yield all prescribing data for the given dates as tuples of the form:

        bnf_code, practice_code, date, items, quantity, actual_cost, net_cost

    sorted by bnf_code, practice and date.
    """
    dates = sorted(dates)
    filenames = [get_prescribing_filename(date) for date in dates]
    missing_files = [f for f in filenames if not os.path.exists(f)]
    if missing_files:
        raise RuntimeError(
            "Some required CSV files were missing:\n  {}".format(
                "\n  ".join(missing_files)
            )
        )
    prescribing_streams = [read_gzipped_prescribing_csv(f) for f in filenames]
    # We assume that the input files are already sorted by (bnf_code, practice,
    # month) so to ensure that the combined stream is sorted we just need to
    # merge them correctly, which heapq.merge handles nicely for us
    return heapq.merge(*prescribing_streams)


def read_gzipped_prescribing_csv(filename):
    with gzip.open(filename, "rt") as f:
        for row in parse_prescribing_csv(f):
            yield row


def parse_prescribing_csv(input_stream):
    """
    Accepts a stream of CSV and yields prescribing data as tuples of the form:

        bnf_code, practice_code, date, items, quantity, actual_cost, net_cost
    """
    reader = csv.reader(input_stream)
    headers = next(reader)
    try:
        bnf_code_col = headers.index("bnf_code")
        practice_col = headers.index("practice")
        date_col = headers.index("month")
        items_col = headers.index("items")
        quantity_col = headers.index("quantity")
        actual_cost_col = headers.index("actual_cost")
        net_cost_col = headers.index("net_cost")
    except ValueError as e:
        raise MissingHeaderError(str(e))
    for row in reader:
        yield (
            # These sometimes have trailing spaces in the CSV
            row[bnf_code_col].strip(),
            row[practice_col].strip(),
            # We only need the YYYY-MM-DD part of the date
            row[date_col][:10],
            int(row[items_col]),
            float(row[quantity_col]),
            pounds_to_pence(row[actual_cost_col]),
            pounds_to_pence(row[net_cost_col]),
        )


def pounds_to_pence(value):
    return int(round(float(value) * 100))


def build_matrices(prescriptions, practices, dates):
    """
    Accepts an iterable of prescriptions plus mappings of pratice codes and
    date strings to their respective row/column offsets. Yields tuples of the
    form:

        bnf_code, items_matrix, quantity_matrix, actual_cost_matrix, net_cost_matrix

    Where the matrices contain the prescribed values for that presentation for
    every practice and date.
    """
    max_row = max(practices.values())
    max_col = max(dates.values())
    shape = (max_row + 1, max_col + 1)
    grouped_by_bnf_code = groupby(prescriptions, lambda row: row[0])
    for bnf_code, row_group in grouped_by_bnf_code:
        items_matrix = sparse_matrix(shape, integer=True)
        quantity_matrix = sparse_matrix(shape, integer=False)
        actual_cost_matrix = sparse_matrix(shape, integer=True)
        net_cost_matrix = sparse_matrix(shape, integer=True)
        for _, practice, date, items, quantity, actual_cost, net_cost in row_group:
            practice_offset = practices[practice]
            date_offset = dates[date]
            items_matrix[practice_offset, date_offset] = items
            quantity_matrix[practice_offset, date_offset] = quantity
            actual_cost_matrix[practice_offset, date_offset] = actual_cost
            net_cost_matrix[practice_offset, date_offset] = net_cost
        yield MatrixRow(
            bnf_code,
            finalise_matrix(items_matrix),
            finalise_matrix(quantity_matrix),
            finalise_matrix(actual_cost_matrix),
            finalise_matrix(net_cost_matrix),
        )


def format_as_sql_rows(matrices, connection):
    """
    Given an iterable of MatrixRows (which contain a BNF code plus all
    prescribing data for that presentation) yield tuples of values ready for
    insertion into SQLite
    """
    cursor = connection.cursor()
    num_presentations = next(cursor.execute("SELECT COUNT(*) FROM presentation"))[0]
    count = 0
    for row in matrices:
        count += 1
        # We make sure we have a row for every BNF code in the data, even ones
        # we didn't know about previously. This is a hack that we won't need
        # once we can use SQLite v3.24.0 which has proper UPSERT support.
        cursor.execute(
            "INSERT OR IGNORE INTO presentation (bnf_code) VALUES (?)", [row.bnf_code]
        )
        if should_log_message(count):
            logger.info(
                "Writing data for %s (%s/%s)", row.bnf_code, count, num_presentations
            )
        yield (
            serialize_compressed(row.items),
            serialize_compressed(row.quantity),
            serialize_compressed(row.actual_cost),
            serialize_compressed(row.net_cost),
            row.bnf_code,
        )
    logger.info("Finished writing data for %s presentations", count)


def should_log_message(n):
    """
    To avoid cluttering log output we don't log the insertion of every single
    presentation
    """
    if n <= 10:
        return True
    if n == 100:
        return True
    return n % 200 == 0
