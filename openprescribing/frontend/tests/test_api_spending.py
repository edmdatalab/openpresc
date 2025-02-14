import csv
import json
from collections import defaultdict

import numpy as np
from django.test import TestCase
from dmd.models import VMPP
from frontend.ghost_branded_generics import MIN_GHOST_GENERIC_DELTA
from frontend.models import Prescription, TariffPrice
from frontend.tests.data_factory import DataFactory
from frontend.tests.utils import round_floats
from matrixstore.tests.decorators import copy_fixtures_to_matrixstore

from .api_test_base import ApiTestBase


def _parse_json_response(response):
    return json.loads(response.content.decode("utf8"))


class TestAPISpendingViewsTariff(ApiTestBase):
    fixtures = ApiTestBase.fixtures + ["dmd-subset", "tariff", "ncso-concessions"]

    def test_tariff_hit(self):
        url = "/tariff?format=csv&codes=0206020T0AAAGAG"
        rows = self._rows_from_api(url)
        self.assertEqual(
            rows,
            [
                {
                    "date": "2014-10-01",
                    "concession": "",
                    "product": "0206020T0AAAGAG",
                    "price_pence": "900",
                    "tariff_category": "Part VIIIA Category C",
                    "vmpp": "Verapamil 160mg tablets 100 tablet",
                    "vmpp_id": "1027111000001105",
                    "pack_size": "100.00",
                }
            ],
        )

    def test_tariff_hits(self):
        url = "/tariff?format=csv&codes=0202010F0AAAAAA,0206020T0AAAGAG"
        rows = self._rows_from_api(url)
        self.assertCountEqual(
            rows,
            [
                {
                    "date": "2014-10-01",
                    "concession": "",
                    "product": "0206020T0AAAGAG",
                    "price_pence": "900",
                    "tariff_category": "Part VIIIA Category C",
                    "vmpp": "Verapamil 160mg tablets 100 tablet",
                    "vmpp_id": "1027111000001105",
                    "pack_size": "100.00",
                },
                {
                    "date": "2014-10-01",
                    "concession": "",
                    "product": "0202010F0AAAAAA",
                    "price_pence": "2400",
                    "tariff_category": "Part VIIIA Category C",
                    "vmpp": "Chlortalidone 50mg tablets 28 tablet",
                    "vmpp_id": "1079211000001106",
                    "pack_size": "28.00",
                },
                {
                    "date": "2014-11-01",
                    "concession": "2650",
                    "product": "0202010F0AAAAAA",
                    "price_pence": "1100",
                    "tariff_category": "Part VIIIA Category C",
                    "vmpp": "Chlortalidone 50mg tablets 28 tablet",
                    "vmpp_id": "1079211000001106",
                    "pack_size": "28.00",
                },
            ],
        )

    def test_tariff_miss(self):
        url = "/tariff?format=csv&codes=ABCDE"
        rows = self._rows_from_api(url)
        self.assertEqual(rows, [])

    def test_tariff_all(self):
        url = "/tariff?format=csv"
        rows = self._rows_from_api(url)
        self.assertEqual(len(rows), 3)


