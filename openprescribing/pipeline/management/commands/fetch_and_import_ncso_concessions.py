# coding=utf8

import datetime
import logging
import re
from collections import defaultdict

import bs4
import requests
from django.core.management import BaseCommand
from django.db import transaction
from dmd.models import VMPP
from frontend.models import NCSOConcession
from gcutils.bigquery import Client

from openprescribing.slack import notify_slack

logger = logging.getLogger(__file__)

PRICE_CONCESSIONS_URL = (
    "https://cpe.org.uk/funding-and-reimbursement/reimbursement/price-concessions/"
)

PRICE_CONCESSIONS_ARCHIVE_URL = (
    "https://cpe.org.uk"
    "/funding-and-reimbursement/reimbursement/price-concessions/archive/"
)

DEFAULT_HEADERS = {
    "User-Agent": "OpenPrescribing-Bot (+https://openprescribing.net)",
}

DATE_RE = r"""
    (?P<month>
        january | february | march | april | may | june | july | august |
        september | october | november | december
    )
    \s+
    (?P<year> 20\d\d)
"""

HEADING_DATE_RE = re.compile(
    rf"""
    ^
    # Optional leading text
    ( The \s+ following \s+ price \s+ concessions \s+ have \s+ been \s+ granted \s+ for \s+ )?
    # Date in the form "March 2020"
    {DATE_RE}
    $
    """,
    re.VERBOSE | re.IGNORECASE,
)

ROLLED_OVER_HEADING_DATE_RE = re.compile(
    rf"""
    ^
    # Optional leading text
    ( The \s+ following \s+ price \s+ concessions \s+ rolled \s+ over \s+ from \s+ \w+ \s+ 20\d\d \s+ to \s+ )?
    # Date in the form "March 2020"
    {DATE_RE}
    :?
    $
    """,
    re.VERBOSE | re.IGNORECASE,
)


NAME_FIXES = {
    "co-amoxiclav 500/125 tablets 21": "co-amoxiclav 500mg/125mg tablets 21",
    "co-amoxiclav 250/125 tablets 21": "co-amoxiclav 250mg/125mg tablets 21",
}


class Command(BaseCommand):
    def handle(self, *args, **kwargs):
        # Fetch and parse the concession data
        response = requests.get(PRICE_CONCESSIONS_URL, headers=DEFAULT_HEADERS)
        items = parse_concessions(response.content)

        # Find matching VMPPs for each concession, where possible
        vmpp_id_to_name = get_vmpp_id_to_name_map()
        matched = match_concession_vmpp_ids(items, vmpp_id_to_name)

        # Insert into database
        inserted = insert_or_update(matched)

        # Upload to BigQuery
        Client("dmd").upload_model(NCSOConcession)

        # Report results
        msg = format_message(inserted)
        notify_slack(msg)


def parse_concessions(html):
    doc = bs4.BeautifulSoup(html, "html5lib")
    # Find all tables with appropriate headers
    tables = [
        td.find_parent("table")
        for td in doc.find_all("td")
        if (td.text or "").strip().lower() == "pack size"
    ]
    for table in tables:
        date = get_date_for_table(table)
        rows = rows_from_table(table)

        # Check headers
        headers = next(rows)
        assert headers[0].lower() in ("drug", "drug name"), headers[0]
        assert headers[1].lower() == "pack size", headers[1]
        assert headers[2].lower() in (
            "price concession",
            "price concessions",
            "price",
        ), headers[2]
        assert len(headers) == 3, headers

        for row in rows:
            yield {
                "date": date,
                # Strip trailing asterisks which are sometimes used to indicate rolled over
                # concessions
                "drug": row[0].strip("*"),
                "pack_size": row[1],
                "price_pence": parse_price(row[2]),
            }


