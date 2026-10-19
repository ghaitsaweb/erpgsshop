from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin

from .forms import OrganizationAccessForm
from .models import Branch, UserOrganizationAccess


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "company", "is_main", "active", "sequence")
    list_filter = ("company", "is_main", "active")
    search_fields = ("code", "name", "company__name")
    autocomplete_fields = ("company", "parent")


class UserOrganizationAccessInline(admin.StackedInline):
    model = UserOrganizationAccess
    form = OrganizationAccessForm
    extra = 1
    max_num = 1
    can_delete = False
    verbose_name = "Company & branch access"
    verbose_name_plural = "Company & branch access"


User = get_user_model()
if admin.site.is_registered(User):
    admin.site.unregister(User)


@admin.register(User)
class OrganizationUserAdmin(UserAdmin):
    inlines = [UserOrganizationAccessInline]
