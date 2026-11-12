import streamlit as st
from services.vendor_service import add_vendor_contact, delete_vendor_contact, update_vendor, update_vendor_contact

@st.dialog("Edit Vendor")
def edit_vendor_dialog(vendor):

    st.caption("Update the vendor's information.")

    statuses = ["active", "inactive", "blocked"]

    with st.form("edit_vendor_form"):

        name = st.text_input(
            "Vendor Name *",
            value=vendor["name"] or "",
        )

        category = st.text_input(
            "Category",
            value=vendor["category"] or "",
            placeholder="e.g. Analytical testing",
        )

        country = st.text_input(
            "Country",
            value=vendor["country"] or "",
            placeholder="e.g. Germany",
        )

        status = st.selectbox(
            "Status",
            statuses,
            index=(
                statuses.index(vendor["status"])
                if vendor["status"] in statuses
                else 0
            ),
            format_func=lambda x: x.capitalize(),
        )

        notes = st.text_area(
            "Notes",
            value=vendor["notes"] or "",
            placeholder="Additional information about this vendor...",
            height=150,
        )

        submitted = st.form_submit_button(
            "Save Changes",
            type="primary",
            use_container_width=True,
        )

        if submitted:

            if not name.strip():
                st.error("Please enter a vendor name.")

            else:
                try:
                    update_vendor(
                        vendor_id=vendor["id"],
                        name=name,
                        category=category,
                        country=country,
                        status=status,
                        notes=notes,
                    )

                    st.session_state["refresh_insights"] = True

                    st.success("Vendor updated successfully!")
                    st.rerun()

                except ValueError as e:
                    st.error(str(e))


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