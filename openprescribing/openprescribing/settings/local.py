"""Development settings and globals."""

import os
from pathlib import Path

from common import utils

from .base import APPS_ROOT, TEMPLATES, REPO_ROOT, INSTALLED_APPS

# DEBUG CONFIGURATION
# See: https://docs.djangoproject.com/en/dev/ref/settings/#debug
DEBUG = True
TEMPLATES[0]["OPTIONS"]["debug"] = DEBUG
# END DEBUG CONFIGURATION

# SITE CONFIGURATION
# Hosts/domain names that are valid for this site
# See https://docs.djangoproject.com/en/1.5/ref/settings/#allowed-hosts
ALLOWED_HOSTS = ["*"]
# END SITE CONFIGURATION


# EMAIL CONFIGURATION
# See: https://docs.djangoproject.com/en/dev/ref/settings/#email-backend
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
# END EMAIL CONFIGURATION


# DATABASE CONFIGURATION
# See: https://docs.djangoproject.com/en/dev/ref/settings/#databases
DATABASES = {
    "default": {
        "ENGINE": "django.contrib.gis.db.backends.postgis",
        "NAME": utils.get_env_setting("DB_NAME"),
        "USER": utils.get_env_setting("DB_USER"),
        "PASSWORD": utils.get_env_setting("DB_PASS"),
        "HOST": utils.get_env_setting("DB_HOST", "127.0.0.1"),
    }
}
# END DATABASE CONFIGURATION

INSTALLED_APPS += ("django_extensions",)

# TOOLBAR CONFIGURATION
# See: http://django-debug-toolbar.readthedocs.org/en/latest/installation.html
# DEBUG_TOOLBAR_PANELS = [
#     'debug_toolbar.panels.versions.VersionsPanel',
#     'debug_toolbar.panels.timer.TimerPanel',
#     'debug_toolbar.panels.profiling.ProfilingPanel',
#     'debug_toolbar.panels.settings.SettingsPanel',
#     'debug_toolbar.panels.headers.HeadersPanel',
#     'debug_toolbar.panels.request.RequestPanel',
#     'debug_toolbar.panels.sql.SQLPanel',
#     'debug_toolbar.panels.templates.TemplatesPanel',
#     'debug_toolbar.panels.staticfiles.StaticFilesPanel',
#     'debug_toolbar.panels.cache.CachePanel',
#     'debug_toolbar.panels.signals.SignalsPanel',
#     'debug_toolbar.panels.logging.LoggingPanel',
#     'debug_toolbar.panels.redirects.RedirectsPanel',
#     'template_timings_panel.panels.TemplateTimings.TemplateTimings',
# ]

# INSTALLED_APPS += (
#     'debug_toolbar.apps.DebugToolbarConfig',
# )

# MIDDLEWARE_CLASSES = ('debug_toolbar.middleware.DebugToolbarMiddleware',
# ) + MIDDLEWARE_CLASSES

# DEBUG_TOOLBAR_PATCH_SETTINGS = False

# http://django-debug-toolbar.readthedocs.org/en/latest/installation.html
INTERNAL_IPS = ("127.0.0.1",)
# END TOOLBAR CONFIGURATION

GOOGLE_TRACKING_ID = "UA-62480003-2"

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
    },
    "loggers": {
        "django": {"level": "WARN", "handlers": ["gunicorn"], "propagate": True},
        "frontend": {"level": "DEBUG", "handlers": ["gunicorn"], "propagate": True},
        "frontend.signals.handlers": {
            "level": "DEBUG",
            "handlers": ["signals"],
            "propagate": False,
        },
    },
}

# Base directory for pipeline metadata
PIPELINE_METADATA_DIR = os.path.join(APPS_ROOT, "pipeline", "metadata")

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

# This is where we put outliers data
OUTLIERS_DATA_DIR = Path(os.path.join(PIPELINE_DATA_BASEDIR, "outliers"))

# Don't send messages to Slack
SLACK_SENDING_ACTIVE = False
