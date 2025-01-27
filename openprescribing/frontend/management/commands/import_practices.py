import csv
import glob

from django.core.management.base import BaseCommand
from frontend.models import PCT, Practice


class Command(BaseCommand):
    help = "Imports practice data from epraccur.csv"

    def add_arguments(self, parser):
        parser.add_argument("--epraccur")

    def handle(self, *args, **options):
        self.IS_VERBOSE = False
        if options["verbosity"] > 1:
            self.IS_VERBOSE = True

        self.import_practices_from_epraccur(options["epraccur"])

    def parse_date(self, d):
        return "-".join([d[:4], d[4:6], d[6:]])

    def import_practices_from_epraccur(self, filename):
        entries = csv.reader(open(filename))
        count = 0
        for row in entries:
            row = [r.strip() for r in row]

            # Skip Welsh practices, see:
            # https://github.com/ebmdatalab/openprescribing/issues/3279
            national_grouping = row[2]
            if national_grouping == "W00":
                continue

            practice, created = Practice.objects.get_or_create(code=row[0])

            practice.name = row[1]
            practice.address1 = row[4]
            practice.address2 = row[5]
            practice.address3 = row[6]
            practice.address4 = row[7]
            practice.address5 = row[8]
            practice.postcode = row[9]

            practice.open_date = self.parse_date(row[10])
            if row[11]:
                practice.close_date = self.parse_date(row[11])
            practice.status_code = row[12]
            practice.setting = row[-2]

            if not practice.ccg_change_reason:
                try:
                    # Not all practices have a CCG - the ones that don't are
                    # mostly in Jersey, Isle of Man, etc.
                    pco_code = row[23].strip()
                    ccg = PCT.objects.get(code=pco_code)
                    practice.ccg = ccg
                except PCT.DoesNotExist:
                    if self.IS_VERBOSE:
                        print("ccg not found with code", pco_code)
                    # We expect all standard GP practices (setting 4) to be assigned to
                    # a known SICBL
                    if practice.setting == 4:
                        raise RuntimeError(
                            f"Practice {practice.code} assigned to unknown SICBL/CCG {pco_code}"
                        )

            if row[15]:
                practice.join_provider_date = self.parse_date(row[15])
            if row[16]:
                practice.leave_provider_date = self.parse_date(row[16])

            practice.save()
            if created:
                count += 1

        if self.IS_VERBOSE:
            print("%s Practice objects created from epraccur" % count)