class TestSpending(ApiTestBase):
    def _get(self, params):
        params["format"] = "csv"
        url = "/api/1.0/spending/"
        return self.client.get(url, params)

    def _get_rows(self, params):
        rsp = self._get(params)
        return list(csv.DictReader(rsp.content.decode("utf8").splitlines()))

    def test_404_returned_for_unknown_short_code(self):
        params = {"code": "0"}
        response = self._get(params)
        self.assertEqual(response.status_code, 404)

    def test_404_returned_for_unknown_dotted_code(self):
        params = {"code": "123.456"}
        response = self._get(params)
        self.assertEqual(response.status_code, 404)

    def test_total_spending(self):
        rows = self._get_rows({})

        self.assertEqual(len(rows), 7)
        self.assertEqual(rows[0]["date"], "2013-04-01")
        self.assertEqual(rows[0]["actual_cost"], "3.12")
        self.assertEqual(rows[0]["items"], "2")
        self.assertEqual(rows[0]["quantity"], "52.0")
        self.assertEqual(rows[5]["date"], "2014-11-01")
        self.assertEqual(rows[5]["actual_cost"], "230.54")
        self.assertEqual(rows[5]["items"], "96")
        self.assertEqual(rows[5]["quantity"], "5143.0")

    def test_total_spending_by_bnf_section(self):
        rows = self._get_rows({"code": "2"})

        self.assertEqual(rows[0]["date"], "2013-04-01")
        self.assertEqual(rows[0]["actual_cost"], "3.12")
        self.assertEqual(rows[0]["items"], "2")
        self.assertEqual(rows[0]["quantity"], "52.0")
        self.assertEqual(rows[5]["date"], "2014-11-01")
        self.assertEqual(rows[5]["actual_cost"], "230.54")
        self.assertEqual(rows[5]["items"], "96")
        self.assertEqual(rows[5]["quantity"], "5143.0")

    def test_total_spending_by_bnf_section_full_code(self):
        rows = self._get_rows({"code": "02"})

        self.assertEqual(rows[0]["date"], "2013-04-01")
        self.assertEqual(rows[0]["actual_cost"], "3.12")
        self.assertEqual(rows[0]["items"], "2")
        self.assertEqual(rows[0]["quantity"], "52.0")
        self.assertEqual(rows[5]["date"], "2014-11-01")
        self.assertEqual(rows[5]["actual_cost"], "230.54")
        self.assertEqual(rows[5]["items"], "96")
        self.assertEqual(rows[5]["quantity"], "5143.0")

    def test_total_spending_by_code(self):
        rows = self._get_rows({"code": "0204000I0"})

        self.assertEqual(rows[0]["date"], "2014-11-01")
        self.assertEqual(rows[0]["actual_cost"], "176.28")
        self.assertEqual(rows[0]["items"], "34")
        self.assertEqual(rows[0]["quantity"], "2355.0")

    def test_total_spending_by_codes(self):
        rows = self._get_rows({"code": "0204000I0,0202010B0"})

        self.assertEqual(rows[3]["date"], "2014-09-01")
        self.assertEqual(rows[3]["actual_cost"], "36.29")
        self.assertEqual(rows[3]["items"], "40")
        self.assertEqual(rows[3]["quantity"], "1209.0")


