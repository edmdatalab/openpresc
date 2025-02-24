# -*- coding: utf-8 -*-
import logging
import os
import re
import subprocess
import urllib.parse
from datetime import date
from html import unescape
from tempfile import NamedTemporaryFile

import numpy as np
import pandas as pd
from anymail.message import attach_inline_image_file
from common.utils import email_as_text, nhs_titlecase
from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.contrib.humanize.templatetags.humanize import apnumber
from django.core.mail import EmailMultiAlternatives
from django.template.loader import get_template
from django.urls import reverse
from django.utils.safestring import mark_safe
from frontend.models import (
    PCN,
    PCT,
    STP,
    ImportLog,
    Measure,
    MeasureValue,
    NCSOConcessionBookmark,
    Practice,
)
from frontend.views.spending_utils import (
    ncso_spending_breakdown_for_entity,
    ncso_spending_for_entity,
)
from frontend.views.views import (
    all_england_low_priority_savings,
    all_england_low_priority_total,
    all_england_measure_savings,
    cached,
    first_or_none,
    get_total_savings_for_org,
)
from premailer import Premailer

GRAB_CMD = (
    "/usr/local/bin/phantomjs --ignore-ssl-errors=true "
    + settings.APPS_ROOT
    + "/frontend/management/commands/grab_chart.js"
)

logger = logging.getLogger(__name__)


class BadAlertImageError(Exception):
    pass


class CUSUM(object):
    """See Introduction to Statistical Quality Control, Montgomery DC, Wiley, 2009
    and our paper
    http://dl4a.org/uploads/pdf/581SPC.pdf
    """

    def __init__(self, data, window_size=12, sensitivity=5, name=""):
        data = np.array([np.nan if x is None else x for x in data])
        # Remove sufficient leading nulls to ensure we can start with
        # any value
        self.start_index = 0
        while pd.isnull(data[self.start_index : self.start_index + window_size]).all():
            if self.start_index > len(data):
                data = []
                break
            self.start_index += 1
        self.data = data
        self.window_size = window_size
        self.sensitivity = sensitivity
        self.pos_cusums = []
        self.neg_cusums = []
        self.target_means = []
        self.alert_thresholds = []
        self.alert_indices = []
        self.pos_alerts = []
        self.neg_alerts = []
        self.name = name

    def work(self):
        for i, datum in enumerate(self.data):
            if i <= self.start_index:
                window = self.data[i : self.window_size + i]
                self.new_target_mean(window)
                self.new_alert_threshold(window)
                self.compute_cusum(datum, reset=True)
            elif self.cusum_within_alert_threshold():
                # Note this will always be true for the first `window_size`
                # data points
                self.maintain_target_mean()
                self.maintain_alert_threshold()
                self.compute_cusum(datum)
            else:
                # Assemble a moving window of the last `window_size`
                # non-null values
                window = self.data[i - self.window_size : i]
                self.new_target_mean(window)
                if self.moving_in_same_direction(datum):  # this "peeks ahead"
                    self.maintain_alert_threshold()
                    self.compute_cusum(datum)
                else:
                    self.new_alert_threshold(window)  # uses window
                    self.compute_cusum(datum, reset=True)
            # Record alert
            self.record_alert(datum, i)
        return self.as_dict()

    def as_dict(self):
        return {
            "smax": self.pos_cusums,
            "smin": self.neg_cusums,
            "target_mean": self.target_means,
            "alert_threshold": self.alert_thresholds,
            "alert": self.alert_indices,
            "alert_percentile_pos": self.pos_alerts,
            "alert_percentile_neg": self.neg_alerts,
        }

    def get_last_alert_info(self):
        """If the current (most recent) month includes an alert, work out when
        that alert period started, and return numbers that approximate
        to the size of the change across that period.

        """
        if any(self.alert_indices) and self.alert_indices[-1] == len(self.data) - 1:
            end_index = start_index = self.alert_indices[-1]
            for x in list(reversed(self.alert_indices))[1:]:
                if x == start_index - 1:
                    start_index = x
                else:
                    break
            duration = (end_index - start_index) + 1
            return {
                "from": self.target_means[start_index - 1],
                "to": self.data[end_index],
                "period": duration,
            }
        else:
            return None

    def moving_in_same_direction(self, datum):
        # Peek ahead to see what the next CUSUM would be
        next_pos_cusum, next_neg_cusum = self.compute_cusum(datum, store=False)
        going_up = (
            next_pos_cusum > self.current_pos_cusum()
            and self.cusum_above_alert_threshold()
        )
        going_down = (
            next_neg_cusum < self.current_neg_cusum()
            and self.cusum_below_alert_threshold()
        )
        return going_up or going_down

    def __repr__(self):
        return """
        name:             {name}
        data:             {data}
        pos_cusums:       {pos_cusums}
        neg_cusums:       {neg_cusums}
        target_means:     {target_means}
        alert_thresholds: {alert_thresholds}"
        alert_incides:    {alert_indices}"
        """.format(**self.__dict__)

    def record_alert(self, datum, i):
        if self.cusum_above_alert_threshold():
            self.alert_indices.append(i)
            self.pos_alerts.append(datum)
            self.neg_alerts.append(None)
        elif self.cusum_below_alert_threshold():
            self.alert_indices.append(i)
            self.pos_alerts.append(None)
            self.neg_alerts.append(datum)
        else:
            self.pos_alerts.append(None)
            self.neg_alerts.append(None)

    def maintain_alert_threshold(self):
        self.alert_thresholds.append(self.alert_thresholds[-1])
        return self.alert_thresholds[-1]

    def maintain_target_mean(self):
        self.target_means.append(self.target_means[-1])
        return self.target_means[-1]

    def cusum_above_alert_threshold(self):
        return self.pos_cusums[-1] > self.alert_thresholds[-1]

    def cusum_below_alert_threshold(self):
        return self.neg_cusums[-1] < -self.alert_thresholds[-1]

    def cusum_within_alert_threshold(self):
        return not (
            self.cusum_above_alert_threshold() or self.cusum_below_alert_threshold()
        )

    def new_target_mean(self, window):
        self.target_means.append(np.nanmean(window))

    def new_alert_threshold(self, window):
        self.alert_thresholds.append(np.nanstd(window * self.sensitivity))

    def current_pos_cusum(self):
        return self.pos_cusums[-1]

    def current_neg_cusum(self):
        return self.neg_cusums[-1]

    def compute_cusum(self, datum, reset=False, store=True):
        alert_threshold = self.alert_thresholds[-1]
        delta = 0.5 * alert_threshold / self.sensitivity
        current_mean = self.target_means[-1]
        cusum_pos = datum - (current_mean + delta)
        cusum_neg = datum - (current_mean - delta)
        if not reset:
            cusum_pos += self.pos_cusums[-1]
            cusum_neg += self.neg_cusums[-1]
        cusum_pos = round(max(0, cusum_pos), 2)
        cusum_neg = round(min(0, cusum_neg), 2)
        if store:
            self.pos_cusums.append(cusum_pos)
            self.neg_cusums.append(cusum_neg)
        return cusum_pos, cusum_neg


