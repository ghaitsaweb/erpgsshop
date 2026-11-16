"""Philippines-owned Purchase print metadata adapter."""

from django.utils import timezone

from modules.apphub.access import is_app_enabled
from modules.finance.models import CountryTaxProfile


def prepare_purchase_pdf_context(instance, user):
    """Attach HMX Philippines report metadata when this add-on is installed."""
    if not is_app_enabled("gs-purchase-ph"):
        return instance
    profile = (
        CountryTaxProfile.objects.filter(
            company_id=instance.company_id,
            country_code__iexact="PH",
            active=True,
        )
        .only("registration_number", "configuration")
        .first()
    )
    if profile is None:
        return instance
    configuration = profile.configuration if isinstance(profile.configuration, dict) else {}
    printed_by = user.get_full_name().strip() or user.get_username()
    instance._pdf_localization_metadata = {
        "country_code": "PH",
        "taxpayer_name": instance.company.name,
        "address": instance.company.address,
        "tin": profile.registration_number,
        "software_name": str(configuration.get("software_name") or ""),
        "software_version": str(configuration.get("software_version") or ""),
        "printed_by": printed_by,
        "printed_on": timezone.localtime(timezone.now()).strftime("%d %b %Y %H:%M"),
    }
    return instance
