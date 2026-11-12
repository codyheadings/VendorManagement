import streamlit as st
from pathlib import Path
import db

DOCS_DIR = (db.BASE_DIR / "data").resolve()

IMAGE_TYPES = {".png", ".jpg", ".jpeg"}

STATUS_COLORS = {
    "draft": "gray",
    "open": "blue",
    "closed": "gray",
    "cancelled": "red",
    "sent": "blue",
    "signed": "green",
    "expired": "orange",
    "terminated": "red",
    "unknown": "gray",
    "pending": "orange",
    "approved": "green",
    "rejected": "red",
}

COL_WIDTHS = [2.5, 2.0, 2.5, 1.5, 1.3, 1.2]


@st.dialog("Source document", width="large")
def show_source(file_name: str, file_path: str, mime_type: str | None):
    path = Path(file_path)

    if not path.is_absolute():
        path = db.BASE_DIR / path

    path = path.resolve()

    # Only allow files inside the data folder
    if not path.is_relative_to(DOCS_DIR):
        st.error("This file is outside the allowed documents folder.")
        return

    if not path.is_file():
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
            st.info(
                "No inline preview for this file type. "
                "Use the download button."
            )

    except Exception:
        st.warning("Couldn't preview this file. Use the download button.")

    try:
        st.download_button(
            "Download original",
            data=path.read_bytes(),
            file_name=file_name,
            mime=mime_type or "application/octet-stream",
            icon=":material/download:",
        )
    except OSError:
        st.error("Couldn't read this file for downloading.")

def get_document_details(doc):
    """Return a useful reference and status for each document type."""

    category = doc["category"]

    if category == "rfq":
        reference = doc["rfq_number"] or "RFQ"
        if doc["rfq_title"]:
            reference += f" · {doc['rfq_title']}"

        return reference, doc["rfq_status"]

    if category == "legal":
        reference = doc["legal_type"] or "Legal document"
        return reference, doc["legal_status"]

    if category == "invoice":
        reference = doc["invoice_number"] or "Invoice"

        if doc["invoice_amount"] is not None:
            reference += (
                f" · {doc['invoice_amount']:,.2f} "
                f"{doc['invoice_currency'] or ''}"
            )

        status = "paid" if doc["paid_date"] else "unpaid"
        return reference, status

    return "—", None