def percentiles_without_jaggedness(df2, is_percentage=False):
    """Remove records that are outside the standard error of the mean or
    where they hit 0% or 100% more than once.

    The parameters used are no more than an educated guess.

    """
    sem = df2.percentile.std() / np.sqrt(len(df2))
    df2.extremes = 0
    if is_percentage:
        df2.extremes += df2.calc_value[df2.calc_value == 1.0].count()
    df2.extremes += df2.calc_value[df2.calc_value == 0.0].count()
    df2.extremes += df2.numerator[df2.numerator < 15].count()
    df2.extremes += df2.percentile[
        (df2.percentile < sem) | (df2.percentile > (100 - sem))
    ].count()
    if df2.extremes == 0:
        return df2.percentile
    else:
        return []


class InterestingMeasureFinder(object):
    def __init__(self, org, interesting_saving=1000, interesting_change_window=12):
        if isinstance(org, Practice):
            self.measure_filter_for_org = {"practice": org}
        elif isinstance(org, PCN):
            self.measure_filter_for_org = {"pcn": org, "practice": None}
        elif isinstance(org, PCT):
            self.measure_filter_for_org = {"pct": org, "practice": None}
        elif isinstance(org, STP):
            self.measure_filter_for_org = {"stp": org, "practice": None, "pct": None}
        else:
            assert False, "Unexpected org {}".format(org)

        self.interesting_change_window = interesting_change_window
        self.interesting_saving = interesting_saving

    def months_ago(self, period):
        now = ImportLog.objects.latest_in_category("prescribing").current_at
        return now + relativedelta(months=-(period - 1))

    def _best_or_worst_performing_in_period(self, period, best_or_worst=None):
        assert best_or_worst in ["best", "worst"]
        worst = []
        measure_filter = {
            "month__gte": self.months_ago(period),
            "measure__include_in_alerts": True,
        }
        measure_filter.update(self.measure_filter_for_org)
        invert_percentile_for_comparison = False
        if best_or_worst == "worst":
            invert_percentile_for_comparison = True
            measure_filter["percentile__gte"] = 90
        else:
            measure_filter["percentile__lte"] = 10
        df = self.measurevalues_dataframe(
            MeasureValue.objects.filter(**measure_filter),
            ["numerator", "calc_value", "percentile"],
        )
        for row in df.iterrows():
            measure = Measure.objects.get(pk=row[0])
            measure_df = row[1]
            non_jagged = percentiles_without_jaggedness(
                measure_df, measure.is_percentage
            )
            if len(non_jagged) == period:
                comparator = non_jagged.iloc[-1]
                if invert_percentile_for_comparison:
                    comparator = 100 - comparator
                worst.append((measure, comparator))
        worst = sorted(worst, key=lambda x: x[-1])
        return [x[0] for x in worst]

    def worst_performing_in_period(self, period):
        """Return every measure where the organisation specified in the given
        bookmark is in the worst decile for each month in the
        specified time range

        """
        return self._best_or_worst_performing_in_period(period, "worst")

    def best_performing_in_period(self, period):
        """Return every measure where organisations specified in the given
        bookmark is in the best decile for each month in the specified
        time range

        """
        return self._best_or_worst_performing_in_period(period, "best")

    def most_change_against_window(self, window):
        """Use CUSUM algorithm to detect cumulative change from a reference
        mean averaged over the previous `window` months.

        Returns a list of dicts of `measure`, `from`, and `to`

        """
        improvements = []
        declines = []
        # We multiply the window because we want to include alerts
        # that are continuing after they were first detected
        window_multiplier = 1.5
        window_plus = int(round(window * window_multiplier))
        measure_filter = {
            "month__gte": self.months_ago(window_plus),
            "measure__include_in_alerts": True,
        }
        measure_filter.update(self.measure_filter_for_org)
        df = self.measurevalues_dataframe(
            MeasureValue.objects.filter(**measure_filter), "percentile"
        )
        for row in df.itertuples():
            measure = Measure.objects.get(pk=row[0])
            percentiles = row[1:]
            cusum = CUSUM(
                percentiles, window_size=window, sensitivity=5, name=measure.id
            )
            cusum.work()

            last_alert = cusum.get_last_alert_info()
            if last_alert:
                last_alert["measure"] = measure
                if last_alert["from"] < last_alert["to"]:
                    if measure.low_is_good:
                        declines.append(last_alert)
                    else:
                        improvements.append(last_alert)
                else:
                    if measure.low_is_good:
                        improvements.append(last_alert)
                    else:
                        declines.append(last_alert)
        improvements = sorted(improvements, key=lambda x: -abs(x["to"] - x["from"]))
        declines = sorted(declines, key=lambda x: -abs(x["to"] - x["from"]))
        return {"improvements": improvements, "declines": declines}

    def measurevalues_dataframe(self, queryset=None, data_col=None):
        """Given a queryset of many measurevalues across many measures,
        returns a dataframe indexed by measure, with month columns,
        and `data_col` values.

        """
        if not isinstance(data_col, list):
            data_col = [data_col]
        data_cols = ["month", "measure_id"] + data_col
        data = list(queryset.order_by("measure_id", "month").values_list(*data_cols))
        if data:
            df = pd.DataFrame.from_records(
                data, columns=data_cols, index=["measure_id", "month"]
            )
            return df.unstack(level="month")
        else:
            return pd.DataFrame()

    def top_and_total_savings_in_period(self, period):
        """Sum total possible savings over time, and find measures where
        possible or achieved savings are greater than self.interesting_saving.

        Returns a dictionary where the keys are
        `possible_top_savings_total`, `possible_savings` and
        `achieved_savings`; and the values are an integer, sorted
        `(measure, saving)` tuples, and sorted `(measure, saving)`
        tuples respectively.

        """
        possible_savings = []
        achieved_savings = []
        total_savings = 0
        measure_filter = {
            "month__gte": self.months_ago(period),
            "measure__include_in_alerts": True,
        }
        measure_filter.update(self.measure_filter_for_org)
        df = self.measurevalues_dataframe(
            MeasureValue.objects.filter(**measure_filter), "cost_savings"
        )
        for row in df.itertuples():
            measure = Measure.objects.get(pk=row[0])
            cost_savings = row[1:]
            if measure.is_cost_based:
                if len(cost_savings) != period:
                    continue
                savings_at_50th = [
                    saving["50"] for saving in cost_savings if isinstance(saving, dict)
                ]
                savings_or_loss_for_measure = sum(savings_at_50th)
                if savings_or_loss_for_measure >= self.interesting_saving:
                    possible_savings.append((measure, savings_or_loss_for_measure))
                if savings_or_loss_for_measure <= -self.interesting_saving:
                    achieved_savings.append((measure, -1 * savings_or_loss_for_measure))
                if measure.low_is_good:
                    savings_at_10th = sum(
                        [
                            max(0, saving["10"])
                            for saving in cost_savings
                            if isinstance(saving, dict)
                        ]
                    )
                else:
                    savings_at_10th = sum(
                        [
                            max(0, saving["90"])
                            for saving in cost_savings
                            if isinstance(saving, dict)
                        ]
                    )
                total_savings += savings_at_10th
        return {
            "possible_savings": sorted(possible_savings, key=lambda x: -x[1]),
            "achieved_savings": sorted(achieved_savings, key=lambda x: x[1]),
            "possible_top_savings_total": total_savings,
        }

    def _move_non_ordinal(self, from_list, to_list):
        """Move any non-ordinal measures (i.e. where `low_is_good` is None)
        from one list to another

        """
        for measure in from_list[:]:
            if isinstance(measure, dict):
                # As returned by most_changing function
                m = measure["measure"]
            else:
                m = measure
            if m.low_is_good is None:
                from_list.remove(measure)
                if measure not in to_list:
                    to_list.append(measure)

    def context_for_org_email(self):
        worst = self.worst_performing_in_period(3)
        best = self.best_performing_in_period(3)
        most_changing = self.most_change_against_window(12)
        interesting = []
        most_changing_interesting = []
        for extreme in [worst, best]:
            self._move_non_ordinal(extreme, interesting)
        for extreme in [most_changing["improvements"], most_changing["declines"]]:
            self._move_non_ordinal(extreme, most_changing_interesting)
        top_savings = self.top_and_total_savings_in_period(6)
        return {
            "interesting": interesting,
            "most_changing_interesting": most_changing_interesting,
            "worst": worst,
            "best": best,
            "most_changing": most_changing,
            "top_savings": top_savings,
        }


