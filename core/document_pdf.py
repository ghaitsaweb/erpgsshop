"""Server-side PDF reports for ERP GSSHOP documents.

The Sales Order arrangement follows the familiar business structure used by
Odoo (company/address, document metadata, lines, totals, and terms), while the
implementation and visual identity are native to ERP GSSHOP.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from html import escape
from io import BytesIO
import json

from django.utils.formats import date_format
from django.utils.html import strip_tags
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


BRAND = colors.HexColor("#14324A")
ACCENT = colors.HexColor("#F0A11A")
INK = colors.HexColor("#17212B")
MUTED = colors.HexColor("#64748B")
LINE = colors.HexColor("#D8E0E7")
SOFT = colors.HexColor("#F4F7F9")
WHITE = colors.white
WATERMARK_BLUE = colors.HexColor("#2563EB")

PRINT_COMPANY = (
    "GS ALKES Semarang",
    "Klampisan, Ngaliyan, Semarang",
    "089509173788",
    "yusuffkurniawan91@gmail.com",
)

SALES_BANK_DETAILS = (
    "Transfer BCA Saja",
    "Atas Nama: Yusuf Kurniawan",
    "No. Rekening: 7961080120",
)

FIELD_LABELS = {
    "active": "Aktif",
    "actual_cost": "Biaya Aktual",
    "agent": "Agen",
    "assignee": "Penanggung Jawab",
    "budget": "Anggaran",
    "code": "Kode",
    "content": "Isi",
    "contract_value": "Nilai Kontrak",
    "credit": "Kredit",
    "customer_reference": "Referensi Pelanggan",
    "debit": "Debit",
    "description": "Keterangan",
    "discount_percent": "Diskon (%)",
    "due_date": "Tanggal Jatuh Tempo",
    "entry_date": "Tanggal Jurnal",
    "end_date": "Tanggal Selesai",
    "invoice_date": "Tanggal Faktur",
    "invoice_type": "Jenis Faktur",
    "instructor": "Pengajar",
    "last_activity_at": "Aktivitas Terakhir",
    "manager": "Manajer",
    "order_date": "Tanggal Pesanan",
    "order_type": "Jenis Pesanan",
    "output_quantity": "Jumlah Hasil",
    "paid_amount": "Jumlah Dibayar",
    "payment_method": "Metode Pembayaran",
    "payment_terms": "Syarat Pembayaran",
    "price_list": "Daftar Harga",
    "product": "Produk",
    "progress": "Kemajuan",
    "published": "Diterbitkan",
    "reference": "Referensi",
    "restaurant_table": "Meja Restoran",
    "salesperson_name": "Tenaga Penjual",
    "school": "Sekolah",
    "sequence": "Urutan",
    "session": "Sesi",
    "site_address": "Alamat Proyek",
    "start_date": "Tanggal Mulai",
    "status": "Status",
    "title": "Judul",
    "user": "Pengguna",
    "validity_date": "Berlaku Hingga",
}

DISPLAY_TRANSLATIONS = {
    "Active": "Aktif",
    "Archived": "Diarsipkan",
    "Assistant": "Asisten",
    "Blocked": "Terhambat",
    "Cancelled": "Dibatalkan",
    "Completed": "Selesai",
    "Confirmed": "Dikonfirmasi",
    "Customer Invoice": "Faktur Pelanggan",
    "Default": "Standar",
    "Delivery": "Pengiriman",
    "Dine In": "Makan di Tempat",
    "Done": "Selesai",
    "Draft": "Rancangan",
    "In Progress": "Sedang Berjalan",
    "On Hold": "Ditunda",
    "Open": "Terbuka",
    "Paid": "Lunas",
    "Planning": "Perencanaan",
    "Posted": "Dibukukan",
    "Refunded": "Dikembalikan",
    "Retail": "Ritel",
    "Reversed": "Dibalik",
    "System": "Sistem",
    "Takeaway": "Bawa Pulang",
    "To Do": "Belum Dikerjakan",
    "Tool": "Alat",
    "User": "Pengguna",
    "Vendor Bill": "Tagihan Pemasok",
}

TOTAL_LABELS = {
    "Actual Cost": "Biaya Aktual",
    "Balance": "Sisa",
    "Contract Value": "Nilai Kontrak",
    "Credit": "Kredit",
    "Debit": "Debit",
    "Paid": "Dibayar",
    "Progress": "Kemajuan",
    "Total": "Total",
}


REPORT_CONFIG = {
    "sales": {
        "title": "PESANAN",
        "draft_title": "PENAWARAN",
        "reference": "number",
        "company_path": "company",
        "partner_path": "customer",
        "select_related": ("company", "customer"),
        "line_select_related": ("product",),
        "columns": (
            ("rental_start_date", "Tgl Sewa", 1.15),
            ("rental_return_date", "Tgl Kembali", 1.15),
            ("description", "Keterangan", 2.35),
            ("quantity", "Jumlah", 0.9),
            ("unit_price", "Harga Satuan", 1.0),
            ("discount_percent", "Diskon", 0.7),
            ("subtotal", "Nilai", 1.1),
        ),
    },
    "purchases": {
        "title": "PESANAN PEMBELIAN",
        "draft_title": "PERMINTAAN PENAWARAN",
        "reference": "number",
        "company_path": "company",
        "partner_path": "vendor",
        "select_related": ("company", "vendor"),
        "line_select_related": ("product",),
        "columns": (
            ("product", "Produk", 2.2),
            ("description", "Keterangan", 2.7),
            ("quantity", "Jumlah", 1.0),
            ("unit_price", "Harga Satuan", 1.2),
            ("subtotal", "Nilai", 1.2),
        ),
    },
    "invoices": {
        "title": "FAKTUR",
        "reference": "number",
        "company_path": "company",
        "partner_path": "partner",
        "select_related": ("company", "partner"),
        "line_select_related": ("product",),
        "columns": (
            ("product", "Produk", 2.0),
            ("description", "Keterangan", 2.8),
            ("quantity", "Jumlah", 1.0),
            ("unit_price", "Harga Satuan", 1.2),
            ("subtotal", "Nilai", 1.2),
        ),
    },
    "journal-entries": {
        "title": "ENTRI JURNAL",
        "reference": "number",
        "company_path": "company",
        "select_related": ("company",),
        "line_select_related": ("account",),
        "columns": (
            ("account", "Akun", 2.2),
            ("description", "Keterangan", 3.4),
            ("debit", "Debit", 1.3),
            ("credit", "Credit", 1.3),
        ),
    },
    "bank-statements": {
        "title": "BANK STATEMENT",
        "reference": "reference",
        "company_path": "company",
        "select_related": ("company", "bank_account", "bank_account__journal"),
        "line_select_related": ("matched_journal_line",),
        "columns": (
            ("transaction_date", "Date", 1.0),
            ("reference", "Reference", 1.2),
            ("partner_name", "Partner", 1.5),
            ("description", "Description", 2.8),
            ("amount", "Amount", 1.1),
            ("reconciled", "Reconciled", 0.9),
        ),
    },
    "boms": {
        "title": "DAFTAR BAHAN",
        "reference": "code",
        "company_path": "company",
        "select_related": ("company", "product"),
        "line_select_related": ("component", "warehouse"),
        "columns": (
            ("component", "Komponen", 3.7),
            ("quantity", "Jumlah", 1.4),
            ("warehouse", "Gudang", 2.5),
        ),
    },
    "pos-orders": {
        "title": "PESANAN POS",
        "reference": "number",
        "company_path": "session.register.company",
        "partner_path": "customer",
        "select_related": ("session__register__company", "customer", "restaurant_table"),
        "line_select_related": ("product",),
        "columns": (
            ("product", "Produk", 3.1),
            ("notes", "Catatan", 2.0),
            ("quantity", "Jumlah", 1.0),
            ("unit_price", "Harga Satuan", 1.2),
            ("subtotal", "Nilai", 1.2),
        ),
    },
    "projects": {
        "title": "RINGKASAN PROYEK",
        "reference": "code",
        "company_path": "company",
        "partner_path": "customer",
        "select_related": ("company", "customer", "manager"),
        "line_select_related": ("assignee",),
        "columns": (
            ("title", "Tugas", 2.8),
            ("assignee", "Penanggung Jawab", 1.5),
            ("due_date", "Jatuh Tempo", 1.2),
            ("status", "Status", 1.1),
            ("progress", "Kemajuan", 0.9),
            ("description", "Keterangan", 2.0),
        ),
    },
    "construction-projects": {
        "title": "PROYEK KONSTRUKSI",
        "reference": "code",
        "company_path": "company",
        "partner_path": "customer",
        "select_related": ("company", "customer"),
        "line_select_related": (),
        "columns": (
            ("code", "Kode", 1.0),
            ("name", "Paket Pekerjaan", 2.7),
            ("budget", "Anggaran", 1.3),
            ("actual_cost", "Biaya Aktual", 1.3),
            ("progress", "Kemajuan", 0.9),
            ("status", "Status", 1.1),
        ),
    },
    "courses": {
        "title": "SILABUS KURSUS",
        "reference": "code",
        "company_path": "school.company",
        "select_related": ("school__company", "instructor"),
        "line_select_related": (),
        "columns": (
            ("sequence", "Urutan", 0.9),
            ("title", "Pelajaran", 2.3),
            ("content", "Isi", 4.1),
            ("published", "Diterbitkan", 1.0),
        ),
    },
    "ai-conversations": {
        "title": "PERCAKAPAN AI",
        "reference": "title",
        "company_path": "agent.workspace.company",
        "select_related": ("agent__workspace__company", "user"),
        "line_select_related": (),
        "columns": (
            ("role", "Peran", 1.1),
            ("content", "Pesan", 5.7),
            ("created_at", "Waktu", 1.4),
        ),
    },
}

REPORT_CONFIG.update(
    {
        "journal-entry-templates": {
            "title": "JOURNAL ENTRY TEMPLATE", "reference": "code", "company_path": "company",
            "select_related": ("company", "journal"), "line_select_related": ("account", "cost_center"),
            "columns": (("sequence", "Seq", 0.7), ("account", "Account", 2.3), ("side", "Side", 0.9), ("amount", "Amount", 1.2), ("description", "Description", 2.8), ("cost_center", "Cost Center", 1.3)),
        },
        "other-income-expenses": {
            "title": "OTHER INCOME / EXPENSE", "reference": "number", "company_path": "company", "partner_path": "partner",
            "select_related": ("company", "partner", "journal"), "line_select_related": ("account", "tax", "cost_center"),
            "columns": (("account", "Account", 2.0), ("description", "Description", 3.0), ("amount", "Amount", 1.2), ("tax", "Tax", 1.1), ("cost_center", "Cost Center", 1.3)),
        },
        "payment-vouchers": {
            "title": "PAYMENT / RECEIPT VOUCHER", "reference": "number", "company_path": "company", "partner_path": "partner",
            "select_related": ("company", "partner", "journal", "payment_method"), "line_select_related": ("account", "partner", "cost_center"),
            "columns": (("account", "Account", 2.0), ("description", "Description", 2.6), ("partner", "Partner", 1.6), ("debit", "Debit", 1.1), ("credit", "Credit", 1.1), ("cost_center", "Cost Center", 1.2)),
        },
        "multi-payment-batches": {
            "title": "MULTI RECEIPT / PAYMENT", "reference": "number", "company_path": "company", "partner_path": "partner",
            "select_related": ("company", "partner", "journal", "payment_method"), "line_select_related": ("invoice",),
            "columns": (("invoice", "Invoice / Bill", 2.2), ("due_amount", "Due", 1.1), ("amount", "Paid", 1.1), ("discount_amount", "Discount", 1.0), ("withholding_amount", "Withholding", 1.1), ("reference", "Reference", 1.5)),
        },
        "withholding-tax-transactions": {
            "title": "WITHHOLDING TAX", "reference": "number", "company_path": "company", "partner_path": "partner",
            "select_related": ("company", "partner", "withholding_tax"), "line_select_related": ("invoice",),
            "columns": (("invoice", "Invoice / Bill", 2.1), ("description", "Description", 3.0), ("tax_base", "Tax Base", 1.3), ("tax_amount", "Tax Amount", 1.3)),
        },
        "closing-entries": {
            "title": "CLOSING / OPENING ENTRY", "reference": "number", "company_path": "company",
            "select_related": ("company", "fiscal_period", "journal"), "line_select_related": ("account", "partner"),
            "columns": (("account", "Account", 2.2), ("partner", "Partner", 1.7), ("description", "Description", 2.8), ("debit", "Debit", 1.2), ("credit", "Credit", 1.2)),
        },
        "company-account-mappings": {
            "title": "COMPANY ACCOUNT MAPPING", "reference": "name", "company_path": "parent_company",
            "select_related": ("parent_company", "subsidiary_company", "elimination_journal"),
            "line_select_related": ("subsidiary_account", "parent_account", "elimination_account"),
            "columns": (("subsidiary_account", "Subsidiary Account", 2.7), ("parent_account", "Parent Account", 2.7), ("elimination_account", "Elimination Account", 2.7), ("active", "Active", 0.8)),
        },
        "currency-revaluations": {
            "title": "CURRENCY REVALUATION", "reference": "number", "company_path": "company",
            "select_related": ("company",), "line_select_related": ("account", "partner"),
            "columns": (("account", "Account", 2.0), ("partner", "Partner", 1.7), ("original_amount", "Original", 1.1), ("base_amount", "Base", 1.1), ("revalued_amount", "Revalued", 1.1), ("gain_loss_amount", "Gain / Loss", 1.2)),
        },
        "petty-cash-vouchers": {
            "title": "PETTY CASH VOUCHER", "reference": "number", "company_path": "fund.company",
            "select_related": ("fund__company", "fund__custodian", "fund__journal"),
            "line_select_related": ("category", "cost_center"),
            "columns": (("category", "Category", 1.8), ("description", "Description", 3.5), ("amount", "Amount", 1.3), ("cost_center", "Cost Center", 1.4)),
        },
    }
)

REPORT_CONFIG.update(
    {
        "account-deposit-conversions": {
            "title": "DEPOSIT CONVERSION", "reference": "number", "company_path": "deposit.company",
            "partner_path": "deposit.partner", "select_related": ("deposit__company", "deposit__partner"),
            "line_select_related": ("invoice",),
            "columns": (("invoice", "Invoice", 4.8), ("amount", "Amount", 2.0)),
        },
        "bank-cash-valuations": {
            "title": "BANK / CASH VALUATION", "reference": "number", "company_path": "company",
            "select_related": ("company",), "line_select_related": ("bank_account",),
            "columns": (("bank_account", "Bank Account", 2.2), ("foreign_balance", "Foreign Balance", 1.4),
                        ("book_value", "Book Value", 1.4), ("revalued_value", "Revalued", 1.4),
                        ("gain_loss", "Gain / Loss", 1.3)),
        },
        "ar-reconciliation-sessions": {
            "title": "AR RECONCILIATION", "reference": "reference", "company_path": "company",
            "partner_path": "partner", "select_related": ("company", "partner", "actor"),
            "line_select_related": ("journal_line__entry", "journal_line__account"),
            "columns": (("journal_line", "Journal Item", 5.0), ("amount", "Amount", 2.0)),
        },
        "sale-order-localizations": {
            "title": "LOCALIZED SALES TAX", "reference": "order.number", "company_path": "order.company",
            "partner_path": "order.customer", "select_related": ("order__company", "order__customer", "profile"),
            "line_select_related": ("order_line__product", "tax_code"),
            "columns": (("order_line", "Order Line", 2.6), ("tax_code", "Tax", 1.7),
                        ("tax_base", "Tax Base", 1.2), ("tax_amount", "Tax", 1.1),
                        ("gross_amount", "Gross", 1.2)),
        },
        "budget-change-requests": {
            "title": "BUDGET CHANGE REQUEST", "reference": "number", "company_path": "company",
            "select_related": ("company", "requested_by", "approved_by"),
            "line_select_related": ("budget", "budget_account"),
            "columns": (("budget", "Budget", 2.4), ("budget_account", "Budget Account", 2.4),
                        ("amount", "Amount", 1.2), ("description", "Description", 2.0)),
        },
        "budget-transfers": {
            "title": "BUDGET TRANSFER", "reference": "number", "company_path": "company",
            "select_related": ("company", "requested_by", "approved_by"),
            "line_select_related": ("source_budget", "destination_budget", "budget_account"),
            "columns": (("source_budget", "Source", 2.0), ("destination_budget", "Destination", 2.0),
                        ("budget_account", "Budget Account", 2.0), ("amount", "Amount", 1.2)),
        },
        "budget-carry-overs": {
            "title": "BUDGET CARRY OVER", "reference": "number", "company_path": "company",
            "select_related": ("company", "requested_by", "approved_by"),
            "line_select_related": ("source_budget", "destination_budget", "budget_account"),
            "columns": (("source_budget", "Source", 2.0), ("destination_budget", "Destination", 2.0),
                        ("budget_account", "Budget Account", 2.0), ("amount", "Amount", 1.2)),
        },
    }
)


REPORT_CONFIG.update(
    {
        "purchase-requests": {
            "title": "PURCHASE REQUEST",
            "reference": "number",
            "company_path": "company",
            "select_related": ("company", "requestor"),
            "line_select_related": ("product",),
            "columns": (
                ("product", "Product", 1.6),
                ("description", "Description", 2.2),
                ("specification", "Specification", 2.0),
                ("quantity", "Requested", 0.8),
                ("approved_quantity", "Approved", 0.8),
                ("ordered_quantity", "Ordered", 0.8),
                ("estimated_unit_price", "Est. Price", 1.0),
            ),
        },
        "purchase-quotations": {
            "title": "REQUEST FOR QUOTATION",
            "reference": "number",
            "company_path": "company",
            "partner_path": "vendor",
            "select_related": ("company", "request", "vendor"),
            "line_select_related": ("request_line", "product"),
            "columns": (
                ("product", "Product", 1.5),
                ("description", "Description", 2.0),
                ("quantity", "Qty", 0.7),
                ("unit_price", "Unit Price", 1.0),
                ("discount_percent", "Discount", 0.8),
                ("tax_percent", "Tax", 0.7),
                ("promised_date", "Promise Date", 1.0),
                ("subtotal", "Subtotal", 1.1),
            ),
        },
        "direct-purchases": {
            "title": "DIRECT PURCHASE",
            "reference": "number",
            "company_path": "company",
            "partner_path": "vendor",
            "select_related": ("company", "vendor"),
            "line_select_related": ("product",),
            "columns": (
                ("product", "Product", 1.8),
                ("description", "Description", 2.4),
                ("quantity", "Qty", 0.8),
                ("unit_price", "Unit Price", 1.1),
                ("tax_percent", "Tax", 0.8),
                ("cost_center", "Cost Center", 1.1),
                ("subtotal", "Subtotal", 1.2),
            ),
        },
        "service-work-orders": {
            "title": "SERVICE WORK ORDER",
            "reference": "number",
            "company_path": "company",
            "partner_path": "vendor",
            "select_related": ("company", "vendor"),
            "line_select_related": (),
            "columns": (
                ("description", "Service", 2.4),
                ("milestone_name", "Milestone", 1.5),
                ("unit", "Unit", 0.8),
                ("quantity", "Qty", 0.8),
                ("unit_rate", "Rate", 1.1),
                ("tax_percent", "Tax", 0.8),
                ("cost_center", "Cost Center", 1.1),
                ("subtotal", "Subtotal", 1.2),
            ),
        },
        "purchase-tenders": {
            "title": "PURCHASE TENDER",
            "reference": "number",
            "company_path": "company",
            "select_related": ("company", "award_strategy"),
            "line_select_related": ("request_line", "product"),
            "columns": (
                ("product", "Product", 1.7),
                ("description", "Scope", 2.3),
                ("specification", "Specification", 2.2),
                ("quantity", "Qty", 0.8),
                ("target_unit_price", "Target Price", 1.1),
                ("delivery_date", "Delivery", 1.0),
            ),
        },
        "blanket-orders": {
            "title": "BLANKET PURCHASE AGREEMENT",
            "reference": "number",
            "company_path": "company",
            "partner_path": "vendor",
            "select_related": ("company", "vendor"),
            "line_select_related": ("product",),
            "columns": (
                ("product", "Product", 2.0),
                ("description", "Description", 2.1),
                ("agreed_quantity", "Agreed Qty", 1.0),
                ("released_quantity", "Released", 1.0),
                ("minimum_release_quantity", "Min. Release", 1.0),
                ("unit_price", "Unit Price", 1.1),
            ),
        },
        "goods-receipts": {
            "title": "GOODS RECEIPT",
            "reference": "number",
            "company_path": "company",
            "partner_path": "order.vendor",
            "select_related": ("company", "order__vendor", "warehouse"),
            "line_select_related": ("order_line__product",),
            "columns": (
                ("order_line", "Purchase Order Line", 3.2),
                ("received_quantity", "Received", 1.1),
                ("accepted_quantity", "Accepted", 1.1),
                ("rejected_quantity", "Rejected", 1.1),
                ("rejection_reason", "Reason", 2.2),
            ),
        },
        "purchase-accounting-documents": {
            "title": "PURCHASE ACCOUNTING DOCUMENT",
            "reference": "number",
            "company_path": "order.company",
            "partner_path": "order.vendor",
            "select_related": ("order__company", "order__vendor", "invoice"),
            "line_select_related": (),
            "columns": (
                ("account_code", "Account", 1.3),
                ("description", "Description", 3.1),
                ("debit", "Debit", 1.2),
                ("credit", "Credit", 1.2),
                ("cost_center", "Cost Center", 1.2),
                ("tax_code", "Tax", 0.8),
            ),
        },
        "purchase-inventory-transfers": {
            "title": "INVENTORY TRANSFER",
            "reference": "reference",
            "company_path": "source_warehouse.company",
            "select_related": ("receipt", "source_warehouse__company", "destination_warehouse"),
            "line_select_related": ("receipt_line", "product"),
            "columns": (
                ("product", "Product", 3.4),
                ("receipt_line", "Receipt Line", 3.4),
                ("quantity", "Quantity", 1.4),
            ),
        },
    }
)

REPORT_CONFIG.update(
    {
        "pos-pricelists": {"title": "POS PRICELIST", "reference": "name", "company_path": "company", "select_related": ("company",), "line_select_related": ("pos_category", "product"), "columns": (("sequence", "Sequence", 0.7), ("apply_on", "Applies To", 1.0), ("pos_category", "Category", 1.5), ("product", "Product", 2.2), ("minimum_quantity", "Min. Qty", 0.8), ("price_method", "Method", 1.0), ("value", "Value", 1.0))},
        "pos-price-shortcuts": {"title": "POS PRICE SHORTCUT", "reference": "name", "company_path": "company", "select_related": ("company",), "line_select_related": (), "columns": (("sequence", "Sequence", 1.0), ("label", "Label", 3.5), ("value", "Value", 2.0))},
        "pos-boms": {"title": "POS BILL OF MATERIAL", "reference": "name", "company_path": "company", "select_related": ("company", "product", "pos_category"), "line_select_related": ("product", "nested_bom"), "columns": (("product", "Component", 3.0), ("quantity", "Quantity", 1.1), ("unit", "Unit", 1.0), ("removable", "Removable", 1.0), ("nested_bom", "Nested BOM", 2.0))},
        "pos-campaigns": {"title": "CUSTOMER DISPLAY CAMPAIGN", "reference": "name", "company_path": "company", "select_related": ("company",), "line_select_related": (), "columns": (("sequence", "Sequence", 0.8), ("title", "Slide", 2.1), ("media_type", "Media Type", 1.2), ("media_url", "Media", 2.8), ("duration_seconds", "Duration", 1.0), ("active", "Active", 0.8))},
        "pos-reservations": {"title": "POS RESERVATION", "reference": "reference", "company_path": "profile.company", "partner_path": "customer", "select_related": ("profile__company", "customer", "area", "primary_table"), "line_select_related": ("product",), "columns": (("product", "Product", 2.4), ("description", "Description", 2.6), ("quantity", "Quantity", 0.9), ("planned_start", "Starts", 1.2), ("planned_end", "Ends", 1.2), ("notes", "Notes", 1.6))},
        "kitchen-tickets": {"title": "KITCHEN ORDER TICKET", "reference": "reference", "company_path": "order.session.register.company", "select_related": ("order__session__register__company", "device"), "line_select_related": ("order_line__product",), "columns": (("display_sequence", "Seq", 0.7), ("order_line", "Order Line", 2.6), ("reference", "Reference", 1.3), ("quantity", "Qty", 0.8), ("unit", "Unit", 0.8), ("status", "Status", 1.0), ("notes", "Notes", 2.0))},
        "employee-meal-programs": {"title": "EMPLOYEE MEAL PROGRAM", "reference": "code", "company_path": "company", "select_related": ("company",), "line_select_related": (), "columns": (("sequence", "Seq", 0.6), ("code", "Code", 0.9), ("name", "Eligibility", 1.7), ("employee_position", "Position", 1.4), ("maximum_pick", "Max Pick", 0.8), ("benefit_type", "Benefit", 1.1), ("budget_limit", "Budget", 1.0), ("free_item_quantity", "Free Qty", 0.8))},
        "employee-meal-records": {"title": "EMPLOYEE MEAL REDEMPTION", "reference": "reference", "company_path": "program.company", "select_related": ("program__company", "employee", "profile", "order"), "line_select_related": ("order_line",), "columns": (("sequence", "Seq", 0.6), ("product_name_snapshot", "Product", 2.4), ("ordered_quantity", "Ordered", 0.9), ("covered_quantity", "Covered", 0.9), ("company_covered_amount", "Company", 1.1), ("employee_payable_amount", "Employee", 1.1), ("net_amount", "Net", 1.0), ("coverage_status", "Status", 1.0))},
        "pos-internal-usages": {"title": "POS INTERNAL USAGE", "reference": "reference", "company_path": "profile.company", "select_related": ("profile__company", "session", "reason", "recorded_by"), "line_select_related": ("product",), "columns": (("sequence", "Seq", 0.7), ("product", "Product", 3.3), ("quantity", "Quantity", 1.2), ("unit", "Unit", 1.0), ("note", "Note", 2.3))},
        "pos-bank-payments": {"title": "POS BANK PAYMENT", "reference": "request_reference", "company_path": "order.session.register.company", "select_related": ("order__session__register__company", "bank", "payment_method"), "line_select_related": (), "columns": (("client_request_id", "Request ID", 1.7), ("origin_from", "Origin", 1.1), ("description", "Description", 2.8), ("status", "Status", 1.0), ("log_message", "Log", 2.4))},
        "pos-outlet-orders": {"title": "OUTLET ORDER", "reference": "reference", "company_path": "company", "select_related": ("company", "source_profile", "destination_profile"), "line_select_related": ("product",), "columns": (("product", "Product", 2.7), ("requested_quantity", "Requested", 1.0), ("prepared_quantity", "Prepared", 1.0), ("received_quantity", "Received", 1.0), ("returned_quantity", "Returned", 1.0), ("unit", "Unit", 0.8), ("notes", "Notes", 1.6))},
        "pos-product-bundles": {"title": "POS PRODUCT BUNDLE", "reference": "code", "company_path": "company", "select_related": ("company", "sale_product"), "line_select_related": ("product", "variant"), "columns": (("sequence", "Seq", 0.8), ("product", "Product", 3.0), ("variant", "Variant", 2.4), ("quantity", "Quantity", 1.2))},
        "pos-stock-demands": {"title": "POS STOCK DEMAND", "reference": "reference", "company_path": "company", "select_related": ("company", "profile", "warehouse", "source_order"), "line_select_related": ("product",), "columns": (("product", "Product", 3.1), ("demanded_quantity", "Demanded", 1.2), ("fulfilled_quantity", "Fulfilled", 1.2), ("unit", "Unit", 1.0), ("lot_serial", "Lot / Serial", 1.7))},
        "pos-inventory-transfers": {"title": "POS INVENTORY TRANSFER", "reference": "reference", "company_path": "company", "select_related": ("company", "demand", "source_warehouse", "destination_warehouse"), "line_select_related": ("product",), "columns": (("product", "Product", 2.8), ("quantity", "Quantity", 1.1), ("received_quantity", "Received", 1.1), ("returned_quantity", "Returned", 1.1), ("unit", "Unit", 0.9), ("lot_serial", "Lot / Serial", 1.7))},
    }
)

REPORT_CONFIG.update(
    {
        "construction-boq-templates": {"title": "BOQ TEMPLATE", "reference": "code", "company_path": "company", "select_related": ("company",), "line_select_related": ("product_group", "product"), "columns": (("code", "WBS", 1.0), ("description", "Item", 2.6), ("quantity", "Qty", 0.8), ("unit", "Unit", 0.8), ("unit_cost", "Rate", 1.1), ("amount", "Amount", 1.2))},
        "construction-boq-template-items": {"title": "BOQ TEMPLATE ITEM", "reference": "pk", "company_path": "template.company", "select_related": ("template__company", "product"), "line_select_related": ("product", "vendor", "work_package"), "columns": (("cost_category", "Category", 1.0), ("description", "Resource", 2.4), ("quantity", "Qty", 0.8), ("unit", "Unit", 0.7), ("unit_rate", "Rate", 1.0), ("amount", "Amount", 1.1))},
        "construction-contracts": {"title": "CONSTRUCTION CONTRACT", "reference": "number", "company_path": "project.company", "partner_path": "customer", "select_related": ("project__company", "customer", "linked_boq", "linked_cost_sheet"), "line_select_related": ("invoice",), "columns": (("sequence", "Seq", 0.6), ("invoice_date", "Invoice Date", 1.1), ("portion_percent", "Portion %", 0.9), ("invoice_amount", "Amount", 1.3), ("payment_status", "Status", 1.0))},
        "construction-boqs": {"title": "BILL OF QUANTITY", "reference": "number", "company_path": "project.company", "partner_path": "project.customer", "select_related": ("project__company", "project__customer", "contract", "template"), "line_select_related": ("product", "work_package", "product_group"), "columns": (("wbs_code", "WBS", 0.9), ("description", "Work Item", 2.4), ("unit", "Unit", 0.7), ("quantity", "Qty", 0.8), ("unit_price", "Rate", 1.0), ("sales_amount", "Amount", 1.1), ("budget_cost", "Cost", 1.0))},
        "construction-boq-items": {"title": "BOQ RESOURCE BREAKDOWN", "reference": "pk", "company_path": "boq.project.company", "select_related": ("boq__project__company", "product", "work_package"), "line_select_related": ("product", "vendor", "work_package"), "columns": (("cost_category", "Category", 1.0), ("description", "Resource", 2.2), ("quantity", "Qty", 0.8), ("unit", "Unit", 0.7), ("unit_rate", "Rate", 1.0), ("amount", "Cost", 1.1), ("revenue_including_tax", "Revenue", 1.1))},
        "construction-cost-sheets": {"title": "CONSTRUCTION COST SHEET", "reference": "number", "company_path": "project.company", "select_related": ("project__company", "boq", "project_manager"), "line_select_related": ("boq_item", "product"), "columns": (("wbs_code", "WBS", 0.9), ("description", "Item", 2.4), ("quantity", "Qty", 0.8), ("unit_cost", "Rate", 1.0), ("revised_revenue", "Revenue", 1.1), ("revised_cost", "Cost", 1.1), ("utilised_budget", "Utilised", 1.0))},
        "construction-cost-sheet-items": {"title": "COST SHEET RESOURCE BREAKDOWN", "reference": "pk", "company_path": "cost_sheet.project.company", "select_related": ("cost_sheet__project__company", "boq_item", "product"), "line_select_related": ("product", "vendor", "work_package"), "columns": (("cost_category", "Category", 1.0), ("description", "Resource", 2.3), ("quantity", "Qty", 0.8), ("unit", "Unit", 0.7), ("unit_rate", "Rate", 1.0), ("revised_cost", "Revised", 1.1), ("utilised_budget", "Utilised", 1.0))},
        "construction-budget-changes": {"title": "BUDGET CHANGE REQUEST", "reference": "number", "company_path": "project.company", "select_related": ("project__company", "cost_sheet", "requested_by", "approver"), "line_select_related": ("cost_sheet_item", "cost_sheet_resource", "product", "vendor"), "columns": (("line_type", "Line Type", 1.0), ("change_type", "Change", 0.9), ("description", "Description", 2.4), ("previous_amount", "Previous", 1.1), ("proposed_amount", "Proposed", 1.1), ("variance", "Impact", 1.1))},
        "construction-job-orders": {"title": "CONSTRUCTION JOB ORDER", "reference": "number", "company_path": "project.company", "select_related": ("project__company", "responsible", "work_package"), "line_select_related": ("recorded_by",), "columns": (("log_date", "Date", 1.0), ("completed_quantity", "Quantity", 1.0), ("cumulative_quantity", "Cumulative", 1.0), ("progress", "Progress %", 1.0), ("weather", "Weather", 1.0), ("notes", "Notes", 2.5))},
        "construction-quantity-surveys": {"title": "QUANTITY SURVEY", "reference": "number", "company_path": "project.company", "select_related": ("project__company", "contract", "boq", "surveyor"), "line_select_related": ("boq_item",), "columns": (("sequence", "Seq", 0.6), ("boq_item", "BOQ Item", 2.7), ("previous_quantity", "Previous", 0.9), ("accepted_quantity", "Approved", 0.9), ("unit_rate", "Rate", 1.0), ("amount", "Amount", 1.1), ("progress_percent", "Progress %", 0.9))},
        "construction-quantity-survey-lines": {"title": "QUANTITY SURVEY SOURCES", "reference": "pk", "company_path": "survey.project.company", "select_related": ("survey__project__company", "boq_item"), "line_select_related": ("progress_log__job_order",), "columns": (("progress_log", "Progress Log", 2.6), ("previous_approved_quantity", "Previous", 1.1), ("available_quantity", "Available", 1.1), ("approved_quantity", "Approved", 1.1), ("notes", "Notes", 2.2))},
        "construction-resource-actualizations": {"title": "RESOURCE ACTUALIZATION", "reference": "number", "company_path": "project.company", "select_related": ("project__company", "cost_sheet", "job_order", "actualized_by"), "line_select_related": ("product", "employee", "asset", "cost_sheet_resource", "job_order"), "columns": (("resource_type", "Type", 0.9), ("description", "Resource", 2.1), ("quantity", "Qty", 0.8), ("unit_cost", "Rate", 1.0), ("amount", "Amount", 1.1), ("remaining_quantity", "Remaining", 1.0), ("reference_document", "Reference", 1.2))},
        "construction-timesheets": {"title": "CONSTRUCTION TIMESHEET", "reference": "number", "company_path": "project.company", "select_related": ("project__company", "approved_by"), "line_select_related": ("employee", "job_order"), "columns": (("work_date", "Date", 1.0), ("employee", "Employee", 2.0), ("job_order", "Job Order", 1.6), ("hours", "Hours", 0.8), ("description", "Description", 2.8))},
        "construction-progressive-claims": {"title": "PROGRESSIVE CLAIM", "reference": "number", "company_path": "project.company", "partner_path": "vendor", "select_related": ("project__company", "contract", "boq", "quantity_survey", "vendor"), "line_select_related": ("boq_item", "quantity_survey_line"), "columns": (("sequence", "Seq", 0.6), ("boq_item", "BOQ Item", 2.4), ("previous_quantity", "Previous", 0.9), ("current_quantity", "Period", 0.9), ("certified_quantity", "Certified", 0.9), ("unit_price", "Rate", 1.0), ("amount", "Amount", 1.1))},
        "construction-material-demands": {"title": "MATERIAL DEMAND", "reference": "number", "company_path": "project.company", "select_related": ("project__company", "job_order", "warehouse", "requested_by"), "line_select_related": ("product",), "columns": (("product", "Product", 2.7), ("description", "Description", 2.2), ("requested_quantity", "Requested", 1.0), ("allocated_quantity", "Allocated", 1.0), ("delivered_quantity", "Delivered", 1.0))},
        "construction-account-settings": {"title": "CONSTRUCTION ACCOUNT SETTINGS", "reference": "pk", "company_path": "project.company", "select_related": ("project__company", "revenue_account", "cost_account", "work_in_progress_account", "retention_account"), "line_select_related": ("account",), "columns": (("resource_category", "Resource Category", 1.7), ("account", "CIP Account", 2.8), ("usage_help", "Usage", 3.5))},
        "construction-subcontract-job-orders": {"title": "SUBCONTRACT JOB ORDER", "reference": "number", "company_path": "project.company", "partner_path": "vendor", "select_related": ("project__company", "job_order", "vendor"), "line_select_related": (), "columns": (("progress_date", "Date", 1.0), ("quantity", "Quantity", 1.0), ("cumulative_quantity", "Cumulative", 1.0), ("progress_percent", "Progress %", 1.0), ("amount", "Amount", 1.2), ("notes", "Notes", 2.5))},
        "construction-inventory-transfers": {"title": "CONSTRUCTION INVENTORY TRANSFER", "reference": "number", "company_path": "project.company", "select_related": ("project__company", "source_warehouse", "destination_warehouse", "purchase_order"), "line_select_related": ("product", "material_demand_line", "cost_sheet_resource"), "columns": (("product", "Product", 3.0), ("material_demand_line", "Demand", 2.0), ("cost_sheet_resource", "Cost Resource", 2.1), ("quantity", "Quantity", 1.0))},
    }
)


def _path_value(obj, path):
    value = obj
    for part in path.split("."):
        value = getattr(value, part, None)
        if value is None:
            break
    return value


def _plain(value):
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "Ya" if value else "Tidak"
    if isinstance(value, datetime):
        return date_format(value, "d M Y, H:i")
    if isinstance(value, date):
        return date_format(value, "d M Y")
    if isinstance(value, Decimal):
        return f"{value:,.2f}"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return " ".join(strip_tags(str(value)).split()) or "—"


def _display(obj, field_name):
    if field_name == "rental_period":
        start = _plain(getattr(obj, "rental_start_date", None))
        end = _plain(getattr(obj, "rental_return_date", None))
        return "—" if start == end == "—" else f"{start} – {end}"
    display = getattr(obj, f"get_{field_name}_display", None)
    value = display() if callable(display) else getattr(obj, field_name, None)
    if field_name == "progress" and value not in (None, ""):
        return f"{value}%"
    plain_value = _plain(value)
    return DISPLAY_TRANSLATIONS.get(plain_value, plain_value)


def _safe(value):
    return escape(_plain(value)).replace("\n", "<br/>")


def _partner_lines(partner):
    if partner is None:
        return []
    return [
        getattr(partner, "name", str(partner)),
        getattr(partner, "address", ""),
        getattr(partner, "phone", ""),
        getattr(partner, "email", ""),
        f"NPWP: {partner.tax_id}" if getattr(partner, "tax_id", "") else "",
    ]


def _company_lines(company):
    return list(PRINT_COMPANY)


def _contact_paragraph(lines, styles):
    clean = [_safe(line) for line in lines if line]
    return Paragraph("<br/>".join(clean) or "—", styles["body"])


def _header_pairs(entity, obj, header_fields):
    excluded = {"company", "customer", "vendor", "partner", "notes", "description"}
    if entity == "sales":
        fields = ["customer_reference", "order_date", "validity_date", "salesperson_name", "payment_terms", "price_list"]
    else:
        fields = [field for field in header_fields if field not in excluded and field not in {"number", "code", "title", "name"}]
    pairs = []
    for field_name in fields:
        try:
            label = FIELD_LABELS.get(field_name, obj._meta.get_field(field_name).verbose_name.title())
        except Exception:
            label = FIELD_LABELS.get(field_name, field_name.replace("_", " ").title())
        pairs.append((label, _display(obj, field_name)))
    return pairs


def _styles():
    sheet = getSampleStyleSheet()
    return {
        "brand": ParagraphStyle("brand", parent=sheet["Normal"], fontName="Helvetica-Bold", fontSize=15, leading=17, textColor=BRAND),
        "tag": ParagraphStyle("tag", parent=sheet["Normal"], fontName="Helvetica-Bold", fontSize=6.8, leading=8, textColor=ACCENT, spaceAfter=2),
        "title": ParagraphStyle("title", parent=sheet["Normal"], fontName="Helvetica-Bold", fontSize=20, leading=23, textColor=INK, alignment=TA_RIGHT),
        "reference": ParagraphStyle("reference", parent=sheet["Normal"], fontName="Helvetica", fontSize=10, leading=13, textColor=MUTED, alignment=TA_RIGHT),
        "label": ParagraphStyle("label", parent=sheet["Normal"], fontName="Helvetica-Bold", fontSize=7, leading=9, textColor=MUTED),
        "body": ParagraphStyle("body", parent=sheet["Normal"], fontName="Helvetica", fontSize=8.5, leading=11, textColor=INK),
        "body_right": ParagraphStyle("body_right", parent=sheet["Normal"], fontName="Helvetica", fontSize=8.5, leading=11, textColor=INK, alignment=TA_RIGHT),
        "table_head": ParagraphStyle("table_head", parent=sheet["Normal"], fontName="Helvetica-Bold", fontSize=7, leading=9, textColor=WHITE),
        "table_head_right": ParagraphStyle("table_head_right", parent=sheet["Normal"], fontName="Helvetica-Bold", fontSize=7, leading=9, textColor=WHITE, alignment=TA_RIGHT),
        "section": ParagraphStyle("section", parent=sheet["Normal"], fontName="Helvetica-Bold", fontSize=8.5, leading=11, textColor=BRAND),
        "note": ParagraphStyle("note", parent=sheet["Normal"], fontName="Helvetica-Oblique", fontSize=8, leading=10, textColor=MUTED),
        "terms": ParagraphStyle("terms", parent=sheet["Normal"], fontName="Helvetica", fontSize=8, leading=11, textColor=MUTED),
        "center": ParagraphStyle("center", parent=sheet["Normal"], fontName="Helvetica", fontSize=8, leading=10, textColor=INK, alignment=TA_CENTER),
    }


def _document_title(entity, obj, config):
    if entity in {"sales", "purchases"} and getattr(obj, "status", "") == "draft":
        return config.get("draft_title", config["title"])
    if entity == "invoices" and getattr(obj, "invoice_type", "") == "vendor":
        return "TAGIHAN PEMASOK"
    return config["title"]


def _header_story(entity, obj, config, styles):
    company = _path_value(obj, config["company_path"])
    partner = _path_value(obj, config.get("partner_path", "")) if config.get("partner_path") else None
    reference = _display(obj, config["reference"])
    brand = Table(
        [[
            Table([[Paragraph("G", styles["title"])]], colWidths=[14 * mm], rowHeights=[14 * mm], style=TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), ACCENT),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ])),
            [Paragraph("ERP GSSHOP", styles["brand"]), Paragraph("DOKUMEN BISNIS", styles["tag"])],
        ]],
        colWidths=[17 * mm, 63 * mm],
        style=TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]),
    )
    title = Table(
        [[Paragraph(_safe(_document_title(entity, obj, config)), styles["title"])], [Paragraph(f"# {_safe(reference)}", styles["reference"])]],
        colWidths=[84 * mm],
        style=TableStyle([("ALIGN", (0, 0), (-1, -1), "RIGHT"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]),
    )
    top = Table([[brand, title]], colWidths=[88 * mm, 82 * mm], style=TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    company_block = [Paragraph("PENJUAL", styles["label"]), _contact_paragraph(_company_lines(company), styles)]
    if partner is not None:
        partner_block = [Paragraph("PELANGGAN / MITRA", styles["label"]), _contact_paragraph(_partner_lines(partner), styles)]
    else:
        partner_block = [Paragraph("DOKUMEN", styles["label"]), Paragraph(_safe(obj), styles["body"])]
    addresses = Table([[company_block, partner_block]], colWidths=[82 * mm, 82 * mm], style=TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.6, LINE),
        ("BACKGROUND", (0, 0), (-1, -1), SOFT),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return [top, Spacer(1, 7 * mm), addresses, Spacer(1, 5 * mm)]


def _info_table(entity, obj, header_fields, styles):
    pairs = _header_pairs(entity, obj, header_fields)
    if not pairs:
        return None
    cells = []
    for label, value in pairs:
        cells.append([Paragraph(_safe(label).upper(), styles["label"]), Paragraph(_safe(value), styles["body"])])
    width = 170 * mm / min(len(cells), 4)
    rows = []
    for start in range(0, len(cells), 4):
        row = cells[start:start + 4]
        while len(row) < 4:
            row.append([Paragraph("", styles["label"]), Paragraph("", styles["body"])])
        rows.append(row)
    return Table(rows, colWidths=[width] * 4, style=TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))


def _is_numeric_field(field_name):
    return field_name in {
        "quantity",
        "approved_quantity",
        "ordered_quantity",
        "estimated_unit_price",
        "unit_price",
        "unit_rate",
        "target_unit_price",
        "agreed_quantity",
        "released_quantity",
        "minimum_release_quantity",
        "received_quantity",
        "accepted_quantity",
        "rejected_quantity",
        "tax_percent",
        "discount_percent",
        "subtotal",
        "debit",
        "credit",
        "budget",
        "actual_cost",
    }


def _line_table(entity, lines, config, styles):
    columns = config["columns"]
    total_weight = sum(column[2] for column in columns)
    widths = [170 * mm * column[2] / total_weight for column in columns]
    header = [Paragraph(_safe(label), styles["table_head_right"] if _is_numeric_field(field) else styles["table_head"]) for field, label, _weight in columns]
    data = [header]
    section_rows = []
    note_rows = []
    for line in lines:
        display_type = getattr(line, "display_type", "")
        if display_type in {"line_section", "line_note"}:
            text = getattr(line, "description", "") or "—"
            data.append([Paragraph(_safe(text), styles["section"] if display_type == "line_section" else styles["note"])] + [""] * (len(columns) - 1))
            row_index = len(data) - 1
            (section_rows if display_type == "line_section" else note_rows).append(row_index)
            continue
        row = []
        for field_name, _label, _weight in columns:
            value = _display(line, field_name)
            style = styles["body_right"] if _is_numeric_field(field_name) else styles["body"]
            row.append(Paragraph(_safe(value), style))
        data.append(row)
    if len(data) == 1:
        data.append([Paragraph("Tidak ada baris rincian", styles["note"])] + [""] * (len(columns) - 1))
        note_rows.append(1)
    table_style = [
        ("BACKGROUND", (0, 0), (-1, 0), BRAND),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, BRAND),
        ("LINEBELOW", (0, 1), (-1, -1), 0.35, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    for row_index in section_rows:
        table_style.extend([("SPAN", (0, row_index), (-1, row_index)), ("BACKGROUND", (0, row_index), (-1, row_index), SOFT)])
    for row_index in note_rows:
        table_style.append(("SPAN", (0, row_index), (-1, row_index)))
    return Table(data, colWidths=widths, repeatRows=1, style=TableStyle(table_style))


def _totals_table(obj, total_fields, styles):
    rows = []
    for label, field_name in total_fields:
        translated_label = TOTAL_LABELS.get(label, label)
        rows.append([Paragraph(_safe(translated_label), styles["body"]), Paragraph(_safe(_display(obj, field_name)), styles["body_right"])])
    if not rows:
        return None
    return Table(rows, colWidths=[35 * mm, 35 * mm], hAlign="RIGHT", style=TableStyle([
        ("LINEABOVE", (0, 0), (-1, 0), 0.8, BRAND),
        ("LINEABOVE", (0, -1), (-1, -1), 1.2, BRAND),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))


def _payment_table(styles):
    return Table(
        [[
            Paragraph(_safe(SALES_BANK_DETAILS[0]).upper(), styles["label"]),
            Paragraph(
                f"<b>{_safe(SALES_BANK_DETAILS[1])}</b><br/>{_safe(SALES_BANK_DETAILS[2])}",
                styles["body"],
            ),
        ]],
        colWidths=[43 * mm, 121 * mm],
        style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), SOFT),
            ("BOX", (0, 0), (-1, -1), 0.6, LINE),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ]),
    )


def _footer(canvas, doc):
    if getattr(doc, "show_paid_watermark", False):
        width, height = A4
        canvas.saveState()
        canvas.setFillColor(WATERMARK_BLUE)
        canvas.setFillAlpha(0.10)
        canvas.translate(width / 2, height / 2)
        canvas.rotate(35)
        canvas.setFont("Helvetica-Bold", 180)
        canvas.drawCentredString(0, -58, "LUNAS")
        canvas.restoreState()
    canvas.saveState()
    width, _height = A4
    y = 12 * mm
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.5)
    canvas.line(20 * mm, y + 5 * mm, width - 20 * mm, y + 5 * mm)
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 7)
    canvas.drawString(20 * mm, y, "Dibuat oleh ERP GSSHOP")
    page_text = f"Halaman {doc.page}"
    canvas.drawString(width - 20 * mm - stringWidth(page_text, "Helvetica", 7), y, page_text)
    canvas.restoreState()


def render_document_pdf(entity, obj, lines, document_config):
    """Return a complete PDF as bytes without performing database queries."""
    config = REPORT_CONFIG[entity]
    styles = _styles()
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=17 * mm,
        bottomMargin=24 * mm,
        title=f"{config['title']} {_display(obj, config['reference'])}",
        author="ERP GSSHOP",
        subject=config["title"],
    )
    document.show_paid_watermark = entity == "sales" and getattr(obj, "status", "") == "completed"
    story = _header_story(entity, obj, config, styles)
    info = _info_table(entity, obj, document_config["header_fields"], styles)
    if info:
        story.extend([info, Spacer(1, 6 * mm)])
    story.extend([_line_table(entity, lines, config, styles), Spacer(1, 5 * mm)])
    totals = _totals_table(obj, document_config.get("total_fields", ()), styles)
    if totals:
        story.extend([totals, Spacer(1, 6 * mm)])
    if entity == "sales":
        story.extend([_payment_table(styles), Spacer(1, 6 * mm)])
    notes = getattr(obj, "notes", "") or (getattr(obj, "description", "") if entity != "journal-entries" else "")
    if entity == "sales" and getattr(obj, "payment_terms", ""):
        story.append(KeepTogether([
            Paragraph("SYARAT PEMBAYARAN", styles["label"]),
            Paragraph(_safe(obj.payment_terms), styles["terms"]),
            Spacer(1, 3 * mm),
        ]))
    if notes:
        story.append(KeepTogether([
            HRFlowable(width="100%", thickness=0.5, color=LINE, spaceBefore=2, spaceAfter=5),
            Paragraph("CATATAN &amp; KETENTUAN", styles["label"]),
            Paragraph(_safe(notes), styles["terms"]),
        ]))
    document.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()
