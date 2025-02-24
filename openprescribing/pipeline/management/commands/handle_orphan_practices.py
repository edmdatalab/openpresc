# We get information about which CCG a practice belongs to from epraccur.csv,
# which is usually published quarterly.  When a CCG's composition changes,
# closed or dormant practices that were previously in the CCG are often not
# updated and may end up apparently belonging to a closed CCG.  We call these
# "orphan practices".
#
# When several practices that were in a CCG leave that CCG, there are three
# cases we need to consider:
#
# A: the CCG has closed and its practices have moved to a single CCG
# B: the CCG has closed and its practices have moved to multiple CCGs
# C: the CCG is still open but some practices have moved to another CCG
#
# We have seen case A several times in the wild, especially when CCGs have
# merged.  We have seen case C once, when the boundary between CCGs O1H and 01K
# changed.
#
# In cases A and B, the closed CCG might leave behind orphan practices.  In
# case A, we can automatically identify which CCG the orphan practices should
# be moved to.  In case B, human intervention would be required, perhaps
# involving looking at the location of the orphan practices and comparing these
# to the locations of the active practices that have moved CCGs.
#
# In case C, the original CCG might have some closed practices that should have
# been moved to a new CCG.  Again, this requires human intervention.
#
# When a human needs to get involved, a message will be sent to Slack.
#
# To manually move a practice to a new CCG, you will need to update
# Practice.ccg_id and Practice.ccg_change_reason.

import csv
import glob
import os
from collections import Counter, defaultdict

from django.conf import settings
from django.core.management import BaseCommand, CommandError
from frontend.models import PCT, Practice

from openprescribing.slack import notify_slack


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("--prev-epraccur")
        parser.add_argument("--curr-epraccur")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **kwargs):
        prev_path, curr_path = kwargs["prev_epraccur"], kwargs["curr_epraccur"]
        self.dry_run = kwargs["dry_run"]

        if prev_path is None:
            if curr_path is not None:
                msg = "Must either provide two paths, or no paths"
                raise CommandError(msg)
            pattern = os.path.join(
                settings.PIPELINE_DATA_BASEDIR, "practice_details", "*", "epraccur.csv"
            )
            paths = sorted(glob.glob(pattern))
            if len(paths) < 2:
                return
            prev_path, curr_path = paths[-2:]

        else:
            if curr_path is None:
                msg = "Must either provide two paths, or no paths"
                raise CommandError(msg)

        self.ccg_to_name = {c.code: c.name for c in PCT.objects.filter(org_type="CCG")}

        closed_practices = [p.code for p in Practice.objects.exclude(status_code="A")]

        practice_to_curr_ccg = {}
        ccg_to_prev_practices = defaultdict(list)
        ccg_to_curr_practices = defaultdict(list)

        with open(prev_path) as f:
            for row in csv.reader(f):
                practice = row[0]
                provider = row[23]
                setting = row[25]
                if setting == "4" and provider in self.ccg_to_name:
                    ccg_to_prev_practices[provider].append(practice)

        with open(curr_path) as f:
            for row in csv.reader(f):
                practice = row[0]
                provider = row[23]
                setting = row[25]
                if setting == "4" and provider in self.ccg_to_name:
                    practice_to_curr_ccg[practice] = provider
                    ccg_to_curr_practices[provider].append(practice)

        for ccg, name in sorted(self.ccg_to_name.items()):
            prev_practices = ccg_to_prev_practices[ccg]
            curr_practices = ccg_to_curr_practices[ccg]

            if len(curr_practices) == 0:
                # There are currently no practices in this CCG
                continue

            if len(prev_practices) - len(curr_practices) <= 2:
                # We expect one or two practices to move out of a CCG or change
                # their status each month, so we'll ignore CCGs where the
                # difference between the number of practices this month and
                # last month is two or fewer.
                continue

            if self.dry_run:
                self.stdout.write("-" * 80)
                self.stdout.write("Sub-ICB Location: {} ({})".format(ccg, name))
                self.stdout.write(
                    "Previous practice count: {}".format(len(prev_practices))
                )
                self.stdout.write(
                    "Current practice count: {}".format(len(curr_practices))
                )

            if all(practice in closed_practices for practice in curr_practices):
                # This is a Counter whose keys are the CCGs that practices
                # which were previously in this CCG are currently in, excluding
                # this CCG.
                new_ccgs = Counter(
                    practice_to_curr_ccg[practice]
                    for practice in prev_practices
                    if practice in practice_to_curr_ccg
                    and practice_to_curr_ccg[practice] != ccg
                )

                if len(new_ccgs) == 1:
                    new_ccg = list(new_ccgs)[0]
                    self.handle_case_a(ccg, new_ccg)
                else:
                    self.handle_case_b(ccg, new_ccgs)
            else:
                self.handle_case_c(ccg)

    def handle_case_a(self, ccg, new_ccg):
        # The CCG has closed and its practices have moved to a single CCG
        new_ccg_name = self.ccg_to_name[new_ccg]

        if self.dry_run:
            self.stdout.write(
                "All practices currently in Sub-ICB Location are closed or dormant"
            )
            self.stdout.write(
                "All active practices previously in Sub-ICB Location are now in {} ({})".format(
                    new_ccg, new_ccg_name
                )
            )
            self.stdout.write(
                "Command would move all inactive practices to {} ({})".format(
                    new_ccg, new_ccg_name
                )
            )
        else:
            practices = Practice.objects.filter(ccg_id=ccg).exclude(status_code="A")
            reason = "Sub-ICB Location set by handle_orphan_practices"
            practices.update(ccg_id=new_ccg, ccg_change_reason=reason)

    def handle_case_b(self, ccg, new_ccgs):
        # The CCG has closed and its practices have moved to multiple CCGs
        name = self.ccg_to_name[ccg]

        if self.dry_run:
            self.stdout.write(
                "All practices currently in Sub-ICB Location are closed or dormant"
            )
            self.stdout.write(
                "Active practices previously in Sub-ICB Location are now in multiple Sub-ICB Locations:"
            )
            for new_ccg, count in new_ccgs.most_common():
                new_ccg_name = self.ccg_to_name[new_ccg]
                self.stdout.write("{:3} {} ({})".format(count, new_ccg, new_ccg_name))

        else:
            msg = """
All active practices previously in Sub-ICB Location {} ({}) are now in multiple Sub-ICB Locations:
Check whether inactive practices remaining in Sub-ICB Location should have moved.
See instructions in handle_orphan_practices.py.
            """.format(ccg, name).strip()
            for new_ccg, count in new_ccgs.most_common():
                new_ccg_name = self.ccg_to_name[new_ccg]
                msg += "\n{:3} {} ({})".format(count, new_ccg, new_ccg_name)
            notify_slack(msg)

    def handle_case_c(self, ccg):
        # The CCG is still open but some practices have moved to another CCG
        name = self.ccg_to_name[ccg]

        if self.dry_run:
            self.stdout.write(
                "Some practices have left Sub-ICB Location and some currently in Sub-ICB Location are not active"
            )
        else:
            msg = """
Practices have left Sub-ICB Location {} ({}) and some remaining practices are not active.
Check whether these inactive practices should have moved.
See instructions in handle_orphan_practices.py.
            """.format(ccg, name).strip()
            notify_slack(msg)