def attach_image(msg, url, file_path, selector, dimensions="1024x1024"):
    if "selectedTab=map" in url:
        wait = 8000
        dimensions = "1000x600"
    elif "selectedTab=chart" in url:
        wait = 1000
        dimensions = "800x600"
    elif "selectedTab" in url:
        wait = 500
        dimensions = "800x600"
    else:
        wait = 1000
    cmd = '{cmd} "{host}{url}" {file_path} "{selector}" {dimensions} {wait}'
    cmd = cmd.format(
        cmd=GRAB_CMD,
        host=settings.GRAB_HOST,
        url=url,
        file_path=file_path,
        selector=selector,
        dimensions=dimensions,
        wait=wait,
    )
    response = subprocess.run(
        cmd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        # Fix for PhantomJS under new versions of Debian/Ubuntu
        # See https://github.com/ariya/phantomjs/issues/15449
        env=dict(os.environ, OPENSSL_CONF="/etc/ssl"),
    )
    if response.returncode > 0:
        raise BadAlertImageError(
            f"phantomjs command failed with code {response.returncode}\n\n"
            f"cmd:\n{cmd}\n\n"
            f"stdout:\n{response.stdout}\n\n"
            f"stderr:\n{response.stderr}"
        )
    else:
        logger.debug(
            "Command %s completed with output %s" % (cmd, response.stdout.strip())
        )
    if os.path.getsize(file_path) == 0:
        msg = "File at %s empty (generated from url %s)" % (file_path, url)
        raise BadAlertImageError(msg)
    return attach_inline_image_file(msg, file_path, subtype="png")


