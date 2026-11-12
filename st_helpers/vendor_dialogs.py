import streamlit as st

from services.vendor_service import add_vendor_with_contact

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
                st.session_state["refresh_insights"] = True
            except ValueError as e:
                st.error(str(e))
            else:
                st.rerun()