class TestSpendingByCCG(ApiTestBase):
    def _get(self, params):
        params["format"] = "csv"
        url = "/api/1.0/spending_by_sicbl/"
        return self.client.get(url, params)

    def _get_rows(self, params):
        rsp = self._get(params)
        return list(csv.DictReader(rsp.content.decode("utf8").splitlines()))

    def test_total_spending_by_ccg(self):
        rows = self._get_rows({})

        self.assertEqual(len(rows), 9)
        self.assertEqual(rows[5]["row_id"], "03V")
        self.assertEqual(rows[5]["row_name"], "NHS Corby")
        self.assertEqual(rows[5]["date"], "2014-09-01")
        self.assertEqual(rows[5]["actual_cost"], "38.28")
        self.assertEqual(rows[5]["items"], "41")
        self.assertEqual(rows[5]["quantity"], "1241.0")

    def test_total_spending_by_one_ccg(self):
        params = {"org": "03V"}
        rows = self._get_rows(params)

        rows = self._rows_from_api("/spending_by_ccg?format=csv&org=03V")
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[-2]["row_id"], "03V")
        self.assertEqual(rows[-2]["row_name"], "NHS Corby")
        self.assertEqual(rows[-2]["date"], "2014-09-01")
        self.assertEqual(rows[-2]["actual_cost"], "38.28")
        self.assertEqual(rows[-2]["items"], "41")
        self.assertEqual(rows[-2]["quantity"], "1241.0")

    def test_total_spending_by_multiple_ccgs(self):
        params = {"org": "03V,03Q"}
        rows = self._get_rows(params)

        rows = self._rows_from_api("/spending_by_ccg?format=csv&org=03V,03Q")
        self.assertEqual(len(rows), 9)
        self.assertEqual(rows[5]["row_id"], "03V")
        self.assertEqual(rows[5]["row_name"], "NHS Corby")
        self.assertEqual(rows[5]["date"], "2014-09-01")
        self.assertEqual(rows[5]["actual_cost"], "38.28")
        self.assertEqual(rows[5]["items"], "41")
        self.assertEqual(rows[5]["quantity"], "1241.0")

    def test_spending_by_all_ccgs_on_chemical(self):
        params = {"code": "0202010B0"}
        rows = self._get_rows(params)

        rows = self._rows_from_api("/spending_by_ccg?format=csv&code=0202010B0")
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[0]["row_id"], "03V")
        self.assertEqual(rows[0]["row_name"], "NHS Corby")
        self.assertEqual(rows[0]["date"], "2013-04-01")
        self.assertEqual(rows[0]["actual_cost"], "3.12")
        self.assertEqual(rows[0]["items"], "2")
        self.assertEqual(rows[0]["quantity"], "52.0")
        self.assertEqual(rows[5]["row_id"], "03V")
        self.assertEqual(rows[5]["row_name"], "NHS Corby")
        self.assertEqual(rows[5]["date"], "2014-11-01")
        self.assertEqual(rows[5]["actual_cost"], "54.26")
        self.assertEqual(rows[5]["items"], "62")
        self.assertEqual(rows[5]["quantity"], "2788.0")

    def test_spending_by_all_ccgs_on_multiple_chemicals(self):
        params = {"code": "0202010B0,0202010F0"}
        rows = self._get_rows(params)

        self.assertEqual(len(rows), 8)
        self.assertEqual(rows[0]["row_id"], "03V")
        self.assertEqual(rows[0]["row_name"], "NHS Corby")
        self.assertEqual(rows[0]["date"], "2013-04-01")
        self.assertEqual(rows[0]["actual_cost"], "3.12")
        self.assertEqual(rows[0]["items"], "2")
        self.assertEqual(rows[0]["quantity"], "52.0")
        self.assertEqual(rows[-3]["row_id"], "03V")
        self.assertEqual(rows[-3]["row_name"], "NHS Corby")
        self.assertEqual(rows[-3]["date"], "2014-09-01")
        self.assertEqual(rows[-3]["actual_cost"], "38.28")
        self.assertEqual(rows[-3]["items"], "41")
        self.assertEqual(rows[-3]["quantity"], "1241.0")

    def test_spending_by_all_ccgs_on_product(self):
        params = {"code": "0204000I0BC"}
        rows = self._get_rows(params)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["row_id"], "03V")
        self.assertEqual(rows[0]["row_name"], "NHS Corby")
        self.assertEqual(rows[0]["date"], "2014-11-01")
        self.assertEqual(rows[0]["actual_cost"], "32.26")
        self.assertEqual(rows[0]["items"], "29")
        self.assertEqual(rows[0]["quantity"], "2350.0")

    def test_spending_by_all_ccgs_on_presentation(self):
        params = {"code": "0202010B0AAABAB"}
        rows = self._get_rows(params)

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[2]["row_id"], "03V")
        self.assertEqual(rows[2]["row_name"], "NHS Corby")
        self.assertEqual(rows[2]["date"], "2014-11-01")
        self.assertEqual(rows[2]["actual_cost"], "54.26")
        self.assertEqual(rows[2]["items"], "62")
        self.assertEqual(rows[2]["quantity"], "2788.0")

    def test_spending_by_all_ccgs_on_multiple_presentations(self):
        params = {"code": "0202010F0AAAAAA,0202010B0AAACAC"}
        rows = self._get_rows(params)

        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[0]["row_id"], "03V")
        self.assertEqual(rows[0]["row_name"], "NHS Corby")
        self.assertEqual(rows[0]["date"], "2013-04-01")
        self.assertEqual(rows[0]["actual_cost"], "1.56")
        self.assertEqual(rows[0]["items"], "1")
        self.assertEqual(rows[0]["quantity"], "26.0")

    def test_spending_by_all_ccgs_on_bnf_section(self):
        params = {"code": "2.2.1"}
        rows = self._get_rows(params)

        self.assertEqual(len(rows), 8)
        self.assertEqual(rows[0]["row_id"], "03V")
        self.assertEqual(rows[0]["row_name"], "NHS Corby")
        self.assertEqual(rows[0]["date"], "2013-04-01")
        self.assertEqual(rows[0]["actual_cost"], "3.12")
        self.assertEqual(rows[0]["items"], "2")
        self.assertEqual(rows[0]["quantity"], "52.0")
        self.assertEqual(rows[-1]["row_id"], "03V")
        self.assertEqual(rows[-1]["row_name"], "NHS Corby")
        self.assertEqual(rows[-1]["date"], "2014-11-01")
        self.assertEqual(rows[-1]["actual_cost"], "54.26")
        self.assertEqual(rows[-1]["items"], "62")
        self.assertEqual(rows[-1]["quantity"], "2788.0")

    def test_spending_by_all_ccgs_on_multiple_bnf_sections(self):
        params = {"code": "2.2,2.4"}
        rows = self._get_rows(params)

        self.assertEqual(len(rows), 8)
        self.assertEqual(rows[-1]["row_id"], "03V")
        self.assertEqual(rows[-1]["row_name"], "NHS Corby")
        self.assertEqual(rows[-1]["date"], "2014-11-01")
        self.assertEqual(rows[-1]["actual_cost"], "230.54")
        self.assertEqual(rows[-1]["items"], "96")
        self.assertEqual(rows[-1]["quantity"], "5143.0")