def getIntroText(stats, org_type):
    declines = len(stats["most_changing"]["declines"])
    improvements = len(stats["most_changing"]["improvements"])
    possible_savings = len(stats["top_savings"]["possible_savings"])
    worst = len(stats["worst"])
    best = len(stats["best"])
    not_great = worst + declines
    pretty_good = best + improvements
    msg = ""
    in_sentence = False
    if not_great or pretty_good or possible_savings:
        if not_great:
            msg = "We've found %s prescribing measure%s where this %s " % (
                apnumber(not_great),
                not_great > 1 and "s" or "",
                org_type,
            )
            in_sentence = True
            if declines and worst:
                msg += "is <span class='worse'>getting worse, or could be "
                msg += "doing better</span>"
            elif declines:
                msg += "is <span class='worse'>getting worse</span>"
            else:
                msg += "could be <span class='worse'>doing better</span>"
        else:
            msg = (
                "Good news: we've not found any problem prescribing "
                "measures for this %s!" % org_type
            )
            in_sentence = False
        if pretty_good:
            if msg and in_sentence:
                msg += ", and %s measure%s where it " % (
                    apnumber(pretty_good),
                    pretty_good > 1 and "s" or "",
                )
            else:
                msg = "We've found %s prescribing measure%s where this %s " % (
                    apnumber(pretty_good),
                    pretty_good > 1 and "s" or "",
                    org_type,
                )
            if best and improvements:
                msg += "is <span class='better'>doing well</span>."
            elif improvements:
                msg += "is <span class='better'>improving</span>."
            else:
                msg += "is <span class='better'>already doing "
                msg += "very well</span>."
            in_sentence = False
        if in_sentence:
            msg += ". "
        if possible_savings:
            if msg:
                msg += " We've also found "
            else:
                msg = "We've found "
            msg += (
                "%s prescribing measure%s where there are some "
                "potential cost savings. "
                % (apnumber(possible_savings), possible_savings > 1 and "s" or "")
            )
        msg += (
            "Note that there can sometimes be good reasons why one %s is "
            "an outlier, and you should interpret the data thoughtfully: "
            'these are <a href="https://openprescribing.net/faq/#measureinterpret">'
            "measures, not indicators</a>." % org_type
        )
    else:
        msg = (
            "We've no new information about this %s this month! "
            "Its performance is not an outlier on any "
            "of our common prescribing measures. " % org_type
        )
    return mark_safe(msg)


