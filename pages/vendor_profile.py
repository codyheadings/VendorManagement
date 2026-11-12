from services.document_service import *
import streamlit as st
import db
from services.vendor_service import *
from st_helpers.vendor_profile_dialogs import *
from util import fmt_price
from services.purchasing_service import vendor_summary

st.set_page_config(
    page_title="Vendor Profile",
    layout="wide"
)

REVIEW_COLORS = {
    "pending": "orange",
    "approved": "green",
    "rejected": "red",
}

vendor_id = st.session_state.get("selected_vendor_id")

if vendor_id is None:
    st.info("Select a vendor from the dashboard.")
    st.stop()

vendor = db.query_one(
    "SELECT * FROM vendors WHERE id = ?",
    (vendor_id,)
)

if vendor is None:
    st.error("That vendor no longer exists.")
    st.stop()

header_col, _, action_col = st.columns([3, 3.5, 1])

with header_col:
    st.title(vendor["name"])
    st.caption("Vendor Profile")

with action_col:
    status = (vendor["status"] or "unknown").capitalize()
        
    if status == "Active":
        st.success(f"● {status}")

    elif status == "Inactive":
        st.warning(f"● {status}")

    else:
        st.error(f"● {status}")

    if st.button("Edit Vendor", icon=":material/edit:", use_container_width=True,):
        edit_vendor_dialog(vendor)

st.divider()

# Key Information
vendor_sum = vendor_summary(vendor_id)
col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric(
        label="Category",
        value=vendor["category"] or "Not specified"
    )

with col2:
    st.metric(
        label="Country",
        value=vendor["country"] or "Not specified"
    )

with col4:
    st.metric(
        label="Spent on this Vendor",
        value=f"${vendor_sum["paid"]}"
    )

st.divider()

tab1, tab2, tab3, tab4 = st.tabs([
    "Overview",
    "Quotes",
    "Contacts",
    "Documents"
])

# Overview
with tab1:
    st.subheader("Vendor Information")

    info_col1, info_col2 = st.columns(2)

    with info_col1:
        with st.container(border=True):
            st.markdown("**Vendor Name**")
            st.write(vendor["name"] or "Not specified")

        with st.container(border=True):
            st.markdown("**Category**")
            st.write(vendor["category"] or "Not specified")

    with info_col2:
        with st.container(border=True):
            st.markdown("**Country**")
            st.write(vendor["country"] or "Not specified")

        with st.container(border=True):
            st.markdown("**Status**")
            st.write(status)

    st.divider()

    st.subheader("Notes")

    notes = vendor["notes"]

    if notes and notes.strip():
        st.info(notes)
    else:
        st.caption("No notes have been added for this vendor.")

# Quotes
def render_vendor_quotes(quotes, key_prefix="vendor"):
    col_widths = [1.8, 2.2, 1.3, 1.2, 1.5, 1.5, 1.5]

    headers = st.columns(col_widths)

    labels = [
        "QUOTE REF",
        "PRICE",
        "DELIVERY",
        "QUOTE DATE",
        "VALID UNTIL",
        "REVIEW",
        "SOURCE",
    ]

    for col, label in zip(headers, labels):
        col.caption(f"**{label}**")

    with st.container(height=520, border=True):
        if not quotes:
            st.info("No quotes found for this vendor.")
            return

        for q in quotes:
            with st.container(
                border=True,
                key=f"vendor_quote_{key_prefix}_{q['id']}"
            ):

                cols = st.columns(
                    col_widths,
                    vertical_alignment="center"
                )

                # Quote reference and RFQ
                cols[0].write(
                    f"**{q['reference_number'] or '—'}**"
                )

                cols[0].caption(
                    q["rfq_number"] or "No RFQ linked"
                )

                # Price
                main, note = fmt_price(
                    q["total_amount"],
                    q["currency"],
                    q["rate_to_usd"],
                )

                cols[1].write(main)

                if note:
                    cols[1].caption(note)

                # Delivery time
                days = q["delivery_time_days"]

                cols[2].write(
                    f"{days} days" if days is not None else "—"
                )

                # Quote date
                cols[3].write(q["quote_date"] or "—")

                # Validity
                cols[4].write(q["valid_until"] or "—")

                if (
                    q["valid_until"]
                    and q["valid_until"] < db.TODAY
                ):
                    cols[4].caption(":red[Expired]")

                # Review status
                with cols[5]:

                    review_status = q["review_status"] or "unknown"

                    with st.container(horizontal=True, vertical_alignment="center"):
                        st.badge(
                            review_status.capitalize(),
                            color=REVIEW_COLORS.get(
                                review_status,
                                "gray"
                            ),
                        )
                        if (review_status == "pending"):
                            if st.button("Review"):
                                st.switch_page("pages/review_queue.py")

                    if q["superseded"]:
                        st.caption("Superseded by revision")

                    elif (
                        review_status == "pending"
                        and q["confidence"] is not None
                    ):
                        st.caption(
                            f"Confidence {q['confidence']:.0%}"
                        )

                # Source document
                with cols[6]:

                    if q["file_path"]:

                        if st.button(
                            "View source",
                            key=f"vendor_quote_src_{key_prefix}_{q['id']}",
                            type="tertiary",
                            icon=":material/description:",
                        ):

                            show_source(
                                q["file_name"],
                                q["file_path"],
                                q["mime_type"],
                            )

                    else:
                        st.caption("No source file")