def get_date_for_table(table):
    heading = get_section_heading(table)
    # Generally speaking the section heading gives the associated date
    if match := HEADING_DATE_RE.match(heading):
        return parse_date(f"1 {match['month']} {match['year']}")
    # However later sections of the historical archive are grouped by year, and in this
    # case the date for the table is given by the text immediately preceeding it
    #
    # Rolled over price concessions may also appear as their own table, again the
    # date for the table is given by the text immediately preceding it
    # The rolled over concessions, when seen in a separate table, ALSO appear
    # in the main table for that month; we could ignore the rolled-over table, but in
    # case they are separated out in future, we parse data from both tables and check
    # for duplicates in match_concession_vmpp_ids() later
    elif re.match(r"[\d\d\d\d]|[Rolled over price concessions]", heading):
        intro = fix_spaces(table.find_previous_sibling().text or "")
        if match := HEADING_DATE_RE.match(intro) or ROLLED_OVER_HEADING_DATE_RE.match(
            intro
        ):
            return parse_date(f"1 {match['month']} {match['year']}")
        else:
            assert False, f"Unhandled table intro: {intro!r}"
    else:
        assert False, f"Unhandled section heading: {heading!r}"


def get_section_heading(table):
    container = table.parent
    assert "toggle_container" in container["class"]
    toggle = container.find_previous_sibling()
    assert "trigger" in toggle["class"]
    return fix_spaces(toggle.text or "")


def get_single_item(iterator):
    unique = set(iterator)
    assert len(unique) == 1, f"Expected exactly one item, got: {unique}"
    return list(unique)[0]


def parse_date(date_str):
    # Parses dates like "1 January 2020"
    return datetime.datetime.strptime(date_str, "%d %B %Y").date()


def rows_from_table(table):
    for tr in table.find_all("tr"):
        yield [fix_spaces(td.text or "") for td in tr.find_all("td")]


def fix_spaces(s):
    """Remove extra spaces and convert non-breaking spaces to normal ones."""

    s = s.replace("\xa0", " ")
    s = s.strip()
    s = re.sub(" +", " ", s)
    return s


def parse_price(price_str):
    # Correct typo. If these happen more regularly we'll need to take a different
    # approach but I want to be maximally conservative to begin with.
    if price_str == "11..35":
        price_str = "£11.35"
    # We need to accept a variety of formats here: £1.50, 1.50, 1.5, 11
    match = re.fullmatch(
        r"""
        # Optional leading currency symbol
        £ ?
        # Pounds digits
        ( \d+ )
        # Optional pence digits (.N or .NN)
        ( \. (\d) (\d)? ) ?
        # Optional previous price in parentheses
        \s*
        ( \( previously \s+ [£\d\.]+ \) ) ?
        """,
        price_str,
        re.VERBOSE,
    )
    assert match, price_str
    return (
        int(match[1]) * 100
        + int(match[3] if match[3] is not None else 0) * 10
        + int(match[4] if match[4] is not None else 0)
    )


def match_concession_vmpp_ids(items, vmpp_id_to_name):
    # Build mapping from regularised names to VMPPs. Most such names map to just one
    # VMPP, but there are some ambiguous cases.
    regular_name_to_vmpp_ids = defaultdict(list)
    for vmpp_id, name in vmpp_id_to_name.items():
        regular_name_to_vmpp_ids[regularise_name(name)].append(vmpp_id)

    matched = []

    for item in items:
        supplied_name = regularise_name(f"{item['drug']} {item['pack_size']}")
        # Apply known fixes
        supplied_name = NAME_FIXES.get(supplied_name, supplied_name)
        matched_vmpp_ids = regular_name_to_vmpp_ids[supplied_name]
        # If there's an unambiguous match then we assume that is the correct VMPP
        if len(matched_vmpp_ids) == 1:
            item["vmpp_id"] = matched_vmpp_ids[0]
        # Otherwse we check if we've previously manually reconciled this concession to a
        # VMPP then re-use that ID if so
        else:
            item["vmpp_id"] = get_vmpp_id_from_previous_concession(
                item["drug"], item["pack_size"]
            )
        if item not in matched:
            matched.append(item)

    return matched


def get_vmpp_id_to_name_map():
    # Build mapping from ID to name for all valid VMPPs. We only want VMPPs which are
    # not marked as "invalid" and which we can match to prescribing data. See:
    # https://github.com/ebmdatalab/openprescribing/issues/3978
    return {
        vmpp_id: name
        for vmpp_id, name in VMPP.objects.filter(bnf_code__isnull=False)
        .exclude(invalid=True)
        .values_list("id", "nm")
    }