def _hasStats(stats):
    return (
        stats["worst"]
        or stats["best"]
        or stats["interesting"]
        or stats["most_changing_interesting"]
        or stats["top_savings"]["possible_top_savings_total"]
        or stats["top_savings"]["possible_savings"]
        or stats["top_savings"]["achieved_savings"]
        or stats["most_changing"]["declines"]
        or stats["most_changing"]["improvements"]
    )


def ga_tracking_qs(context):
    tracking_params = {
        "utm_medium": "email",
        "utm_campaign": context["campaign_name"],
        "utm_source": context["campaign_source"],
        "utm_content": context["email_id"],
    }
    return urllib.parse.urlencode(tracking_params)


def truncate_subject(prefix, subject):
    assert subject, "Subject must not be empty"
    max_length = 78 - len(prefix)
    ellipsis = "..."
    subject = nhs_titlecase(subject)
    if len(subject) <= max_length:
        truncated = subject
    else:
        if " by " in subject:
            end_bit = subject.split(" by ")[-1]
            end_bit = "by " + end_bit
        else:
            end_bit = ""
        if len(end_bit) + len(ellipsis) > max_length:
            end_bit = ""
        start_bit = subject[: (max_length - len(end_bit) - len(ellipsis))]
        truncated = start_bit + ellipsis + end_bit
    return prefix + truncated


def initialise_email(bookmark, campaign_source):
    campaign_name = "monthly alert %s" % date.today().strftime("%Y-%m-%d")
    email_id = "/email/%s/%s/%s" % (campaign_name, campaign_source, bookmark.id)
    if isinstance(bookmark, NCSOConcessionBookmark):
        subject_prefix = "Your update about "
    else:
        subject_prefix = "Your monthly update about "
    msg = EmailMultiAlternatives(
        truncate_subject(subject_prefix, bookmark.name),
        "...placeholder...",
        settings.DEFAULT_FROM_EMAIL,
        [bookmark.user.email],
    )
    metadata = {
        "subject": msg.subject,
        "campaign_name": campaign_name,
        "campaign_source": campaign_source,
        "email_id": email_id,
    }
    msg.metadata = metadata
    msg.qs = ga_tracking_qs(metadata)
    # Set the message id now, so we can reuse it
    msg.extra_headers = {"message-id": msg.message()["message-id"]}
    return msg