with tab2:
    st.subheader("Quote History")
    st.caption(f"Quotes received from {vendor['name']}.")

    summary = db.query_one(
        """
        SELECT
            COUNT(*) AS total_quotes,

            SUM(
                CASE WHEN review_status = 'pending'
                THEN 1 ELSE 0 END
            ) AS pending_quotes,

            SUM(
                CASE WHEN review_status = 'approved'
                THEN 1 ELSE 0 END
            ) AS approved_quotes,

            SUM(
                CASE WHEN review_status = 'rejected'
                THEN 1 ELSE 0 END
            ) AS rejected_quotes

        FROM quotes
        WHERE vendor_id = ?
        """,
        (vendor_id,)
    )

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric(
            "Total Quotes",
            summary["total_quotes"] or 0
        )

    with col2:
        st.metric(
            "Pending Review",
            summary["pending_quotes"] or 0
        )

    with col3:
        st.metric(
            "Approved",
            summary["approved_quotes"] or 0
        )

    with col4:
        st.metric(
            "Rejected",
            summary["rejected_quotes"] or 0
        )

    st.divider()

    # Search and filtering controls
    f1, f2, f3, f4 = st.columns(
        [3, 2, 0.8, 1],
        vertical_alignment="center"
    )

    search = f1.text_input(
        "Search",
        placeholder="Search quote reference or RFQ...",
        label_visibility="collapsed",
        key="vendor_quote_search",
    )

    status_filter = f2.segmented_control(
        "Review status",
        ["All", "Pending", "Approved", "Rejected"],
        default="All",
        label_visibility="collapsed",
        key="vendor_quote_status",
    ) or "All"

    sort = f3.selectbox(
        "Sort By",
        ["Date", "Price", "Delivery"],
        label_visibility="collapsed",
        key="vendor_quote_sort",
    )

    order = f4.selectbox(
        "Order",
        ["Descending", "Ascending"],
        label_visibility="collapsed",
        key="vendor_quote_order",
    )

    # Retrieve vendor-specific quotes
    quotes = get_vendor_quotes(
        vendor_id=vendor_id,
        search=search,
        status_filter=status_filter,
        sort=sort,
        order=order,
    )

    render_vendor_quotes(
        quotes,
        key_prefix=f"vendor_{vendor_id}",
    )

# Contacts
with tab3:
    header_col, button_col = st.columns(
        [5, .5],
        vertical_alignment="center"
    )

    with header_col:
        st.subheader("Vendor Contacts")

    with button_col:
        if st.button(
            "Add Contact",
            type="primary",
            icon=":material/person_add:",
            use_container_width=True,
        ):
            add_contact_dialog(vendor_id)

    contacts = db.query(
        """
        SELECT
            id,
            name,
            email,
            phone,
            role,
            is_primary
        FROM vendor_contacts
        WHERE vendor_id = ?
        ORDER BY is_primary DESC, name ASC
        """,
        (vendor_id,)
    )

    if not contacts:
        st.info("No contacts have been added for this vendor.")

        st.caption(
            "Use the Add Contact button to create the first contact."
        )

    else:
        st.caption(f"{len(contacts)} contact(s)")

        for contact in contacts:
            with st.container(border=True):
                col1, _, col2 = st.columns(
                    [5, 3, 1],
                    vertical_alignment="bottom"
                )
                with col1:
                    st.markdown(f"### {contact['name']}")

                    if contact["role"]:
                        st.caption(f"{contact["role"]}")
                    else: 
                        st.caption("Role unknown")

                    detail_col1, detail_col2 = st.columns(2)
                    
                    with detail_col1:
    
                        st.markdown("**Email**")
    
                        if contact["email"]:
                            st.markdown(
                                f"[{contact['email']}](mailto:{contact['email']})"
                            )
                        else:
                            st.caption("Not provided")
    
                    with detail_col2:
    
                        st.markdown("**Phone**")
    
                        if contact["phone"]:
                            st.write(contact["phone"])
                        else:
                            st.caption("Not provided")

                with col2:
                    if contact["is_primary"]:
                        st.success("★ Primary Contact")

                    if st.button(
                        "Edit",
                        key=f"edit_contact_{contact['id']}",
                        icon=":material/edit:",
                        use_container_width=True,
                    ):
                        edit_contact_dialog(
                            vendor_id,
                            contact,
                        )

                    if st.button(
                        "Delete",
                        key=f"delete_contact_{contact['id']}",
                        type="secondary",
                        icon=":material/delete:",
                        disabled=bool(contact["is_primary"]),
                        use_container_width=True,
                        help=(
                            "The primary contact cannot be deleted."
                            if contact["is_primary"]
                            else "Delete this contact"
                        ),
                    ):
                        delete_contact_dialog(
                            vendor_id,
                            contact["id"],
                            contact["name"],
                        )