def regularise_name(name):
    if name is None:
        return

    # replace "smart" quotes
    name = name.replace("‘", "'")
    name = name.replace("’", "'")

    # dm+d uses "microgram" or "micrograms", usually with these rules
    name = name.replace("mcg ", "microgram ")
    name = name.replace("mcg/", "micrograms/")

    # dm+d uses "microgram" rather than "0.X.mg"
    name = name.replace("0.5mg", "500microgram")
    name = name.replace("0.25mg", "250microgram")

    # dm+d uses "square cm"
    name = name.replace("sq cm", "square cm")

    # dm+d records measured in mg/ml have a space before the final "ml"
    # eg: Abacavir 20mg/ml oral solution sugar free 240 ml
    name = re.sub(r"(\d)ml$", r"\1 ml", name)

    # dm+d records have "gram$" not "g$"
    # eg: Estriol 0.01% cream 80 gram
    name = re.sub(r"(\d)g$", r"\1 gram", name)

    # Misc. common replacements
    name = name.replace("Oral Susp SF", "oral suspension sugar free")
    name = name.replace("gastro- resistant", "gastro-resistant")
    name = name.replace("/ml", "/1ml")
    name = re.sub(r"\bcaps\b", "capsules", name)

    # Strip leading asterisks which are sometimes used to indicate the presence of
    # additional notes
    name = re.sub(r"^\s*\*\s*", "", name)

    # Lowercase
    name = name.lower()

    # Remove spaces around slashes
    name = re.sub(" */ *", "/", name)

    # dm+d names often have the units appended after the pack size (e.g. "12 tablets"),
    # we want to remove the units leaving just the "12" so as to match the names we get
    # from the price concessions feed. Note that this can, in rare cases, result in
    # ambiguities. Where these arise we say that we are unable to find a matching name,
    # rather than arbitrarily picking one. See:
    # https://github.com/ebmdatalab/openprescribing/issues/3979
    name = re.sub(r"(\d+) [^\d]+$", r"\1", name)

    # Strip trailing asterisk
    name = re.sub(r"\s*\*\s*(\d+)$", r" \1", name)

    return name


def get_vmpp_id_from_previous_concession(drug, pack_size):
    previous_vmpp_ids = list(
        NCSOConcession.objects.filter(
            drug=drug, pack_size=pack_size, vmpp__isnull=False
        ).values_list("vmpp_id", flat=True)
    )
    if previous_vmpp_ids:
        return get_single_item(previous_vmpp_ids)


def insert_or_update(items):
    inserted = []
    with transaction.atomic():
        for item in items:
            if item["vmpp_id"] is not None:
                _, created = NCSOConcession.objects.update_or_create(
                    date=item["date"],
                    vmpp_id=item["vmpp_id"],
                    defaults=dict(
                        drug=item["drug"],
                        pack_size=item["pack_size"],
                        price_pence=item["price_pence"],
                    ),
                )
            else:
                # Unmatched concessions get inserted as placeholder entries, ready for
                # manual reconciliation
                _, created = NCSOConcession.objects.update_or_create(
                    date=item["date"],
                    drug=item["drug"],
                    pack_size=item["pack_size"],
                    vmpp_id=None,
                    defaults=dict(
                        price_pence=item["price_pence"],
                    ),
                )

            item["created"] = created
            inserted.append(item)
            if created:
                logger.info("Creating {drug} {pack_size} {price_pence}".format(**item))

    return inserted


def format_message(inserted):
    created = [i for i in inserted if i["created"]]
    unmatched = [i for i in inserted if i["vmpp_id"] is None]

    msg = f"Fetched {len(inserted)} concessions. "

    if not created:
        msg += "Found no new concessions to import."
    else:
        msg += f"Imported {len(created)} new concessions."

    # Warn about cases where we couldn't match the drug name and pack size to a VMPP
    if unmatched:
        msg += "\n\nThe following concessions will need to be manually matched:\n"
        for item in unmatched:
            msg += f"Name: {item['drug']} {item['pack_size']}\n"

    return msg