def finalise_email(msg, template_name, context, tags):
    """Set message body, add HTML alternative, and add some headers."""

    template = get_template(template_name)
    html = template.render(context)
    html = Premailer(html, cssutils_logging_level=logging.ERROR).transform()
    html = unescape_href(html)
    text = email_as_text(html)
    msg.body = text
    msg.attach_alternative(html, "text/html")
    msg.extra_headers["list-unsubscribe"] = "<%s>" % context["unsubscribe_link"]
    msg.tags = ["monthly_update"] + tags
    return msg


def get_chart_id(measure_id):
    return "#{}-with-title".format(measure_id)


def make_org_email(org_bookmark, stats, tag=None):
    msg = initialise_email(org_bookmark, "dashboard-alerts")
    dashboard_uri = org_bookmark.dashboard_url()
    dashboard_uri = settings.GRAB_HOST + dashboard_uri + "?" + msg.qs

    with NamedTemporaryFile(suffix=".png") as getting_worse_file:
        most_changing = stats["most_changing"]
        if most_changing["declines"]:
            measure_id = most_changing["declines"][0]["measure"].id
            getting_worse_img = attach_image(
                msg,
                org_bookmark.dashboard_url(measure_id),
                getting_worse_file.name,
                get_chart_id(measure_id),
            )
        else:
            getting_worse_img = None

    with NamedTemporaryFile(suffix=".png") as still_bad_file:
        if stats["worst"]:
            measure_id = stats["worst"][0].id
            still_bad_img = attach_image(
                msg,
                org_bookmark.dashboard_url(measure_id),
                still_bad_file.name,
                get_chart_id(measure_id),
            )
        else:
            still_bad_img = None

    with NamedTemporaryFile(suffix=".png") as interesting_file:
        if stats["interesting"]:
            measure_id = stats["interesting"][0].id
            interesting_img = attach_image(
                msg,
                org_bookmark.dashboard_url(measure_id),
                interesting_file.name,
                get_chart_id(measure_id),
            )
        else:
            interesting_img = None

    unsubscribe_link = settings.GRAB_HOST + reverse(
        "bookmarks", kwargs={"key": org_bookmark.user.profile.key}
    )

    context = {
        "intro_text": getIntroText(stats, org_bookmark.org_type()),
        "total_possible_savings": sum(
            [x[1] for x in stats["top_savings"]["possible_savings"]]
        ),
        "has_stats": _hasStats(stats),
        "domain": settings.GRAB_HOST,
        "measures_count": Measure.objects.non_preview().count(),
        "getting_worse_image": getting_worse_img,
        "still_bad_image": still_bad_img,
        "interesting_image": interesting_img,
        "bookmark": org_bookmark,
        "dashboard_uri": mark_safe(dashboard_uri),
        "qs": mark_safe(msg.qs),
        "stats": stats,
        "unsubscribe_link": unsubscribe_link,
    }

    finalise_email(msg, "bookmarks/email_for_measures.html", context, ["measures", tag])

    return msg


def make_search_email(search_bookmark, tag=None):
    msg = initialise_email(search_bookmark, "analyse-alerts")
    parsed_url = urllib.parse.urlparse(search_bookmark.dashboard_url())
    dashboard_uri = parsed_url.path
    if parsed_url.query:
        qs = "?" + parsed_url.query + "&" + msg.qs
    else:
        qs = "?" + msg.qs
    dashboard_uri = settings.GRAB_HOST + dashboard_uri + qs + "#" + parsed_url.fragment

    with NamedTemporaryFile(suffix=".png") as graph_file:
        graph = attach_image(
            msg,
            search_bookmark.dashboard_url(),
            graph_file.name,
            "#results .tab-pane.active",
        )

    unsubscribe_link = settings.GRAB_HOST + reverse(
        "bookmarks", kwargs={"key": search_bookmark.user.profile.key}
    )

    context = {
        "bookmark": search_bookmark,
        "domain": settings.GRAB_HOST,
        "graph": graph,
        "dashboard_uri": mark_safe(dashboard_uri),
        "unsubscribe_link": unsubscribe_link,
    }

    finalise_email(msg, "bookmarks/email_for_searches.html", context, ["analyse", tag])

    return msg


