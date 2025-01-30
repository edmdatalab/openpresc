import json
import warnings
from collections import defaultdict
from unittest import mock

import numpy
from django.core.cache import CacheKeyWarning
from django.test import TestCase, override_settings
from frontend.models import PCT, Practice, Presentation
from frontend.price_per_unit.savings import (
    CONFIG_MIN_SAVINGS_FOR_ORG_TYPE,
    CONFIG_TARGET_CENTILE,
    get_total_savings_for_org,
)
from frontend.price_per_unit.substitution_sets import get_substitution_sets
from matrixstore.tests.data_factory import DataFactory
from matrixstore.tests.matrixstore_factory import (
    matrixstore_from_data_factory,
    patch_global_matrixstore,
)

# The dummy cache backend we use in testing warns that our binary cache keys
# won't be compatible with memcached, but we really don't care
warnings.simplefilter("ignore", CacheKeyWarning)
DUMMY_CACHE_SETTING = {
    "default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}
}


@override_settings(CACHES=DUMMY_CACHE_SETTING)
class PricePerUnitSavingsTest(TestCase):
    maxDiff = None

    @classmethod
    def setUpTestData(cls):
        # Create some sets of substitutable presentations, which each consist
        # of a generic plus some branded equivalents
        substitution_sets = []
        for i in range(4):
            generic_code = invent_generic_bnf_code(i)
            brands = invent_brands_from_generic_bnf_code(generic_code)
            substitution_sets.append([generic_code] + brands)

        # Create some practices and some prescribing for these presentations
        factory = DataFactory()
        factory.create_months("2020-01-01", 2)
        factory.create_practices(10)
        for bnf_codes in substitution_sets:
            for bnf_code in bnf_codes:
                factory.create_presentation(bnf_code=bnf_code)
        factory.create_prescribing(
            factory.presentations, factory.practices, factory.months
        )

        # The DataFactory creates data that can be written to the MatrixStore
        # but doesn't automatically create anything in the database, so we do
        # that manually here
        ccg = PCT.objects.create(name="CCG1", code="ABC", org_type="CCG")
        for practice in factory.practices:
            Practice.objects.create(
                name=practice["name"], code=practice["code"], setting=4, ccg=ccg
            )
        for presentation in factory.presentations:
            Presentation.objects.create(
                bnf_code=presentation["bnf_code"], name=presentation["name"]
            )

        cls.substitution_sets = substitution_sets
        cls.factory = factory
        cls._remove_patch = patch_global_matrixstore(
            matrixstore_from_data_factory(factory)
        )
        # Bypass the complicated dm+d based SQL which finds appropriate swaps and just
        # return our list of swaps directly
        with mock.patch(f"{get_substitution_sets.__module__}.get_swaps", cls.get_swaps):
            # Clear the cache and the invoke the function to repopulate it
            get_substitution_sets.cache_clear()
            get_substitution_sets()

    @classmethod
    def get_swaps(cls):
        names = {p["bnf_code"]: p["name"] for p in cls.factory.presentations}
        for substitution_set in cls.substitution_sets:
            code = substitution_set[0]
            other_codes = substitution_set[1:]
            for other_code in other_codes:
                # The empty strings are the formulations, which we don't care about for
                # the purposes of this test
                yield code, names[code], "", other_code, names[other_code], ""

    def test_practice_savings(self):
        # Pick an arbitrary month and practice
        date = self.factory.months[0][:10]
        practice = self.factory.practices[0]

        # Call the API to get the savings
        response = self.client.get(
            "/api/1.0/price_per_unit/",
            {"format": "json", "entity_code": practice["code"], "date": date},
        )
        results = json.loads(response.content.decode("utf8"))

        # Calculate the expected savings the boring way
        expected = get_savings_for_practice(
            date,
            practice,
            self.factory.prescribing,
            self.substitution_sets,
            CONFIG_MIN_SAVINGS_FOR_ORG_TYPE["practice"] / 100,
        )
        # Make sure we've actually got some savings to compare
        self.assertTrue(results)
        self.assertEqual(round_floats(results), round_floats(expected))

    def test_practice_total_savings(self):
        # Pick an arbitrary month and practice
        date = self.factory.months[-1][:10]
        practice = self.factory.practices[-1]

        result = get_total_savings_for_org(date, "practice", practice["code"])

        # Calculate the expected savings the boring way
        expected = sum_savings(
            get_savings_for_practice(
                date,
                practice,
                self.factory.prescribing,
                self.substitution_sets,
                CONFIG_MIN_SAVINGS_FOR_ORG_TYPE["practice"] / 100,
            )
        )

        self.assertEqual(round_floats(result), round_floats(expected))

    def test_all_england_savings(self):
        # Pick an arbitrary month
        date = self.factory.months[0][:10]

        # Temporarily lower the threshold so we get some savings
        orig_value = CONFIG_MIN_SAVINGS_FOR_ORG_TYPE["all_standard_practices"]
        CONFIG_MIN_SAVINGS_FOR_ORG_TYPE["all_standard_practices"] = 100

        # Call the API to get the savings
        response = self.client.get(
            "/api/1.0/price_per_unit/",
            {"format": "json", "aggregate": "True", "date": date},
        )
        results = json.loads(response.content.decode("utf8"))

        # Calculate the expected savings the boring way
        expected = get_savings_for_all_england(
            date,
            self.factory.prescribing,
            self.substitution_sets,
            CONFIG_MIN_SAVINGS_FOR_ORG_TYPE["all_standard_practices"] / 100,
        )

        # Reset config value
        CONFIG_MIN_SAVINGS_FOR_ORG_TYPE["all_standard_practices"] = orig_value

        # Make sure we've actually got some savings to compare
        self.assertTrue(results)
        self.assertEqual(round_floats(results), round_floats(expected))

    def test_all_england_total_savings(self):
        # Pick an arbitrary month
        date = self.factory.months[-1][:10]

        # Temporarily lower the threshold so we get some savings
        orig_value = CONFIG_MIN_SAVINGS_FOR_ORG_TYPE["all_standard_practices"]
        CONFIG_MIN_SAVINGS_FOR_ORG_TYPE["all_standard_practices"] = 100

        result = get_total_savings_for_org(date, "all_standard_practices", None)

        # Calculate the expected savings the boring way
        expected = sum_savings(
            get_savings_for_all_england(
                date,
                self.factory.prescribing,
                self.substitution_sets,
                CONFIG_MIN_SAVINGS_FOR_ORG_TYPE["all_standard_practices"] / 100,
            )
        )

        # Reset config value
        CONFIG_MIN_SAVINGS_FOR_ORG_TYPE["all_standard_practices"] = orig_value

        self.assertEqual(round_floats(result), round_floats(expected))

    @classmethod
    def tearDownClass(cls):
        cls._remove_patch()
        super().tearDownClass()


