"""Common settings and globals."""

import sys
from os.path import abspath, basename, dirname, join, normpath
from sys import path

from common import utils
from django.core.exceptions import ImproperlyConfigured

# Replace stdlib sqlite3 with a more up-to-date version. This bit of
# monkey-patching is required because we want the DiskCache library to use this
# version as well and we can't (easily) rewrite its imports. And also, because
# this is an experiment for now this is a quick and non-invasive way of trying
# it out. Longer term we can change our own code to import pysqlite3 directly
# and do some more targetted monkey patching for DiskCache.
__import__("pysqlite3")
sys.modules["sqlite3"] = sys.modules.pop("pysqlite3")

# PATH CONFIGURATION
# Absolute filesystem path to the Django project directory:
SETTINGS_ROOT = dirname(dirname(abspath(__file__)))

# Absolute filesystem path to the top-level project folder:
APPS_ROOT = dirname(SETTINGS_ROOT)

REPO_ROOT = abspath(join(APPS_ROOT, ".."))

# Site name:
SITE_NAME = basename(SETTINGS_ROOT)

# Site ID (django.contrib.sites framework, required by django-anyauth
SITE_ID = 1

# Useful flag for special-casing shell operations
SHELL = len(sys.argv) > 1 and sys.argv[1] in ["shell", "dbshell"]

# Add our project to our pythonpath, this way we don't need to type our project
# name in our dotted import paths:
path.append(SETTINGS_ROOT)
# END PATH CONFIGURATION


# DEBUG CONFIGURATION
# See: https://docs.djangoproject.com/en/dev/ref/settings/#debug
DEBUG = False
# END DEBUG CONFIGURATION


# MANAGER CONFIGURATION
# See: https://docs.djangoproject.com/en/dev/ref/settings/#admins
ADMINS = (("EBM Data Lab", "tech@ebmdatalab.net"),)

# See: https://docs.djangoproject.com/en/dev/ref/settings/#managers
MANAGERS = ADMINS
# END MANAGER CONFIGURATION


# GENERAL CONFIGURATION
# See: https://docs.djangoproject.com/en/dev/ref/settings/#time-zone
TIME_ZONE = "Europe/London"

# See: https://docs.djangoproject.com/en/dev/ref/settings/#language-code
LANGUAGE_CODE = "en-gb"

# See: https://docs.djangoproject.com/en/dev/ref/settings/#site-id
SITE_ID = 1

# See: https://docs.djangoproject.com/en/dev/ref/settings/#use-i18n
USE_I18N = False

# See: https://docs.djangoproject.com/en/dev/ref/settings/#use-l10n
USE_L10N = True

# See: https://docs.djangoproject.com/en/dev/ref/settings/#use-tz
USE_TZ = True
# END GENERAL CONFIGURATION

# https://docs.djangoproject.com/en/3.2/releases/3.2/#customizing-type-of-auto-created-primary-keys
DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

# MEDIA CONFIGURATION
# See: https://docs.djangoproject.com/en/dev/ref/settings/#media-root
MEDIA_ROOT = normpath(join(APPS_ROOT, "media"))

# See: https://docs.djangoproject.com/en/dev/ref/settings/#media-url
MEDIA_URL = "/media/"
# END MEDIA CONFIGURATION


# STATIC FILE CONFIGURATION

# The directory from where files should be served. The `collectstatic`
# command will copy all files from static folders to here for serving.
# The reason we need to do this (rather than just serve things
# straignt from a static directory) is because dependent apps may also
# provide static files for serving -- specifically, the Django admin
# app.
# See: https://docs.djangoproject.com/en/dev/ref/settings/#static-root
STATIC_ROOT = normpath(join(APPS_ROOT, "assets"))

# The base URL which will be used in URLs for static assets
# See: https://docs.djangoproject.com/en/dev/ref/settings/#static-url
STATIC_URL = "/static/"

# Places that are searched -- by the django development server ahd the
# `collectstatic` command -- for static files
# See:
# https://docs.djangoproject.com/en/dev/ref/contrib/staticfiles/#std:setting-STATICFILES_DIRS
STATICFILES_DIRS = (normpath(join(APPS_ROOT, "static")),)

# See:
# https://docs.djangoproject.com/en/dev/ref/contrib/staticfiles/#staticfiles-finders
STATICFILES_FINDERS = (
    "django.contrib.staticfiles.finders.FileSystemFinder",
    "django.contrib.staticfiles.finders.AppDirectoriesFinder",
)