class TestSpendingByPractice(ApiTestBase):
    def _get(self, params):
        params["format"] = "csv"
        url = "/api/1.0/spending_by_practice/"
        return self.client.get(url, params)

    def _get_rows(self, params):
        rsp = self._get(params)
        return list(csv.DictReader(rsp.content.decode("utf8").splitlines()))

    def test_spending_by_all_practices_on_product_without_date(self):
        response = self._get({"code": "0204000I0BC"})
        self.assertEqual(response.status_code, 400)

    def test_total_spending_by_practice(self):
        params = {"date": "2014-11-01"}
        rows = self._get_rows(params)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["row_id"], "K83059")
        self.assertEqual(rows[0]["row_name"], "DR KHALID & PARTNERS")
        self.assertEqual(rows[0]["date"], "2014-11-01")
        self.assertEqual(rows[0]["setting"], "-1")
        self.assertEqual(rows[0]["ccg"], "03V")
        self.assertEqual(rows[0]["actual_cost"], "166.28")
        self.assertEqual(rows[0]["items"], "41")
        self.assertEqual(rows[0]["quantity"], "2544.0")

    def test_total_spending_by_practice_with_old_date(self):
        params = {"date": "1066-11-01"}
        rsp = self._get(params)
        self.assertContains(
            rsp, "Date is outside the 5 years of data available", status_code=404
        )

    def test_total_spending_by_practice_with_malformed_date(self):
        params = {"date": "2015-1-1"}
        rsp = self._get(params)
        self.assertContains(rsp, "Dates must be in YYYY-MM-DD format", status_code=404)

    def test_spending_by_practice_on_chemical(self):
        params = {"code": "0204000I0", "date": "2014-11-01"}
        rows = self._get_rows(params)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["row_id"], "K83059")
        self.assertEqual(rows[0]["row_name"], "DR KHALID & PARTNERS")
        self.assertEqual(rows[0]["setting"], "-1")
        self.assertEqual(rows[0]["ccg"], "03V")
        self.assertEqual(rows[0]["date"], "2014-11-01")
        self.assertEqual(rows[0]["actual_cost"], "154.15")
        self.assertEqual(rows[0]["items"], "17")
        self.assertEqual(rows[0]["quantity"], "1155.0")

    def test_spending_by_all_practices_on_chemical_with_date(self):
        params = {"code": "0202010F0", "date": "2014-09-01"}
        rows = self._get_rows(params)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["row_id"], "N84014")
        self.assertEqual(rows[0]["actual_cost"], "11.99")
        self.assertEqual(rows[0]["items"], "1")
        self.assertEqual(rows[0]["quantity"], "128.0")
        self.assertEqual(rows[1]["row_id"], "P87629")
        self.assertEqual(rows[1]["actual_cost"], "1.99")
        self.assertEqual(rows[1]["items"], "1")
        self.assertEqual(rows[1]["quantity"], "32.0")

    def test_spending_by_one_practice(self):
        params = {"org": "P87629"}
        rows = self._get_rows(params)

        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[-1]["row_id"], "P87629")
        self.assertEqual(rows[-1]["row_name"], "1/ST ANDREWS MEDICAL PRACTICE")
        self.assertEqual(rows[-1]["date"], "2014-11-01")
        self.assertEqual(rows[-1]["actual_cost"], "64.26")
        self.assertEqual(rows[-1]["items"], "55")
        self.assertEqual(rows[-1]["quantity"], "2599.0")

    def test_spending_by_two_practices_with_date(self):
        params = {"org": "P87629,K83059", "date": "2014-11-01"}
        rows = self._get_rows(params)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["row_id"], "P87629")
        self.assertEqual(rows[1]["row_name"], "1/ST ANDREWS MEDICAL PRACTICE")
        self.assertEqual(rows[1]["date"], "2014-11-01")
        self.assertEqual(rows[1]["actual_cost"], "64.26")
        self.assertEqual(rows[1]["items"], "55")
        self.assertEqual(rows[1]["quantity"], "2599.0")

    def test_spending_by_one_practice_on_chemical(self):
        params = {"code": "0202010B0", "org": "P87629"}
        rows = self._get_rows(params)

        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[-1]["row_id"], "P87629")
        self.assertEqual(rows[-1]["row_name"], "1/ST ANDREWS MEDICAL PRACTICE")
        self.assertEqual(rows[-1]["setting"], "4")
        self.assertEqual(rows[-1]["ccg"], "03V")
        self.assertEqual(rows[-1]["date"], "2014-11-01")
        self.assertEqual(rows[-1]["actual_cost"], "42.13")
        self.assertEqual(rows[-1]["items"], "38")
        self.assertEqual(rows[-1]["quantity"], "1399.0")

    def test_spending_by_practice_on_multiple_chemicals(self):
        params = {"code": "0202010B0,0204000I0", "org": "P87629,K83059"}
        rows = self._get_rows(params)

        self.assertEqual(len(rows), 7)
        self.assertEqual(rows[3]["row_id"], "P87629")
        self.assertEqual(rows[3]["row_name"], "1/ST ANDREWS MEDICAL PRACTICE")
        self.assertEqual(rows[3]["date"], "2013-10-01")
        self.assertEqual(rows[3]["actual_cost"], "1.62")
        self.assertEqual(rows[3]["items"], "1")
        self.assertEqual(rows[3]["quantity"], "24.0")

    def test_spending_by_all_practices_on_product(self):
        params = {"code": "0202010B0AA", "date": "2014-11-01"}
        rows = self._get_rows(params)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["row_id"], "K83059")
        self.assertEqual(rows[0]["actual_cost"], "12.13")
        self.assertEqual(rows[0]["items"], "24")
        self.assertEqual(rows[0]["quantity"], "1389.0")
        self.assertEqual(rows[1]["row_id"], "P87629")
        self.assertEqual(rows[1]["actual_cost"], "42.13")
        self.assertEqual(rows[1]["items"], "38")
        self.assertEqual(rows[1]["quantity"], "1399.0")

    def test_spending_by_all_practices_on_presentation(self):
        params = {"code": "0202010B0AAABAB", "date": "2014-11-01"}
        rows = self._get_rows(params)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["row_id"], "K83059")
        self.assertEqual(rows[0]["actual_cost"], "12.13")
        self.assertEqual(rows[0]["items"], "24")
        self.assertEqual(rows[0]["quantity"], "1389.0")
        self.assertEqual(rows[1]["row_id"], "P87629")
        self.assertEqual(rows[1]["actual_cost"], "42.13")
        self.assertEqual(rows[1]["items"], "38")
        self.assertEqual(rows[1]["quantity"], "1399.0")

    def test_spending_by_practice_on_presentation(self):
        params = {"code": "0204000I0BCAAAB", "org": "03V"}
        rows = self._get_rows(params)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["row_id"], "P87629")
        self.assertEqual(rows[1]["row_name"], "1/ST ANDREWS MEDICAL PRACTICE")
        self.assertEqual(rows[1]["setting"], "4")
        self.assertEqual(rows[1]["ccg"], "03V")
        self.assertEqual(rows[1]["date"], "2014-11-01")
        self.assertEqual(rows[1]["actual_cost"], "22.13")
        self.assertEqual(rows[1]["items"], "17")
        self.assertEqual(rows[1]["quantity"], "1200.0")

    def test_spending_by_practice_on_multiple_presentations(self):
        params = {"code": "0204000I0BCAAAB,0202010B0AAABAB", "org": "03V"}
        rows = self._get_rows(params)

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[2]["row_id"], "P87629")
        self.assertEqual(rows[2]["row_name"], "1/ST ANDREWS MEDICAL PRACTICE")
        self.assertEqual(rows[2]["date"], "2014-11-01")
        self.assertEqual(rows[2]["actual_cost"], "64.26")
        self.assertEqual(rows[2]["items"], "55")
        self.assertEqual(rows[2]["quantity"], "2599.0")

    def test_spending_by_practice_on_section(self):
        params = {"code": "2", "org": "03V"}
        rows = self._get_rows(params)

        self.assertEqual(len(rows), 7)
        self.assertEqual(rows[-1]["row_id"], "P87629")
        self.assertEqual(rows[-1]["row_name"], "1/ST ANDREWS MEDICAL PRACTICE")
        self.assertEqual(rows[-1]["date"], "2014-11-01")
        self.assertEqual(rows[-1]["actual_cost"], "64.26")
        self.assertEqual(rows[-1]["items"], "55")
        self.assertEqual(rows[-1]["quantity"], "2599.0")

    def test_spending_by_practice_on_multiple_sections(self):
        params = {"code": "0202,0204", "org": "03Q"}
        rows = self._get_rows(params)

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["row_id"], "N84014")
        self.assertEqual(rows[0]["row_name"], "AINSDALE VILLAGE SURGERY")
        self.assertEqual(rows[0]["date"], "2013-08-01")
        self.assertEqual(rows[0]["actual_cost"], "1.53")
        self.assertEqual(rows[0]["items"], "1")
        self.assertEqual(rows[0]["quantity"], "28.0")


