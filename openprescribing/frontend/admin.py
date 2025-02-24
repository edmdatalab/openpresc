import base64
import hmac
import json

from django.conf import settings
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.db import connection
from django.db.models import Count
from django.http import StreamingHttpResponse
from django.urls import path
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from matrixstore.csv_utils import dicts_to_csv

from .models import (
    PCN,
    STP,
    EmailMessage,
    MailLog,
    NCSOConcessionBookmark,
    OrgBookmark,
    SearchBookmark,
    User,
)

admin.site.unregister(User)


class UserVerifiedFilter(admin.SimpleListFilter):
    # Human-readable title which will be displayed in the
    # right admin sidebar just above the filter options.
    title = "Verified"

    # Parameter for the filter that will be used in the URL query.
    parameter_name = "verified"

    def lookups(self, request, model_admin):
        """
        Returns a list of tuples. The first element in each
        tuple is the coded value for the option that will
        appear in the URL query. The second element is the
        human-readable name for the option that will appear
        in the right sidebar.
        """
        return ((True, "Yes"), (False, "No"))

    def queryset(self, request, queryset):
        """
        Returns the filtered queryset based on the value
        provided in the query string and retrievable via
        `self.value()`.
        """
        # Compare the requested value (either '80s' or '90s')
        # to decide how to filter the queryset.
        if self.value() == "True":
            return queryset.filter(emailaddress__verified=True)
        elif self.value() == "False":
            return queryset.filter(emailaddress__verified=False)


@admin.register(SearchBookmark)
class SearchBookmarkAdmin(admin.ModelAdmin):
    date_hierarchy = "created_at"
    list_display = ("name", "user", "created_at")
    list_filter = ("created_at",)
    readonly_fields = ("dashboard_link",)
    search_fields = ("user__email",)

    def dashboard_link(self, obj):
        return format_html('<a href="{}">view in site</a>', obj.dashboard_url())


# See Django documentation for SimpleListFilter:
# https://docs.djangoproject.com/en/1.11/ref/contrib/admin/#django.contrib.admin.ModelAdmin.list_filter
class OrgBookmarkTypeFilter(admin.SimpleListFilter):
    title = "organisation type"
    parameter_name = "org_bookmark_type"

    # Defines what options appear in the right-hand filter panel
    def lookups(self, request, model_admin):
        return (
            ("practice", "Practice"),
            ("ccg", "Sub-ICB Location"),
            ("all_england", "All England"),
        )

    # Defines how the selected option is used to filter the queryset
    def queryset(self, request, queryset):
        value = self.value()
        if value == "practice":
            return queryset.filter(practice__isnull=False)
        elif value == "ccg":
            return queryset.filter(practice__isnull=True, pct__isnull=False)
        elif value == "all_england":
            return queryset.filter(practice__isnull=True, pct__isnull=True)
        else:
            return queryset


@admin.register(OrgBookmark)
class OrgBookmarkAdmin(admin.ModelAdmin):
    date_hierarchy = "created_at"
    list_display = ("name", "user", "created_at")
    list_filter = ("created_at", OrgBookmarkTypeFilter)
    readonly_fields = ("dashboard_link",)
    search_fields = ("user__email",)

    def dashboard_link(self, obj):
        return format_html('<a href="{}">view in site</a>', obj.dashboard_url())


@admin.register(NCSOConcessionBookmark)
class NCSOConcessionBookmarkAdmin(admin.ModelAdmin):
    date_hierarchy = "created_at"
    list_display = ("name", "user", "created_at")
    list_filter = ("created_at", OrgBookmarkTypeFilter)
    readonly_fields = ("dashboard_link",)
    search_fields = ("user__email",)

    def dashboard_link(self, obj):
        return format_html('<a href="{}">view in site</a>', obj.dashboard_url())


class EmailMessageInline(admin.TabularInline):
    model = EmailMessage
    readonly_fields = fields = ("message_id", "to", "subject", "tags", "created_at")


@admin.register(User)
class UserWithProfile(UserAdmin):
    list_display = (UserAdmin.list_display[0],) + (
        "is_staff",
        "emails_received",
        "emails_opened",
        "emails_clicked",
        "orgbookmarks",
        "searchbookmarks",
    )
    list_filter = (UserVerifiedFilter,)
    inlines = [EmailMessageInline]

    def get_queryset(self, request):
        qs = super(UserAdmin, self).get_queryset(request)
        return qs.annotate(
            searchbookmark_count=Count("searchbookmark", distinct=True),
            orgbookmark_count=Count("orgbookmark", distinct=True),
        )

    def orgbookmarks(self, obj):
        return obj.orgbookmark_set.count()

    orgbookmarks.short_description = "Orgs bookmarked"
    orgbookmarks.admin_order_field = "orgbookmark_count"

    def searchbookmarks(self, obj):
        return obj.searchbookmark_set.count()

    searchbookmarks.short_description = "Searches bookmarked"
    searchbookmarks.admin_order_field = "searchbookmark_count"

    def emails_received(self, obj):
        return obj.profile.emails_received

    emails_received.short_description = "Emails received"
    emails_received.admin_order_field = "profile__emails_received"

    def emails_opened(self, obj):
        return obj.profile.emails_opened

    emails_opened.short_description = "Emails opened"
    emails_opened.admin_order_field = "profile__emails_opened"

    def emails_clicked(self, obj):
        return obj.profile.emails_clicked

    emails_clicked.short_description = "Links clicked"
    emails_clicked.admin_order_field = "profile__emails_clicked"

    def get_urls(self):
        return [
            path(
                "exports/alert-signups.csv",
                self.admin_site.admin_view(self.export_alert_signups),
                name="export_alert_signups",
            ),
            *super().get_urls(),
        ]

    def export_alert_signups(self, request):
        return StreamingHttpResponse(
            dicts_to_csv(get_all_bookmark_details()),
            content_type="text/csv",
            headers={
                "content-disposition": 'attachment; filename="alert-signups.csv"',
            },
        )