STATICFILES_STORAGE = "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"
# END STATIC FILE CONFIGURATION


# SECRET CONFIGURATION
# See: https://docs.djangoproject.com/en/dev/ref/settings/#secret-key
SECRET_KEY = utils.get_env_setting("SECRET_KEY")
# END SECRET CONFIGURATION

# SITE CONFIGURATION
# Hosts/domain names that are valid for this site
# See https://docs.djangoproject.com/en/1.5/ref/settings/#allowed-hosts
ALLOWED_HOSTS = []
# END SITE CONFIGURATION


# FIXTURE CONFIGURATION
# See:
# https://docs.djangoproject.com/en/dev/ref/settings/#std:setting-FIXTURE_DIRS
FIXTURE_DIRS = (normpath(join(APPS_ROOT, "frontend", "tests", "fixtures")),)
# END FIXTURE CONFIGURATION


# TEMPLATE CONFIGURATION
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [normpath(join(APPS_ROOT, "templates"))],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.contrib.auth.context_processors.auth",
                "django.template.context_processors.debug",
                "django.template.context_processors.i18n",
                "django.template.context_processors.media",
                "django.template.context_processors.static",
                "django.template.context_processors.tz",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.request",
                "frontend.context_processors.support_email",
                "frontend.context_processors.google_tracking_id",
                "frontend.context_processors.source_commit_id",
                "frontend.context_processors.api_host",
                "frontend.context_processors.debug",
                "pipeline.context_processors.import_in_progress",
            ],
            "debug": DEBUG,
        },
    }
]
# END TEMPLATE CONFIGURATION


# MIDDLEWARE CONFIGURATION
# See: https://docs.djangoproject.com/en/dev/ref/settings/#middleware-classes
MIDDLEWARE = (
    "corsheaders.middleware.CorsMiddleware",
    # Default Django middleware.
    "django.middleware.common.CommonMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    # 'django.middleware.clickjacking.XFrameOptionsMiddleware',
    "frontend.middleware.stp_redirect_middleware",
)
# END MIDDLEWARE CONFIGURATION


# URL CONFIGURATION
# See: https://docs.djangoproject.com/en/dev/ref/settings/#root-urlconf
ROOT_URLCONF = "%s.urls" % SITE_NAME
# END URL CONFIGURATION


# APP CONFIGURATION
DJANGO_APPS = (
    # Default Django apps:
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.sites",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.gis",
    # Useful template tags:
    "django.contrib.humanize",
    "rest_framework",
    "corsheaders",
)

# Apps specific for this project go here.
LOCAL_APPS = ("frontend", "dmd", "pipeline", "gcutils", "matrixstore", "outliers")

CONTRIB_APPS = (
    "anymail",
    "crispy_forms",
    "crispy_bootstrap3",
    "raven.contrib.django.raven_compat",
)

# See: https://docs.djangoproject.com/en/dev/ref/settings/#installed-apps
INSTALLED_APPS = DJANGO_APPS + CONTRIB_APPS + LOCAL_APPS
# END APP CONFIGURATION


# LOGGING CONFIGURATION
# See: https://docs.djangoproject.com/en/dev/ref/settings/#logging
# A sample logging configuration. The only tangible logging
# performed by this configuration is to send an email to
# the site admins on every HTTP 500 error when DEBUG=False.
# See http://docs.djangoproject.com/en/dev/topics/logging for
# more details on how to customize your logging configuration.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": (
                "%(asctime)s %(levelname)s "
                "[%(name)s:%(lineno)s] %(module)s "
                "%(process)d %(thread)d %(message)s"
            )
        }
    },
    "filters": {"require_debug_false": {"()": "django.utils.log.RequireDebugFalse"}},
    "handlers": {
        "mail_admins": {
            "level": "ERROR",
            "filters": ["require_debug_false"],
            "class": "django.utils.log.AdminEmailHandler",
        }
    },
    "loggers": {
        "django.request": {
            "handlers": ["mail_admins"],
            "level": "ERROR",
            "propagate": True,
        }
    },
}
# END LOGGING CONFIGURATION


# WSGI CONFIGURATION
# See: https://docs.djangoproject.com/en/dev/ref/settings/#wsgi-application
WSGI_APPLICATION = "%s.wsgi.application" % SITE_NAME
# END WSGI CONFIGURATION

