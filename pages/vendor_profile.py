from pathlib import Path
from services.document_service import *
import streamlit as st
import pandas as pd
import db
import sqlite3

st.set_page_config(
    page_title="Vendor Profile",
    layout="wide"
)

REVIEW_COLORS = {
    "pending": "orange",
    "approved": "green",
    "rejected": "red",
}


def fmt_price(amount, currency, rate_to_usd):
    """Format quote price, converting to USD when possible."""

    if amount is None:
        return "—", "Total not stated"

    if currency == "USD":
        return f"${amount:,.2f}", ""

    if rate_to_usd:
        return (
            f"${amount * rate_to_usd:,.2f}",
            f"converted from {amount:,.2f} {currency}"
        )

    return (
        f"{amount:,.2f} {currency}",
        "no exchange rate on file"
    )

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

header_col, status_col = st.columns([5, .35])

with header_col:
    st.title(vendor["name"])
    st.caption("Vendor Profile")
with status_col:
    status = (vendor["status"] or "unknown").capitalize()
    if status == "Active":
        st.success(f"● {status}")
    else:
        st.warning(f"● {status}")

st.divider()

# Key Information

col1, col2, col3 = st.columns(3)

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

with col3:
    st.metric(
        label="Vendor Status",
        value=status
    )

st.divider()

@st.dialog("Add Vendor Contact")
def add_contact_dialog(vendor_id):

    st.caption("Add a new contact for this vendor.")

    with st.form("add_vendor_contact_form", clear_on_submit=True):

        name = st.text_input(
            "Full Name *",
            placeholder="e.g. Anna Müller",
        )

        email = st.text_input(
            "Email Address",
            placeholder="anna@example.com",
        )

        phone = st.text_input(
            "Phone Number",
            placeholder="+49 30 12345678",
        )

        role = st.text_input(
            "Job Title / Role",
            placeholder="e.g. Sales Representative",
        )

        is_primary = st.checkbox(
            "Set as primary contact",
            value=False,
        )

        submitted = st.form_submit_button(
            "Save Contact",
            type="primary",
            use_container_width=True,
        )

        if submitted:

            if not name.strip():
                st.error("Please enter the contact's name.")

            else:
                try:
                    add_vendor_contact(
                        vendor_id=vendor_id,
                        name=name,
                        email=email,
                        phone=phone,
                        role=role,
                        is_primary=is_primary,
                    )

                    st.success("Contact added successfully!")
                    st.rerun()

                except ValueError as e:
                    st.error(str(e))


@st.dialog("Delete Contact")
def delete_contact_dialog(vendor_id, contact_id, contact_name):

    st.warning(
        f"Are you sure you want to delete {contact_name}?"
    )

    st.caption("This action cannot be undone.")

    col1, col2 = st.columns(2)

    with col1:
        if st.button(
            "Cancel",
            use_container_width=True,
        ):
            st.rerun()

    with col2:
        if st.button(
            "Delete Contact",
            type="primary",
            use_container_width=True,
            icon=":material/delete:",
        ):
            try:
                delete_vendor_contact(
                    vendor_id,
                    contact_id,
                )

                st.success("Contact deleted successfully.")
                st.rerun()

            except ValueError as e:
                st.error(str(e))


@st.dialog("Edit Vendor Contact")
def edit_contact_dialog(vendor_id, contact):

    st.caption("Update the vendor contact information.")

    with st.form(
        f"edit_vendor_contact_{contact['id']}",
        clear_on_submit=False,
    ):

        name = st.text_input(
            "Full Name *",
            value=contact["name"] or "",
        )

        email = st.text_input(
            "Email Address",
            value=contact["email"] or "",
        )

        phone = st.text_input(
            "Phone Number",
            value=contact["phone"] or "",
        )

        role = st.text_input(
            "Job Title / Role",
            value=contact["role"] or "",
        )

        is_primary = st.checkbox(
            "Set as primary contact",
            value=bool(contact["is_primary"]),
        )

        submitted = st.form_submit_button(
            "Save Changes",
            type="primary",
            use_container_width=True,
        )

        if submitted:

            if not name.strip():
                st.error("Please enter the contact's name.")

            else:
                try:
                    update_vendor_contact(
                        vendor_id=vendor_id,
                        contact_id=contact["id"],
                        name=name,
                        email=email,
                        phone=phone,
                        role=role,
                        is_primary=is_primary,
                    )

                    st.success("Contact updated successfully!")
                    st.rerun()

                except ValueError as e:
                    st.error(str(e))

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

