import os
from common import utils
from .test import APPS_ROOT

DATABASES = {
    "default": {
        "ENGINE": "django.contrib.gis.db.backends.postgis",
        "NAME": utils.get_env_setting("E2E_DB_NAME"),
        "USER": utils.get_env_setting("DB_USER"),
        "PASSWORD": utils.get_env_setting("DB_PASS"),
        "HOST": utils.get_env_setting("DB_HOST", "127.0.0.1"),
    }
}

PIPELINE_METADATA_DIR = os.path.join(APPS_ROOT, "pipeline", "metadata")

PIPELINE_DATA_BASEDIR = os.path.join(APPS_ROOT, "pipeline", "e2e-test-data", "data", "")

PIPELINE_IMPORT_LOG_PATH = os.path.join(
    APPS_ROOT, "pipeline", "e2e-test-data", "log.json"
)

MATRIXSTORE_IMPORT_DIR = os.path.join(PIPELINE_DATA_BASEDIR, "matrixstore_import")
MATRIXSTORE_BUILD_DIR = os.path.join(PIPELINE_DATA_BASEDIR, "matrixstore_build")
MATRIXSTORE_LIVE_FILE = os.path.join(MATRIXSTORE_BUILD_DIR, "matrixstore_live.sqlite")

SLACK_SENDING_ACTIVE = True

BQ_DEFAULT_TABLE_EXPIRATION_MS = 24 * 60 * 60 * 1000  # 24 hours

# We want to use the real measure definitions, not the test ones!
MEASURE_DEFINITIONS_PATH = os.path.join(APPS_ROOT, "measures", "definitions")

# When building the matrixstore, should we check whether data is in BQ before
# downloading it?  Usually we want to, but because only two months of data are
# uploaded in the end-to-end tests, and because we try to download five years
# of data, we need to disable the checks.
CHECK_DATA_IN_BQ = False