class TestSpendingByOrg(ApiTestBase):
    def _get(self, params):
        params["format"] = "csv"
        url = "/api/1.0/spending_by_org/"
        return self.client.get(url, params)

    def _get_rows(self, params):
        rsp = self._get(params)
        return list(csv.DictReader(rsp.content.decode("utf8").splitlines()))

    def test_spending_by_all_stps(self):
        rows = self._get_rows({"org_type": "stp"})
        self.assertEqual(len(rows), 9)
        self.assertEqual(
            rows[1],
            {
                "actual_cost": "1.53",
                "date": "2013-08-01",
                "items": "1",
                "quantity": "28.0",
                "row_id": "E54",
                "row_name": "Humber, Coast and Vale",
            },
        )
        self.assertEqual(
            rows[2],
            {
                "actual_cost": "1.69",
                "date": "2013-08-01",
                "items": "1",
                "quantity": "23.0",
                "row_id": "E55",
                "row_name": "Northamptonshire",
            },
        )

    def test_spending_by_one_stp_on_chapter(self):
        rows = self._get_rows({"org_type": "stp", "org": "E55", "code": "02"})
        self.assertEqual(len(rows), 5)
        self.assertEqual(
            rows[-1],
            {
                "actual_cost": "230.54",
                "date": "2014-11-01",
                "items": "96",
                "quantity": "5143.0",
                "row_id": "E55",
                "row_name": "Northamptonshire",
            },
        )

    def test_spending_by_all_regional_teams(self):
        rows = self._get_rows({"org_type": "regional_team"})
        self.assertEqual(len(rows), 9)
        self.assertEqual(
            rows[1],
            {
                "actual_cost": "1.53",
                "date": "2013-08-01",
                "items": "1",
                "quantity": "28.0",
                "row_id": "Y54",
                "row_name": "NORTH OF ENGLAND COMMISSIONING REGION",
            },
        )
        self.assertEqual(
            rows[2],
            {
                "actual_cost": "1.69",
                "date": "2013-08-01",
                "items": "1",
                "quantity": "23.0",
                "row_id": "Y55",
                "row_name": "MIDLANDS AND EAST OF ENGLAND COMMISSIONING REGION",
            },
        )

    def test_spending_by_one_regional_team_on_chapter(self):
        rows = self._get_rows({"org_type": "regional_team", "org": "Y55", "code": "02"})
        self.assertEqual(len(rows), 5)
        self.assertEqual(
            rows[-1],
            {
                "actual_cost": "230.54",
                "date": "2014-11-01",
                "items": "96",
                "quantity": "5143.0",
                "row_id": "Y55",
                "row_name": "MIDLANDS AND EAST OF ENGLAND COMMISSIONING REGION",
            },
        )

    def test_spending_by_all_pcns(self):
        rows = self._get_rows({"org_type": "pcn"})
        self.assertEqual(len(rows), 7)
        self.assertEqual(
            rows[1],
            {
                "date": "2013-08-01",
                "actual_cost": "3.22",
                "items": "2",
                "quantity": "51.0",
                "row_id": "PCN0001",
                "row_name": "Transformational Sustainability",
            },
        )
        self.assertEqual(
            rows[2],
            {
                "date": "2013-10-01",
                "actual_cost": "1.62",
                "items": "1",
                "quantity": "24.0",
                "row_id": "PCN0001",
                "row_name": "Transformational Sustainability",
            },
        )