def get_vendor_quotes(vendor_id, search="", status_filter="All",
                      sort="Date", order="Descending"):

    like = f"%{search.strip()}%"
    status = status_filter.lower()

    sort_columns = {
        "Date": "q.quote_date",
        "Price": "q.total_amount",
        "Delivery": "q.delivery_time_days",
    }

    sort_by = sort_columns[sort]
    order_by = "DESC" if order == "Descending" else "ASC"

    return db.query(
        f"""
        SELECT
            q.id,
            q.reference_number,
            q.quote_date,
            q.valid_until,
            q.currency,
            q.total_amount,
            q.delivery_time_days,
            q.review_status,
            q.confidence,

            v.name AS vendor_name,

            r.rfq_number,

            d.file_name,
            d.file_path,
            d.mime_type,

            er.rate_to_usd,

            EXISTS (
                SELECT 1
                FROM quotes q2
                WHERE q2.supersedes_quote_id = q.id
                  AND q2.review_status = 'approved'
            ) AS superseded

        FROM quotes q

        JOIN vendors v
            ON v.id = q.vendor_id

        LEFT JOIN rfqs r
            ON r.id = q.rfq_id

        LEFT JOIN documents d
            ON d.id = q.document_id

        LEFT JOIN exchange_rates er
            ON er.currency = q.currency

        WHERE q.vendor_id = ?

          AND (
              q.reference_number LIKE ?
              OR COALESCE(r.rfq_number, '') LIKE ?
              OR COALESCE(r.title, '') LIKE ?
          )

          AND (
              ? = 'all'
              OR q.review_status = ?
          )

        ORDER BY {sort_by} {order_by}, q.id DESC
        """,
        (
            vendor_id,
            like,
            like,
            like,
            status,
            status,
        ),
    )


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

                    st.badge(
                        review_status.capitalize(),
                        color=REVIEW_COLORS.get(
                            review_status,
                            "gray"
                        ),
                    )

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

