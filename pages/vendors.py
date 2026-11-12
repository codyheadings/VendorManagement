from datetime import datetime
from pathlib import Path
import streamlit as st
import db
from services.email_service import build_rfq_email, send_rfq_email
from services.rfq_service import add_rfq, get_next_rfq_number
from io import BytesIO
from services.rfq_pdf_service import generate_rfq_pdf
from services.vendor_service import add_vendor_with_contact
from services.vendor_service import get_vendor_primary_contact
from services.vendor_service import get_vendor_from_id
from upload_dialog import show_upload_dialog

st.set_page_config(
    page_title="Dashboard",
    layout="wide"
)

# Main page content
st.markdown("# Vendor Overview")
st.caption("View and manage your vendors.")

@st.dialog("Add Vendor", width="medium")
def show_vendor_form():
    with st.form("create_vendor"):
        col1, divider, col2 = st.columns([5, 0.1, 5])
        with col1:
            st.subheader("Vendor Information")
            v_name = st.text_input("Vendor Name", required=True)
            v_category = st.text_input("Category")
            v_country = st.text_input("Country")

        with divider:
            st.html(
                '''
                    <div class="divider-vertical-line"></div>
                    <style>
                        .divider-vertical-line {
                            border-left: 2px solid rgba(120, 120, 120, 0.2);
                            height: 320px;
                            margin: auto;
                        }
                    </style>
                '''
            )

        with col2:
            st.subheader("Primary Contact")
            v_contact = st.text_input("Contact Name", required=True)
            v_email = st.text_input("Email", required=True)
            role = st.text_input("Role")

        submit = st.form_submit_button("Submit", 
            type="primary",
            use_container_width=True)

    if submit:
        if not v_name.strip() or not v_contact.strip() or not v_email.strip():
            st.error("Vendor name, contact name, and email are required.")
        else:
            try:
                add_vendor_with_contact(v_name, v_category, v_country, v_contact, v_email, role)
            except ValueError as e:
                st.error(str(e))
            else:
                st.rerun()


def build_rfq_pdf_data():
    selected_ids = st.session_state.selected_vendor_ids

    vendor_names = [
        get_vendor_from_id(vendor_id)
        for vendor_id in selected_ids
    ]

    return {
        "rfq_id": get_next_rfq_number(),
        "rfq_subtitle": st.session_state.rfq_subtitle,
        "issued_date": st.session_state.issued_date,
        "rfq_deadline": st.session_state.rfq_deadline,
        "rfq_contact": st.session_state.rfq_contact,
        "deliver_to": st.session_state.deliver_to,
        "vendors": vendor_names,
        "items": [
            {
                "line": index,
                "description": st.session_state[f"{row_id}_description"].strip(),
                "quantity": st.session_state[f"{row_id}_quantity"].strip(),
                "unit": st.session_state[f"{row_id}_unit"].strip(),
                "required": st.session_state[f"{row_id}_required"].strip()
            }
            for index, row_id in enumerate(
                st.session_state.rfq_item_rows, start=1
            )
            if st.session_state[f"{row_id}_description"].strip()
        ],
        "specifications": [
            st.session_state[f"{row_id}_value"].strip()
            for row_id in st.session_state["specifications_rows"]
            if st.session_state[f"{row_id}_value"].strip()
        ],
        "quote_requirements": [
            st.session_state[f"{row_id}_value"].strip()
            for row_id in st.session_state["quote_requirements_rows"]
            if st.session_state[f"{row_id}_value"].strip()
        ],
    }

