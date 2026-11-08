"""Portable PDF and Excel-compatible exports for accounting analytics."""

from io import BytesIO, StringIO
import csv

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def _statement_export_rows(analytics):
    headers = ["Description", "Current Period"]
    if analytics["has_comparison"]:
        headers.extend(["Comparison", "Variance", "Variance %"])
    rows = [headers]
    for row in analytics["statement_rows"]:
        values = [row["label"], str(row.get("current", ""))]
        if analytics["has_comparison"]:
            values.extend([
                str(row.get("comparison", "")),
                str(row.get("variance", "")),
                "" if row.get("variance_percent") is None else f"{row['variance_percent']}%",
            ])
        rows.append(values)
    return rows


def render_analytics_pdf(title, analytics):
    output = BytesIO()
    document = SimpleDocTemplate(output, pagesize=landscape(A4), title=title)
    styles = getSampleStyleSheet()
    story = [Paragraph(title, styles["Title"]), Spacer(1, 12)]
    metrics = [["Metric", "Value", "Description"]] + [
        [metric["label"], str(metric["value"]), metric["detail"]] for metric in analytics["metrics"]
    ]
    if analytics.get("view_type") == "profit_loss":
        period = analytics["period"]
        story.extend([
            Paragraph(
                f"Period: {period['start']:%d %b %Y} – {period['end']:%d %b %Y}",
                styles["Normal"],
            ),
            Spacer(1, 8),
        ])
        breakdown = _statement_export_rows(analytics)
    else:
        breakdown = [[analytics["breakdown_title"], "Total"]] + [
            [str(row["label"]), str(row["total"])] for row in analytics["breakdown"]
        ]
    for rows in (metrics, breakdown):
        table = Table(rows, repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17324d")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ]))
        story.extend([table, Spacer(1, 16)])
    document.build(story)
    return output.getvalue()


def render_analytics_excel(title, analytics):
    """Return UTF-8 tabular data that Excel and LibreOffice open directly."""
    output = StringIO()
    writer = csv.writer(output, dialect="excel-tab")
    writer.writerow([title])
    writer.writerow([])
    writer.writerow(["Metric", "Value", "Description"])
    for metric in analytics["metrics"]:
        writer.writerow([metric["label"], metric["value"], metric["detail"]])
    writer.writerow([])
    if analytics.get("view_type") == "profit_loss":
        period = analytics["period"]
        writer.writerow(["Period", period["start"].isoformat(), period["end"].isoformat()])
        writer.writerows(_statement_export_rows(analytics))
    else:
        writer.writerow([analytics["breakdown_title"], "Total"])
        for row in analytics["breakdown"]:
            writer.writerow([row["label"], row["total"]])
    return "\ufeff" + output.getvalue()
