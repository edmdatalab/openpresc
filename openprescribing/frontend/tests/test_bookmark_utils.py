import base64
import os
import re
import socket
import unittest
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import requests
from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.test import SimpleTestCase, TestCase
from frontend.models import (
    PCT,
    ImportLog,
    Measure,
    MeasureValue,
    NCSOConcessionBookmark,
    Practice,
)
from frontend.templatetags.template_extras import deltawords
from frontend.tests.data_factory import DataFactory
from frontend.views import bookmark_utils
from frontend.views.spending_utils import ncso_spending_for_entity
from matrixstore.tests.decorators import copy_fixtures_to_matrixstore
from mock import MagicMock, patch


class IntroTextTest(unittest.TestCase):
    def test_nothing(self):
        stats = _makeContext(possible_top_savings_total=9000.1)
        msg = bookmark_utils.getIntroText(stats, "Sub-ICB Location")
        self.assertIn("We've no new information about this Sub-ICB Location", msg)

    def test_worst(self):
        stats = _makeContext(worst=[None])
        msg = bookmark_utils.getIntroText(stats, "Sub-ICB Location")
        self.assertNotIn("We've no new information about this Sub-ICB Location", msg)
        self.assertIn(
            "We've found one prescribing measure where this "
            "Sub-ICB Location could be <span class='worse'>doing better",
            msg,
        )

    def test_worst_plural(self):
        stats = _makeContext(worst=[None, None])
        msg = bookmark_utils.getIntroText(stats, "Sub-ICB Location")
        self.assertIn(
            "We've found two prescribing measures where this "
            "Sub-ICB Location could be <span class='worse'>doing better",
            msg,
        )

    def test_decline_plural(self):
        stats = _makeContext(declines=[None, None])
        msg = bookmark_utils.getIntroText(stats, "Sub-ICB Location")
        self.assertIn(
            "We've found two prescribing measures where this "
            "Sub-ICB Location is <span class='worse'>getting worse",
            msg,
        )

    def test_decline_and_worse(self):
        stats = _makeContext(declines=[None], worst=[None])
        msg = bookmark_utils.getIntroText(stats, "thing")
        self.assertIn(
            "We've found two prescribing measures where this "
            "thing is <span class='worse'>getting worse, "
            "or could be doing better",
            msg,
        )

    def test_improvement(self):
        stats = _makeContext(improvements=[None])
        msg = bookmark_utils.getIntroText(stats, "thing")
        self.assertIn(
            "We've found one prescribing measure where this "
            "thing is <span class='better'>improving",
            msg,
        )

    def test_improvement_and_best(self):
        stats = _makeContext(improvements=[None], best=[None])
        msg = bookmark_utils.getIntroText(stats, "thing")
        self.assertIn(
            "We've found two prescribing measures where this "
            "thing is <span class='better'>doing well",
            msg,
        )

    def test_decline_and_improvement(self):
        stats = _makeContext(declines=[None], improvements=[None])
        msg = bookmark_utils.getIntroText(stats, "thing")
        self.assertIn(
            "We've found one prescribing measure where this "
            "thing is <span class='worse'>getting worse</span>, "
            "and one measure where it is <span class='better'>"
            "improving",
            msg,
        )

    def test_possible_savings(self):
        stats = _makeContext(possible_savings=[None])
        msg = bookmark_utils.getIntroText(stats, "thing")
        self.assertIn(
            "We've also found one prescribing measure where there "
            "are some potential cost savings",
            msg,
        )


