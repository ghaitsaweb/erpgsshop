APP_SLUG = "erpshop-moduleai-assistant"


def launcher_is_visible(context):
    """Keep the launcher aligned with AppHub access and active ERP scope."""
    request = context.get("request")
    return bool(
        request
        and request.user.is_authenticated
        and context.get("active_company")
        and APP_SLUG in context.get("installed_app_slugs", ())
    )
