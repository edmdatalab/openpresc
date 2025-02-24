"""Production settings and globals."""

import os

from common import utils

from .base import TEMPLATES, REPO_ROOT, APPS_ROOT, BQ_MEASURES_DATASET, ANYMAIL

# DEBUG CONFIGURATION
# See: https://docs.djangoproject.com/en/dev/ref/settings/#debug
DEBUG = False  # Not so safe to set to True as staging is not behind a password
TEMPLATES[0]["OPTIONS"]["debug"] = DEBUG
# END DEBUG CONFIGURATION

# HOST CONFIGURATION
# See:
# https://docs.djangoproject.com/en/1.5/releases/1.5/#allowed-hosts-required-in-production
ALLOWED_HOSTS = ["staging.openprescribing.net"]
# END HOST CONFIGURATION


# DATABASE CONFIGURATION
DATABASES = {
    "default": {
        "ENGINE": "django.contrib.gis.db.backends.postgis",
        "NAME": utils.get_env_setting("STAGING_DB_NAME", ""),
        "USER": utils.get_env_setting("DB_USER", ""),
        "PASSWORD": utils.get_env_setting("DB_PASS", ""),
        "HOST": utils.get_env_setting("DB_HOST", "127.0.0.1"),
    }
}
# END DATABASE CONFIGURATION


ANYMAIL["MAILGUN_SENDER_DOMAIN"] = "staging.openprescribing.net"
SUPPORT_FROM_EMAIL = "feedback@staging.openprescribing.net"
DEFAULT_FROM_EMAIL = "OpenPrescribing <{}>".format(SUPPORT_FROM_EMAIL)

# LOGGING CONFIGURATION
# See: https://docs.djangoproject.com/en/dev/ref/settings/#logging
LOGGING = {
    "version": 1,
    "disable_existing_loggers": True,
    "formatters": {
        "verbose": {
            "format": (
                "%(asctime)s %(levelname)s [%(name)s:%(lineno)s] "
                "%(module)s %(process)d %(thread)d %(message)s"
            )
        }
    },
    "handlers": {
        "gunicorn": {
            "level": "DEBUG",
            "class": "logging.handlers.RotatingFileHandler",
            "formatter": "verbose",
            "filename": "%s/logs/gunicorn.log" % REPO_ROOT,
            "maxBytes": 1024 * 1024 * 100,  # 100 mb
        },
        "signals": {
            "level": "DEBUG",
            "class": "logging.handlers.RotatingFileHandler",
            "formatter": "verbose",
            "filename": "%s/logs/mail-signals.log" % REPO_ROOT,
            "maxBytes": 1024 * 1024 * 100,  # 100 mb
        },
        "sentry": {
            "level": "WARNING",
            "class": "raven.contrib.django.raven_compat.handlers.SentryHandler",
        },
    },
    "loggers": {
        "django": {"level": "WARN", "handlers": ["gunicorn"], "propagate": True},
        "django.security.csrf": {
            "handlers": ["sentry"],
            "level": "WARNING",
            "propagate": True,
        },
        "frontend": {"level": "DEBUG", "handlers": ["gunicorn"], "propagate": True},
        "frontend.signals.handlers": {
            "level": "DEBUG",
            "handlers": ["signals"],
            "propagate": False,
        },
    },
}

# BigQuery project name
BQ_MEASURES_DATASET = "staging_{}".format(BQ_MEASURES_DATASET)

# For grabbing images that we insert into alert emails
GRAB_HOST = "http://staging.openprescribing.net"

GOOGLE_TRACKING_ID = "UA-62480003-3"
GOOGLE_OPTIMIZE_CONTAINER_ID = "GTM-KRQSJM9"

sentry_raven_dsn = utils.get_env_setting("STAGING_SENTRY_RAVEN_DSN", default="")
if sentry_raven_dsn:
    RAVEN_CONFIG = {"dsn": sentry_raven_dsn}

# Base directory for pipeline data
PIPELINE_DATA_BASEDIR = os.path.join(APPS_ROOT, "pipeline", "data")
# Contains monthly data downloaded fom BigQuery and stored as gzipped CSV
# (about 80MB/month)
MATRIXSTORE_IMPORT_DIR = os.path.join(PIPELINE_DATA_BASEDIR, "matrixstore_import")
# Contains MatrixStore SQLite files, each containing 5 years' worth of data at
# about 4GB each
MATRIXSTORE_BUILD_DIR = os.path.join(PIPELINE_DATA_BASEDIR, "matrixstore_build")
# This is expected to be a symlink to a file in MATRIXSTORE_BUILD_DIR
MATRIXSTORE_LIVE_FILE = os.path.join(MATRIXSTORE_BUILD_DIR, "matrixstore_live.sqlite")
