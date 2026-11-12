from datetime import datetime
from io import BytesIO
from pathlib import Path

import streamlit as st

import db
from services.email_service import build_rfq_email, send_email
from services.rfq_pdf_service import generate_rfq_pdf
from services.rfq_service import RfqError, add_rfq, get_next_rfq_number, mark_rfq_sent, vendors_not_yet_sent
from services.vendor_service import get_vendor_from_id, get_vendor_primary_contact
from st_helpers.rfq_form_helpers import *

def send_rfq_email(email):
    if not db.SEND_ENABLED:
        raise SystemError("Sending mail on this app has been disabled.")
    else:
        send_email(email)

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
                    sent_ids = []
                    skipped_vendors = []
                    failed_vendors = []

                    rfq_id = st.session_state.new_rfq_id
                    selected = vendors_not_yet_sent(rfq_id, st.session_state.selected_vendor_ids)

                    with st.spinner("Sending RFQ emails to selected vendors..."):
                        for vendor_id in selected:
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
                                sent_ids.append(vendor["vendor_id"])

                            except Exception as e:
                                failed_vendors.append({
                                    "vendor": vendor["vendor_name"],
                                    "error": str(e)
                                })

                    # Track the vendor IDs actually emailed in sent_ids:
                    if sent_ids:
                        try:
                            mark_rfq_sent(rfq_id, sent_ids)
                        except RfqError as e:
                            st.error(f"Emails were sent, but the RFQ could not be marked as sent: {e}")

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