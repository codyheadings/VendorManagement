import streamlit as st
from pathlib import Path
import db
from services.document_service import *

st.set_page_config(
    page_title="Documents",
    layout="wide"
)
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


# Display helpers
def render_documents(documents, key_prefix="all"):
    """Render a consistent document list."""

    header = st.columns(COL_WIDTHS)

    labels = [
        "DOCUMENT",
        "VENDOR",
        "REFERENCE / DETAILS",
        "DATE ADDED",
        "STATUS",
        "SOURCE",
    ]

    for col, label in zip(header, labels):
        col.caption(f"**{label}**")

    with st.container(height=520, border=True):

        if not documents:
            st.info("No documents match these filters.")
            return

        for doc in documents:
            with st.container(border=True, key=f"docrow_{key_prefix}_{doc['id']}"):

                cols = st.columns(
                    COL_WIDTHS,
                    vertical_alignment="center"
                )

                # Document name and category
                cols[0].write(f"**{doc['file_name']}**")
                cols[0].caption(doc["category"].capitalize())

                # Vendor
                cols[1].write(doc["vendor_name"] or "—")

                # Reference / details
                reference, status = get_document_details(doc)
                cols[2].write(reference)

                if doc["category"] == "rfq" and doc["rfq_due_date"]:
                    cols[2].caption(
                        f"Due {doc['rfq_due_date']}"
                    )

                elif doc["category"] == "legal":
                    if doc["expiry_date"]:
                        cols[2].caption(
                            f"Expires {doc['expiry_date']}"
                        )

                elif doc["category"] == "invoice":
                    if doc["invoice_due_date"]:
                        cols[2].caption(
                            f"Due {doc['invoice_due_date']}"
                        )

                # Date added
                cols[3].write(
                    (doc["created_at"] or "—")[:10]
                )

                # Status
                if status:
                    with cols[4]:
                        st.badge(
                            status.capitalize(),
                            color=STATUS_COLORS.get(status, "gray"),
                        )
                else:
                    cols[4].write("—")

                # Source document preview
                with cols[5]:
                    if doc["file_path"]:
                        if st.button(
                            "View",
                            key=f"src_doc_{key_prefix}_{doc['id']}",
                            type="tertiary",
                            icon=":material/description:",
                        ):
                            show_source(
                                doc["file_name"],
                                doc["file_path"],
                                doc["mime_type"],
                            )
                    else:
                        st.caption("No file")

st.title("Documents")
st.caption("Browse and manage your business documents.")

# Search and sorting controls
f1, f2, f3 = st.columns([3, 1, 1], vertical_alignment="center")

search = f1.text_input(
    "Search",
    placeholder="Search by filename, vendor, reference...",
    label_visibility="collapsed",
    key="document_search",
)

sort = f2.selectbox(
    "Sort by",
    ["Date", "Name", "Category"],
    label_visibility="collapsed",
    key="document_sort",
)

order = f3.selectbox(
    "Order",
    ["Descending", "Ascending"],
    label_visibility="collapsed",
    key="document_order",
)

# Category tabs
tabs = st.tabs([
    "All Documents",
    "RFQs",
    "Legal",
    "Invoices",
    "Emails",
    "Other",
])

categories = [
    None,
    "rfq",
    "legal",
    "invoice",
    "email",
    "other",
]

tab_names = [
    "all",
    "rfq",
    "legal",
    "invoice",
    "email",
    "other",
]

for tab, category, tab_name in zip(tabs, categories, tab_names):
    with tab:
        documents = get_documents(
            category=category,
            search=search,
            sort=sort,
            order=order,
        )

        render_documents(documents, key_prefix=tab_name)