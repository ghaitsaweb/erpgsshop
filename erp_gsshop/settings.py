import os
import sys
from pathlib import Path

from modules.apphub.discovery import discover_django_apps


BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "unsafe-development-key")
DEBUG = os.getenv("DJANGO_DEBUG", "False").lower() == "true"
ALLOWED_HOSTS = [item.strip() for item in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if item.strip()]
CSRF_TRUSTED_ORIGINS = [
    item.strip()
    for item in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "http://localhost:8010,http://127.0.0.1:8010").split(",")
    if item.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core.apps.CoreConfig",
    "modules.organization.apps.OrganizationConfig",
    "modules.apphub.apps.AppHubConfig",
    "modules.hr.apps.HRConfig",
    "modules.purchase.apps.PurchaseConfig",
    "modules.construction.apps.ConstructionConfig",
    "modules.finance.apps.FinanceConfig",
    "modules.crm.apps.CRMConfig",
    "modules.operations.apps.OperationsConfig",
    "modules.education.apps.EducationConfig",
    "modules.pos.apps.POSConfig",
    "modules.ai_workspace.apps.AIWorkspaceConfig",
    "modules.localization.apps.LocalizationConfig",
    "modules.database_manager.apps.DatabaseManagerConfig",
] + discover_django_apps()

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "modules.database_manager.middleware.DatabaseSelectionMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "modules.apphub.middleware.DeveloperImpersonationMiddleware",
    "modules.organization.middleware.OrganizationContextMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "erp_gsshop.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.navigation",
                "modules.organization.context_processors.organization_context",
            ],
        },
    },
]

WSGI_APPLICATION = "erp_gsshop.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("POSTGRES_DB", "erp_gsshop"),
        "USER": os.getenv("POSTGRES_USER", "erp_gsshop"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", "erp_gsshop"),
        "HOST": os.getenv("DATABASE_HOST", "db"),
        "PORT": os.getenv("DATABASE_PORT", "5432"),
        "CONN_MAX_AGE": 60,
    }
}
DATABASE_ROUTERS = ["modules.database_manager.runtime.SelectedDatabaseRouter"]

ERP_DATABASE_PREFIX = os.getenv("ERP_DATABASE_PREFIX", "erp_gsshop").strip().lower()
ERP_DATABASE_COOKIE = os.getenv("ERP_DATABASE_COOKIE", "erp_gsshop_database")
ERP_DATABASE_BACKUP_DIR = os.getenv("ERP_DATABASE_BACKUP_DIR", str(BASE_DIR / "backups"))
ERP_DATABASE_MANAGER_SESSION_SECONDS = int(os.getenv("ERP_DATABASE_MANAGER_SESSION_SECONDS", "900"))

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "id"
TIME_ZONE = "Asia/Jakarta"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if "test" in sys.argv
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
    },
}
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/1")
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 1800
CELERY_BEAT_SCHEDULE = {
    "finance-due-asset-disposals": {
        "task": "finance.finalize_due_asset_disposals",
        "schedule": 900.0,
    },
    "finance-due-recurring-journals": {
        "task": "finance.run_due_recurring_journals",
        "schedule": 3600.0,
    },
}

SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
