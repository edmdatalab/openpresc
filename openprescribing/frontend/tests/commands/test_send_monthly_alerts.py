# -*- coding: utf-8 -*-
import re
import unittest

from common.alert_utils import BatchedEmailErrors
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from frontend.management.commands.send_monthly_alerts import Command
from frontend.models import PCN, STP, EmailMessage, Measure
from frontend.tests.data_factory import DataFactory
from frontend.tests.test_api_spending import ApiTestBase
from frontend.tests.test_bookmark_utils import _makeContext
from frontend.views.bookmark_utils import BadAlertImageError
from mock import MagicMock, patch

CMD_NAME = "send_monthly_alerts"


class ValidateOptionsTestCase(unittest.TestCase):
    def _defaultOpts(self, **extra):
        default = {
            "url": None,
            "ccg": None,
            "practice": None,
            "recipient_email": None,
        }
        for k, v in extra.items():
            default[k] = v
        return default

    def test_options_depended_on_by_recipient_email(self):
        opts = self._defaultOpts(url="thing")
        with self.assertRaises(CommandError):
            Command().validate_options(**opts)
        opts = self._defaultOpts(ccg="thing")
        with self.assertRaises(CommandError):
            Command().validate_options(**opts)
        opts = self._defaultOpts(practice="thing")
        with self.assertRaises(CommandError):
            Command().validate_options(**opts)

        opts = self._defaultOpts(practice="thing", recipient_email="thing")
        Command().validate_options(**opts)

    def test_incompatibile_options(self):
        opts = self._defaultOpts(url="thing", ccg="thing")
        with self.assertRaises(CommandError):
            Command().validate_options(**opts)
        opts = self._defaultOpts(url="thing", practice="thing")
        with self.assertRaises(CommandError):
            Command().validate_options(**opts)


class GetBookmarksTestCase(TestCase):
    fixtures = ["bookmark_alerts"]

    def setUp(self):
        self.now_month = "2014-11-01"

    def test_get_org_bookmarks_without_options(self):
        bookmarks = Command().get_org_bookmarks(
            self.now_month,
            recipient_email=None,
            recipient_email_file=None,
            skip_email_file=None,
        )
        active = all([x.user.is_active for x in bookmarks])
        self.assertEqual(len(bookmarks), 2)
        self.assertTrue(active)

    def test_get_org_bookmarks_with_test_options(self):
        bookmarks = Command().get_org_bookmarks(
            self.now_month,
            recipient_email="s@s.com",
            ccg="03V",
            practice="P87629",
            pcn=None,
            stp=None,
            recipient_email_file=None,
            skip_email_file=None,
        )
        self.assertEqual(len(bookmarks), 1)
        self.assertEqual(bookmarks[0].user.email, "s@s.com")
        self.assertTrue(bookmarks[0].user.profile.key)
        self.assertTrue(bookmarks[0].user.id)
        self.assertEqual(bookmarks[0].pct.code, "03V")
        self.assertEqual(bookmarks[0].practice.code, "P87629")

    def test_get_org_bookmarks_with_skip_file(self):
        skip_file = "frontend/tests/fixtures/commands/" "skip_alerts_recipients.txt"
        bookmarks = Command().get_org_bookmarks(
            self.now_month,
            skip_email_file=skip_file,
            recipient_email=None,
            recipient_email_file=None,
        )
        self.assertEqual(len(bookmarks), 0)

    def test_get_search_bookmarks_without_options(self):
        bookmarks = Command().get_search_bookmarks(self.now_month, recipient_email=None)
        active = all([x.user.is_active for x in bookmarks])
        self.assertEqual(len(bookmarks), 1)
        self.assertEqual(bookmarks[0].url, "foo")
        self.assertTrue(active)

    def test_get_search_bookmarks_with_options(self):
        bookmarks = Command().get_search_bookmarks(
            self.now_month, recipient_email="s@s.com", url="frob", search_name="baz"
        )
        self.assertEqual(len(bookmarks), 1)
        self.assertEqual(bookmarks[0].user.email, "s@s.com")
        self.assertTrue(bookmarks[0].user.profile.key)
        self.assertTrue(bookmarks[0].user.id)
        self.assertEqual(bookmarks[0].url, "frob")


