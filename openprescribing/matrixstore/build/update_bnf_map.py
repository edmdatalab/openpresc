"""
Update the prescribing data in a SQLite file using the `bnf_map` table in
BigQuery which maps old BNF codes to their current versions
"""

import logging
import os.path
import sqlite3

from gcutils.bigquery import Client
from matrixstore.matrix_ops import sparse_matrix, finalise_matrix, is_integer
from matrixstore.serializer import deserialize, serialize_compressed


logger = logging.getLogger(__name__)


def update_bnf_map(sqlite_path):
    if not os.path.exists(sqlite_path):
        raise RuntimeError("No SQLite file at: {}".format(sqlite_path))
    connection = sqlite3.connect(sqlite_path)
    # Disable the sqlite module's magical transaction handling features because
    # we want to use our own transactions below
    connection.isolation_level = None
    cursor = connection.cursor()
    bigquery_connection = Client("hscic")
    bnf_map = get_old_to_new_bnf_codes(bigquery_connection)
    for old_code, new_code in bnf_map:
        move_values_from_old_code_to_new(cursor, old_code, new_code)
    # Until we've completed the BNF code update we don't know which
    # presentations actually have prescribing data, so we have to wait until
    # now to do this cleanup
    delete_presentations_with_no_prescribing(cursor)
    connection.commit()
    connection.close()


def get_old_to_new_bnf_codes(bigquery_connection):
    result = bigquery_connection.query(
        "SELECT former_bnf_code, current_bnf_code FROM {hscic}.bnf_map"
    )
    rows = result.rows
    logger.info("Applying %s BNF code updates", len(rows))
    return rows


def move_values_from_old_code_to_new(cursor, old_code, new_code):
    """
    Move prescribing data stored under `old_code` to `new_code`

    If we have data stored under both the old and new codes then sum them.
    """
    old_values = get_values_for_bnf_code(cursor, old_code)
    # If there's no prescribing under the old code then there's nothing to do
    if not old_values:
        return
    new_values = get_values_for_bnf_code(cursor, new_code)
    if not new_values:
        logger.info(
            "Moving prescribing data from %s to %s (new code had no existing data)",
            old_code,
            new_code,
        )
        new_values = old_values
    else:
        logger.info(
            "Merging prescribing data for %s with %s (both codes have existing data)",
            old_code,
            new_code,
        )
        new_values = sum_rows([new_values, old_values])
    # We want saving the new value and deleting the old to be an atomic
    # operation. We use savepoints for this which are equivalent to
    # transactions except they're allowed to nest so it doesn't matter if
    # we're already inside a transaction when we get here.
    cursor.execute("SAVEPOINT bnf_code_update")
    cursor.execute(
        "INSERT OR IGNORE INTO presentation (bnf_code) VALUES (?)", [new_code]
    )
    cursor.execute(
        """
        UPDATE presentation SET items=?, quantity=?, actual_cost=?, net_cost=?
        WHERE bnf_code=?
        """,
        format_values_for_sqlite(new_values) + [new_code],
    )
    cursor.execute("DELETE FROM presentation WHERE bnf_code=?", [old_code])
    cursor.execute("RELEASE bnf_code_update")


def get_values_for_bnf_code(cursor, code):
    """
    Return a list of matrices which are the prescribing values for the supplied
    BNF code, or None if there is no prescribing data
    """
    result = cursor.execute(
        """
        SELECT items, quantity, actual_cost, net_cost
        FROM presentation
        WHERE bnf_code=? AND items IS NOT NULL
        """,
        [code],
    )
    rows = list(result)
    if rows:
        return [deserialize(value) for value in rows[0]]


def sum_rows(rows):
    """
    Accepts mutliple rows of matrices and sums the matrices in each column
    """
    first_row = rows[0]
    accumulators = [
        sparse_matrix(matrix.shape, integer=is_integer(matrix)) for matrix in first_row
    ]
    for row in rows:
        for accumulator, matrix in zip(accumulators, row):
            accumulator += matrix
    return [finalise_matrix(matrix) for matrix in accumulators]


def format_values_for_sqlite(row):
    """
    Accepts a list of matrices and formats them ready for insertion into SQLite
    """
    return [serialize_compressed(value) for value in row]


def delete_presentations_with_no_prescribing(cursor):
    cursor.execute("DELETE FROM presentation WHERE items IS NULL")
