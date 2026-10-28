from django.contrib.auth.models import Group, Permission


APPROVAL_ADMIN = "Approval Administrator"
APPROVAL_MANAGER = "Approval Manager"
APPROVAL_USER = "Approval User"


def ensure_approval_roles(**_kwargs):
    configuration_models = {
        "approvalworkflow",
        "approvalworkflowperimeter",
        "approvalrule",
        "approvalruleconditiongroup",
        "approvalruleconditionline",
        "approvalrulelevel",
        "approvalrulelevelassignee",
        "approvaltimeoutwhitelist",
    }
    runtime_models = {
        "approvalinstance",
        "approvalaction",
        "approvaldelegation",
        "approvaloutbox",
        "approvalnotification",
    }
    permissions = Permission.objects.filter(content_type__app_label="approval_workflow")
    admin, _ = Group.objects.get_or_create(name=APPROVAL_ADMIN)
    manager, _ = Group.objects.get_or_create(name=APPROVAL_MANAGER)
    user, _ = Group.objects.get_or_create(name=APPROVAL_USER)
    admin.permissions.add(*permissions)
    manager.permissions.add(
        *permissions.filter(
            content_type__model__in=runtime_models,
            codename__regex=r"^(view|add|change)_",
        ),
        *permissions.filter(content_type__model__in=configuration_models, codename__startswith="view_"),
    )
    user.permissions.add(
        *permissions.filter(
            content_type__model="approvaldelegation",
            codename__regex=r"^(view|add|change)_",
        )
    )