@patch("frontend.views.bookmark_utils.InterestingMeasureFinder")
@patch("frontend.views.bookmark_utils.attach_image")
class FailingEmailTestCase(TestCase):
    """Exercise the error batching mechanism that allowed a maximum number
    of errors before failing the batch.

    """

    fixtures = ["bookmark_alerts", "measures", "importlog"]

    def test_successful_sends(self, attach_image, finder):
        attach_image.side_effect = [Exception, None, None]
        measure = MagicMock()
        measure.id = "measureid"
        test_context = _makeContext(worst=[measure])
        self.assertEqual(EmailMessage.objects.count(), 1)
        with self.assertRaises(BatchedEmailErrors):
            call_mocked_command(test_context, finder, max_errors="4")
        self.assertEqual(EmailMessage.objects.count(), 3)
        self.assertEqual(len(mail.outbox), 2)

    def test_bad_alert_image_error_not_sent_and_exits_with_error(
        self, attach_image, finder
    ):
        attach_image.side_effect = BadAlertImageError
        measure = MagicMock()
        measure.id = "measureid"
        test_context = _makeContext(worst=[measure])
        with self.assertRaises(SystemExit) as exc:
            call_mocked_command(test_context, finder, max_errors="0")
        self.assertNotEqual(exc.exception.code, 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_max_errors(self, attach_image, finder):
        attach_image.side_effect = [Exception, None, None]
        measure = MagicMock()
        measure.id = "measureid"
        test_context = _makeContext(worst=[measure])
        self.assertEqual(EmailMessage.objects.count(), 1)
        with self.assertRaises(BatchedEmailErrors):
            call_mocked_command(test_context, finder, max_errors="0")
        self.assertEqual(EmailMessage.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 0)


@patch("frontend.views.bookmark_utils.InterestingMeasureFinder")
@patch("frontend.views.bookmark_utils.attach_image")
class OrgEmailTestCase(TestCase):
    fixtures = ["bookmark_alerts", "measures", "importlog"]

    def test_email_recipient(self, attach_image, finder):
        test_context = _makeContext()
        self.assertEqual(EmailMessage.objects.count(), 1)  # a text fixture
        call_mocked_command_with_defaults(test_context, finder)
        self.assertEqual(EmailMessage.objects.count(), 2)
        self.assertEqual(len(mail.outbox), 1)
        email_message = EmailMessage.objects.last()
        self.assertEqual(mail.outbox[-1].to, email_message.to)
        self.assertEqual(mail.outbox[-1].to, ["s@s.com"])

    def test_email_all_recipients(self, attach_image, finder):
        test_context = _makeContext()
        self.assertEqual(EmailMessage.objects.count(), 1)
        call_mocked_command(test_context, finder)
        self.assertEqual(EmailMessage.objects.count(), 4)
        self.assertEqual(len(mail.outbox), 3)

    def test_email_body_no_data(self, attach_image, finder):
        test_context = _makeContext()
        call_mocked_command_with_defaults(test_context, finder)
        message = mail.outbox[-1].alternatives[0]
        html = message[0]
        # Name of the practice
        self.assertIn("1/ST Andrews Medical Practice", html)
        # Unsubscribe link
        self.assertIn("/bookmarks/dummykey/", html)
        self.assertIn("We've no new information", html)

    def test_email_headers(self, attach_image, finder):
        test_context = _makeContext()
        call_mocked_command_with_defaults(test_context, finder)
        message = mail.outbox[-1]
        self.assertIn(
            message.extra_headers["list-unsubscribe"],
            "<http://localhost/bookmarks/dummykey/>",
        )

    def test_email_body_text(self, attach_image, finder):
        test_context = _makeContext()
        call_mocked_command_with_defaults(test_context, finder)
        message = mail.outbox[-1].body
        self.assertIn("**Hello!**", message)

    def test_email_body_has_ga_tracking(self, attach_image, finder):
        measure = Measure.objects.get(pk="cerazette")
        call_mocked_command_with_defaults(
            _makeContext(declines=[{"measure": measure, "from": 99.92, "to": 0.12}]),
            finder,
        )
        message = mail.outbox[-1].alternatives[0]
        html = message[0]
        self.assertRegex(html, '<a href=".*&utm_content=.*#cerazette".*>')

    def test_email_body_declines(self, attach_image, finder):
        attach_image.return_value = "unique-image-id"
        measure = Measure.objects.get(pk="cerazette")
        call_mocked_command_with_defaults(
            _makeContext(declines=[{"measure": measure, "from": 99.92, "to": 0.12}]),
            finder,
        )
        message = mail.outbox[-1].alternatives[0]
        html = message[0]
        self.assertIn("this practice slipped", html)
        self.assertRegex(
            html,
            "slipped massively on "
            '<a href=".*/practice/P87629/.*#cerazette".*>'
            "Cerazette vs. Desogestrel</a>",
        )
        self.assertIn('<span class="worse"', html)
        self.assertIn('<img src="cid:unique-image-id', html)
        self.assertNotIn("Your best prescribing areas", html)
        self.assertNotIn("Cost savings", html)

    def test_email_body_two_declines(self, attach_image, finder):
        measure = Measure.objects.get(pk="cerazette")
        call_mocked_command_with_defaults(
            _makeContext(
                declines=[
                    {"measure": measure, "from": 99.92, "to": 0.12},
                    {"measure": measure, "from": 30, "to": 10},
                ]
            ),
            finder,
        )
        message = mail.outbox[-1].alternatives[0]
        html = message[0]
        self.assertRegex(html, "It also slipped considerably")

    def test_email_body_three_declines(self, attach_image, finder):
        measure = Measure.objects.get(pk="cerazette")
        call_mocked_command_with_defaults(
            _makeContext(
                declines=[
                    {"measure": measure, "from": 99.92, "to": 0.12},
                    {"measure": measure, "from": 30, "to": 10},
                    {"measure": measure, "from": 20, "to": 10},
                ]
            ),
            finder,
        )
        message = mail.outbox[-1].alternatives[0]
        html = message[0]
        self.assertRegex(html, "It also slipped:")
        self.assertRegex(
            html,
            re.compile(
                "<ul.*<li>considerably on.*" "<li>moderately on.*</ul>", re.DOTALL
            ),
        )

    def test_email_body_worst(self, attach_image, finder):
        measure = Measure.objects.get(pk="cerazette")
        attach_image.return_value = "unique-image-id"
        call_mocked_command_with_defaults(_makeContext(worst=[measure]), finder)
        message = mail.outbox[-1].alternatives[0]
        html = message[0]
        self.assertIn("We've found", html)
        self.assertRegex(
            html,
            re.compile(
                'the worst 10% on.*<a href=".*/practice/P87629'
                '/.*#cerazette".*>'
                "Cerazette vs. Desogestrel</a>",
                re.DOTALL,
            ),
        )
        self.assertIn('<img src="cid:unique-image-id', html)

    def test_email_body_three_worst(self, attach_image, finder):
        measure = Measure.objects.get(pk="cerazette")
        call_mocked_command_with_defaults(
            _makeContext(worst=[measure, measure, measure]), finder
        )
        message = mail.outbox[-1].alternatives[0]
        html = message[0]
        self.assertRegex(html, "It was also in the worst 10% on:")
        self.assertRegex(
            html,
            re.compile(
                "<ul.*<li>.*Desogestrel.*" "<li>.*Desogestrel.*</ul>", re.DOTALL
            ),
        )

    def test_email_body_two_savings(self, attach_image, finder):
        measure = Measure.objects.get(pk="cerazette")
        call_mocked_command_with_defaults(
            _makeContext(possible_savings=[(measure, 9.9), (measure, 1.12)]), finder
        )
        message = mail.outbox[-1].alternatives[0]
        html = message[0]
        self.assertIn("These add up to around <b>£10</b> of " "potential savings", html)
        self.assertRegex(
            html,
            '<li.*>\n<b>£10</b> on <a href=".*/practice/P87629'
            '/.*#cerazette".*>'
            "Cerazette vs. Desogestrel</a>",
        )

    def test_email_body_one_saving(self, attach_image, finder):
        measure = Measure.objects.get(pk="cerazette")
        call_mocked_command_with_defaults(
            _makeContext(possible_savings=[(measure, 9.9)]), finder
        )
        message = mail.outbox[-1].alternatives[0]
        html = message[0]
        self.assertIn("if it had prescribed in line with the average practice", html)
        self.assertRegex(
            html,
            "it could have saved about <b>£10</b> on "
            '<a href=".*/practice/P87629/.*#cerazette".*>'
            "Cerazette vs. Desogestrel</a>",
        )

    def test_email_body_achieved_saving(self, attach_image, finder):
        measure = Measure.objects.get(pk="cerazette")
        call_mocked_command_with_defaults(
            _makeContext(achieved_savings=[(measure, 9.9)]), finder
        )
        message = mail.outbox[-1].alternatives[0]
        html = message[0]
        self.assertIn("this practice saved around <b>£10", html)

    def test_email_body_two_achieved_savings(self, attach_image, finder):
        measure = Measure.objects.get(pk="cerazette")
        call_mocked_command_with_defaults(
            _makeContext(achieved_savings=[(measure, 9.9), (measure, 12.0)]), finder
        )
        message = mail.outbox[-1].alternatives[0]
        html = message[0]
        self.assertIn("<li>\n<b>£10</b> on", html)
        self.assertIn("<li>\n<b>£10</b> on", html)

    def test_email_body_total_savings(self, attach_image, finder):
        call_mocked_command_with_defaults(
            _makeContext(possible_top_savings_total=9000.1), finder
        )
        message = mail.outbox[-1].alternatives[0]
        html = message[0]
        self.assertIn("it could save around <b>£9,000</b>", html)


@patch("frontend.views.bookmark_utils.attach_image")
class SearchEmailTestCase(TestCase):
    fixtures = ["bookmark_alerts", "measures", "importlog"]

    def test_all_recipients(self, attach_image):
        self.assertEqual(EmailMessage.objects.count(), 1)
        call_command(CMD_NAME)
        mail_queue = mail.outbox
        self.assertEqual(EmailMessage.objects.count(), 4)
        self.assertEqual(len(mail_queue), 3)

    def test_all_recipients_idempotent(self, attach_image):
        self.assertEqual(EmailMessage.objects.count(), 1)
        call_command(CMD_NAME)
        call_command(CMD_NAME)
        mail_queue = mail.outbox
        self.assertEqual(EmailMessage.objects.count(), 4)
        self.assertEqual(len(mail_queue), 3)

    def test_email_recipient(self, attach_image):
        opts = {
            "recipient_email": "s@s.com",
            "url": "something",
            "search_name": "some name",
        }
        self.assertEqual(EmailMessage.objects.count(), 1)  # a fixture
        call_command(CMD_NAME, **opts)
        self.assertEqual(EmailMessage.objects.count(), 2)
        email_message = EmailMessage.objects.last()
        self.assertEqual(email_message.send_count, 1)
        mail_queue = mail.outbox[-1]
        self.assertEqual(mail_queue.to, email_message.to)
        self.assertEqual(mail_queue.to, [opts["recipient_email"]])
        self.assertEqual(
            mail_queue.extra_headers["message-id"], email_message.message_id
        )

    def test_email_headers(self, attach_image):
        opts = {
            "recipient_email": "s@s.com",
            "url": "something",
            "search_name": "some name",
        }
        call_command(CMD_NAME, **opts)
        email_message = EmailMessage.objects.last()
        mail_queue = mail.outbox[-1]
        self.assertEqual(
            mail_queue.extra_headers["message-id"], email_message.message_id
        )
        self.assertEqual(
            mail_queue.extra_headers["list-unsubscribe"],
            "<http://localhost/bookmarks/dummykey/>",
        )

    def test_email_body(self, attach_image):
        opts = {
            "recipient_email": "s@s.com",
            "url": "something",
            "search_name": "some name",
        }
        call_command(CMD_NAME, **opts)
        message = mail.outbox[-1].alternatives[0]
        html = message[0]
        mime_type = message[1]
        self.assertIn(opts["search_name"], html)
        self.assertEqual(mime_type, "text/html")

        self.assertIn("/bookmarks/dummykey/", html)
        self.assertRegex(html, '<a href="http://localhost/analyse/.*#%s' % "something")

    def test_email_body_text(self, attach_image):
        opts = {
            "recipient_email": "s@s.com",
            "url": "something",
            "search_name": "some name",
        }
        call_command(CMD_NAME, **opts)
        text = mail.outbox[-1].body
        self.assertIn("**Hello!**", text)
        self.assertIn("/bookmarks/dummykey/", text)
        self.assertRegex(text, "http://localhost/analyse/.*#%s" % "something")


class AllEnglandAlertTestCase(ApiTestBase):
    fixtures = ApiTestBase.fixtures + ["functional-measures-dont-edit"]

    def test_all_england_alerts_sent(self):
        factory = DataFactory()

        # Create an All England bookmark, send alerts, and make sure one email
        # is sent to correct user
        bookmark = factory.create_org_bookmark(None)
        call_command(CMD_NAME)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [bookmark.user.email])


