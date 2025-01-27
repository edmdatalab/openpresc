import datetime

from django.core.management import call_command
from django.test import TestCase
from frontend.models import PCT, STP, RegionalTeam


def setUpModule():
    RegionalTeam.objects.create(code="Y54")
    PCT.objects.create(code="00C", name="NHS OLD NAME CCG")
    STP.objects.create(code="QOP", name="NHS GREATER MANCHESTER INTEGRATED CARE BOARD")


def tearDownModule():
    call_command("flush", verbosity=0, interactive=False)


class CommandsTestCase(TestCase):
    def test_import_ccgs(self):
        call_command("import_ccgs", ccg="frontend/tests/fixtures/commands/eccg.csv")

        ccgs = PCT.objects.filter(org_type="CCG")
        self.assertEqual(ccgs.count(), 4)
        ccg = PCT.objects.get(code="00C")
        # Check that the CCG's name has not changed
        self.assertEqual(ccg.name, "NHS OLD NAME CCG")
        address = "DR PIPER HOUSE, KING STREET, DARLINGTON, COUNTY DURHAM"
        self.assertEqual(ccg.address, address)
        self.assertEqual(ccg.postcode, "DL3 6JL")
        self.assertEqual(ccg.open_date, datetime.date(2013, 4, 1))
        self.assertEqual(ccg.close_date, None)
        self.assertEqual(ccg.regional_team.code, "Y54")
        self.assertEqual(ccg.stp.code, "Q74")

        # Check that a newly-created CCG's name is set
        new_ccg = PCT.objects.get(code="00D")
        self.assertEqual(new_ccg.name, "NHS DURHAM DALES, EASINGTON AND SEDGEFIELD CCG")

        # Check that the Sub-ICB Reporting Entity is not imported
        self.assertFalse(PCT.objects.filter(code="Z000X").exists())

        # Check that SICBL which is alone in its ICB inherits name of ICB
        solo_ccg = PCT.objects.get(code="00T")
        self.assertEqual(solo_ccg.name, "NHS GREATER MANCHESTER")