# Documents
def render_vendor_documents(documents, key_prefix="vendor"):
    col_widths = [2.5, 2.5, 1.5, 1.5, 1]

    headers = st.columns(col_widths)

    labels = [
        "DOCUMENT",
        "REFERENCE / DETAILS",
        "DATE ADDED",
        "STATUS",
        "FILE",
    ]

    for col, label in zip(headers, labels):
        col.caption(f"**{label}**")

    with st.container(height=520, border=True):
        if not documents:
            st.info("No documents found for this vendor.")
            return

        for doc in documents:
            with st.container(border=True):
                cols = st.columns(
                    col_widths,
                    vertical_alignment="center"
                )

                # Document name
                cols[0].write(f"**{doc['file_name']}**")
                cols[0].caption(doc["category"].capitalize())

                # Reference / details
                reference, status = get_document_details(doc)

                cols[1].write(reference)

                if doc["category"] == "rfq" and doc["rfq_due_date"]:
                    cols[1].caption(f"Due {doc['rfq_due_date']}")

                elif doc["category"] == "legal" and doc["expiry_date"]:
                    cols[1].caption(f"Expires {doc['expiry_date']}")

                elif doc["category"] == "invoice" and doc["invoice_due_date"]:
                    cols[1].caption(f"Due {doc['invoice_due_date']}")

                # Date added
                cols[2].write(
                    (doc["created_at"] or "—")[:10]
                )

                # Status
                if status:
                    with cols[3]:
                        st.badge(
                            status.capitalize(),
                            color=STATUS_COLORS.get(
                                status.lower(), "gray"
                            ),
                        )
                else:
                    cols[3].write("—")

                # Preview
                with cols[4]:
                    if doc["file_path"]:
                        if st.button(
                            "View",
                            key=f"vendor_doc_{key_prefix}_{doc['id']}",
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

# Documents
with tab4:

    st.subheader("Vendor Documents")

    st.caption(
        f"Documents associated with {vendor['name']}."
    )

    # Search and sorting
    f1, f2, f3 = st.columns([3, 1, 1], vertical_alignment="center")

    search = f1.text_input(
        "Search",
        placeholder="Search documents...",
        label_visibility="collapsed",
        key="vendor_document_search",
    )

    sort = f2.selectbox(
        "Sort by",
        ["Date", "Name", "Category"],
        label_visibility="collapsed",
        key="vendor_document_sort",
    )

    order = f3.selectbox(
        "Order",
        ["Descending", "Ascending"],
        label_visibility="collapsed",
        key="vendor_document_order",
    )

    # Document categories
    document_tabs = st.tabs([
        "All Documents",
        "Legal",
        "Invoices",
        "Emails",
        "Other",
    ])

    categories = [
        None,
        "legal",
        "invoice",
        "email",
        "other",
    ]

    tab_keys = [
        "all",
        "legal",
        "invoice",
        "email",
        "other",
    ]

    for doc_tab, category, tab_key in zip(
        document_tabs, categories, tab_keys
    ):

        with doc_tab:

            documents = get_vendor_documents(
                vendor_id=vendor_id,
                category=category,
                search=search,
                sort=sort,
                order=order,
            )

            st.caption(
                f"{len(documents)} document(s)"
            )

            render_vendor_documents(
                documents,
                key_prefix=tab_key,
            )


st.divider()

if st.button("Return to Dashboard"):
    st.switch_page("pages/vendors.py")