def build_rfq_data():
    return {
        "rfq_id": get_next_rfq_number(),
        "rfq_subtitle": st.session_state.rfq_subtitle,
        "issued_date": st.session_state.issued_date,
        "rfq_deadline": st.session_state.rfq_deadline,
        "rfq_contact": st.session_state.rfq_contact,
        "deliver_to": st.session_state.deliver_to,
        "vendors": list(st.session_state.selected_vendor_ids),
        "items": [
            {
                "line": index,
                "description": st.session_state[f"{row_id}_description"].strip(),
                "quantity": st.session_state[f"{row_id}_quantity"].strip(),
                "unit": st.session_state[f"{row_id}_unit"].strip(),
                "required": st.session_state[f"{row_id}_required"].strip()
            }
            for index, row_id in enumerate(
                st.session_state.rfq_item_rows, start=1
            )
            if st.session_state[f"{row_id}_description"].strip()
        ],
        "specifications": [
            st.session_state[f"{row_id}_value"].strip()
            for row_id in st.session_state["specifications_rows"]
            if st.session_state[f"{row_id}_value"].strip()
        ],
        "quote_requirements": [
            st.session_state[f"{row_id}_value"].strip()
            for row_id in st.session_state["quote_requirements_rows"]
            if st.session_state[f"{row_id}_value"].strip()
        ],
    }

def get_rfq_pdf_path(rfq_id: int) -> Path:
    """Return the absolute path for an RFQ PDF and create its directory."""

    rfq_dir = db.BASE_DIR / "data" / "rfqs"

    rfq_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    return rfq_dir / f"{rfq_id}.pdf"

def get_stored_document_path(file_path: Path) -> str:
    """Convert a file path to a project-relative database path."""

    try:
        return (
            file_path.resolve()
            .relative_to(db.BASE_DIR.resolve())
            .as_posix()
        )
    except ValueError:
        return str(file_path.resolve())

def generate_preview(pdf_data):
    pdf_buffer = BytesIO()

    generate_rfq_pdf(pdf_data, pdf_buffer)

    pdf_buffer.seek(0)
    return pdf_buffer

defaults = {
    "rfq_id": "RFQ-2026-016",
    "rfq_subtitle": "",
    "issued_date": datetime.strptime(db.TODAY, "%Y-%m-%d").date(),
    "rfq_deadline": datetime.strptime(db.TODAY, "%Y-%m-%d").date(),
    "rfq_contact": "sourcing@oddity-labs.example",
    "deliver_to": "ODDITY Labs, Boston, MA, USA",
    "rfq_items": [],
    "specifications": [],
    "quote_requirements": [],
}

for key, value in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value

def initialize_dynamic_list(key, default_values=None):
    if f"{key}_rows" not in st.session_state:
        default_values = default_values or []

        row_ids = []

        for i, value in enumerate(default_values):
            row_id = f"{key}_{i}"
            row_ids.append(row_id)
            st.session_state[f"{row_id}_value"] = value

        st.session_state[f"{key}_rows"] = row_ids


def add_dynamic_row(key):
    row_ids = st.session_state[f"{key}_rows"]

    row_id = f"{key}_{len(row_ids)}_{len(row_ids)}"
    row_ids.append(row_id)

    st.session_state[f"{row_id}_value"] = ""


def remove_dynamic_row(key, row_id):
    st.session_state[f"{key}_rows"].remove(row_id)


def dynamic_list_input(label, key, placeholder="Enter an item..."):

    st.write(f"**{label}**")

    initialize_dynamic_list(key)

    row_ids = st.session_state[f"{key}_rows"]

    for row_id in row_ids:

        col1, col2 = st.columns([5, 1])

        with col1:
            st.text_input(
                label,
                key=f"{row_id}_value",
                placeholder=placeholder,
                label_visibility="collapsed"
            )

        with col2:
            st.button(
                "✕",
                key=f"remove_{row_id}",
                on_click=remove_dynamic_row,
                args=(key, row_id)
            )

    st.button(
        f"+ Add {label}",
        key=f"add_{key}",
        on_click=add_dynamic_row,
        args=(key,)
    )

def initialize_rfq_items():

    if "rfq_item_rows" not in st.session_state:
        st.session_state.rfq_item_rows = []

        # Start with one blank item
        add_rfq_item()


def add_rfq_item():

    row_id = f"rfq_item_{len(st.session_state.rfq_item_rows)}"

    st.session_state.rfq_item_rows.append(row_id)

    st.session_state[f"{row_id}_description"] = ""
    st.session_state[f"{row_id}_quantity"] = ""
    st.session_state[f"{row_id}_unit"] = ""
    st.session_state[f"{row_id}_required"] = ""


def remove_rfq_item(row_id):

    st.session_state.rfq_item_rows.remove(row_id)