def invent_generic_bnf_code(index):
    assert 0 <= index <= 9
    chemical = "0601022B{}".format(index)
    strength_and_form = "AS"
    product = "AA"  # Generic
    return chemical + product + strength_and_form + strength_and_form


def invent_brands_from_generic_bnf_code(generic_code, num_brands=5):
    assert 0 <= num_brands <= 9
    chemical = generic_code[0:9]
    strength_and_form = generic_code[13:15]
    products = ["B{}".format(j) for j in range(num_brands)]
    return [
        chemical + product + strength_and_form + strength_and_form
        for product in products
    ]


def get_savings_for_practice(
    date, practice, prescriptions, substitution_sets, min_saving
):
    savings = []
    for substitution_set in substitution_sets:
        # By convention we use the smallest code in each set to represent it
        generic_code = sorted(substitution_set)[0]
        generic_name = Presentation.names_for_bnf_codes([generic_code])[generic_code]
        filtered_prescriptions = [
            p
            for p in prescriptions
            if p["bnf_code"] in substitution_set and p["month"].startswith(date)
        ]
        target_ppu = get_target_ppu(filtered_prescriptions)
        quantity = 0
        net_cost = 0
        for prescription in filtered_prescriptions:
            if prescription["practice"] == practice["code"]:
                quantity += prescription["quantity"]
                net_cost += prescription["net_cost"]
        target_cost = quantity * target_ppu
        saving = net_cost - target_cost
        if saving >= min_saving:
            savings.append(
                {
                    "date": date,
                    "presentation": generic_code,
                    "name": generic_name,
                    "quantity": quantity,
                    "price_per_unit": net_cost / quantity,
                    "lowest_decile": target_ppu,
                    "possible_savings": saving,
                    "practice": practice["code"],
                    "practice_name": practice["name"],
                    "price_concession": False,
                    "formulation_swap": None,
                }
            )
    savings.sort(key=lambda i: i["possible_savings"], reverse=True)
    return savings


