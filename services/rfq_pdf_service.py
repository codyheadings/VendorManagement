"""
Reusable RFQ PDF template generator.
"""

from pathlib import Path
from io import IOBase
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    HRFlowable,
)

COMPANY = {
    "name": "ODDITY Labs",
    "department": "Sourcing & Operations",
    "location": "Boston, MA",
    "email": "sourcing@oddity-labs.example",
}

INK = colors.HexColor("#202938")
MUTED = colors.HexColor("#5F6B7A")
ACCENT = colors.HexColor("#243B53")
PALE = colors.HexColor("#F1F4F7")
GRID = colors.HexColor("#D7DEE7")

def _p(value, style):
    """Create a safely escaped ReportLab paragraph."""
    if value is None:
        value = ""
    return Paragraph(escape(str(value)).replace("\n", "<br/>"), style)


def _make_styles():
    base = getSampleStyleSheet()
    return {
        "company": ParagraphStyle(
            "Company", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=15, leading=18, textColor=INK, spaceAfter=2,
        ),
        "company_meta": ParagraphStyle(
            "CompanyMeta", parent=base["Normal"], fontName="Helvetica",
            fontSize=8.5, leading=11, textColor=MUTED,
        ),
        "title": ParagraphStyle(
            "RFQTitle", parent=base["Title"], fontName="Helvetica-Bold",
            fontSize=17, leading=21, alignment=TA_LEFT,
            textColor=INK, spaceBefore=5, spaceAfter=3,
        ),
        "subtitle": ParagraphStyle(
            "Subtitle", parent=base["Normal"], fontName="Helvetica",
            fontSize=10, leading=14, textColor=MUTED, spaceAfter=10,
        ),
        "section": ParagraphStyle(
            "Section", parent=base["Heading2"], fontName="Helvetica-Bold",
            fontSize=11, leading=14, textColor=ACCENT,
            spaceBefore=12, spaceAfter=6, keepWithNext=True,
        ),
        "body": ParagraphStyle(
            "Body", parent=base["Normal"], fontName="Helvetica",
            fontSize=8.5, leading=12, textColor=INK,
        ),
        "small": ParagraphStyle(
            "Small", parent=base["Normal"], fontName="Helvetica",
            fontSize=7.8, leading=10.5, textColor=INK,
        ),
        "table_header": ParagraphStyle(
            "TableHeader", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=8, leading=10, textColor=colors.white,
        ),
        "bullet": ParagraphStyle(
            "Bullet", parent=base["Normal"], fontName="Helvetica",
            fontSize=8.5, leading=12, leftIndent=12,
            firstLineIndent=-8, textColor=INK, spaceAfter=3,
        ),
        "footer": ParagraphStyle(
            "Footer", parent=base["Normal"], fontName="Helvetica-Oblique",
            fontSize=7, leading=9, textColor=MUTED, alignment=TA_CENTER,
        ),
    }


def _details_table(data, styles, usable_width):
    rows = [
        ("RFQ number", data.get("rfq_id", "")),
        ("Issued", data.get("issued_date", "")),
        ("Quotes due", data.get("rfq_deadline", "")),
        ("Contact", data.get("rfq_contact", COMPANY["email"])),
        ("Deliver to", data.get("deliver_to", "")),
        ("Vendors invited", ", ".join(data.get("vendors", []))),
    ]

    label_style = ParagraphStyle(
        "DetailLabel", parent=styles["small"], fontName="Helvetica-Bold",
        textColor=MUTED,
    )
    table_data = [
        [_p(label, label_style), _p(value, styles["small"])]
        for label, value in rows
    ]
    table = Table(table_data, colWidths=[1.25 * inch, usable_width - 1.25 * inch])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("BACKGROUND", (0, 0), (0, -1), PALE),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, GRID),
        ("BOX", (0, 0), (-1, -1), 0.6, GRID),
    ]))
    return table


