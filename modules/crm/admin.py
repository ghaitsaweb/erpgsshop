from django.contrib import admin

from .models import CRMActivity, Lead, Opportunity


for model in [Lead, Opportunity, CRMActivity]:
    admin.site.register(model)

