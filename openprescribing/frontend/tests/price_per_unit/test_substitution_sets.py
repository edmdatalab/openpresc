from pathlib import Path
from unittest import mock

import frontend.price_per_unit
from django.test import TestCase
from dmd.management.commands.translate_bq_sql import rewrite_sql
from dmd.models import AMP
from frontend.price_per_unit.substitution_sets import (
    get_substitution_sets,
    groups_from_pairs,
)


class SubstitutionSetsSQLTest(TestCase):
    fixtures = ["dmd-objs"]

    def test_sql_executes_with_plausible_results(self):
        # This is not a great test in that it involves loading a big opaque fixture I
        # don't understand and then running a big blob of opaque SQL I don't understand;
        # but it still has value in that it confirms that the SQL does execute, that the
        # surrounding machinery all works, and that the results are vaguely sensible.
        #
        # Worrying about whether the SQL does the right thing now comes under the
        # purview of the clinical informaticians, so I think its reasonable to test only
        # this much.
        bnf_codes = set(AMP.objects.values_list("bnf_code", flat=True))
        with mock.patch(
            f"{get_substitution_sets.__module__}.get_all_prescribed_bnf_codes"
        ) as get_bnf_codes:
            get_bnf_codes.return_value = bnf_codes
            get_substitution_sets.cache_clear()
            substitution_sets = get_substitution_sets()

        expected_code = "0204000C0AAAAAA"
        self.assertEqual(substitution_sets.keys(), {expected_code})
        substitution_set = substitution_sets[expected_code]
        self.assertEqual(substitution_set.name, "Acebutolol 100mg capsules")
        self.assertEqual(
            substitution_set.presentations,
            ["0204000C0AAAAAA", "0204000C0BBAAAA"],
        )

    def test_postgres_sql_matches_bigquery_sql(self):
        module_path = Path(frontend.price_per_unit.__file__).parent
        postgres_sql = module_path.joinpath("swaps_postgres.sql").read_text()
        bigquery_sql = module_path.joinpath("swaps_bigquery.sql").read_text()
        expected_sql = rewrite_sql(bigquery_sql)

        header = "\n".join(
            ln
            for ln in postgres_sql.splitlines()[:3]
            if "Rewritten from BigQuery" in ln or "translate_bq_sql" in ln
        )
        expected_sql = header + ("\n\n" if header else "") + expected_sql

        matches = postgres_sql == expected_sql
        # We don't use assertEqual because we don't want unittest to try to generate a
        # diff here: it won't be helpful. Instead we output the entire expected SQL so
        # the clinical informatics team have the possibiity of self-serving this by
        # copying it from CI logs. This obviously isn't a great workflow, but it's
        # better than nothing.
        self.assertTrue(
            matches,
            f"\nThe Postgres SQL which determines PPU swaps no longer matches the"
            f" equivalent BigQuery SQL.\n"
            f"\n"
            f"You can either use the `translate_bq_sql` management command to update"
            f" this, or copy the SQL below to `swap_postgres.sql`:"
            f"\n"
            f"\n"
            f"------------------------------------\n"
            f"{expected_sql}\n"
            f"------------------------------------\n",
        )


class GroupsFromPairsTest(TestCase):
    def test_groups_from_pairs(self):
        # fmt: off
        pairs = [
            (1, 2),
            (3, 4),
            (5, 6),
            (1, 3),
        ]
        expected_groups = [
            [1, 2, 3, 4],
            [5, 6]
        ]
        # fmt: on
        self.assertEqual(list(groups_from_pairs(pairs)), expected_groups)
