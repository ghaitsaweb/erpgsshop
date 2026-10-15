from django.core.management.base import BaseCommand

from modules.localization.models import LocalizationPack


PACKS = [
    {"country_code": "ID", "country_name": "Indonesia", "currency": "IDR", "tax_label": "PPN", "language_code": "id"},
    {"country_code": "SG", "country_name": "Singapore", "currency": "SGD", "tax_label": "GST", "language_code": "en"},
    {"country_code": "MY", "country_name": "Malaysia", "currency": "MYR", "tax_label": "SST", "language_code": "ms"},
    {"country_code": "PH", "country_name": "Philippines", "currency": "PHP", "tax_label": "VAT", "language_code": "en"},
    {"country_code": "TH", "country_name": "Thailand", "currency": "THB", "tax_label": "VAT", "language_code": "th"},
    {"country_code": "AU", "country_name": "Australia", "currency": "AUD", "tax_label": "GST", "language_code": "en"},
]


class Command(BaseCommand):
    help = "Synchronize supported country localization packs."

    def handle(self, *args, **options):
        existing = {pack.country_code: pack for pack in LocalizationPack.objects.all()}
        new_packs = []
        changed_packs = []
        for definition in PACKS:
            pack = existing.get(definition["country_code"])
            if pack is None:
                new_packs.append(LocalizationPack(**definition))
                continue
            for field, value in definition.items():
                setattr(pack, field, value)
            changed_packs.append(pack)
        LocalizationPack.objects.bulk_create(new_packs)
        if changed_packs:
            LocalizationPack.objects.bulk_update(changed_packs, ["country_name", "currency", "tax_label", "language_code"])
        self.stdout.write(self.style.SUCCESS(f"Localization packs ready: {len(PACKS)} countries."))