TEST_RUNNER = "frontend.tests.custom_runner.AssetBuildingTestRunner"

CONN_MAX_AGE = 1200


REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": (
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
        "rest_framework_csv.renderers.CSVRenderer",
    ),
    "DEFAULT_CONTENT_NEGOTIATION_CLASS": "frontend.negotiation.IgnoreAcceptsContentNegotiation",
    # This removes HTTP BasicAuthentication, which DRF includes by default, as that
    # clashes with the BasicAuth we use to protect staging from bots
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework.authentication.SessionAuthentication",
    ),
    "EXCEPTION_HANDLER": "api.exception_handler.custom_exception_handler",
}

CORS_URLS_REGEX = r"^/api/.*$"
CORS_ORIGIN_ALLOW_ALL = True
CORS_ALLOW_METHODS = "GET"
SUPPORT_TO_EMAIL = "bennett@phc.ox.ac.uk"
SUPPORT_FROM_EMAIL = "feedback@openprescribing.net"
DEFAULT_FROM_EMAIL = "OpenPrescribing <{}>".format(SUPPORT_FROM_EMAIL)
GDOC_DOCS = {
    "zooming": "1lz1uRfNOy2fQ-xSy_6BiLV_7Mgr-Z2V0-VWzo6HlCO0",
    "analyse": "1HqlJlUA86cnlyJpUxiQdGsM46Gsv9xyZkmhkTqjbwH0",
    "analyse-by-practice": "1idnk9yczLLBLbYUbp06dMglfivobTNoKY7pA2zCDPI8",
    "analyse-by-ccg": "1izun1jIGW7Wica-eMkUOU1x7RWqCZ9BJrbWNvsCzWm0",
    "how-to-find-cost-savings": "1QHz4hl_8XklcAULawEPCIS0iMaDURhaPsW1VjdTAuUg",
    "querying-the-raw-data-yourself": "e/2PACX-1vS_AGRCngeMMIaCuPicia7QVUEyqrnRo8vXI0S6w7cfyzb9IjlxNcOrKcZZ85larUuKOf_mB7dg-Y7S",
}

API_HOST = utils.get_env_setting("API_HOST", default="")

# BigQuery project name
BQ_PROJECT = "ebmdatalab"

# BigQuery dataset names
BQ_HSCIC_DATASET = "hscic"
BQ_MEASURES_DATASET = "measures"
BQ_TMP_EU_DATASET = "tmp_eu"
BQ_DMD_DATASET = "dmd"
BQ_ARCHIVE_DATASET = "archive"
BQ_PRESCRIBING_EXPORT_DATASET = "prescribing_export"
BQ_PUBLIC_DATASET = "public_draft"
BQ_SCMD_DATASET = "scmd"

# Other BQ settings
BQ_DEFAULT_TABLE_EXPIRATION_MS = None
BQ_LOCATION = "EU"

# Use django-anymail through mailgun for sending emails
EMAIL_BACKEND = "anymail.backends.mailgun.EmailBackend"
ANYMAIL = {
    "MAILGUN_API_KEY": utils.get_env_setting("MAILGUN_API_KEY"),
    "MAILGUN_SENDER_DOMAIN": "staging.openprescribing.net",
    "WEBHOOK_SECRET": "%s:%s"
    % (
        utils.get_env_setting("MAILGUN_WEBHOOK_USER"),
        utils.get_env_setting("MAILGUN_WEBHOOK_PASS"),
    ),
}

# See: https://docs.djangoproject.com/en/dev/ref/settings/#server-email
SERVER_EMAIL = "errors@openprescribing.net"

# Easy bootstrap styling of Django forms
CRISPY_TEMPLATE_PACK = "bootstrap3"

# For grabbing images that we insert into alert emails
GRAB_HOST = "https://openprescribing.net"

# For sending messages to Slack
# Webhook URLs for posting to different channels can be configured at
# https://api.slack.com/apps/A03UM1N45JN/incoming-webhooks
SLACK_TECHNOISE_POST_KEY = utils.get_env_setting("SLACK_TECHNOISE_POST_KEY", default="")
SLACK_TEAM_POST_KEY = utils.get_env_setting("SLACK_TEAM_POST_KEY", default="")
SLACK_OP_POST_KEY = utils.get_env_setting("SLACK_OP_POST_KEY", default="")
SLACK_SENDING_ACTIVE = True


ENABLE_CACHING = utils.get_env_setting_bool("ENABLE_CACHING", default=False)


