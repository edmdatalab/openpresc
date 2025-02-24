# -*- coding: utf-8 -*-
import unittest

from mock import PropertyMock, patch
from selenium.webdriver.common.by import By

from .selenium_base import SeleniumTestCase


class GeneralFrontendTest(SeleniumTestCase):
    # These tests run against a MockAPIServer started by the
    # custom_runner
    fixtures = ["functional_test_data"]

    def test_menu_dropdown_on_doc_page(self):
        with patch("requests.get") as mock_response:
            # Mock the text fetched from Google Docs
            text = PropertyMock(
                return_value=("<head><style></style></head><body>some text</body>")
            )
            type(mock_response.return_value).text = text
            url = self.live_server_url + "/docs/analyse/"
            self.browser.get(url)
            self.find_by_xpath("//a[contains(text(), 'More')]").click()
            self.assertTrue(
                self.find_by_xpath("//a[contains(text(), 'About')]").is_displayed(),
                "dropdown functionality broken at %s" % url,
            )

    def test_menu_dropdown(self):
        for url in [
            "/sicbl/03Q/",
            "/sicbl/03Q/measures/",
            "/practice/P87629/",
            "/measure/cerazette/",
            "/chemical/0202010D0/",
            "/bnf/020201/",
            "/analyse/",
            "/measure/",
            "/about",
        ]:
            url = self.live_server_url + url
            self.browser.get(url)
            try:
                self.find_by_xpath("//a[contains(text(), 'More')]").click()
            except AssertionError as e:
                e.args += ("at URL %s" % url,)
                raise
            self.assertTrue(
                self.find_by_xpath("//a[contains(text(), 'About')]").is_displayed(),
                "dropdown functionality broken at %s" % url,
            )

    def test_drug_name_typeahead(self):
        self.browser.get(self.live_server_url + "/analyse/")
        el = self.find_by_xpath(
            '//div[@id="numIds-wrapper"]//input[@class="select2-search__field"]'
        )
        el.send_keys("chl")
        # This should succeed; if not, the JSON dropdown-filling has not:
        self.find_by_xpath('//ul[@id="select2-numIds-results"]//li')

    def test_practice_name_typeahead(self):
        self.browser.get(self.live_server_url + "/analyse/")
        self.find_by_xpath('//span[@id="select2-org-container"]').click()
        self.find_by_xpath('//li[contains(text(), "Practices")]').click()
        el = self.find_by_xpath(
            '//div[@id="orgIds-container"]//input[@class="select2-search__field"]'
        )
        el.send_keys("ains")
        # This should succeed; if not, the JSON dropdown-filling has not:
        self.find_by_xpath('//ul[@id="select2-orgIds-results"]//li')

    def test_ccg_measures_sorting(self):
        url = self.live_server_url + "/sicbl/02Q/measures/"
        self.browser.get(url)
        # The default should be sorting by percentile, then id
        self.assertEqual(
            self.find_by_xpath("//div[@id='charts']/div[2]").get_attribute("id"),
            "measure_cerazette",
        )

        # Now click to sort by potential savings
        self.browser.find_element(By.XPATH, "//button[@data-orderby='savings']").click()

        # hack: not sure of the correct way to await the element
        # being visible.
        import time

        time.sleep(1)
        self.assertEqual(
            self.find_by_xpath("//div[@id='charts']/div[1]").get_attribute("id"),
            "measure_keppra",
        )

    def test_ccg_measures_tags(self):
        url = self.live_server_url + "/sicbl/02Q/measures/?tags=foobar"
        self.browser.get(url)
        # nothing is tagged foobar, so should return the text expected
        # when no measures are shown
        import time

        time.sleep(1)
        self.assertTrue(self.find_by_xpath("//p[contains(text(), 'Unrecognised tag')]"))

    def test_ccg_measures_explore_link(self):
        url = self.live_server_url + "/sicbl/02Q/measures/"
        self.browser.get(url)
        measure = self.find_by_xpath("//div[@id='measure_keppra']")
        self.assertIn(
            "/measure/keppra",
            measure.find_element(
                By.PARTIAL_LINK_TEXT, "Compare all Sub-ICB Locations"
            ).get_attribute("href"),
        )
        self.assertIn(
            "/sicbl/02Q/keppra",
            measure.find_element(
                By.PARTIAL_LINK_TEXT, "Split the measure"
            ).get_attribute("href"),
        )


if __name__ == "__main__":
    unittest.main()
