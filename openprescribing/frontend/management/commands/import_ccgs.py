import csv
import itertools

from django.core.management.base import BaseCommand
from frontend.models import PCT, STP


class Command(BaseCommand):
    help = "Imports CCG/PCT names and details from HSCIC organisational data. "
    help += "You should import CCG boundaries BEFORE running this."  # Why?!

    def add_arguments(self, parser):
        parser.add_argument("--ccg")

    def handle(self, **options):
        for row in csv.reader(open(options["ccg"])):
            row = [r.strip() for r in row]
            if row[2] == "Y99" or row[3] == "Q99":
                # This indicates a National Commissioning Hub which does not
                # belong to a region, and which in any case we ignore.
                continue
            if row[1].endswith("SUB-ICB REPORTING ENTITY"):
                # These aren't "real" SICBLs and we don't want them in our table.
                # Unfortunately there's no field in the structured data which identifies
                # these so we have to look for a string in the name. See:
                # https://github.com/ebmdatalab/openprescribing/issues/4862
                continue

            ccg, created = PCT.objects.get_or_create(code=row[0])
            if created:
                # For existing CCGs, we don't want to take the name in eccg.csv, since
                # it's a meaningless combination of the ICB name and the CCG code.
                ccg.name = row[1]
            ccg.regional_team_id = row[2]
            ccg.stp, _ = STP.objects.get_or_create(
                code=row[3], defaults={"name": f"ICB {row[3]}"}
            )
            ccg.address = ", ".join([r for r in row[4:9] if r])
            ccg.postcode = row[9]
            od = row[10]
            ccg.open_date = od[:4] + "-" + od[4:6] + "-" + od[-2:]
            cd = row[11]
            if cd:
                ccg.close_date = cd[:4] + "-" + cd[4:6] + "-" + cd[-2:]
            if row[13] == "C":
                ccg.org_type = "CCG"
            ccg.save()
        self.update_names()

    def update_names(self):
        # Any SICBLs which are the only active one in their ICB take the name of the ICB
        active_ccgs = PCT.objects.filter(
            org_type="CCG", close_date__isnull=True, stp_id__isnull=False
        )
        for stp_id, ccgs in itertools.groupby(
            active_ccgs.order_by("stp_id"), key=lambda i: i.stp_id
        ):
            ccgs = list(ccgs)
            if len(ccgs) == 1:
                ccg = ccgs[0]
                ccg.name = ccg.stp.name.replace(" INTEGRATED CARE BOARD", "")
                ccg.save()