@copy_fixtures_to_matrixstore
class TestAPISpendingViewsGhostGenerics(TestCase):
    @classmethod
    def setUpTestData(self):
        self.api_prefix = "/api/1.0"
        factory = DataFactory()
        self.months = factory.create_months_array(start_date="2018-02-01")
        self.ccgs = [factory.create_ccg() for _ in range(2)]
        self.practices = []
        for ccg in self.ccgs:
            for _ in range(2):
                self.practices.append(factory.create_practice(ccg=ccg, setting=4))
        self.presentations = factory.create_presentations(2, vmpp_per_presentation=2)
        factory.create_tariff_and_ncso_costings_for_presentations(
            presentations=self.presentations, months=self.months
        )

        # Create prescribing for each of the practices we've created
        for practice in self.practices:
            factory.create_prescribing_for_practice(
                practice, presentations=self.presentations, months=self.months
            )

    def _get(self, **data):
        data["format"] = "json"
        url = self.api_prefix + "/ghost_generics/"
        rsp = self.client.get(url, data, follow=True)
        return _parse_json_response(rsp)

    def _practice_savings_for_ccg(self, ccg, expected):
        practices_in_ccg = [x.code for x in ccg.practice_set.all()]
        savings = []
        for p in practices_in_ccg:
            savings.extend(expected["practice_savings"][p].values())
        return savings

    def test_savings(self):
        # Calculate expected values in python, to validate the
        # application output which is generated by SQL
        expected = self._expected_savings()

        # Are practice-level savings as expected?
        for practice in self.practices:
            practice_data = self._get(
                entity_code=practice.code,
                entity_type="practice",
                date="2018-02-01",
                group_by="presentation",
            )
            practice_expected = expected["practice_savings"][practice.code]
            for data in practice_data:
                self.assertEqual(
                    round(data["possible_savings"], 4),
                    practice_expected[data["bnf_code"]],
                )

        # Same, but for all practices in one CCG
        ccg_data = self._get(
            entity_code=self.ccgs[0].code, entity_type="CCG", date="2018-02-01"
        )
        self.assertTrue(all([x["ccg"] == self.ccgs[0].code for x in ccg_data]))
        savings_count = len(self._practice_savings_for_ccg(self.ccgs[0], expected))
        self.assertEqual(len(ccg_data), savings_count)

        # CCG-level, grouped by presentations
        grouped_ccg_data = self._get(
            entity_code=self.ccgs[0].code,
            entity_type="CCG",
            group_by="presentation",
            date="2018-02-01",
        )

        for d in grouped_ccg_data:
            self.assertEqual(
                round(d["possible_savings"], 3),
                round(expected["ccg_savings"][d["ccg"]][d["bnf_code"]], 3),
            )

        # Single presentations which have more than one tariff price
        # (e.g. 20 pills for 10 pounds and 40 pills for 30 pounds)
        # should be ignored when calculating possible savings, as we
        # don't have enough data to know which VMPP was dispensed
        #
        # The fixtures already created have identical price-per-unit
        # for each of their (two) tariff prices. Alter just one of
        # them:
        price_to_alter = TariffPrice.objects.last()
        price_to_alter.price_pence *= 2
        price_to_alter.save()

        # Now test the savings are as expected, and fewer than
        # previously
        expected_2 = self._expected_savings()
        ccg_data_2 = self._get(
            entity_code=self.ccgs[0].code, entity_type="CCG", date="2018-02-01"
        )
        savings_count_2 = len(self._practice_savings_for_ccg(self.ccgs[0], expected_2))
        self.assertEqual(len(ccg_data_2), savings_count_2)
        self.assertTrue(savings_count > savings_count_2)

    def _expected_savings(self):
        def autovivify(levels=1, final=dict):
            """Create an arbitrarily-nested dict"""
            return (
                defaultdict(final)
                if levels < 2
                else defaultdict(lambda: autovivify(levels - 1, final))
            )

        # Compute median prices for each presentation; we use these as
        # a proxy for drug tariff prices (see #1318 for an explanation)
        presentation_medians = {}
        for presentation in self.presentations:
            net_costs = []
            for rx in Prescription.objects.filter(
                presentation_code=presentation.bnf_code
            ):
                net_costs.append(rx.net_cost / rx.quantity)
            presentation_medians[presentation.bnf_code] = np.percentile(
                net_costs, 50, interpolation="lower"
            )
        practice_savings = autovivify(levels=2, final=int)
        ccg_savings = autovivify(levels=2, final=int)
        for practice in self.practices:
            for rx in Prescription.objects.filter(practice=practice):
                vmpps = VMPP.objects.filter(bnf_code=rx.presentation_code)
                prices_per_pill = set()
                for vmpp in vmpps:
                    tariff = TariffPrice.objects.get(vmpp=vmpp)
                    tariff_price_per_pill = tariff.price_pence / vmpp.qtyval
                    prices_per_pill.add(tariff_price_per_pill)
                only_one_tariff_price = len(prices_per_pill) == 1
                if only_one_tariff_price:
                    possible_saving = round(
                        rx.net_cost
                        - (presentation_medians[rx.presentation_code] * rx.quantity),
                        4,
                    )
                    if possible_saving <= (
                        -MIN_GHOST_GENERIC_DELTA / 100
                    ) or possible_saving >= (MIN_GHOST_GENERIC_DELTA / 100):
                        practice_savings[practice.code][
                            rx.presentation_code
                        ] = possible_saving
                        ccg_savings[practice.ccg.code][
                            rx.presentation_code
                        ] += possible_saving
        return {
            "practice_savings": practice_savings,
            "ccg_savings": ccg_savings,
            "medians": presentation_medians,
        }