def each_cusum_test(test_cases):
    """Iterate over tests defined in the input file, validating as we go.

    It validates as follows:

    One test in the input file is a comment, followed by input data,
    followed by expected outputs (`d` or `u`), followed by a word
    indicating the size of the change (per `deltawords` template tag),
    followed by a blank line.

    Input data and expected outputs should be left aligned in columns
    of width 4.

    """
    for i in range(0, len(test_cases), 5):
        # Validate each row first
        alignment_header = "*".join(["..."] * 12)
        comment_msg = "At line %s: %s does not start with #" % (i, test_cases[i])
        assert test_cases[i].startswith("#"), comment_msg
        test_name = test_cases[i].strip()
        alignment_msg = (
            "%s: Every column must be three wide "
            "followed by a space: \n%s\n%s"
            % (test_name, alignment_header, test_cases[i + 1])
        )
        assert test_cases[i + 1][3::4].strip() == "", alignment_msg
        directions = [n for n, ltr in enumerate(test_cases[i + 2]) if ltr in ("u", "d")]
        alignment_msg = (
            "%s: Every column must be three wide followed "
            "by a space:\n%s\n%s"
            % (test_name, alignment_header, str(test_cases[i + 2]))
        )
        assert sum([x % 4 for x in directions]) == 0, alignment_msg
        data = [
            round(int(x) / 100.0, 2) if x.strip() else None
            for x in re.findall(r"(   |\d+ {0,2}) ?", test_cases[i + 1])
        ]
        expected = test_cases[i + 2].rstrip()
        deltawords = test_cases[i + 3].rstrip()
        yield {
            "name": test_name,
            "data": data,
            "expected": expected,
            "deltawords": deltawords,
        }


def extract_percentiles_for_alerts(result):
    neg = result["alert_percentile_neg"]
    pos = result["alert_percentile_pos"]
    combined = []
    assert len(neg) == len(pos)
    for i, val in enumerate(neg):
        if val is not None:
            assert not pos[i]
            combined.append("d")
        elif pos[i] is not None:
            combined.append("u")
        else:
            combined.append(" ")
    return "   ".join(combined).rstrip()


class TestCUSUM(unittest.TestCase):
    def test_various_data(self):
        """Test alert logic against data in the specified fixture.

        Note that the input data is actually divided by 100 before
        being input to the function under test. This slightly
        unfortunate design comes from wanting to exercise a couple of
        interesting edge cases without making the tests less readable
        by introducing extra floating points to the test input
        fixture.

        """
        with open(
            settings.APPS_ROOT + "/frontend/tests/fixtures/alert_test_cases.txt"
        ) as expected:
            test_cases = expected.readlines()
        for test in each_cusum_test(test_cases):
            cusum = bookmark_utils.CUSUM(test["data"], window_size=3, sensitivity=5)
            cusum.work()
            new_result_formatted = extract_percentiles_for_alerts(cusum.as_dict())
            error_message = "In test '%s':\n" % test["name"]
            error_message += "   Input values: %s\n" % test["data"]
            error_message += "Expected alerts: %s\n" % test["expected"]
            self.assertEqual(
                new_result_formatted,
                test["expected"],
                error_message + "            Got: %s" % new_result_formatted,
            )
            info = cusum.get_last_alert_info()
            if info:
                change = deltawords(info["to"] * 100.0, info["from"] * 100.0)
                self.assertEqual(test["deltawords"], change)
            else:
                self.assertEqual(test["deltawords"], "not at all")