def _items_table(items, styles, usable_width):
    headers = ["Line", "Description", "Quantity", "Required"]
    data = [[_p(h, styles["table_header"]) for h in headers]]

    for index, item in enumerate(items, start=1):
        quantity = item.get("quantity", "")
        unit = item.get("unit", "")

        quantity_display = f"{quantity} {unit}".strip()

        data.append([
            _p(item.get("line", index), styles["small"]),
            _p(item.get("description", ""), styles["small"]),
            _p(quantity_display, styles["small"]),
            _p(item.get("required", ""), styles["small"]),
        ])

    widths = [
        0.48 * inch,
        usable_width * 0.40,
        usable_width * 0.16,
        usable_width - (0.48 * inch) - (usable_width * 0.56),
    ]
    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.5, GRID),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE]),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return table


def _bullet_section(title, entries, styles):
    story = [Paragraph(escape(title), styles["section"])]
    if not entries:
        story.append(Paragraph("No additional requirements specified.", styles["body"]))
        return story

    for entry in entries:
        story.append(Paragraph(
            f"&bull;&nbsp;&nbsp;{escape(str(entry))}",
            styles["bullet"],
        ))
    return story


def generate_rfq_pdf(rfq_data: dict, output_path: str | Path) -> str:
    """
    Generate an RFQ PDF.

    Expected rfq_data keys:
      rfq_id: str
      rfq_subtitle: str
      issued_date: str
      rfq_deadline: str
      rfq_contact: str
      deliver_to: str
      vendors: list[str]
      items: list[dict] with description, quantity, unit, required
      specifications: list[str]
      quote_requirements: list[str]

    Returns the generated file path as a string.
    """
    required_keys = ["rfq_id", "rfq_subtitle", "issued_date", "rfq_deadline", "deliver_to"]
    missing = [key for key in required_keys if not rfq_data.get(key)]
    if missing:
        raise ValueError(f"Missing required RFQ fields: {', '.join(missing)}")

    if isinstance(output_path, IOBase):
        output = output_path
    else:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(output) if isinstance(output, Path) else output,
        pagesize=letter,
        rightMargin=0.68 * inch,
        leftMargin=0.68 * inch,
        topMargin=0.55 * inch,
        bottomMargin=0.55 * inch,
        title=f"Request for Quotation {rfq_data['rfq_id']}",
        author=COMPANY["name"],
    )

    styles = _make_styles()
    usable_width = letter[0] - doc.leftMargin - doc.rightMargin
    story = []

    # Standard company header
    story.append(Paragraph(escape(COMPANY["name"]), styles["company"]))
    story.append(Paragraph(
        f"{escape(COMPANY['department'])} · {escape(COMPANY['location'])}",
        styles["company_meta"],
    ))
    story.append(Paragraph(escape(COMPANY["email"]), styles["company_meta"]))
    story.append(Spacer(1, 9))
    story.append(HRFlowable(width="100%", thickness=0.8, color=GRID))
    story.append(Spacer(1, 12))

    # RFQ title and subtitle
    story.append(Paragraph(
        f"Request for Quotation {escape(str(rfq_data['rfq_id']))}",
        styles["title"],
    ))
    story.append(Paragraph(escape(str(rfq_data["rfq_subtitle"])), styles["subtitle"]))

    # RFQ metadata
    story.append(_details_table(rfq_data, styles, usable_width))

    # Requested line items
    story.append(Paragraph("Requested items", styles["section"]))
    items = rfq_data.get("items", [])
    if items:
        story.append(_items_table(items, styles, usable_width))
    else:
        story.append(Paragraph("No line items have been added.", styles["body"]))

    # Variable-length sections
    story.extend(_bullet_section(
        "Specifications",
        rfq_data.get("specifications", []),
        styles,
    ))
    story.extend(_bullet_section(
        "Please include in your quote",
        rfq_data.get("quote_requirements", []),
        styles,
    ))

    if rfq_data.get("footer_note"):
        story.append(Spacer(1, 12))
        story.append(Paragraph(escape(str(rfq_data["footer_note"])), styles["footer"]))

    def add_page_number(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(MUTED)
        canvas.drawRightString(
            letter[0] - doc.rightMargin,
            0.32 * inch,
            f"{rfq_data['rfq_id']}  ·  Page {document.page}",
        )
        canvas.restoreState()

    doc.build(story, onFirstPage=add_page_number, onLaterPages=add_page_number)
    return str(output) if isinstance(output, Path) else output