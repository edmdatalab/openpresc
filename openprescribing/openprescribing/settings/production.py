"""Production settings and globals."""

import logging
import os
import time
from pathlib import Path

from common import utils

from .base import APPS_ROOT, REPO_ROOT, ANYMAIL, LOCAL_APPS, SITE_NAME

# HOST CONFIGURATION
# See:
# https://docs.djangoproject.com/en/1.5/releases/1.5/#allowed-hosts-required-in-production
ALLOWED_HOSTS = [
    "openprescribing.net",
    "deploy.openprescribing.net",
    "openprescriptions.net",
]
# END HOST CONFIGURATION

# See: https://docs.djangoproject.com/en/dev/ref/settings/#email-subject-prefix
EMAIL_SUBJECT_PREFIX = "[%s] " % SITE_NAME

# END EMAIL CONFIGURATION

# DATABASE CONFIGURATION
DATABASES = {
    "default": {
        "ENGINE": "django.contrib.gis.db.backends.postgis",
        "NAME": utils.get_env_setting("DB_NAME"),
        "USER": utils.get_env_setting("DB_USER"),
        "PASSWORD": utils.get_env_setting("DB_PASS"),
        "HOST": utils.get_env_setting("DB_HOST", "127.0.0.1"),
        "CONN_MAX_AGE": 0,
    },
    "old": {
        "ENGINE": "django.contrib.gis.db.backends.postgis",
        "NAME": utils.get_env_setting("DB_NAME"),
        "USER": utils.get_env_setting("DB_USER"),
        "PASSWORD": utils.get_env_setting("DB_PASS"),
        "HOST": utils.get_env_setting("DB_HOST", "138.68.140.164"),
    },
}
# END DATABASE CONFIGURATION


GOOGLE_TRACKING_ID = "UA-62480003-1"
GOOGLE_OPTIMIZE_CONTAINER_ID = "GTM-5PX77GZ"

ANYMAIL["MAILGUN_SENDER_DOMAIN"] = "openprescribing.net"

# This causes logging to be in UTC everywhere.  See
# https://stackoverflow.com/a/26453979
logging.Formatter.converter = time.gmtime
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": (
                "%(asctime)s %(levelname)s [%(name)s:%(lineno)s] "
                "%(module)s %(process)d %(thread)d %(message)s"
            )
        }
    },
    "filters": {"require_debug_false": {"()": "django.utils.log.RequireDebugFalse"}},
    "handlers": {
        "gunicorn": {
            "level": "DEBUG",
            "class": "logging.handlers.RotatingFileHandler",
            "formatter": "verbose",
            "filename": "%s/logs/django.log" % REPO_ROOT,
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
        "django": {"handlers": ["gunicorn"], "level": "WARN", "propagate": True},
        "django.security.csrf": {
            "handlers": ["sentry"],
            "level": "WARNING",
            "propagate": True,
        },
        "frontend.signals.handlers": {
            "level": "DEBUG",
            "handlers": ["signals"],
            "propagate": False,
        },
    },
}


# Log everything produced by our apps at INFO or above
for app in LOCAL_APPS:
    LOGGING["loggers"][app] = {
        "level": "INFO",
        "handlers": ["gunicorn"],
        "propagate": True,
    }

# Base directory for pipeline metadata
PIPELINE_METADATA_DIR = os.path.join(APPS_ROOT, "pipeline", "metadata")

# Base directory for pipeline data
PIPELINE_DATA_BASEDIR = "/home/hello/openprescribing-data/data/"

# Path to import log for pipeline data
PIPELINE_IMPORT_LOG_PATH = "/home/hello/openprescribing-data/log.json"

# Contains monthly data downloaded fom BigQuery and stored as gzipped CSV
# (about 80MB/month)
MATRIXSTORE_IMPORT_DIR = os.path.join(PIPELINE_DATA_BASEDIR, "matrixstore_import")
# Contains MatrixStore SQLite files, each containing 5 years' worth of data at
# about 4GB each
MATRIXSTORE_BUILD_DIR = "/mnt/database/matrixstore"
# This is expected to be a symlink to a file in MATRIXSTORE_BUILD_DIR
MATRIXSTORE_LIVE_FILE = os.path.join(MATRIXSTORE_BUILD_DIR, "matrixstore_live.sqlite")

# This is where we put outliers data
OUTLIERS_DATA_DIR = Path(os.path.join(PIPELINE_DATA_BASEDIR, "outliers"))