class TestBookmarkUtilsPerforming(TestCase):
    fixtures = ["bookmark_alerts", "measurevalues_with_performance", "importlog"]

    def setUp(self):
        self.measure = Measure.objects.get(pk="cerazette")
        self.exotic_measure = Measure.objects.get(pk="exotic")
        pct = PCT.objects.get(pk="03V")
        practice_with_high_percentiles = Practice.objects.get(pk="P87629")
        practice_with_low_percentiles = Practice.objects.get(pk="P87630")
        self.pct = pct
        self.high_percentile_practice = practice_with_high_percentiles
        self.low_percentile_practice = practice_with_low_percentiles

    # Worst performing
    # CCG bookmarks
    def test_hit_where_ccg_worst_in_specified_number_of_months(self):
        finder = bookmark_utils.InterestingMeasureFinder(self.pct)
        worst_measures = finder.worst_performing_in_period(3)
        self.assertIn(self.measure, worst_measures)
        self.assertNotIn(self.exotic_measure, worst_measures)

    def test_miss_where_not_enough_global_data(self):
        finder = bookmark_utils.InterestingMeasureFinder(self.pct)
        worst_measures = finder.worst_performing_in_period(6)
        self.assertFalse(worst_measures)

    def test_miss_where_not_worst_in_specified_number_of_months(self):
        MeasureValue.objects.all().delete()
        finder = bookmark_utils.InterestingMeasureFinder(self.pct)
        worst_measures = finder.worst_performing_in_period(3)
        self.assertFalse(worst_measures)

    # Practice bookmarks
    def test_hit_where_practice_worst_in_specified_number_of_months(self):
        finder = bookmark_utils.InterestingMeasureFinder(self.high_percentile_practice)
        worst_measures = finder.worst_performing_in_period(3)
        self.assertIn(self.measure, worst_measures)

    # Best performing
    def test_hit_where_practice_best_in_specified_number_of_months(self):
        finder = bookmark_utils.InterestingMeasureFinder(self.low_percentile_practice)
        best_measures = finder.best_performing_in_period(3)
        self.assertIn(self.measure, best_measures)


class TestLastAlertFinding(SimpleTestCase):
    def test_no_alert_when_empty(self):
        cusum = bookmark_utils.CUSUM(["a", "b"])
        cusum.alert_indices = []
        self.assertIsNone(cusum.get_last_alert_info(), None)

    def test_no_alert_when_alert_not_most_recent(self):
        cusum = bookmark_utils.CUSUM(["a", "b"])
        cusum.alert_indices = [0]
        cusum.target_means = ["b"]
        self.assertIsNone(cusum.get_last_alert_info(), None)

    def test_alert_parsed_when_only_alert(self):
        cusum = bookmark_utils.CUSUM(["c", "c", "c"])
        cusum.target_means = ["b", "b", "b"]
        cusum.alert_indices = [2]
        self.assertDictEqual(
            cusum.get_last_alert_info(), {"from": "b", "to": "c", "period": 1}
        )

    def test_period_parsed(self):
        cusum = bookmark_utils.CUSUM(["c", "c", "c"])
        cusum.alert_indices = [1, 2]
        cusum.target_means = ["a", "a", "a"]
        self.assertDictEqual(
            cusum.get_last_alert_info(), {"from": "a", "to": "c", "period": 2}
        )

    def test_alert_parsed_when_more_than_one_alert(self):
        cusum = bookmark_utils.CUSUM(["1", "2", "3", "b"])
        cusum.alert_indices = [1, 3]
        cusum.target_means = ["a", "a", "a", "a"]
        self.assertDictEqual(
            cusum.get_last_alert_info(), {"from": "a", "to": "b", "period": 1}
        )


