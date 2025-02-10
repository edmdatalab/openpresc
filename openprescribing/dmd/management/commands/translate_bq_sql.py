"""
Attempts to translate SQL queries written against the dm+d in BigQuery into something
that will run against the dm+d data stored in Postgres
"""

import re
import sys
from pathlib import Path

from django.apps import apps
from django.core.management import BaseCommand


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument(
            "-i", "--input", type=Path, help="File to read (stdin by default)"
        )
        parser.add_argument(
            "-o", "--output", type=Path, help="File to write (stdout by default)"
        )

    def handle(self, input=None, output=None, **kwargs):
        in_sql = sys.stdin.read() if input is None else input.read_text()

        out_sql = rewrite_sql(in_sql)
        out_sql = (
            f"-- Rewritten from BigQuery-flavoured SQL using:\n"
            f"--   {' '.join(sys.argv)}\n\n"
            f"{out_sql}"
        )

        if output is None:
            self.stdout.write(out_sql)
        else:
            output.write_text(out_sql)


def rewrite_sql(in_sql):
    # Find all references to dm+d tables
    table_refs = set(re.findall(r"\bdmd\.(\w+)\b", in_sql))
    # Generate CTEs which map the BigQuery column names to the Postgres column names for
    # these tables
    ctes = generate_dmd_table_ctes(table_refs)
    sql = in_sql
    # Postgres will only accept a single `WITH` statement so if the query has it's own
    # CTEs we need to strip the WITH and fold the declaration into our own CTEs
    sql = re.sub(r"^\s*WITH ", ", ", sql, flags=re.MULTILINE)
    # Replace all the table references with references to our CTEs
    sql = re.sub(r"\bdmd\.(\w+)\b", r"bq_dmd_\1", sql)
    # Postgres uses type TEXT instead of STRING
    sql = re.sub(r"\bAS STRING\b", "AS TEXT", sql)
    return ctes + "\n" + sql


def generate_dmd_table_ctes(tables):
    ctes = []
    for model in apps.get_app_config("dmd").get_models():
        table_name = model._meta.db_table
        if table_name.removeprefix("dmd_") not in tables:
            continue
        column_aliases = [
            f"    {f.db_column or f.column} AS {f.name}" for f in model._meta.fields
        ]
        # We specify that these are non-materialized CTEs so they act as simple table
        # aliases and don't mess up the query plans
        cte = (
            f"bq_{table_name} AS NOT MATERIALIZED (\n"
            f"  SELECT\n"
            f"{',\n'.join(column_aliases)}\n"
            f"  FROM {table_name}\n"
            f")"
        )
        ctes.append(cte)
    return f"WITH\n{',\n'.join(ctes)}\n"
