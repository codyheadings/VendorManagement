import streamlit as st
from services.document_service import *
import db

db.ensure_db()

st.set_page_config(
    page_title="Documents",
    layout="wide"
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
                if doc["category"] == "legal" and status in ("draft", "sent"):
                    cols[2].write(f"{reference} - Unsigned")
                elif doc["category"] == "legal" and status == "signed":
                    cols[2].write(f"{reference} - Signed by {doc["signer"] or 'Unknown'}")
                elif doc["category"] == "legal":
                    cols[2].write(f"{reference} - Sign status unknown")
                else:
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
                        with st.container(horizontal=True, vertical_alignment="center"):
                            st.badge(status.capitalize(), color=STATUS_COLORS.get(status,"gray"))
                            if (status == "pending" or status == "unknown"):
                                if st.button("Review"):
                                    st.switch_page("pages/review_queue.py")
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