def get_savings_for_all_england(date, prescriptions, substitution_sets, min_saving):
    savings = []
    for substitution_set in substitution_sets:
        # By convention we use the smallest code in each set to represent it
        generic_code = sorted(substitution_set)[0]
        generic_name = Presentation.names_for_bnf_codes([generic_code])[generic_code]
        filtered_prescriptions = [
            p
            for p in prescriptions
            if p["bnf_code"] in substitution_set and p["month"].startswith(date)
        ]
        target_ppu = get_target_ppu(filtered_prescriptions)
        quantities = defaultdict(float)
        net_costs = defaultdict(float)
        for prescription in filtered_prescriptions:
            quantities[prescription["practice"]] += prescription["quantity"]
            net_costs[prescription["practice"]] += prescription["net_cost"]
        saving = 0
        for k in quantities.keys():
            target_cost = quantities[k] * target_ppu
            practice_saving = net_costs[k] - target_cost
            if practice_saving > 0:
                saving += practice_saving
        net_cost = sum(net_costs.values())
        quantity = sum(quantities.values())
        if saving >= min_saving:
            savings.append(
                {
                    "date": date,
                    "presentation": generic_code,
                    "name": generic_name,
                    "quantity": quantity,
                    "price_per_unit": net_cost / quantity,
                    "lowest_decile": target_ppu,
                    "possible_savings": saving,
                    "pct": None,
                    "pct_name": "NHS England",
                    "price_concession": False,
                    "formulation_swap": None,
                }
            )
    savings.sort(key=lambda i: i["possible_savings"], reverse=True)
    return savings


def get_target_ppu(prescriptions):
    quantity_by_practice = defaultdict(float)
    cost_by_practice = defaultdict(float)
    for prescription in prescriptions:
        practice = prescription["practice"]
        quantity_by_practice[practice] += prescription["quantity"]
        cost_by_practice[practice] += prescription["net_cost"]
    prices_per_unit = []
    for practice, cost in cost_by_practice.items():
        quantity = quantity_by_practice[practice]
        cost = cost_by_practice[practice]
        prices_per_unit.append(cost / quantity)
    return numpy.nanpercentile(prices_per_unit, q=CONFIG_TARGET_CENTILE)


def sum_savings(savings):
    return sum([i["possible_savings"] for i in savings])


def round_floats(value):
    """
    Round all floating point values found anywhere within the supplied data
    structure, recursing our way through any nested lists, tuples or dicts
    """
    if isinstance(value, float):
        return round(value, 9)
    elif isinstance(value, list):
        return [round_floats(i) for i in value]
    elif isinstance(value, tuple):
        return tuple(round_floats(i) for i in value)
    elif isinstance(value, dict):
        return {k: round_floats(v) for (k, v) in value.items()}
    else:
        return value