class TestBookmarkUtilsChanging(TestCase):
    fixtures = ["bookmark_alerts", "measures"]

    def setUp(self):
        self.measure_id = "cerazette"
        self.measure = Measure.objects.get(pk=self.measure_id)
        ImportLog.objects.create(category="prescribing", current_at=datetime.today())
        practice_with_high_change = Practice.objects.get(pk="P87629")
        practice_with_high_neg_change = Practice.objects.get(pk="P87631")
        practice_with_low_change = Practice.objects.get(pk="P87630")
        for i in range(3):
            month = datetime.today() + relativedelta(months=i)
            MeasureValue.objects.create(
                measure=self.measure,
                practice=practice_with_high_change,
                percentile=(i + 1) * 7,
                month=month,
            )
            MeasureValue.objects.create(
                measure=self.measure,
                practice=practice_with_high_neg_change,
                percentile=(3 - i) * 7,
                month=month,
            )
            MeasureValue.objects.create(
                measure=self.measure,
                practice=practice_with_low_change,
                percentile=i + 1,
                month=month,
            )
        self.practice_with_low_change = practice_with_low_change
        self.practice_with_high_change = practice_with_high_change
        self.practice_with_high_neg_change = practice_with_high_neg_change

    def test_high_change_returned(self):
        finder = bookmark_utils.InterestingMeasureFinder(
            self.practice_with_high_change, interesting_change_window=10
        )
        sorted_measure = finder.most_change_against_window(1)
        measure_info = sorted_measure["improvements"][0]
        self.assertEqual(measure_info["measure"].id, "cerazette")
        self.assertAlmostEqual(measure_info["from"], 7)  # start
        self.assertAlmostEqual(measure_info["to"], 21)  # end

    def test_high_change_declines_when_low_is_good(self):
        self.measure.low_is_good = True
        self.measure.save()
        finder = bookmark_utils.InterestingMeasureFinder(
            self.practice_with_high_change, interesting_change_window=10
        )
        sorted_measure = finder.most_change_against_window(1)
        measure_info = sorted_measure["declines"][0]
        self.assertEqual(measure_info["measure"].id, "cerazette")
        self.assertAlmostEqual(measure_info["from"], 7)  # start
        self.assertAlmostEqual(measure_info["to"], 21)  # end

    def test_high_negative_change_returned(self):
        finder = bookmark_utils.InterestingMeasureFinder(
            self.practice_with_high_neg_change, interesting_change_window=10
        )
        sorted_measure = finder.most_change_against_window(1)
        measure_info = sorted_measure["declines"][0]
        self.assertEqual(measure_info["measure"].id, "cerazette")
        self.assertAlmostEqual(measure_info["from"], 21)  # start
        self.assertAlmostEqual(measure_info["to"], 7)  # end


def _makeCostSavingMeasureValues(measure, practice, savings):
    """Create measurevalues for the given practice and measure with
    savings at the 50th centile taken from the specified `savings`
    array.  Savings at the 90th centile are set as 100 times those at
    the 50th, and at the 10th as 0.1 times.

    """
    for i in range(len(savings)):
        month = datetime.today() + relativedelta(months=i)
        MeasureValue.objects.create(
            measure=measure,
            practice=practice,
            percentile=0.5,
            cost_savings={
                "10": savings[i] * 0.1,
                "50": savings[i],
                "90": savings[i] * 100,
            },
            month=month,
        )


class TestBookmarkUtilsSavingsBase(TestCase):
    fixtures = ["bookmark_alerts", "measures"]

    def setUp(self):
        self.measure_id = "cerazette"
        self.measure = Measure.objects.get(pk=self.measure_id)
        ImportLog.objects.create(category="prescribing", current_at=datetime.today())
        self.practice = Practice.objects.get(pk="P87629")


class TestBookmarkUtilsSavingsPossible(TestBookmarkUtilsSavingsBase):
    def setUp(self):
        super(TestBookmarkUtilsSavingsPossible, self).setUp()
        _makeCostSavingMeasureValues(self.measure, self.practice, [0, 1500, 2000])

    def test_possible_savings_for_practice(self):
        finder = bookmark_utils.InterestingMeasureFinder(self.practice)
        savings = finder.top_and_total_savings_in_period(3)
        self.assertEqual(savings["possible_savings"], [(self.measure, 3500)])
        self.assertEqual(savings["achieved_savings"], [])
        self.assertEqual(savings["possible_top_savings_total"], 350000)

    def test_possible_savings_for_practice_not_enough_months(self):
        finder = bookmark_utils.InterestingMeasureFinder(self.practice)
        savings = finder.top_and_total_savings_in_period(10)
        self.assertEqual(savings["possible_savings"], [])
        self.assertEqual(savings["achieved_savings"], [])
        self.assertEqual(savings["possible_top_savings_total"], 0)

    def test_possible_savings_for_ccg(self):
        finder = bookmark_utils.InterestingMeasureFinder(self.practice.ccg)
        savings = finder.top_and_total_savings_in_period(3)
        self.assertEqual(savings["possible_savings"], [])
        self.assertEqual(savings["achieved_savings"], [])
        self.assertEqual(savings["possible_top_savings_total"], 0)

    def test_possible_savings_low_is_good(self):
        self.measure.low_is_good = True
        self.measure.save()
        finder = bookmark_utils.InterestingMeasureFinder(self.practice)
        savings = finder.top_and_total_savings_in_period(3)
        self.assertEqual(savings["possible_savings"], [(self.measure, 3500)])
        self.assertEqual(savings["achieved_savings"], [])
        self.assertEqual(savings["possible_top_savings_total"], 350.0)


