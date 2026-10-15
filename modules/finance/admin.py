from django.contrib import admin

from .models import Account, Budget, ExpenseClaim, JournalEntry, JournalLine, Loan


class JournalLineInline(admin.TabularInline):
    model = JournalLine
    extra = 2


@admin.register(JournalEntry)
class JournalEntryAdmin(admin.ModelAdmin):
    list_display = ("number", "company", "entry_date", "status", "total_debit", "total_credit", "balanced")
    list_filter = ("company", "status", "entry_date")
    readonly_fields = ("total_debit", "total_credit")
    inlines = [JournalLineInline]


for model in [Account, Budget, ExpenseClaim, Loan]:
    admin.site.register(model)

