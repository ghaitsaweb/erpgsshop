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
    return field_name in {"quantity", "unit_price", "discount_percent", "subtotal", "debit", "credit", "budget", "actual_cost"}


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
