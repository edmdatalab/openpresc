from django.core.management import call_command
from django.test import TestCase
from frontend.models import PCT


class CommandsTestCase(TestCase):
    fixtures = ["orgs"]

    def test_import_ccg_boundaries(self):
        args = []
        opts = {
            "filename": ("frontend/tests/fixtures/commands/ccg_boundaries_04_2021.tab")
        }
        call_command("import_ccg_boundaries", *args, **opts)

        pct = PCT.objects.get(code="03Q")
        self.assertAlmostEqual(pct.boundary.centroid.x, -1.0307437386533043)