class TestBookmarkUtilsSavingsAchieved(TestBookmarkUtilsSavingsBase):
    def setUp(self):
        super(TestBookmarkUtilsSavingsAchieved, self).setUp()
        _makeCostSavingMeasureValues(self.measure, self.practice, [-1000, -500, 100])

    def test_achieved_savings(self):
        finder = bookmark_utils.InterestingMeasureFinder(self.practice)
        savings = finder.top_and_total_savings_in_period(3)
        self.assertEqual(savings["possible_savings"], [])
        self.assertEqual(savings["achieved_savings"], [(self.measure, 1400)])
        self.assertEqual(savings["possible_top_savings_total"], 10000)


class MockServerRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == "/page.html":
            self.send_response(requests.codes.ok)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            response_content = b"""
            <html>
             <head>
              <script src='/jquery.min.js'></script>
              <style>
               div {width: 100%; height: 100%}
               #thing1 {background-color:red}
               #thing1 {background-color:green}
              </style>
             </head>
             <div id='thing1'></div>
             <div id='thing2'></div>
            </html>
            """
            self.wfile.write(response_content)
            return
        elif self.path == "/jquery.min.js":
            self.send_response(requests.codes.ok)
            self.send_header("Content-Type", "text/javascript")
            self.end_headers()
            with open(
                settings.APPS_ROOT + "/media/js/node_modules/jquery/dist/jquery.min.js",
                "rb",
            ) as f:
                self.wfile.write(f.read())
                return


def get_free_port():
    s = socket.socket(socket.AF_INET, type=socket.SOCK_STREAM)
    s.bind(("localhost", 0))
    address, port = s.getsockname()
    s.close()
    return port


def start_mock_server(port):
    mock_server = HTTPServer(("localhost", port), MockServerRequestHandler)
    mock_server_thread = Thread(target=mock_server.serve_forever)
    mock_server_thread.setDaemon(True)
    mock_server_thread.start()


