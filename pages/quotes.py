import streamlit as st
from pathlib import Path
import db

st.set_page_config(
    page_title="Quotes",
    layout="wide"
)

# Main page content
DOCS_DIR = (db.BASE_DIR / "data" ).resolve()  # only files under here can be opened
IMAGE_TYPES = {".png", ".jpg", ".jpeg"}
REVIEW_COLORS = {"pending": "orange", "approved": "green", "rejected": "red"}
COL_WIDTHS = [2.5, 2, 2.5, 1.5, 2, 1.7, 1.5]


def fmt_price(amount, currency, rate_to_usd):
    """Return (main_line, caption). Converted to USD when we have a rate."""
    if amount is None:
        return "—", "Total not stated"
    if currency == "USD":
        return f"${amount:,.2f}", ""
    if rate_to_usd:
        return f"${amount * rate_to_usd:,.2f}", f"converted from {amount:,.2f} {currency}"
    return f"{amount:,.2f} {currency}", "no exchange rate on file"


@st.dialog("Source document", width="large")
def show_source(file_name: str, file_path: str, mime_type: str | None):
    path = Path(file_path)
    if not path.is_absolute():
        path = db.BASE_DIR / path
    path = path.resolve()

    # Never serve anything outside the data folder
    if not path.is_relative_to(DOCS_DIR):
        st.error("This file is outside the allowed documents folder.")
        return
    if not path.exists():
        st.error("The file is missing from disk.")
        return

    st.caption(file_name)
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            st.pdf(str(path), height=600)
        elif suffix in IMAGE_TYPES:
            st.image(str(path))
        elif suffix in {".eml", ".txt"}:
            st.text(path.read_text(errors="replace")[:8000])
        else:
            st.info("No inline preview for this file type. Use the download button.")
    except Exception:
        st.warning("Couldn't preview this file. Use the download button.")

    st.download_button(
        "Download original",
        data=path.read_bytes(),
        file_name=file_name,
        mime=mime_type or "application/octet-stream",
        icon=":material/download:",
    )


st.title("Quotes")
st.caption("Intelligently extracted quote data.")

f1, f2, f3, f4 = st.columns([3, 2, 0.6, .8], vertical_alignment="center")
search = f1.text_input(
    "Search", placeholder="Search by vendor, quote ref or RFQ number",
    label_visibility="collapsed", key="quote_search",
)
status_filter = f2.segmented_control(
    "Review status", ["All", "Pending", "Approved", "Rejected"],
    default="All", label_visibility="collapsed", key="quote_status",
) or "All"
sort = f3.selectbox("Sort By", ["Date", "Price", "Delivery"])
order = f4.selectbox("Order", ["Descending", "Ascending"])

like = f"%{search.strip()}%"
status = status_filter.lower()

sort_columns = {
    "Date": "q.quote_date",
    "Price": "q.total_amount",
    "Delivery": "q.delivery_time_days",
}

sort_by = sort_columns[sort]

order_by = "DESC" if order == "Descending" else "ASC"

quotes = db.query(
    f"""
    SELECT q.id, q.reference_number, q.quote_date, q.valid_until, q.currency,
           q.total_amount, q.delivery_time_days, q.review_status, q.confidence,
           v.name AS vendor_name,
           r.rfq_number,
           d.file_name, d.file_path, d.mime_type,
           er.rate_to_usd,
           EXISTS (SELECT 1 FROM quotes q2
                   WHERE q2.supersedes_quote_id = q.id
                     AND q2.review_status = 'approved') AS superseded
    FROM quotes q
    JOIN vendors v            ON v.id = q.vendor_id
    LEFT JOIN rfqs r          ON r.id = q.rfq_id
    LEFT JOIN documents d     ON d.id = q.document_id
    LEFT JOIN exchange_rates er ON er.currency = q.currency
    WHERE (v.name LIKE ? OR q.reference_number LIKE ? OR r.rfq_number LIKE ?)
      AND (? = 'all' OR q.review_status = ?)
    ORDER BY {sort_by} {order_by}, q.id DESC
    """,
    (like, like, like, status, status),
)

header = st.columns(COL_WIDTHS)
for col, label in zip(
    header, ["Vendor / RFQ", "Quote Ref", "Price", "Delivery", "Valid Until", "Review", "Source"]
):
    col.caption(f"**{label.upper()}**")

with st.container(height=520, border=True):
    if not quotes:
        st.info("No quotes match these filters.")

    for q in quotes:
        with st.container(border=True, key=f"qrow_{q['id']}"):
            cols = st.columns(COL_WIDTHS, vertical_alignment="center")

            cols[0].write(f"**{q['vendor_name']}**")
            cols[0].caption(q["rfq_number"] or "No RFQ linked")

            cols[1].write(q["reference_number"] or "—")
            cols[1].caption(q["quote_date"] or "No date")

            main, note = fmt_price(q["total_amount"], q["currency"], q["rate_to_usd"])
            cols[2].write(main)
            if note:
                cols[2].caption(note)

            days = q["delivery_time_days"]
            cols[3].write(f"{days} days" if days is not None else "—")

            cols[4].write(q["valid_until"] or "—")
            if q["valid_until"] and q["valid_until"] < db.TODAY:
                cols[4].caption(":red[Expired]")

            with cols[5]:
                st.badge(
                    q["review_status"].capitalize(),
                    color=REVIEW_COLORS.get(q["review_status"], "gray"),
                )
                if q["superseded"]:
                    st.caption("Superseded by revision")
                elif q["review_status"] == "pending" and q["confidence"] is not None:
                    st.caption(f"Confidence {q['confidence']:.0%}")

            with cols[6]:
                if q["file_path"]:
                    if st.button(
                        "View source", key=f"src_{q['id']}",
                        type="tertiary", icon=":material/description:",
                    ):
                        show_source(q["file_name"], q["file_path"], q["mime_type"])
                else:
                    st.caption("No source file")