# Total on-disk size of the cache. We want _some_ limit here so it doesn't grow
# without bound, but I don't think we need to be too fussy about exactly what
# it is as we're not short on disk space.  For reference, a month's worth of
# cached PPU data is about 850MB.
cache_size_limit = 16 * 1024**3

CACHES = {
    "default": {
        # See: https://github.com/grantjenks/python-diskcache
        "BACKEND": "diskcache.DjangoCache",
        "LOCATION": utils.get_env_setting(
            "DISKCACHE_PATH", default=join(REPO_ROOT, "diskcache")
        ),
        # How long to cache values for by default (we want "forever")
        "TIMEOUT": None,
        # DiskCache uses a "fan-out" system to prevent simultaneous writers
        # from blocking each other (readers are never blocked). This involves
        # splitting the cache into multiple shards (8 by default).  However
        # this seems to have a noticeable impact on performance and given that
        # our workload is extremely light on writes (the first few requests
        # after a new data load will do a whole batch of writes and then
        # nothing) we don't really need this. Setting the shard count to 1
        # effectively disables the fan-out behaviour. See:
        # http://www.grantjenks.com/docs/diskcache/tutorial.html#fanoutcache
        "SHARDS": 1,
        # In the event that a write does get blocked this timeout determines
        # how long to wait before giving up and just not writing the value to
        # the cache. It's by no means a disaster if this happens: we'll just
        # cache the value the next time round.
        "DATABASE_TIMEOUT": 0.1,
        "OPTIONS": {
            "size_limit": cache_size_limit,
            # By default DiskCache writes larger values to individual files on
            # disk and just uses SQLite for the index and to store smaller
            # values. However by bumping the below limit up to something huge
            # we can get it to store values of any size in SQLite. By also
            # raising SQLite's memory-map limit to something large we can then
            # access all these values via memory-mapping (just as we do with
            # the MatrixStore). This gives us much better I/O performance when
            # reading from the cache. Possibly this comes at the expense of
            # worse write performance, but we're not particularly worried about
            # that.
            "disk_min_file_size": cache_size_limit,
            "sqlite_mmap_size": cache_size_limit,
            # This disables automatic deletion of older values when the cache
            # exceeds its size limit. By default this is done automatically
            # after each write (synchronously, as part of the same thread) but
            # in our case, posssibly because we're storing unusually large
            # objects in the db, this results in SQLite locking up and the
            # request erroring out. So we disable this behaviour and instead do
            # the garbage collection in the `diskcache_garbage_collect`
            # management command triggered by a cron job.
            "cull_limit": 0,
        },
    }
}


# The git sha of the currently running version of the code (will be empty in
# development). We set this conditionally so that if it isn't defined any
# attempt to access it will blow up with an attribute error, rather than
# silently getting an empty value
source_commit_id = utils.get_env_setting("SOURCE_COMMIT_ID", default="")
if source_commit_id:
    SOURCE_COMMIT_ID = source_commit_id


# Guard against invalid configurations
if ENABLE_CACHING and not source_commit_id:
    raise ImproperlyConfigured(
        "If ENABLE_CACHING is True then SOURCE_COMMIT_ID must be set"
    )


sentry_raven_dsn = utils.get_env_setting("SENTRY_RAVEN_DSN", default="")
if sentry_raven_dsn and not SHELL:
    RAVEN_CONFIG = {"dsn": sentry_raven_dsn}
    if source_commit_id:
        RAVEN_CONFIG["release"] = source_commit_id


# For downloading data from TRUD
TRUD_USERNAME = utils.get_env_setting("TRUD_USERNAME", default="")
TRUD_PASSWORD = utils.get_env_setting("TRUD_PASSWORD", default="")

# check_numbers.py will write copies of scraped pages here.  By writing to a
# location in /tmp/, we benefit from tmpreaper, which is run by cron to delete
# temporary files older than a week.
CHECK_NUMBERS_BASE_PATH = "/tmp/numbers-checker/"

# Path of directory containing measure definitions.
MEASURE_DEFINITIONS_PATH = join(APPS_ROOT, "measures", "definitions")

# When building the matrixstore, should we check whether data is in BQ before
# downloading it?
CHECK_DATA_IN_BQ = True

# Prefix we add to measure IDs to indicate that they are "previews" and should not be
# shown by default
MEASURE_PREVIEW_PREFIX = "preview_"