class GenerateImageTestCase(unittest.TestCase):
    def setUp(self):
        port = get_free_port()
        start_mock_server(port)
        self.msg = EmailMultiAlternatives(
            "Subject", "body", "sender@email.com", ["recipient@email.com"]
        )
        self.url = ":%s/page.html" % port
        self.file_path = "/tmp/image.png"
        self.selector = "#thing2"

    def tearDown(self):
        try:
            os.remove(self.file_path)
        except OSError as e:
            import errno

            # We don't care about a "No such file or directory" error
            if e.errno != errno.ENOENT:
                raise

    @patch("subprocess.run")
    def test_empty_image_raises(self, run):
        run.return_value.returncode = 0
        with open(self.file_path, "a"):
            # create an empty file
            os.utime(self.file_path, None)
        with self.assertRaises(bookmark_utils.BadAlertImageError):
            bookmark_utils.attach_image(
                self.msg, self.url, self.file_path, self.selector
            )

    def test_image_generated(self):
        # Note that the expected image may differ depending on the
        # server where this is run: phantomjs interacts with the
        # display settings on a server which may vary from your own
        self.assertEqual(len(self.msg.attachments), 0)
        image = bookmark_utils.attach_image(
            self.msg, self.url, self.file_path, self.selector
        )
        with open(
            settings.APPS_ROOT + "/frontend/tests/fixtures/alert-email-image.png",
            "rb",
        ) as expected:
            self.assertEqual(len(self.msg.attachments), 1)
            attachment = self.msg.attachments[0]
            # Check the attachment is as we expect
            self.assertEqual(attachment.get_filename(), "image.png")
            self.assertIn(image, attachment["Content-ID"])
            # Attachments in emails are base64 *with line breaks*, so
            # we remove those.
            self.assertEqual(
                attachment.get_payload().replace("\n", "").encode("utf8"),
                base64.b64encode(expected.read()),
            )

    def test_small_image_generated_with_viewport_dimensions_specified(self):
        # Note that the expected image may differ depending on the
        # server where this is run: phantomjs interacts with the
        # display settings on a server which may vary from your own
        bookmark_utils.attach_image(
            self.msg, self.url, self.file_path, self.selector, "100x100"
        )
        with open(
            settings.APPS_ROOT + "/frontend/tests/fixtures/alert-email-image-small.png",
            "rb",
        ) as expected:
            attachment = self.msg.attachments[0]
            self.assertEqual(
                attachment.get_payload().replace("\n", "").encode("utf8"),
                base64.b64encode(expected.read()),
            )


class UnescapeTestCase(unittest.TestCase):
    def test_no_url(self):
        example = "Foo bar"
        self.assertEqual(bookmark_utils.unescape_href(example), example)

    def test_unescaped_url(self):
        example = "Foo bar href='http://foo.com/frob?b=3#bar'"
        self.assertEqual(bookmark_utils.unescape_href(example), example)

    def test_escaped_url(self):
        example = "href='http://localhost/analyse/?u=7&amp;m=9#bong'"
        expected = "href='http://localhost/analyse/?u=7&m=9#bong'"
        self.assertEqual(bookmark_utils.unescape_href(example), expected)

    def test_mixture(self):
        example = (
            'Foo href="http://localhost/analyse/?u=7&amp;m=9" '
            'href="http://foo.com/frob?b=3#bar" '
            "Baz"
        )
        expected = (
            'Foo href="http://localhost/analyse/?u=7&m=9" '
            'href="http://foo.com/frob?b=3#bar" '
            "Baz"
        )
        self.assertEqual(bookmark_utils.unescape_href(example), expected)


class TestContextForOrgEmail(unittest.TestCase):
    def setUp(self):
        ImportLog.objects.create(category="prescribing", current_at=datetime.today())

    @patch(
        "frontend.views.bookmark_utils.InterestingMeasureFinder."
        "worst_performing_in_period"
    )
    @patch(
        "frontend.views.bookmark_utils.InterestingMeasureFinder."
        "best_performing_in_period"
    )
    @patch(
        "frontend.views.bookmark_utils.InterestingMeasureFinder."
        "most_change_against_window"
    )
    def test_non_ordinal_sorting(
        self,
        most_change_against_window,
        best_performing_in_period,
        worst_performing_in_period,
    ):
        ordinal_measure_1 = MagicMock(low_is_good=True)
        non_ordinal_measure_1 = MagicMock(low_is_good=None)
        non_ordinal_measure_2 = MagicMock(low_is_good=None)
        most_change_against_window.return_value = {
            "improvements": [
                {"measure": ordinal_measure_1},
                {"measure": non_ordinal_measure_2},
            ],
            "declines": [{"measure": non_ordinal_measure_1}],
        }
        best_performing_in_period.return_value = [
            ordinal_measure_1,
            non_ordinal_measure_2,
        ]
        worst_performing_in_period.return_value = [
            ordinal_measure_1,
            non_ordinal_measure_1,
        ]
        finder = bookmark_utils.InterestingMeasureFinder(PCT.objects.create(code="000"))
        context = finder.context_for_org_email()
        self.assertCountEqual(
            context["most_changing_interesting"],
            [{"measure": non_ordinal_measure_1}, {"measure": non_ordinal_measure_2}],
        )
        self.assertCountEqual(
            context["interesting"], [non_ordinal_measure_1, non_ordinal_measure_2]
        )
        self.assertEqual(context["best"], [ordinal_measure_1])
        self.assertEqual(context["worst"], [ordinal_measure_1])
        self.assertEqual(
            context["most_changing"]["improvements"], [{"measure": ordinal_measure_1}]
        )


