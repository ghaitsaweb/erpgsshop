"""Portable PDF and Excel-compatible exports for accounting analytics."""

from io import BytesIO, StringIO
import csv

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def render_analytics_pdf(title, analytics):
    output = BytesIO()
    document = SimpleDocTemplate(output, pagesize=landscape(A4), title=title)
    styles = getSampleStyleSheet()
    story = [Paragraph(title, styles["Title"]), Spacer(1, 12)]
    metrics = [["Metric", "Value", "Description"]] + [
        [metric["label"], str(metric["value"]), metric["detail"]] for metric in analytics["metrics"]
    ]
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
    writer.writerow([analytics["breakdown_title"], "Total"])
    for row in analytics["breakdown"]:
        writer.writerow([row["label"], row["total"]])
    return "\ufeff" + output.getvalue()
