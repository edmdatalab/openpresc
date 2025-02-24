"""
Pre-calculate totals for prescribing over all presentations. We sometimes need
these values (e.g. to show prescribing of X as a percentage of all prescribing)
and they're slightly too expensive to calculate at runtime (45-60 seconds).

The resulting matrices are the same shape as the rest of the matrices and thus
contain individual totals for each practice and month.
"""

import logging
import os.path
import sqlite3

from matrixstore.connection import MatrixStore
from matrixstore.matrix_ops import is_integer, convert_to_smallest_int_type
from matrixstore.serializer import serialize_compressed


logger = logging.getLogger(__name__)


def precalculate_totals(sqlite_path):
    if not os.path.exists(sqlite_path):
        raise RuntimeError("No SQLite file at: {}".format(sqlite_path))
    connection = sqlite3.connect(sqlite_path)
    # Disable the sqlite module's magical transaction handling features because
    # we want to use our own transactions below
    previous_isolation_level = connection.isolation_level
    connection.isolation_level = None
    precalculate_totals_for_db(connection)
    connection.isolation_level = previous_isolation_level
    connection.commit()
    connection.close()


def precalculate_totals_for_db(connection):
    matrixstore = MatrixStore(connection)
    logger.info("Summing prescribing over all presentations")
    values = matrixstore.query_one(
        """
        SELECT
          MATRIX_SUM(items),
          MATRIX_SUM(quantity),
          MATRIX_SUM(actual_cost),
          MATRIX_SUM(net_cost)
        FROM
          presentation
        WHERE
          items IS NOT NULL
        """
    )
    logger.info("Writing precalculated totals to db")
    cursor = connection.cursor()
    # We want saving the new value and deleting the old to be an atomic
    # operation. We use savepoints for this which are equivalent to
    # transactions except they're allowed to nest so it doesn't matter if we're
    # already inside a transaction when we get here.
    cursor.execute("SAVEPOINT update_totals")
    cursor.execute("DELETE FROM all_presentations")
    cursor.execute(
        """
        INSERT INTO
          all_presentations (items, quantity, actual_cost, net_cost)
        VALUES
          (?, ?, ?, ?)
        """,
        list(map(prepare_matrix_value, values)),
    )
    cursor.execute("RELEASE update_totals")


def prepare_matrix_value(matrix):
    if is_integer(matrix):
        matrix = convert_to_smallest_int_type(matrix)
    data = serialize_compressed(matrix)
    return data