def dynamic_rfq_items():

    st.write("**Requested Items**")

    initialize_rfq_items()

    for index, row_id in enumerate(
        st.session_state.rfq_item_rows, start=1
    ):

        st.markdown(f"**Item {index}**")

        description_col, quantity_col, unit_col= st.columns([2.5, .75, .5])

        with description_col:
            st.text_input(
                "Description",
                key=f"{row_id}_description",
                placeholder="e.g. Certificate of Analysis"
            )

        with quantity_col:
            st.text_input(
                "Quantity",
                key=f"{row_id}_quantity",
                placeholder="500"
            )

        with unit_col:
            st.text_input(
                "Unit",
                key=f"{row_id}_unit",
                placeholder="unit"
            )

        date_col, remove_col = st.columns([3, 1])

        with date_col:
            st.text_input(
                "Required",
                key=f"{row_id}_required",
                placeholder="e.g. Included with delivery"
            )

        with remove_col:
            st.write("")
            st.button(
                "Remove",
                key=f"remove_{row_id}",
                on_click=remove_rfq_item,
                args=(row_id,)
            )

        st.divider()

    st.button(
        "+ Add Item",
        key="add_rfq_item",
        on_click=add_rfq_item
    )

@st.dialog("Request a Quote", width="large")
def show_rfq_form():
    initialize_dynamic_list("rfq_items", defaults["rfq_items"])
    initialize_dynamic_list("specifications", defaults["specifications"])
    initialize_dynamic_list("quote_requirements", defaults["quote_requirements"])

    request_col, pdf_col = st.columns(2)

    with request_col:
        with st.container(height=540):
            st.subheader("RFQ Details")

            st.text_input("RFQ Subtitle", key="rfq_subtitle")
            st.date_input("Quote Deadline", key="rfq_deadline")

            st.text_input("Contact", key="rfq_contact")
            st.text_input("Deliver To", key="deliver_to")

            st.divider()

            dynamic_rfq_items()

            st.divider()

            dynamic_list_input(
                "Specifications",
                "specifications",
                "Enter a technical specification..."
            )

            st.divider()

            dynamic_list_input(
                "Quote Requirements",
                "quote_requirements",
                "Enter a quotation requirement..."
            )
            
            st.divider()

            st.write("**Requesting From:**")
            
            for vendor_id in st.session_state.selected_vendor_ids:
                st.write(get_vendor_from_id(vendor_id))


    with pdf_col:
        st.subheader("PDF Preview")
        pdf_data = build_rfq_pdf_data()

        if not pdf_data["rfq_subtitle"].strip():
            st.info("Enter an RFQ subtitle to generate your preview.")
        else:
            pdf_buffer = generate_preview(pdf_data)
            st.pdf(pdf_buffer.getvalue(), height=520)

    st.divider()

    subtitle_valid = bool(st.session_state.rfq_subtitle.strip())
    if not subtitle_valid:
        st.caption("RFQ subtitle cannot be blank.")
        st.session_state.rfq_submitted = False

    submit_col, download_col, email_col, _ = st.columns([.4, .4, 2, 1])
    send_results = st.container()
    st.session_state.new_rfq_id = None

    with submit_col:
        submitted = st.button(
            "Create RFQ",
            type="primary",
            disabled=not subtitle_valid,
            use_container_width=True
        )

    if submitted:
        pdf_data = build_rfq_pdf_data()
        rfq_data = build_rfq_data()

        pdf_path = get_rfq_pdf_path(pdf_data["rfq_id"])

        with open(pdf_path, "wb") as pdf_file:
            generate_rfq_pdf(pdf_data, pdf_file)

        st.session_state.submitted_pdf = pdf_path.read_bytes()

        stored_path = get_stored_document_path(pdf_path)

        st.session_state.new_rfq_id = add_rfq(rfq_data, stored_path)

        st.session_state.submitted_rfq_id = pdf_data["rfq_id"]
        st.session_state.rfq_submitted = True
        st.session_state.send_results = None

        st.success("RFQ created successfully! Download the file or send to vendors.")

    with download_col:
        if st.session_state.get("rfq_submitted", False):
            st.download_button(
                label="Download PDF",
                data=st.session_state.submitted_pdf,
                file_name=f"{st.session_state.submitted_rfq_id}.pdf",
                mime="application/pdf",
                use_container_width=True
            )

    with email_col:
        if st.session_state.get("rfq_submitted", False):
            with st.container(horizontal=True):
                if st.button("Send to Selected"):
                    pdf_data = build_rfq_pdf_data()
                    pdf_bytes = st.session_state.submitted_pdf

                    sent_vendors = []
                    skipped_vendors = []
                    failed_vendors = []

                    with st.spinner("Sending RFQ emails to selected vendors..."):
                        for vendor_id in st.session_state.selected_vendor_ids:
                            vendor = get_vendor_primary_contact(vendor_id)

                            if not vendor or not vendor["contact_name"] or not vendor["email"]:
                                skipped_vendors.append(
                                    vendor["vendor_name"] if vendor else f"Vendor ID {vendor_id}"
                                )
                                continue

                            try:
                                email = build_rfq_email(vendor, pdf_data, pdf_bytes)
                                send_rfq_email(email)
                                sent_vendors.append(vendor["vendor_name"])

                            except Exception as e:
                                failed_vendors.append({
                                    "vendor": vendor["vendor_name"],
                                    "error": str(e)
                                })

                    # Update RFQ status if at least one email was successfully sent
                    if sent_vendors:
                        try:
                            db.execute(
                                """
                                UPDATE rfqs
                                SET status = 'open'
                                WHERE id = ?
                                """,
                                (st.session_state.new_rfq_id,)
                            )

                        except Exception as e:
                            st.error(f"Emails were sent, but RFQ status could not be updated: {e}")

                    # Store results after sending is complete
                    st.session_state.send_results = {
                        "sent": sent_vendors,
                        "skipped": skipped_vendors,
                        "failed": failed_vendors
                    }

    if st.session_state.get("send_results") is not None:
        results = st.session_state.send_results

        with send_results:
            st.divider()
            st.subheader("Email Sending Results")

            with st.container(height=250, border=True):
                if results["sent"]:
                    st.success(
                        f"Successfully sent to {len(results['sent'])} vendor(s)."
                    )

                    for vendor in results["sent"]:
                        st.write(f"- {vendor}")

                if results["skipped"]:
                    st.warning(
                        f"Skipped {len(results['skipped'])} vendor(s) "
                        "without a primary contact or email."
                    )

                    for vendor in results["skipped"]:
                        st.write(f"- {vendor}")

                if results["failed"]:
                    st.error(
                        f"Failed to send to {len(results['failed'])} vendor(s)."
                    )

                    for item in results["failed"]:
                        st.write(f"**{item['vendor']}**: {item['error']}")

                if not any(results.values()):
                    st.info("No vendors were selected.")