class TruncateSubjectTestCase(unittest.TestCase):
    def test_truncate_subject(self):
        data = [
            {
                "input": "short title by me",
                "expected": "Your monthly update about Short Title by Me",
            },
            {
                "input": "THING IN CAPS",
                "expected": "Your monthly update about Thing in Caps",
            },
            {
                "input": (
                    "Items for Abacavir + Levocabastine + Levacetylmethadol "
                    "Hydrochloride + 5-Hydroxytryptophan vs Frovatriptan + "
                    "Alverine Citrate + Boceprevir by All Sub-ICB Locations"
                ),
                "expected": (
                    "Your monthly update about Items for Abacavir + Levo..."
                    "by All Sub-ICB Locations"
                ),
            },
            {
                "input": (
                    "The point is that the relative freedom which we enjoy"
                    "depends of public opinion. The law is no protection."
                ),
                "expected": (
                    "Your monthly update about The Point Is That the Relative "
                    "Freedom Which WE E..."
                ),
            },
            {
                "input": (
                    "Items for Zopiclone + Zolpidem Tartrate + Lorazepam + "
                    "Chlordiazepoxide Hydrochloride + Diazepam + Clonazepam + "
                    "Temazepam vs patients on list by HEATHCOT MEDICAL PRACTICE "
                    "and other practices in Sub-ICB Location"
                ),
                "expected": (
                    "Your monthly update about Items for Zopiclone + Zolpidem "
                    "Tartrate + Lorazep..."
                ),
            },
            {
                "input": (
                    "Items for Apixaban + Edoxaban + Dabigatran Etexilate + "
                    "Rivaroxaban vs Warfarin Sodium + Apixaban + Edoxaban + "
                    "Dabigatran Etexilate + Rivaroxâ\x80¦ by practices in "
                    "NHS SUNDERLAND CCG123456789"
                ),
                "expected": (
                    "Your monthly update about Items ...by Practices in NHS "
                    "SUNDERLAND CCG123456789"
                ),
            },
        ]

        for test_case in data:
            self.assertEqual(
                bookmark_utils.truncate_subject(
                    "Your monthly update about ", test_case["input"]
                ),
                test_case["expected"],
            )


def _makeContext(**kwargs):
    empty_context = {
        "most_changing": {"declines": [], "improvements": []},
        "top_savings": {
            "possible_top_savings_total": 0.0,
            "possible_savings": [],
            "achieved_savings": [],
        },
        "worst": [],
        "best": [],
        "most_changing_interesting": [],
        "interesting": [],
    }
    if "declines" in kwargs:
        empty_context["most_changing"]["declines"] = kwargs["declines"]
    if "improvements" in kwargs:
        empty_context["most_changing"]["improvements"] = kwargs["improvements"]
    if "possible_top_savings_total" in kwargs:
        empty_context["top_savings"]["possible_top_savings_total"] = kwargs[
            "possible_top_savings_total"
        ]
    if "possible_savings" in kwargs:
        empty_context["top_savings"]["possible_savings"] = kwargs["possible_savings"]
    if "achieved_savings" in kwargs:
        empty_context["top_savings"]["achieved_savings"] = kwargs["achieved_savings"]
    if "worst" in kwargs:
        empty_context["worst"] = kwargs["worst"]
    if "best" in kwargs:
        empty_context["best"] = kwargs["best"]
    if "interesting" in kwargs:
        empty_context["interesting"] = kwargs["interesting"]
    if "most_changing_interesting" in kwargs:
        empty_context["most_changing_interesting"] = kwargs["most_changing_interesting"]
    return empty_context


