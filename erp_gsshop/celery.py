import os

from celery import Celery


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "erp_gsshop.settings")

app = Celery("erp_gsshop")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