def update_vendor_selection(vendor_id):
    checkbox_key = f"select_vendor_{vendor_id}"

    if st.session_state[checkbox_key]:
        st.session_state.selected_vendor_ids.add(vendor_id)
    else:
        st.session_state.selected_vendor_ids.discard(vendor_id)

def reset_vendor_selection():
    for vendor_id in list(st.session_state.selected_vendor_ids):
        checkbox_key = f"select_vendor_{vendor_id}"

        if checkbox_key in st.session_state:
            st.session_state[checkbox_key] = False

    st.session_state.selected_vendor_ids.clear()

if "selected_vendor_ids" not in st.session_state:
    st.session_state.selected_vendor_ids = set()

vendors_col, insights_col = st.columns([2, 1], gap="large")

with vendors_col:
    STATUS_COLORS = {"active": "green", "inactive": "yellow", "blocked": "red"}
    COL_WIDTHS = [0.4, 3, 2, 2, 3, 1.5]

    PAGE_SIZE = 20

    def reset_limit():
        st.session_state.vendor_limit = PAGE_SIZE

    selected_ids = st.session_state.selected_vendor_ids

    search = st.text_input(
        "Search", key="vendor_search", on_change=reset_limit,
        placeholder="Search by name, category or country", label_visibility="collapsed",
    )
    limit = st.session_state.setdefault("vendor_limit", PAGE_SIZE)

    like = f"%{search.strip()}%"
    where = "WHERE v.name LIKE ? OR v.category LIKE ? OR v.country LIKE ?"

    total = db.scalar(f"SELECT COUNT(*) FROM vendors v {where}", (like, like, like))
    vendors = db.query(f"""
        SELECT v.id, v.name, v.category, v.country, v.status,
            c.name AS primary_contact, c.email AS contact_email
        FROM vendors v
        LEFT JOIN vendor_contacts c ON c.vendor_id = v.id AND c.is_primary = 1
        {where}
        ORDER BY v.name
        LIMIT ?
    """, (like, like, like, limit))

    header = st.columns(COL_WIDTHS)

    for col, label in zip(header, ["", "Vendor", "Category", "Country", "Primary Contact", "Status"]):
        if label:
            col.caption(f"**{label.upper()}**")

    with st.container(height=540, border=True):
        for v in vendors:
            checkbox_key = f"select_vendor_{v['id']}"

            # Restore checkbox state from persistent selection
            if checkbox_key not in st.session_state:
                st.session_state[checkbox_key] = (
                    v["id"] in st.session_state.selected_vendor_ids
                )

            with st.container(key=f"row_{v['id']}", border=True):
                cols = st.columns(COL_WIDTHS, vertical_alignment="center")

                cols[0].checkbox(
                    "Select",
                    key=checkbox_key,
                    label_visibility="collapsed",
                    on_change=update_vendor_selection,
                    args=(v["id"],),
                )

                if cols[1].button(v["name"], key=f"vendor_{v['id']}", type="tertiary"):
                    st.session_state["selected_vendor_id"] = v["id"]
                    st.switch_page("pages/vendor_profile.py")

                cols[2].write(v["category"] or "—")
                cols[3].write(v["country"] or "—")

                with cols[4]:
                    if v["primary_contact"]:
                        st.write(v["primary_contact"])
                        st.caption(v["contact_email"])
                    else:
                        st.caption("No primary contact")

                with cols[5]:
                    with st.container(horizontal_alignment="center"):
                        st.badge(v["status"].capitalize(), color=STATUS_COLORS.get(v["status"], "gray"))

        if limit < total:
            if st.button(f"Load more ({total - limit} remaining)", use_container_width=True):
                st.session_state.vendor_limit += PAGE_SIZE
                st.rerun()

    upload_col, add_col, rfq_col, deselect_col, spacer = st.columns([1, 1, 1.5, 1, 4])

    with upload_col:
        if st.button("Upload files", use_container_width=True):
            show_upload_dialog()

    with add_col:
        st.button("Add Vendor", on_click=show_vendor_form, use_container_width=True)

    with rfq_col:
        if selected_ids:
            if st.button(f"Send RFQ ({len(selected_ids)})", type="primary", use_container_width=True):
                st.session_state["rfq_vendor_ids"] = list(selected_ids)
                st.session_state.rfq_submitted = False
                st.session_state.submitted_pdf = None
                st.session_state.submitted_rfq_id = None
                st.session_state.send_results = None
                show_rfq_form()

    with deselect_col:
        if selected_ids:
            st.button("Deselect All", on_click=reset_vendor_selection, use_container_width=True)

with insights_col:
    with st.container(horizontal_alignment="center", height="stretch"):
        st.subheader("Insights")

        col1, col2, col3 = st.columns(3)
        with col1:
            total_vendors = db.scalar("SELECT COUNT(*) FROM vendors")
            st.metric("Total Vendors", total_vendors)

        with col2:
            active_vendors = db.scalar("SELECT COUNT(*) FROM vendors WHERE status LIKE 'active'")
            st.metric("Active Vendors", active_vendors)

        with col3:
            vendor_countries = db.scalar("SELECT COUNT(DISTINCT country) FROM vendors")
            st.metric("Countries", vendor_countries)
            
        st.divider()
        st.metric(
            "Open Quote Requests",
            db.scalar("SELECT COUNT(*) FROM rfqs WHERE status = 'open'"),
        )
        st.divider()

        st.metric(
            "Documents Pending",
            db.scalar("SELECT COUNT(*) FROM quotes WHERE review_status = 'pending'"),
        )
        st.divider()