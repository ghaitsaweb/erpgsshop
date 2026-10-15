from django.contrib import admin

from .models import DocumentSequence, LocalizationPack, PayrollParameter, TaxRule


class TaxRuleInline(admin.TabularInline):
    model = TaxRule
    extra = 1


class PayrollParameterInline(admin.TabularInline):
    model = PayrollParameter
    extra = 1


@admin.register(LocalizationPack)
class LocalizationPackAdmin(admin.ModelAdmin):
    list_display = ("country_code", "country_name", "currency", "tax_label", "version", "enabled")
    inlines = [TaxRuleInline, PayrollParameterInline]


admin.site.register(DocumentSequence)

