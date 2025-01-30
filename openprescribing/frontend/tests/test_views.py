import datetime
import os
from urllib.parse import parse_qs, urlparse

import mock
from django.conf import settings
from django.core import mail
from django.core.management import call_command
from django.http import QueryDict
from django.test import SimpleTestCase, TestCase, override_settings
from frontend.models import EmailMessage, Measure, OrgBookmark, SearchBookmark
from frontend.price_per_unit.substitution_sets import (
    get_substitution_sets,
    get_substitution_sets_by_presentation,
)
from frontend.views.views import BadRequestError, _get_measure_tag_filter, cached
from matrixstore.tests.decorators import copy_fixtures_to_matrixstore
from mock import Mock
from pyquery import PyQuery as pq


class TestAlertViews(TestCase):
    fixtures = [
        "chemicals",
        "sections",
        "orgs",
        "practices",
        "prescriptions",
        "measures",
        "importlog",
    ]

    def _post_org_signup(self, entity_id, email="foo@baz.com", follow=True):
        form_data = {"email": email}
        if entity_id == "all_england":
            url = "/national/england/"
        elif len(entity_id) == 3:
            url = "/sicbl/%s/" % entity_id
            form_data["pct_id"] = entity_id
        else:
            url = "/practice/%s/" % entity_id
            form_data["practice_id"] = entity_id
        return self.client.post(url, form_data, follow=follow)

    def _post_search_signup(self, url, name, email="foo@baz.com"):
        form_data = {"email": email}
        form_data["url"] = url
        form_data["name"] = name
        return self.client.post("/analyse/", form_data, follow=True)

    def test_search_email_sent(self):
        response = self._post_search_signup("stuff", "mysearch")
        self.assertContains(response, "alerts about mysearch")
        self.assertRedirects(response, "/analyse/#stuff")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("about mysearch", mail.outbox[0].body)

    def test_search_email_copy_kept(self):
        self._post_search_signup("stuff", "mysearch")
        msg = EmailMessage.objects.first()
        self.assertIn("about mysearch", msg.message.body)
        self.assertIn("foo@baz.com", msg.to)

    def test_search_bookmark_created(self):
        self.assertEqual(SearchBookmark.objects.count(), 0)
        self._post_search_signup("stuff", "%7Emysearch")
        self.assertEqual(SearchBookmark.objects.count(), 1)
        bookmark = SearchBookmark.objects.last()
        self.assertEqual(bookmark.url, "stuff")
        # Check the name is URL-decoded
        self.assertEqual(bookmark.name, "~mysearch")

    def test_ccg_email_sent(self):
        email = "a@a.com"
        response = self._post_org_signup("03V", email=email)
        self.assertRedirects(response, "/sicbl/03V/measures/")
        self.assertContains(response, "alerts about prescribing in NHS Corby.")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(email, mail.outbox[0].to)
        self.assertIn("about prescribing in NHS Corby", mail.outbox[0].body)

    def test_ccg_bookmark_created(self):
        self.assertEqual(OrgBookmark.objects.count(), 0)
        self._post_org_signup("03V")
        self.assertEqual(OrgBookmark.objects.count(), 1)
        bookmark = OrgBookmark.objects.last()
        self.assertEqual(bookmark.pct.code, "03V")

    def test_ccg_duplicate_bookmark_not_created(self):
        self.assertEqual(OrgBookmark.objects.count(), 0)
        self._post_org_signup("03V")
        self.assertEqual(OrgBookmark.objects.count(), 1)
        self._post_org_signup("03V")
        self.assertEqual(OrgBookmark.objects.count(), 1)
        bookmark = OrgBookmark.objects.last()
        self.assertEqual(bookmark.pct.code, "03V")

    def test_ccg_duplicate_bookmark_not_created_when_email_not_lowercase(self):
        self.assertEqual(OrgBookmark.objects.count(), 0)
        self._post_org_signup("03V")
        self.assertEqual(OrgBookmark.objects.count(), 1)
        self._post_org_signup("03V", email="FOO@BAZ.COM")
        self.assertEqual(OrgBookmark.objects.count(), 1)
        bookmark = OrgBookmark.objects.last()
        self.assertEqual(bookmark.pct.code, "03V")

    def test_practice_email_sent(self):
        response = self._post_org_signup("P87629")
        self.assertContains(
            response, "alerts about prescribing in 1/ST Andrews Medical Practice"
        )
        self.assertRedirects(response, "/practice/P87629/measures/")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("about prescribing in 1/ST Andrews", mail.outbox[0].body)

    def test_practice_bookmark_created(self):
        self.assertEqual(OrgBookmark.objects.count(), 0)
        self._post_org_signup("P87629")
        self.assertEqual(OrgBookmark.objects.count(), 1)
        bookmark = OrgBookmark.objects.last()
        self.assertEqual(bookmark.practice.code, "P87629")

    def test_all_england_bookmark_created(self):
        self.assertEqual(OrgBookmark.objects.count(), 0)
        # We don't follow the redirect, because we don't have the necessary test data for
        # testing the all-england page.
        response = self._post_org_signup("all_england", follow=False)
        self.assertRedirects(
            response, "/national/england/", fetch_redirect_response=False
        )
        self.assertEqual(OrgBookmark.objects.count(), 1)
        bookmark = OrgBookmark.objects.last()
        self.assertEqual(bookmark.practice, None)
        self.assertEqual(bookmark.pct, None)
        self.assertEqual(bookmark.org_type(), "all_england")

    def test_all_england_bookmark_created_when_user_has_another_org_bookmark(self):
        # Regression test for #2440

        self._post_org_signup("P87629")
        self.assertEqual(OrgBookmark.objects.count(), 1)
        # We don't follow the redirect, because we don't have the necessary test data for
        # testing the all-england page.
        response = self._post_org_signup("all_england", follow=False)
        self.assertRedirects(
            response, "/national/england/", fetch_redirect_response=False
        )
        self.assertEqual(OrgBookmark.objects.count(), 2)
        bookmark = OrgBookmark.objects.last()
        self.assertEqual(bookmark.practice, None)
        self.assertEqual(bookmark.pct, None)
        self.assertEqual(bookmark.org_type(), "all_england")

    def test_pcn_bookmark_created(self):
        self.assertEqual(OrgBookmark.objects.count(), 0)
        form_data = {
            "email": "foo@baz.com",
            "newsletters": ["alerts"],
            "pcn_id": "PCN0001",
        }
        url = "/pcn/{}/".format("PCN0001")
        self.client.post(url, form_data, follow=True)
        self.assertEqual(OrgBookmark.objects.count(), 1)
        bookmark = OrgBookmark.objects.last()
        self.assertEqual(bookmark.pcn.code, "PCN0001")


