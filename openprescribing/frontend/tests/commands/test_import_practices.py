import datetime

from django.core.management import call_command
from django.test import TestCase
from frontend.models import PCT, Practice


def setUpModule():
    PCT.objects.create(code="00M", name="SOUTHPORT AND FORMBY CCG")
    PCT.objects.create(code="00K", name="SOUTH MANCHESTER CCG")


def tearDownModule():
    call_command("flush", verbosity=0, interactive=False)


class CommandsTestCase(TestCase):
    def test_import_practices_from_epraccur(self):
        Practice.objects.create(
            code="A81044", ccg_id="00M", ccg_change_reason="Manually set"
        )

        args = []
        epraccur = "frontend/tests/fixtures/commands/"
        epraccur += "epraccur_sample.csv"
        opts = {"epraccur": epraccur}
        call_command("import_practices", *args, **opts)

        # Test import from epraccur.
        p = Practice.objects.get(code="A81043")
        self.assertEqual(p.ccg.code, "00M")
        self.assertEqual(p.name, "THE MANOR HOUSE SURGERY")
        addr = "THE MANOR HOUSE SURGERY, BRAIDWOOD ROAD, NORMANBY, "
        addr += "MIDDLESBROUGH, CLEVELAND, TS6 0HA"
        self.assertEqual(p.address_pretty(), addr)
        self.assertEqual(p.postcode, "TS6 0HA")
        self.assertEqual(p.open_date, datetime.date(1974, 4, 1))
        self.assertEqual(p.close_date, None)
        self.assertEqual(p.status_code, "A")
        self.assertEqual(p.join_provider_date, datetime.date(2013, 4, 1))
        self.assertEqual(p.leave_provider_date, None)
        self.assertEqual(p.get_setting_display(), "Prison")

        p = Practice.objects.get(code="A81044")
        # We're checking that the CCG code hasn't been updated, since
        # ccg_change_reason is set.
        self.assertEqual(p.ccg.code, "00M")
        self.assertEqual(p.get_setting_display(), "GP Practice")

        p = Practice.objects.get(code="Y01063")
        self.assertEqual(p.ccg, None)