class TestAPISpendingViewsPPUBubble(ApiTestBase):
    fixtures = ApiTestBase.fixtures + ["importlog"]

    def test_simple(self):
        url = "/bubble?format=json"
        url += "&bnf_code=0204000I0BCAAAB&date=2014-11-01&highlight=03V"
        url = self.api_prefix + url
        response = self.client.get(url, follow=True)
        data = _parse_json_response(response)
        self.assertEqual(len(data["series"]), 1)  # Only Trandate prescribed
        self.assertEqual(len([x for x in data if x[1]]), 3)

    def test_date(self):
        url = "/bubble?format=json"
        url += "&bnf_code=0204000I0BCAAAB&date=2000-01-01&highlight=03V"
        url = self.api_prefix + url
        response = self.client.get(url, follow=True)
        data = _parse_json_response(response)
        self.assertEqual(len(data["series"]), 0)

    def test_highlight(self):
        url = "/bubble?format=json"
        url += "&bnf_code=0204000I0BCAAAB&date=2014-11-01&highlight=03V"
        url = self.api_prefix + url
        response = self.client.get(url, follow=True)
        data = _parse_json_response(response)
        # N.B. This is the mean of a *single* value; although there are two
        # values in the raw data one is ignored as it belongs to a
        # non-setting-4 practice
        self.assertEqual(data["plotline"], 0.030875)

    def test_code_without_matches(self):
        url = "/bubble?format=json"
        url += "&bnf_code=0204000I0BCAAAX&date=2014-11-01&highlight=03V"
        url = self.api_prefix + url
        response = self.client.get(url, follow=True)
        data = _parse_json_response(response)
        self.assertIsNone(data["plotline"])

    def test_focus(self):
        url = "/bubble?format=json"
        url += "&bnf_code=0202010F0AAAAAA&date=2014-09-01"
        url += "&highlight=03V&focus=1"
        url = self.api_prefix + url
        response = self.client.get(url, follow=True)
        data = _parse_json_response(response)
        self.assertEqual(
            data,
            {
                "series": [
                    {
                        "y": 0.08,
                        "x": 1,
                        "z": 32,
                        "name": "Chlortalidone 50mg tablets",
                        "mean_ppu": 0.0843125,
                    }
                ],
                "categories": [
                    {"is_generic": True, "name": "Chlortalidone 50mg tablets"}
                ],
                "plotline": 0.0843125,
            },
        )

    def test_no_focus(self):
        url = "/bubble?format=json"
        url += "&bnf_code=0202010F0AAAAAA&date=2014-09-01"
        url += "&highlight=03V"
        url = self.api_prefix + url
        response = self.client.get(url, follow=True)
        data = _parse_json_response(response)
        data = round_floats(data)
        self.assertEqual(
            data,
            {
                "series": [
                    {
                        "x": 1,
                        "y": 0.08,
                        "z": 32.0,
                        "name": "Chlortalidone 50mg tablets",
                        "mean_ppu": 0.0931,
                    },
                    {
                        "x": 1,
                        "y": 0.1,
                        "z": 128.0,
                        "name": "Chlortalidone 50mg tablets",
                        "mean_ppu": 0.0931,
                    },
                ],
                "categories": [
                    {"is_generic": True, "name": "Chlortalidone 50mg tablets"}
                ],
                "plotline": 0.0843125,
            },
        )