@copy_fixtures_to_matrixstore
class TestFrontendHomepageViews(TestCase):
    fixtures = [
        "practices",
        "orgs",
        "one_month_of_measures",
        "homepage_importlog",
        "dmd-subset",
        "homepage_prescriptions",
    ]

    def test_call_regional_team_homepage(self):
        response = self.client.get("/regional-team/Y01/")
        doc = pq(response.content)
        ccgs = doc(".ccg-list li")
        self.assertEqual(len(ccgs), 1)

    def test_call_stp_homepage(self):
        response = self.client.get("/icb/E01/")
        doc = pq(response.content)
        ccgs = doc(".ccg-list li")
        self.assertEqual(len(ccgs), 1)

    def test_call_view_ccg_homepage(self):
        response = self.client.get("/sicbl/02Q/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "entity_home_page.html")
        self.assertEqual(response.context["measure"].id, "cerazette")
        self.assertEqual(response.context["measures_count"], 2)
        self.assertEqual(response.context["possible_savings"], 0.0)
        self.assertEqual(response.context["entity"].code, "02Q")
        self.assertEqual(response.context["entity_type"], "ccg")
        self.assertEqual(response.context["date"], datetime.date(2015, 9, 1))
        doc = pq(response.content)
        practices = doc(".practice-list li")
        self.assertEqual(len(practices), 7)
        pcns = doc(".pcn-list li")
        self.assertEqual(len(pcns), 1)

    def test_call_view_pcn_homepage(self):
        response = self.client.get("/pcn/PCN001/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "entity_home_page.html")
        self.assertEqual(response.context["entity"].code, "PCN001")
        self.assertEqual(response.context["entity_type"], "pcn")
        self.assertEqual(response.context["date"], datetime.date(2015, 9, 1))
        doc = pq(response.content)
        practices = doc(".practice-list li")
        self.assertEqual(len(practices), 5)

    def test_call_view_practice_homepage(self):
        response = self.client.get("/practice/C84001/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "entity_home_page.html")
        self.assertEqual(response.context["measure"].id, "cerazette")
        self.assertEqual(response.context["measures_count"], 2)
        self.assertEqual(response.context["possible_savings"], 0.0)
        self.assertEqual(response.context["entity"].code, "C84001")
        self.assertEqual(response.context["entity_type"], "practice")
        self.assertEqual(response.context["date"], datetime.date(2015, 9, 1))
        doc = pq(response.content)
        title = doc("h1")
        self.assertEqual(title.text(), "Larwood Surgery")

    def test_call_view_regional_team_homepage_no_prescribing(self):
        response = self.client.get("/regional-team/Y60/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "closed_entity_home_page.html")

    def test_call_view_stp_homepage_no_prescribing(self):
        response = self.client.get("/icb/E55/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "closed_entity_home_page.html")

    def test_call_view_ccg_homepage_no_prescribing(self):
        response = self.client.get("/sicbl/03X/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "closed_entity_home_page.html")

    def test_call_view_pcn_homepage_no_prescribing(self):
        response = self.client.get("/pcn/PCN0002/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "closed_entity_home_page.html")

    def test_call_view_practice_homepage_no_prescribing(self):
        response = self.client.get("/practice/P87629/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "closed_entity_home_page.html")


@copy_fixtures_to_matrixstore
class TestFrontendViews(TestCase):
    fixtures = [
        "chemicals",
        "sections",
        "orgs",
        "practices",
        "measures",
        "homepage_importlog",
        "homepage_prescriptions",
    ]

    def test_call_view_homepage(self):
        response = self.client.get("")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "index.html")

    def test_robots_txt(self):
        response = self.client.get("/robots.txt")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Disallow")

    def test_javascript_inclusion(self):
        with self.settings(DEBUG=False):
            response = self.client.get("")
            doc = pq(response.content)
            mainjs = doc("script")[-1].attrib["src"]
            self.assertIn("global.min.js", mainjs)
        with self.settings(DEBUG=True, INTERNAL_IPS=("127.0.0.1",)):
            response = self.client.get("")
            doc = pq(response.content)
            mainjs = doc("script")[-1].attrib["src"]
            self.assertIn("global.js", mainjs)

    def test_call_view_analyse(self):
        response = self.client.get("/analyse/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "analyse.html")
        self.assertNotContains(response, "Preview alert email")

    def test_call_view_bnf_all(self):
        response = self.client.get("/bnf/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "all_bnf.html")
        self.assertContains(response, "<h1>All BNF sections</h1>")
        doc = pq(response.content)
        sections = doc("#all-results li")
        self.assertEqual(len(sections), 5)
        first_section = doc("#all-results li:first")
        self.assertEqual(first_section.text(), "2: Cardiovascular System")

    def test_call_view_bnf_chapter(self):
        response = self.client.get("/bnf/02/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "bnf_section.html")
        doc = pq(response.content)
        title = doc("h1")
        self.assertEqual(title.text(), "2: Cardiovascular System")
        subsections = doc("a.subsection")
        self.assertEqual(len(subsections), 2)

    def test_call_view_bnf_section(self):
        response = self.client.get("/bnf/0202/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "bnf_section.html")
        doc = pq(response.content)
        title = doc("h1")
        self.assertEqual(title.text(), "2.2: Diuretics")
        lead = doc(".lead")
        self.assertEqual(lead.text(), "Part of chapter 2 Cardiovascular System")
        subsections = doc("a.subsection")
        self.assertEqual(len(subsections), 1)

    def test_call_view_bnf_para(self):
        response = self.client.get("/bnf/020201/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "bnf_section.html")
        doc = pq(response.content)
        title = doc("h1")
        self.assertEqual(title.text(), "2.2.1: Thiazides And Related Diuretics")
        lead = doc(".lead")
        self.assertEqual(
            lead.text(),
            "Part of chapter 2 Cardiovascular System, section 2.2 Diuretics",
        )
        subsections = doc("a.subsection")
        self.assertEqual(len(subsections), 0)

    def test_call_view_chemical_all(self):
        response = self.client.get("/chemical/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "all_chemicals.html")
        self.assertContains(response, "<h1>All chemicals</h1>")
        doc = pq(response.content)
        title = doc("h1")
        self.assertEqual(title.text(), "All chemicals")
        sections = doc("#all-results li")
        self.assertEqual(len(sections), 4)
        first_section = doc("#all-results li:first")
        self.assertEqual(first_section.text(), "Bendroflumethiazide (0202010B0)")

    def test_call_view_chemical_section(self):
        response = self.client.get("/chemical/0202010D0/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "chemical.html")
        doc = pq(response.content)
        title = doc("h1")
        self.assertEqual(title.text(), "Chlorothiazide (0202010D0)")
        lead = doc(".lead")
        self.assertEqual(
            lead.text(),
            (
                "Part of chapter 2 Cardiovascular System, section 2.2 "
                "Diuretics, paragraph 2.2.1 Thiazides And Related Diuretics"
            ),
        )

    def test_call_view_ccg_all(self):
        response = self.client.get("/sicbl/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "all_ccgs.html")
        doc = pq(response.content)
        title = doc("h1")
        self.assertEqual(title.text(), "All Sub-ICB Locations")
        ccgs = doc("a.ccg")
        self.assertEqual(len(ccgs), 2)

    def test_ccg_homepage_redirects_with_tags_query(self):
        response = self.client.get("/sicbl/03V/?tags=lowpriority")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/sicbl/03V/measures/?tags=lowpriority")

    def test_call_single_measure_for_ccg(self):
        response = self.client.get("/measure/cerazette/sicbl/03V/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "measure_for_one_entity.html")

    def test_call_single_measure_for_pcn(self):
        response = self.client.get("/measure/cerazette/pcn/PCN0001/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "measure_for_one_entity.html")

    def test_call_view_practice_all(self):
        response = self.client.get("/practice/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "all_practices.html")
        doc = pq(response.content)
        title = doc("h1")
        self.assertEqual(title.text(), "Find a practice")
        practices = doc("#all-results a.practice")
        self.assertEqual(len(practices), 0)

    def test_call_view_pcn_all(self):
        response = self.client.get("/pcn/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "all_pcns.html")

    def test_practice_homepage_redirects_with_tags_query(self):
        response = self.client.get("/practice/P87629/?tags=lowpriority")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"], "/practice/P87629/measures/?tags=lowpriority"
        )

    def test_call_single_measure_for_practice(self):
        response = self.client.get("/measure/cerazette/practice/P87629/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "measure_for_one_entity.html")

    def test_call_view_measure_ccg(self):
        response = self.client.get("/sicbl/03V/measures/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "measures_for_one_entity.html")
        doc = pq(response.content)
        title = doc("h1")
        self.assertEqual(title.text(), "NHS Corby")
        practices = doc(".practice-list li")
        self.assertEqual(len(practices), 2)

    def test_call_view_measure_pcn(self):
        response = self.client.get("/pcn/PCN0001/measures/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "measures_for_one_entity.html")
        doc = pq(response.content)
        title = doc("h1")
        self.assertEqual(title.text(), "Transformational Sustainability")
        practices = doc(".practice-list li")
        self.assertEqual(len(practices), 2)

    def test_call_view_measure_practice(self):
        response = self.client.get("/practice/P87629/measures/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "measures_for_one_entity.html")
        doc = pq(response.content)
        title = doc("h1")
        self.assertEqual(title.text(), "1/ST Andrews Medical Practice")

    def test_call_view_measure_practices_in_ccg(self):
        response = self.client.get("/sicbl/03V/cerazette/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "measure_for_children_in_entity.html")
        doc = pq(response.content)
        title = doc("h1")
        t = "Cerazette vs. Desogestrel by practices in NHS Corby"
        self.assertEqual(title.text(), t)

    def test_call_view_measure_practices_in_pcn(self):
        response = self.client.get("/pcn/PCN0001/cerazette/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "measure_for_children_in_entity.html")
        doc = pq(response.content)
        title = doc("h1")
        t = "Cerazette vs. Desogestrel by practices in Transformational Sustainability"
        self.assertEqual(title.text(), t)

    def test_all_measures(self):
        response = self.client.get("/measure/")
        self.assertContains(response, "Cerazette")

    def test_all_measures_with_tag_filter(self):
        response = self.client.get("/measure/?tags=lowpriority")
        self.assertNotContains(response, "Cerazette")
        self.assertContains(response, "This list is filtered")

    def test_all_measures_with_tag_filter_core(self):
        response = self.client.get("/measure/?tags=core")
        self.assertContains(response, "Cerazette")
        self.assertContains(response, "This list is filtered")

    def test_all_measures_without_tag_filter(self):
        response = self.client.get("/measure/")
        self.assertContains(response, "Cerazette")
        self.assertNotContains(response, "This list is filtered")

    def test_call_single_measure_for_all_england(self):
        response = self.client.get("/measure/cerazette/national/england/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "measure_for_one_entity.html")

    def test_measure_definition(self):
        response = self.client.get("/measure/cerazette/definition/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "SUM(quantity)")
        self.assertTemplateUsed(response, "measure_definition.html")

    def test_gdoc_inclusion(self):
        for doc_id in settings.GDOC_DOCS.keys():
            response = self.client.get("/docs/%s/" % doc_id)
            self.assertEqual(response.status_code, 200)


@override_settings(
    MEASURE_DEFINITIONS_PATH=os.path.join(settings.APPS_ROOT, "measures", "definitions")
)
class TestMeasureDefinitionViews(TestCase):
    """
    Import all measures and check that their definitions pages load correctly
    """

    # We need a prescribing import entry in order for the measure import
    # command to run
    fixtures = ["importlog"]

    @classmethod
    def setUpTestData(cls):
        # We have to patch these functions as otherwise the measure import
        # process tries to query BigQuery to get a list of matching BNF codes
        fn1 = "frontend.management.commands.import_measures.get_num_or_denom_bnf_codes"
        fn2 = "frontend.management.commands.import_measures.get_bnf_codes"
        with mock.patch(fn1) as get_num_or_denom_bnf_codes:
            with mock.patch(fn2) as get_bnf_codes:
                get_num_or_denom_bnf_codes.return_value = []
                get_bnf_codes.return_value = []
                call_command("import_measures", definitions_only=True)

    def test_can_load_all_measure_definition_pages(self):
        measure_ids = list(Measure.objects.values_list("id", flat=True))
        self.assertGreater(len(measure_ids), 1)
        for measure_id in measure_ids:
            with self.subTest(measure_id=measure_id):
                url = "/measure/{}/definition/".format(measure_id)
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)


class TestTariffView(TestCase):
    fixtures = ["dmd-subset", "tariff"]

    def test_tariff(self):
        response = self.client.get("/tariff/0202010F0AAAAAA/")
        self.assertContains(response, "Tariff")
        self.assertContains(response, 'bnfCodes = "0202010F0AAAAAA"')
        self.assertContains(
            response,
            '<option value="0202010F0AAAAAA" selected>Chlortalidone 50mg tablets</option>',
        )
        self.assertContains(
            response,
            """
            <ul>
              <li><a href="/dmd/vmp/317935006/">Chlortalidone 50mg tablets</a></li>
            </ul>
            """,
            html=True,
        )


@copy_fixtures_to_matrixstore
class TestPPUViews(TestCase):
    fixtures = ["orgs", "importlog", "practices", "prescriptions", "presentations"]

    def setUp(self):
        with mock.patch(
            f"{get_substitution_sets.__module__}.get_swaps", self.get_swaps
        ):
            get_substitution_sets.cache_clear()
            get_substitution_sets_by_presentation.cache_clear()
            get_substitution_sets()
            get_substitution_sets_by_presentation()

    def get_swaps(self):
        return [("0204000I0AAALAL", "", "", "0204000I0JKKKAL", "", "")]

    def test_practice_price_per_unit(self):
        response = self.client.get("/practice/P87629/price_per_unit/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["entity"].code, "P87629")

    def test_ccg_price_per_unit(self):
        response = self.client.get("/sicbl/03V/price_per_unit/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["entity"].code, "03V")
        self.assertEqual(response.context["date"].strftime("%Y-%m-%d"), "2014-11-01")

    def test_ccg_price_per_unit_returns_400_on_invalid_date(self):
        response = self.client.get("/sicbl/03V/price_per_unit/", {"date": "not-a-date"})
        self.assertEqual(response.status_code, 400)

    def test_price_per_unit_histogram_with_ccg(self):
        response = self.client.get("/sicbl/03V/0202010F0AAAAAA/price_per_unit/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["highlight_name"], "NHS Corby")
        self.assertEqual(response.context["date"].strftime("%Y-%m-%d"), "2014-11-01")

    def test_price_per_unit_histogram_with_practice(self):
        response = self.client.get("/practice/P87629/0202010F0AAAAAA/price_per_unit/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["highlight_name"], "1/ST Andrews Medical Practice"
        )
        self.assertEqual(response.context["date"].strftime("%Y-%m-%d"), "2014-11-01")
        bubble_data_url = response.context["bubble_data_url"]
        parsed_url = urlparse(bubble_data_url)
        q = parse_qs(parsed_url.query)
        self.assertEqual(
            q,
            {
                "format": ["json"],
                "bnf_code": ["0202010F0AAAAAA"],
                "highlight": ["P87629"],
                "date": ["2014-11-01"],
            },
        )

    def test_branded_code_redirects_to_generic(self):
        response = self.client.get("/practice/P87629/0204000I0JKKKAL/price_per_unit/")
        self.assertRedirects(
            response, "/practice/P87629/0204000I0AAALAL/price_per_unit/"
        )


class TestGetMeasureTagFilter(TestCase):
    def test_rejects_bad_tags(self):
        with self.assertRaises(BadRequestError):
            _get_measure_tag_filter(QueryDict("tags=nosuchtag"))

    def test_filters_on_core_tag_by_default(self):
        tag_filter = _get_measure_tag_filter(QueryDict())
        self.assertEqual(tag_filter["tags"], ["core"])

    def test_filters_on_no_tags_if_show_all_is_set(self):
        tag_filter = _get_measure_tag_filter(QueryDict(), show_all_by_default=True)
        self.assertEqual(tag_filter["tags"], [])

    def test_show_message_is_not_set_when_using_default_filtering(self):
        tag_filter = _get_measure_tag_filter(QueryDict())
        self.assertEqual(tag_filter["show_message"], False)
        tag_filter = _get_measure_tag_filter(QueryDict("tags=core"))
        self.assertEqual(tag_filter["show_message"], False)

    def test_show_message_is_set_when_using_non_default_filtering(self):
        tag_filter = _get_measure_tag_filter(QueryDict("tags=lowpriority"))
        self.assertEqual(tag_filter["show_message"], True)
        tag_filter = _get_measure_tag_filter(
            QueryDict("tags=core"), show_all_by_default=True
        )
        self.assertEqual(tag_filter["show_message"], True)

    def test_returns_tag_name(self):
        tag_filter = _get_measure_tag_filter(QueryDict("tags=pain"))
        self.assertEqual(tag_filter["names"], ["Pain"])


@override_settings(
    ENABLE_CACHING=True,
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    SOURCE_COMMIT_ID="abc123",
)
class TestCacheWrapper(SimpleTestCase):
    def test_function_calls_are_cached(self):
        test_func = Mock(side_effect=lambda s: "foo%s" % s, __name__="test_func")
        result = cached(test_func, "bar")
        self.assertEqual(result, "foobar")
        result2 = cached(test_func, "bar")
        self.assertEqual(result2, result)
        test_func.assert_called_once_with("bar")

    def test_source_commit_id_used_in_cache_key(self):
        test_func = Mock(__name__="test_func", return_value="foo")
        cached(test_func)
        cached(test_func)
        self.assertEqual(test_func.call_count, 1)
        with override_settings(SOURCE_COMMIT_ID="def456"):
            cached(test_func)
            cached(test_func)
        self.assertEqual(test_func.call_count, 2)

    def test_no_caching_if_not_enabled(self):
        test_func = Mock(__name__="test_func", return_value="foo")
        with override_settings(ENABLE_CACHING=False):
            cached(test_func)
            cached(test_func)
        self.assertEqual(test_func.call_count, 2)


class TestNationalRedirects(TestCase):
    def test_dashboard(self):
        self.assertRedirects(
            self.client.get("/all-england/?foo=bar"),
            "/national/england/?foo=bar",
            fetch_redirect_response=False,
        )

    def test_price_per_unit(self):
        self.assertRedirects(
            self.client.get("/all-england/ABCD00001/price-per-unit/?date=2020-01"),
            "/national/england/ABCD00001/price-per-unit/?date=2020-01",
            fetch_redirect_response=False,
        )

    def test_concessions(self):
        self.assertRedirects(
            self.client.get("/all-england/concessions/"),
            "/national/england/concessions/",
            fetch_redirect_response=False,
        )

    def test_measures(self):
        self.assertRedirects(
            self.client.get("/measure/somemeasure/all-england/"),
            "/measure/somemeasure/national/england/",
            fetch_redirect_response=False,
        )