class PCNAlertTestCase(ApiTestBase):
    fixtures = ApiTestBase.fixtures + ["functional-measures-dont-edit"]

    def test_pcn_alerts_sent(self):
        """Create a PCN bookmark, send alerts, and make sure an email is sent
        to correct user, and that its contents mention PCNs

        """
        factory = DataFactory()
        pcn = PCN.objects.get(pk="E00000011")
        bookmark = factory.create_org_bookmark(pcn)
        call_command(CMD_NAME)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [bookmark.user.email])
        self.assertIn("PCN", mail.outbox[0].body)


class STPAlertTestCase(ApiTestBase):
    fixtures = ApiTestBase.fixtures + ["functional-measures-dont-edit"]

    def test_stp_alerts_sent(self):
        """Create an STP bookmark, send alerts, and make sure an email is sent to
        correct user, and that its contents mention "ICBs" (which is what STPs have now
        become)
        """
        factory = DataFactory()
        stp = STP.objects.get(pk="E01")
        bookmark = factory.create_org_bookmark(stp)
        call_command(CMD_NAME)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [bookmark.user.email])
        self.assertIn("ICB", mail.outbox[0].body)


def call_mocked_command(context, mock_finder, **opts):
    mock_finder.return_value.context_for_org_email.return_value = context
    call_command(CMD_NAME, **opts)


def call_mocked_command_with_defaults(context, mock_finder, **opts):
    default_opts = {"recipient_email": "s@s.com", "ccg": "03V", "practice": "P87629"}
    for k, v in opts.items():
        default_opts[k] = v
    call_mocked_command(context, mock_finder, **default_opts)