def get_all_bookmark_details():
    for bookmark_type, bookmark in get_all_bookmarks():
        if bookmark_type == "search":
            org_type = ""
            org_code = ""
            org_name = bookmark.name
        else:
            org = bookmark.get_org()
            if org_is_closed(org):
                continue
            if org:
                org_type = org.HUMAN_NAME
                org_code = org.code
                org_name = org.cased_name
            else:
                org_type = "National"
                org_code = "England"
                org_name = "All England"
        yield {
            "email": hash_email_username(bookmark.user.email),
            "created_at": bookmark.created_at.date(),
            "alert_type": bookmark_type,
            "org_type": org_type,
            "org_id": org_code,
            "org_name": org_name,
        }


def hash_email_username(email):
    user, _, domain = email.rpartition("@")
    digest = hmac.digest(
        settings.SECRET_KEY.encode("utf8"), user.encode("utf8"), "sha1"
    )
    hashed_user = base64.urlsafe_b64encode(digest).decode("ascii")[:16]
    return f"{hashed_user}@{domain}"


def org_is_closed(org):
    # All England – never closed
    if org is None:
        return False
    # We don't have close dates for PCNs or ICBs
    if isinstance(org, PCN | STP):
        return False
    return org.close_date is not None


def get_all_bookmarks():
    org_bookmarks = OrgBookmark.objects.select_related(
        "user",
    ).prefetch_related(
        "pct",
        "practice",
        "pcn",
        "stp",
    )
    for bookmark in org_bookmarks:
        yield "monthly", bookmark

    ncso_bookmarks = NCSOConcessionBookmark.objects.select_related(
        "user",
    ).prefetch_related(
        "pct",
        "practice",
    )
    for bookmark in ncso_bookmarks:
        yield "price_concessions", bookmark

    search_bookmarks = SearchBookmark.objects.select_related("user")
    for bookmark in search_bookmarks:
        yield "search", bookmark


class MailLogInline(admin.TabularInline):
    model = MailLog
    fields = readonly_fields = (
        "timestamp",
        "event_type",
        "message_id",
        "recipient",
        "tags",
        "reject_reason",
        "metadata_prettyprinted",
    )
    search_fields = ("recipient",)

    def metadata_prettyprinted(self, obj):
        return mark_safe("<pre>%s</pre>" % json.dumps(obj.metadata, indent=2))

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class TagsFilter(admin.SimpleListFilter):
    title = "Tags"

    # Parameter for the filter that will be used in the URL query.
    parameter_name = "tags"

    def lookups(self, request, model_admin):
        """
        Returns a list of tuples. The first element in each
        tuple is the coded value for the option that will
        appear in the URL query. The second element is the
        human-readable name for the option that will appear
        in the right sidebar.
        """
        tags = model_admin.model.objects.order_by().values("tags").distinct()
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT UNNEST(tags) AS tag FROM frontend_maillog GROUP BY tag"
            )
            tags = cursor.fetchall()
            return ((t[0], t[0]) for t in tags)

    def queryset(self, request, queryset):
        """
        Returns the filtered queryset based on the value
        provided in the query string and retrievable via
        `self.value()`.
        """
        if self.value():
            return queryset.filter(tags__contains=[self.value()])
        else:
            return queryset


@admin.register(EmailMessage)
class EmailMessageAdmin(admin.ModelAdmin):
    list_display = ("to", "subject", "tags_str", "created_at", "send_count")
    list_filter = (TagsFilter, "subject")
    readonly_fields = fields = (
        "message_id",
        "to",
        "subject",
        "tags_str",
        "created_at",
        "send_count",
        "user",
        "message_html",
    )
    inlines = [MailLogInline]
    date_hierarchy = "created_at"

    def message_html(self, obj):
        if obj.message.alternatives:
            return mark_safe(obj.message.alternatives[0][0])
        else:
            return mark_safe(obj.message.body)

    def tags_str(self, obj):
        return ", ".join(obj.tags)

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MailLog)
class MailLogAdmin(admin.ModelAdmin):
    date_hierarchy = "timestamp"
    list_display = (
        "timestamp",
        "event_type",
        "subject_from_metadata",
        "recipient",
        "tags_str",
    )
    list_filter = (TagsFilter, "event_type")
    readonly_fields = fields = (
        "timestamp",
        "event_type",
        "subject_from_metadata",
        "recipient",
        "tags_str",
        "reject_reason",
        "raw_message_id",
        "metadata_prettyprinted",
    )

    def metadata_prettyprinted(self, obj):
        return mark_safe("<pre>%s</pre>" % json.dumps(obj.metadata, indent=2))

    def tags_str(self, obj):
        return ", ".join(obj.tags)

    def raw_message_id(self, obj):
        # We can't use `message_id` as this is resolved to a
        # ForeignKey by Django, and we don't actually have a
        # constraint on that
        return obj.message_id

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
