import os
import random

from common import utils
from .base import (
    TEMPLATES,
    REPO_ROOT,
    APPS_ROOT,
    BQ_SCMD_DATASET,
    BQ_PUBLIC_DATASET,
    BQ_PRESCRIBING_EXPORT_DATASET,
    BQ_ARCHIVE_DATASET,
    BQ_DMD_DATASET,
    BQ_TMP_EU_DATASET,
    BQ_MEASURES_DATASET,
    BQ_HSCIC_DATASET,
)

DEBUG = True
TEMPLATES[0]["OPTIONS"]["debug"] = DEBUG
DATABASES = {
    "default": {
        "ENGINE": "django.contrib.gis.db.backends.postgis",
        "NAME": utils.get_env_setting("DB_NAME"),
        "USER": utils.get_env_setting("DB_USER"),
        "PASSWORD": utils.get_env_setting("DB_PASS"),
        "HOST": utils.get_env_setting("DB_HOST", "127.0.0.1"),
    }
}
INTERNAL_IPS = ("127.0.0.1",)

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

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
    "handlers": {
        "test-file": {
            "level": "INFO",
            "class": "logging.handlers.RotatingFileHandler",
            "formatter": "verbose",
            "filename": "%s/logs/test.log" % REPO_ROOT,
            "maxBytes": 1024 * 1024 * 100,  # 100 mb
        }
    },
    "loggers": {
        "django": {"handlers": ["test-file"], "level": "DEBUG", "propagate": True},
        "frontend": {"handlers": ["test-file"], "level": "DEBUG", "propagate": True},
    },
}

# BigQuery project name
BQ_PROJECT = "ebmdatalabtest"

# Nonce to ensure test runs do not clash
BQ_NONCE = int(utils.get_env_setting("BQ_NONCE", random.randrange(10000)))
print("BQ_NONCE:", BQ_NONCE)

# BigQuery dataset names
BQ_HSCIC_DATASET = "{}_{:04d}".format(BQ_HSCIC_DATASET, BQ_NONCE)
BQ_MEASURES_DATASET = "{}_{:04d}".format(BQ_MEASURES_DATASET, BQ_NONCE)
BQ_TMP_EU_DATASET = "{}_{:04d}".format(BQ_TMP_EU_DATASET, BQ_NONCE)
BQ_DMD_DATASET = "{}_{:04d}".format(BQ_DMD_DATASET, BQ_NONCE)
BQ_ARCHIVE_DATASET = "{}_{:04d}".format(BQ_ARCHIVE_DATASET, BQ_NONCE)
BQ_PRESCRIBING_EXPORT_DATASET = "{}_{:04d}".format(
    BQ_PRESCRIBING_EXPORT_DATASET, BQ_NONCE
)
BQ_PUBLIC_DATASET = "{}_{:04d}".format(BQ_PUBLIC_DATASET, BQ_NONCE)
BQ_SCMD_DATASET = "{}_{:04d}".format(BQ_SCMD_DATASET, BQ_NONCE)
BQ_TEST_DATASET = "test_{:04d}".format(BQ_NONCE)

# Other BQ settings
BQ_DEFAULT_TABLE_EXPIRATION_MS = 3 * 60 * 60 * 1000  # 3 hours

# For grabbing images that we insert into alert emails
GRAB_HOST = "http://localhost"

# This is the same as the dev/local one
GOOGLE_TRACKING_ID = "UA-62480003-2"

# Base directory for pipeline metadata
PIPELINE_METADATA_DIR = os.path.join(APPS_ROOT, "pipeline", "test-data", "metadata")

# Base directory for pipeline data
PIPELINE_DATA_BASEDIR = os.path.join(APPS_ROOT, "pipeline", "test-data", "data")

# Path to import log for pipeline data
PIPELINE_IMPORT_LOG_PATH = os.path.join(APPS_ROOT, "pipeline", "test-data", "log.json")

# Contains monthly data downloaded fom BigQuery and stored as gzipped CSV
# (about 80MB/month)
MATRIXSTORE_IMPORT_DIR = os.path.join(PIPELINE_DATA_BASEDIR, "matrixstore_import")
# Contains MatrixStore SQLite files, each containing 5 years' worth of data at
# about 4GB each
MATRIXSTORE_BUILD_DIR = os.path.join(PIPELINE_DATA_BASEDIR, "matrixstore_build")
# This is expected to be a symlink to a file in MATRIXSTORE_BUILD_DIR
MATRIXSTORE_LIVE_FILE = os.path.join(MATRIXSTORE_BUILD_DIR, "matrixstore_live.sqlite")

SLACK_SENDING_ACTIVE = False

# Running with a different storage backend in test is not ideal but it's what
# the Django docs recommend:
# https://docs.djangoproject.com/en/1.11/ref/contrib/staticfiles/#django.contrib.staticfiles.storage.ManifestStaticFilesStorage.manifest_strict
STATICFILES_STORAGE = "django.contrib.staticfiles.storage.StaticFilesStorage"

# Path of directory containing measure definitions.
MEASURE_DEFINITIONS_PATH = os.path.join(
    APPS_ROOT, "frontend", "tests", "measure_definitions"
)
