import csv
import sys
from collections import OrderedDict
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Company, Contact, Product, SaleOrder, SaleOrderLine


def _date(value):
    return date.fromisoformat(value) if value else None


def _decimal(value, default="0"):
    try:
        return Decimal(value or default)
    except InvalidOperation as exc:
        raise CommandError(f"Invalid decimal value: {value}") from exc


class Command(BaseCommand):
    help = "Import an Odoo rental Sales Order CSV from stdin without creating duplicates."

    def add_arguments(self, parser):
        parser.add_argument("--company-code", default="GSS")
        parser.add_argument("--dry-run", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        reader = csv.DictReader(sys.stdin)
        rows = list(reader)
        required = {
            "order_source_id",
            "order_number",
            "partner_source_id",
            "partner_name",
            "line_source_id",
            "product_source_id",
            "product_name",
            "description",
            "quantity",
            "unit_price",
            "rental_start_date",
            "rental_return_date",
            "rental_status",
        }
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise CommandError(f"Missing CSV columns: {', '.join(sorted(missing))}")
        if not rows:
            raise CommandError("The Odoo rental CSV is empty.")

        order_data = OrderedDict((row["order_source_id"], row) for row in rows)
        contact_data = OrderedDict((row["partner_source_id"], row) for row in rows)
        product_data = OrderedDict((row["product_source_id"], row) for row in rows if row["product_source_id"])
        if options["dry_run"]:
            self.stdout.write(
                f"DRY RUN: {len(order_data)} orders, {len(rows)} lines, "
                f"{len(contact_data)} customers, {len(product_data)} products parsed."
            )
            transaction.set_rollback(True)
            return

        try:
            company = Company.objects.get(code=options["company_code"])
        except Company.DoesNotExist as exc:
            raise CommandError(f"Company {options['company_code']} does not exist.") from exc

        contact_codes = {source_id: f"ODOO-C-{source_id}" for source_id in contact_data}
        existing_contacts = {
            item.code: item
            for item in Contact.objects.filter(company=company, code__in=contact_codes.values())
        }
        new_contacts = []
        changed_contacts = []
        for source_id, row in contact_data.items():
            code = contact_codes[source_id]
            values = {
                "name": (row["partner_name"] or f"Odoo Customer {source_id}")[:160],
                "email": row.get("partner_email", "") or "",
                "phone": (row.get("partner_phone", "") or "")[:40],
                "address": row.get("partner_address", "") or "",
                "contact_type": Contact.ContactType.CUSTOMER,
                "active": True,
            }
            contact = existing_contacts.get(code)
            if contact is None:
                new_contacts.append(Contact(company=company, code=code, **values))
            else:
                for field, value in values.items():
                    setattr(contact, field, value)
                changed_contacts.append(contact)
        Contact.objects.bulk_create(new_contacts, batch_size=500)
        if changed_contacts:
            Contact.objects.bulk_update(
                changed_contacts,
                ["name", "email", "phone", "address", "contact_type", "active", "updated_at"],
                batch_size=500,
            )
        contacts = {
            item.code: item
            for item in Contact.objects.filter(company=company, code__in=contact_codes.values())
        }

        product_codes = {source_id: f"ODOO-P-{source_id}" for source_id in product_data}
        existing_products = {
            item.sku: item
            for item in Product.objects.filter(company=company, sku__in=product_codes.values())
        }
        new_products = []
        changed_products = []
        for source_id, row in product_data.items():
            code = product_codes[source_id]
            values = {
                "name": (row["product_name"] or row["description"] or f"Odoo Product {source_id}")[:160],
                "product_type": Product.ProductType.SERVICE,
                "unit": (row.get("unit_name", "") or "Unit")[:24],
                "sale_price": _decimal(row["unit_price"]),
                "active": True,
            }
            product = existing_products.get(code)
            if product is None:
                new_products.append(Product(company=company, sku=code, **values))
            else:
                for field, value in values.items():
                    setattr(product, field, value)
                changed_products.append(product)
        Product.objects.bulk_create(new_products, batch_size=500)
        if changed_products:
            Product.objects.bulk_update(
                changed_products,
                ["name", "product_type", "unit", "sale_price", "active", "updated_at"],
                batch_size=500,
            )
        products = {
            item.sku: item
            for item in Product.objects.filter(company=company, sku__in=product_codes.values())
        }

        source_ids = [int(value) for value in order_data]
        existing_orders = {item.odoo_source_id: item for item in SaleOrder.objects.filter(odoo_source_id__in=source_ids)}
        occupied_numbers = {
            number: source_id
            for number, source_id in SaleOrder.objects.filter(company=company).values_list("number", "odoo_source_id")
        }
        new_orders = []
        changed_orders = []
        status_map = {
            "draft": SaleOrder.Status.DRAFT,
            "sent": SaleOrder.Status.DRAFT,
            "sale": SaleOrder.Status.CONFIRMED,
            "done": SaleOrder.Status.COMPLETED,
            "cancel": SaleOrder.Status.CANCELLED,
        }
        for source_text, row in order_data.items():
            source_id = int(source_text)
            requested_number = (row["order_number"] or f"ODOO-{source_id}")[:40]
            number = requested_number
            if number in occupied_numbers and occupied_numbers[number] not in {None, source_id}:
                number = f"ODOO-{requested_number}"[:40]
            values = {
                "company": company,
                "number": number,
                "customer": contacts[contact_codes[row["partner_source_id"]]],
                "order_date": _date(row.get("order_date", "")) or date.today(),
                "validity_date": _date(row.get("validity_date", "")),
                "customer_reference": (row.get("customer_reference", "") or "")[:120],
                "salesperson_name": (row.get("salesperson_name", "") or "")[:160],
                "price_list": (row.get("price_list", "") or "IDR")[:120],
                "payment_terms": (row.get("payment_terms", "") or "")[:120],
                "notes": row.get("notes", "") or "",
                "status": status_map.get(row.get("order_status", ""), SaleOrder.Status.DRAFT),
            }
            order = existing_orders.get(source_id)
            if order is None:
                new_orders.append(SaleOrder(odoo_source_id=source_id, **values))
            else:
                for field, value in values.items():
                    setattr(order, field, value)
                changed_orders.append(order)
        SaleOrder.objects.bulk_create(new_orders, batch_size=500)
        if changed_orders:
            SaleOrder.objects.bulk_update(
                changed_orders,
                [
                    "company",
                    "number",
                    "customer",
                    "order_date",
                    "validity_date",
                    "customer_reference",
                    "salesperson_name",
                    "price_list",
                    "payment_terms",
                    "notes",
                    "status",
                    "updated_at",
                ],
                batch_size=500,
            )
        orders = {item.odoo_source_id: item for item in SaleOrder.objects.filter(odoo_source_id__in=source_ids)}

        line_source_ids = [int(row["line_source_id"]) for row in rows]
        existing_lines = {item.odoo_source_id: item for item in SaleOrderLine.objects.filter(odoo_source_id__in=line_source_ids)}
        new_lines = []
        changed_lines = []
        totals = {source_id: Decimal("0") for source_id in source_ids}
        rental_status_map = {
            "customer": SaleOrderLine.RentalStatus.AT_CUSTOMER,
            "kembaii": SaleOrderLine.RentalStatus.RETURNED,
        }
        for row in rows:
            order_source_id = int(row["order_source_id"])
            line_source_id = int(row["line_source_id"])
            quantity = _decimal(row["quantity"], "1")
            unit_price = _decimal(row["unit_price"])
            discount = _decimal(row.get("discount_percent", "0"))
            values = {
                "order": orders[order_source_id],
                "product": products[product_codes[row["product_source_id"]]] if row["product_source_id"] else None,
                "display_type": row.get("display_type", "") or "",
                "description": (row["description"] or row["product_name"])[:200],
                "quantity": quantity,
                "unit_price": unit_price,
                "discount_percent": discount,
                "rental_start_date": _date(row["rental_start_date"]),
                "rental_return_date": _date(row["rental_return_date"]),
                "rental_status": rental_status_map.get(row["rental_status"], ""),
            }
            totals[order_source_id] += quantity * unit_price * (Decimal("1") - discount / Decimal("100"))
            line = existing_lines.get(line_source_id)
            if line is None:
                new_lines.append(SaleOrderLine(odoo_source_id=line_source_id, **values))
            else:
                for field, value in values.items():
                    setattr(line, field, value)
                changed_lines.append(line)
        SaleOrderLine.objects.bulk_create(new_lines, batch_size=500)
        if changed_lines:
            SaleOrderLine.objects.bulk_update(
                changed_lines,
                [
                    "order",
                    "product",
                    "display_type",
                    "description",
                    "quantity",
                    "unit_price",
                    "discount_percent",
                    "rental_start_date",
                    "rental_return_date",
                    "rental_status",
                    "updated_at",
                ],
                batch_size=500,
            )
        orders_to_total = list(orders.values())
        for order in orders_to_total:
            order.total = totals[order.odoo_source_id]
        SaleOrder.objects.bulk_update(orders_to_total, ["total", "updated_at"], batch_size=500)

        self.stdout.write(
            self.style.SUCCESS(
                f"Odoo rental import ready: {len(orders)} orders, {len(rows)} lines, "
                f"{len(contacts)} customers, {len(products)} products."
            )
        )