@patch("frontend.views.bookmark_utils.attach_image")
@copy_fixtures_to_matrixstore
class TestNCSOConcessions(TestCase):
    @classmethod
    def setUpTestData(cls):
        factory = DataFactory()
        cls.months = factory.create_months_array(start_date="2018-02-01", num_months=6)
        # Our NCSO and tariff data extends further than our prescribing data by
        # a couple of months
        cls.prescribing_months = cls.months[:-2]
        # Create some CCGs (we need more than one so we can test aggregation
        # across CCGs at the All England level)
        cls.ccgs = [factory.create_ccg() for _ in range(2)]
        # Populate those CCGs with practices
        cls.practices = []
        for ccg in cls.ccgs:
            for _ in range(2):
                cls.practices.append(factory.create_practice(ccg=ccg))
        # Create some presentations
        cls.presentations = factory.create_presentations(6)
        # Create drug tariff and price concessions costs for these presentations
        factory.create_tariff_and_ncso_costings_for_presentations(
            cls.presentations, months=cls.months
        )
        # Create prescribing for each of the practices we've created
        for practice in cls.practices:
            factory.create_prescribing_for_practice(
                practice, presentations=cls.presentations, months=cls.prescribing_months
            )
        # Pull out an individual practice and CCG to use in our tests
        cls.practice = cls.practices[0]
        cls.ccg = cls.ccgs[0]
        # Create user to own a bookmark
        cls.user = factory.create_user()

    def test_make_ncso_concessions_email_for_practice(self, attach_image):
        bookmark = NCSOConcessionBookmark.objects.create(
            user=self.user, practice=self.practice
        )

        msg = bookmark_utils.make_ncso_concession_email(bookmark)

        self.assertEqual(
            msg.subject, "Your update about Price Concessions for Practice 2"
        )
        self.assertIn("published for **July 2018**", msg.body)
        self.assertIn("at Practice 2", msg.body)
        additional_cost = round(
            ncso_spending_for_entity(self.practice, "practice", 1)[0]["additional_cost"]
        )
        self.assertIn("**\xa3{:,}**".format(additional_cost), msg.body)

        html = msg.alternatives[0][0]
        self.assertInHTML("<b>July 2018</b>", html)

    def test_make_ncso_concessions_email_for_ccg(self, attach_image):
        bookmark = NCSOConcessionBookmark.objects.create(user=self.user, pct=self.ccg)

        msg = bookmark_utils.make_ncso_concession_email(bookmark)

        self.assertEqual(msg.subject, "Your update about Price Concessions for CCG 0")
        self.assertIn("published for **July 2018**", msg.body)
        additional_cost = round(
            ncso_spending_for_entity(self.ccg, "ccg", 1)[0]["additional_cost"]
        )
        self.assertIn("**\xa3{:,}**".format(additional_cost), msg.body)

        html = msg.alternatives[0][0]
        self.assertInHTML("<b>July 2018</b>", html)

    def test_make_ncso_concessions_email_for_all_england(self, attach_image):
        bookmark = NCSOConcessionBookmark.objects.create(user=self.user)

        msg = bookmark_utils.make_ncso_concession_email(bookmark)

        self.assertEqual(
            msg.subject, "Your update about Price Concessions for the NHS in England"
        )
        self.assertIn("published for **July 2018**", msg.body)
        additional_cost = round(
            ncso_spending_for_entity(None, "all_england", 1)[0]["additional_cost"]
        )
        self.assertIn("**\xa3{:,}**".format(additional_cost), msg.body)

        html = msg.alternatives[0][0]
        self.assertInHTML("<b>July 2018</b>", html)