def add_vendor_contact(
    vendor_id: int,
    name: str,
    email: str = "",
    phone: str = "",
    role: str = "",
    is_primary: bool = False,
):
    """Add a vendor contact, optionally replacing the primary contact."""
    try:
        with db.transaction() as conn:
            # Unset the existing primary contact if requested
            if is_primary:
                conn.execute(
                    """
                    UPDATE vendor_contacts
                    SET is_primary = 0
                    WHERE vendor_id = ?
                      AND is_primary = 1
                    """,
                    (vendor_id,),
                )

            # Insert the new contact
            conn.execute(
                """
                INSERT INTO vendor_contacts (
                    vendor_id,
                    name,
                    email,
                    phone,
                    role,
                    is_primary
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    vendor_id,
                    name.strip(),
                    email.strip() or None,
                    phone.strip() or None,
                    role.strip() or None,
                    int(is_primary),
                ),
            )

    except sqlite3.IntegrityError as e:
        msg = str(e)

        if "CHECK constraint failed" in msg and "email" in msg:
            raise ValueError(
                "The submitted email address is incorrectly formatted."
            ) from e

        if "UNIQUE constraint failed" in msg:
            raise ValueError(
                "A contact with this email already exists for this vendor."
            ) from e

        raise ValueError(f"Could not add contact: {msg}") from e


def delete_vendor_contact(vendor_id: int, contact_id: int):
    """Delete a non-primary vendor contact."""

    try:
        with db.transaction() as conn:

            result = conn.execute(
                """
                DELETE FROM vendor_contacts
                WHERE id = ?
                  AND vendor_id = ?
                  AND is_primary = 0
                """,
                (contact_id, vendor_id),
            )

            if result.rowcount == 0:
                raise ValueError(
                    "Contact could not be deleted. "
                    "It may be the primary contact or it no longer exists."
                )

    except sqlite3.IntegrityError as e:
        raise ValueError(
            f"Could not delete contact: {e}"
        ) from e


def update_vendor_contact(
    vendor_id: int,
    contact_id: int,
    name: str,
    email: str = "",
    phone: str = "",
    role: str = "",
    is_primary: bool = False,
):
    """Update a vendor contact, optionally replacing the primary contact."""

    try:
        with db.transaction() as conn:

            if is_primary:
                conn.execute(
                    """
                    UPDATE vendor_contacts
                    SET is_primary = 0
                    WHERE vendor_id = ?
                      AND id != ?
                      AND is_primary = 1
                    """,
                    (vendor_id, contact_id),
                )

            result = conn.execute(
                """
                UPDATE vendor_contacts
                SET
                    name = ?,
                    email = ?,
                    phone = ?,
                    role = ?,
                    is_primary = ?
                WHERE id = ?
                  AND vendor_id = ?
                """,
                (
                    name.strip(),
                    email.strip() or None,
                    phone.strip() or None,
                    role.strip() or None,
                    int(is_primary),
                    contact_id,
                    vendor_id,
                ),
            )

            if result.rowcount == 0:
                raise ValueError("Contact no longer exists.")

    except sqlite3.IntegrityError as e:
        msg = str(e)

        if "CHECK constraint failed" in msg and "email" in msg:
            raise ValueError(
                "The submitted email address is incorrectly formatted."
            ) from e

        if "UNIQUE constraint failed" in msg:
            raise ValueError(
                "A contact with this email already exists for this vendor."
            ) from e

        raise ValueError(f"Could not update contact: {msg}") from e


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
                        st.caption(f"role: {contact["role"]}")
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


def get_vendor_documents(vendor_id, category=None, search="", sort="Date",
                         order="Descending"):

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

            v.name AS vendor_name,

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

            (SELECT i.due_date
             FROM invoices i
             WHERE i.document_id = d.id
             LIMIT 1) AS invoice_due_date,

            (SELECT i.paid_date
             FROM invoices i
             WHERE i.document_id = d.id
             LIMIT 1) AS paid_date

        FROM documents d

        LEFT JOIN vendors v ON v.id = d.vendor_id

        WHERE d.category != 'quote'

        AND (
            d.vendor_id = ?

            OR EXISTS (
                SELECT 1
                FROM legal_documents l
                WHERE l.document_id = d.id
                  AND l.vendor_id = ?
            )

            OR EXISTS (
                SELECT 1
                FROM invoices i
                WHERE i.document_id = d.id
                  AND i.vendor_id = ?
            )
        )

        AND (? IS NULL OR d.category = ?)

        AND (
            d.file_name LIKE ?
            OR d.category LIKE ?
            OR d.source LIKE ?
            OR COALESCE(v.name, '') LIKE ?

            OR EXISTS (
                SELECT 1
                FROM rfqs r
                WHERE r.document_id = d.id
                  AND (
                      r.rfq_number LIKE ?
                      OR r.title LIKE ?
                  )
            )

            OR EXISTS (
                SELECT 1
                FROM legal_documents l
                WHERE l.document_id = d.id
                  AND l.doc_type LIKE ?
            )

            OR EXISTS (
                SELECT 1
                FROM invoices i
                WHERE i.document_id = d.id
                  AND i.invoice_number LIKE ?
            )
        )

        ORDER BY {sort_by} {order_by}, d.id DESC
        """,
        (
            vendor_id,
            vendor_id,
            vendor_id,
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

with tab4:

    st.subheader("Vendor Documents")

    st.caption(
        f"Documents associated with {vendor['name']}."
    )

    # Search and sorting
    f1, f2, f3 = st.columns(
        [3, 1, 1],
        vertical_alignment="center"
    )

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