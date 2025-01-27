import re
import sys

import requests
from django.conf import settings
from django.core.management import BaseCommand
from requests.exceptions import InvalidJSONError, RequestException

from .import_measures import BadRequest
from .import_measures import Command as ImportMeasuresCommand
from .import_measures import ImportLog, relativedelta


class Command(BaseCommand):
    def handle(self, github_url, **options):
        try:
            measure_id = import_preview_measure(github_url)
        except BadRequest as e:
            # We want these errors to be visble to users who run via bennettbot but the only
            # way to achieve that is to write them to stderr and exit 0 :(
            self.stdout.write(
                f"Importing measure preview failed for {github_url}\n\n{e.message}"
            )
            sys.exit(0)
        measure_url = f"https://openprescribing.net/measure/{measure_id}/"
        self.stdout.write(
            f"Measure can be previewed at:\n{measure_url}\n\n"
            f"When you've finished remember to delete the preview with:\n"
            f"@bennett_bot op measures delete_preview {measure_id}"
        )

    def add_arguments(self, parser):
        parser.add_argument("github_url")


def import_preview_measure(github_url):
    measure_id, json_url = get_id_and_json_url(github_url)
    measure_def = fetch_measure_def(json_url)
    measure_def["id"] = measure_id

    measure_def = make_preview_measure(measure_def)
    import_measure(measure_def)

    return measure_def["id"]


def get_id_and_json_url(github_url):
    match = re.match(
        r"^"
        r"https://github\.com/(ebmdatalab|bennettoxford)/openprescribing/blob/"
        r"(?P<git_ref>[^/\.]+)"
        r"/openprescribing/measures/definitions/"
        r"(?P<measure_id>[^/\.]+)"
        r"\.json"
        r"$",
        github_url,
    )
    if not match:
        raise BadRequest(
            "Expecting a URL in the format:\n"
            "https://github.com/ebmdatalab/openprescribing/blob/<GIT_REF>/"
            "openprescribing/measures/definitions/<MEASURE_ID>.json\n"
            "\n"
            "You can get this URL by finding the measure file in your branch on "
            "Github:\n"
            "https://github.com/ebmdatalab/openprescribing/branches\n"
            "\n"
            "Or if you have a PR open you can go to the Files tab, click the three "
            "dots next to the measure filename and select 'View file'"
        )
    git_ref = match.group("git_ref")
    measure_id = match.group("measure_id")
    json_url = (
        f"https://raw.githubusercontent.com/ebmdatalab/openprescribing/"
        f"{git_ref}/openprescribing/measures/definitions/{measure_id}.json"
    )
    return measure_id, json_url


def fetch_measure_def(json_url):
    try:
        response = requests.get(json_url)
        response.raise_for_status()
    except RequestException as e:
        raise BadRequest(f"Failed to fetch measure JSON, got error:\n{e}")
    try:
        measure_def = response.json()
    except InvalidJSONError as e:
        raise BadRequest(f"Measure definition was not valid JSON, got error:\n{e}")
    return measure_def


def make_preview_measure(measure_def):
    measure_def = measure_def.copy()
    measure_def["id"] = settings.MEASURE_PREVIEW_PREFIX + measure_def["id"]
    measure_def["name"] = f"PREVIEW: {measure_def['name']}"
    measure_def["tags"] = []
    measure_def["include_in_alerts"] = False
    return measure_def


def import_measure(measure_def):
    end_date = ImportLog.objects.latest_in_category("prescribing").current_at
    start_date = end_date - relativedelta(years=5)

    command = ImportMeasuresCommand()
    command.check_definitions([measure_def], start_date, end_date, verbose=False)
    command.build_measures(
        [measure_def],
        start_date,
        end_date,
        verbose=False,
        options={
            "measure": measure_def["id"],
            "definitions_only": False,
            "bigquery_only": False,
        },
    )
