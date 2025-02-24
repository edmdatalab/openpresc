import textwrap

from django.core.management.base import BaseCommand
from django.db import transaction
from frontend.models import Presentation

BNF_CODES = """
0206010F0AACJCJ
1202010U0AAAAAA
"""


class Command(BaseCommand):
    help = textwrap.dedent(
        """
        This command sets the `quantity_means_pack` flag on the Presentations
        model. We don't yet have a reliable source for this so we have to rely
        on various heuristics. This command should be the *only* way these
        flags get set so don't be tempted to modify the table by hand.
        """
    )

    @transaction.atomic
    def handle(self, *args, **options):
        # At present the only "heuristic" this uses is an explict list of BNF
        # codes, but we expect this to change shortly
        codes = [_f for _f in [line.strip() for line in BNF_CODES.splitlines()] if _f]
        query = Presentation.objects
        query.filter(bnf_code__in=codes).update(quantity_means_pack=True)
        query.exclude(bnf_code__in=codes).update(quantity_means_pack=False)
