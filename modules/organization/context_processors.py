def organization_context(request):
    user = getattr(request, "user", None)
    can_use_developer_mode = bool(
        user
        and user.is_authenticated
        and user.is_staff
        and user.has_perm("auth.view_permission")
    )
    return {
        "active_company": getattr(request, "active_company", None),
        "active_branch": getattr(request, "active_branch", None),
        "active_companies": getattr(request, "active_companies", []),
        "active_branches": getattr(request, "active_branches", []),
        "organization_context": getattr(request, "organization_context", None),
        "can_use_developer_mode": can_use_developer_mode,
        "developer_mode": can_use_developer_mode and bool(request.session.get("developer_mode")),
    }
