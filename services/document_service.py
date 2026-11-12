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

def get_documents(category=None, search="", sort="Date", order="Descending"):
    """Retrieve documents, optionally filtered by category and search."""

    sort_columns = {
        "Date": "d.created_at",
        "Name": "d.file_name",
        "Category": "d.category",
    }

    sort_by = sort_columns[sort]
    order_by = "DESC" if order == "Descending" else "ASC"

    like = f"%{search.strip()}%"

    category_filter = category if category else None

    return db.query(
        f"""
        SELECT
            d.id,
            d.file_name,
            d.file_path,
            d.mime_type,
            d.category,
            d.source,
            d.created_at,

            COALESCE(
                (SELECT v.name
                 FROM vendors v
                 WHERE v.id = d.vendor_id),

                (SELECT v.name
                 FROM legal_documents l
                 JOIN vendors v ON v.id = l.vendor_id
                 WHERE l.document_id = d.id
                 LIMIT 1),

                (SELECT v.name
                 FROM invoices i
                 JOIN vendors v ON v.id = i.vendor_id
                 WHERE i.document_id = d.id
                 LIMIT 1)
            ) AS vendor_name,

            (SELECT r.rfq_number
             FROM rfqs r
             WHERE r.document_id = d.id
             LIMIT 1) AS rfq_number,

            (SELECT r.title
             FROM rfqs r
             WHERE r.document_id = d.id
             LIMIT 1) AS rfq_title,

            (SELECT r.status
             FROM rfqs r
             WHERE r.document_id = d.id
             LIMIT 1) AS rfq_status,

            (SELECT r.issue_date
             FROM rfqs r
             WHERE r.document_id = d.id
             LIMIT 1) AS rfq_issue_date,

            (SELECT r.due_date
             FROM rfqs r
             WHERE r.document_id = d.id
             LIMIT 1) AS rfq_due_date,

            (SELECT l.doc_type
             FROM legal_documents l
             WHERE l.document_id = d.id
             LIMIT 1) AS legal_type,

            (SELECT l.status
             FROM legal_documents l
             WHERE l.document_id = d.id
             LIMIT 1) AS legal_status,

            (SELECT l.review_status
             FROM legal_documents l
             WHERE l.document_id = d.id
             LIMIT 1) AS legal_review_status,

            (SELECT l.effective_date
             FROM legal_documents l
             WHERE l.document_id = d.id
             LIMIT 1) AS effective_date,

            (SELECT l.expiry_date
             FROM legal_documents l
             WHERE l.document_id = d.id
             LIMIT 1) AS expiry_date,

            (SELECT i.invoice_number
             FROM invoices i
             WHERE i.document_id = d.id
             LIMIT 1) AS invoice_number,

            (SELECT i.amount
             FROM invoices i
             WHERE i.document_id = d.id
             LIMIT 1) AS invoice_amount,

            (SELECT i.currency
             FROM invoices i
             WHERE i.document_id = d.id
             LIMIT 1) AS invoice_currency,

            (SELECT i.invoice_date
             FROM invoices i
             WHERE i.document_id = d.id
             LIMIT 1) AS invoice_date,

            (SELECT i.due_date
             FROM invoices i
             WHERE i.document_id = d.id
             LIMIT 1) AS invoice_due_date,

            (SELECT i.paid_date
             FROM invoices i
             WHERE i.document_id = d.id
             LIMIT 1) AS paid_date

        FROM documents d

        WHERE d.category != 'quote'
          AND (? IS NULL OR d.category = ?)

          AND (
              d.file_name LIKE ?
              OR d.category LIKE ?
              OR d.source LIKE ?

              OR COALESCE(
                  (SELECT v.name
                   FROM vendors v
                   WHERE v.id = d.vendor_id),

                  (SELECT v.name
                   FROM legal_documents l
                   JOIN vendors v ON v.id = l.vendor_id
                   WHERE l.document_id = d.id
                   LIMIT 1),

                  (SELECT v.name
                   FROM invoices i
                   JOIN vendors v ON v.id = i.vendor_id
                   WHERE i.document_id = d.id
                   LIMIT 1)
              ) LIKE ?

              OR EXISTS (
                  SELECT 1 FROM rfqs r
                  WHERE r.document_id = d.id
                    AND (
                        r.rfq_number LIKE ?
                        OR r.title LIKE ?
                    )
              )

              OR EXISTS (
                  SELECT 1 FROM legal_documents l
                  WHERE l.document_id = d.id
                    AND l.doc_type LIKE ?
              )

              OR EXISTS (
                  SELECT 1 FROM invoices i
                  WHERE i.document_id = d.id
                    AND i.invoice_number LIKE ?
              )
          )

        ORDER BY {sort_by} {order_by}, d.id DESC
        """,
        (
            category_filter,
            category_filter,
            like,
            like,
            like,
            like,
            like,
            like,
            like,
            like,
        ),
    )