def make_ncso_concession_email(bookmark, tag=None):
    msg = initialise_email(bookmark, "ncso-concessions-alerts")

    monthly_totals = ncso_spending_for_entity(
        bookmark.entity, bookmark.entity_type, num_months=1
    )
    latest_month = max(row["month"] for row in monthly_totals)
    breakdown = ncso_spending_breakdown_for_entity(
        bookmark.entity, bookmark.entity_type, latest_month
    )[:10]
    last_prescribing_month = ImportLog.objects.latest_in_category(
        "prescribing"
    ).current_at

    if bookmark.entity_type == "CCG":
        concessions_view_name = "spending_for_one_ccg"
        concessions_kwargs = {"entity_code": bookmark.entity.code}
        dashboard_view_name = "ccg_home_page"
        dashboard_kwargs = {"ccg_code": bookmark.entity.code}
    elif bookmark.entity_type == "practice":
        concessions_view_name = "spending_for_one_practice"
        concessions_kwargs = {"entity_code": bookmark.entity.code}
        dashboard_view_name = "practice_home_page"
        dashboard_kwargs = {"practice_code": bookmark.entity.code}
    elif bookmark.entity_type == "all_england":
        concessions_view_name = "spending_for_all_england"
        concessions_kwargs = {}
        dashboard_view_name = "all_england"
        dashboard_kwargs = {}
    else:
        assert False

    concessions_path = reverse(concessions_view_name, kwargs=concessions_kwargs)
    concessions_url = settings.GRAB_HOST + concessions_path

    dashboard_path = reverse(dashboard_view_name, kwargs=dashboard_kwargs)
    dashboard_url = settings.GRAB_HOST + dashboard_path

    unsubscribe_path = reverse("bookmarks", kwargs={"key": bookmark.user.profile.key})
    unsubscribe_link = settings.GRAB_HOST + unsubscribe_path

    with NamedTemporaryFile(suffix=".png") as f:
        chart_image_cid = attach_image(
            msg, concessions_path, f.name, "#monthly-totals-chart"
        )

    context = {
        "latest_month": latest_month,
        "last_prescribing_month": last_prescribing_month,
        "entity_name": bookmark.entity_cased_name,
        "entity_type": bookmark.entity_type,
        "additional_cost": monthly_totals[0]["additional_cost"],
        "breakdown": breakdown,
        "concessions_url": concessions_url,
        "dashboard_url": dashboard_url,
        "chart_image_cid": chart_image_cid,
        "unsubscribe_link": unsubscribe_link,
    }

    finalise_email(
        msg,
        "bookmarks/email_for_ncso_concessions.html",
        context,
        ["ncso_concessions", tag],
    )

    return msg


def unescape_href(text):
    """Unfortunately, premailer escapes hrefs and there's [not much we can
    do about it](https://github.com/peterbe/premailer/issues/72).
    Unencode them again."""
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', text)
    for href in hrefs:
        text = text.replace(href, unescape(href))
    return text


def make_all_england_email(bookmark, tag=None):
    msg = initialise_email(bookmark, "all-england-alerts")
    msg.subject = "Your monthly update on prescribing across NHS England"

    date = ImportLog.objects.latest_in_category("dashboard_data").current_at

    # This allows us to switch between calculating savings at the practice or
    # CCG level. We use CCG at present for performance reasons but we may want
    # to switch in future.
    entity_type = "CCG"
    ppu_savings = get_total_savings_for_org(str(date), "all_standard_practices", None)
    measure_savings = cached(all_england_measure_savings, entity_type, date)
    low_priority_savings = cached(all_england_low_priority_savings, entity_type, date)
    low_priority_total = cached(all_england_low_priority_total, entity_type, date)
    ncso_spending = first_or_none(
        ncso_spending_for_entity(None, "all_england", num_months=1)
    )

    unsubscribe_path = reverse("bookmarks", kwargs={"key": bookmark.user.profile.key})
    unsubscribe_link = settings.GRAB_HOST + unsubscribe_path

    context = {
        "entity_type": entity_type,
        "ppu_savings": ppu_savings,
        "measure_savings": measure_savings,
        "low_priority_savings": low_priority_savings,
        "low_priority_total": low_priority_total,
        "ncso_spending": ncso_spending,
        "date": date,
        "unsubscribe_link": unsubscribe_link,
        "HOST": settings.GRAB_HOST,
    }

    finalise_email(
        msg, "bookmarks/email_for_all_england.html", context, ["all_england", tag]
    )

    return msg
