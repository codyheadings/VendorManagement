import streamlit as st
import pandas as pd
import db

st.set_page_config(
    page_title="Vendor Profile",
    layout="wide"
)

vendor_id = st.session_state.get("selected_vendor_id")
if vendor_id is None:
    st.info("Select a vendor from the dashboard.")
    st.stop()

vendor = db.query_one("SELECT * FROM vendors WHERE id = ?", (vendor_id,))
if vendor is None:
    st.error("That vendor no longer exists.")
    st.stop()

st.title(vendor["name"])
st.divider()

col1, col2, col3 = st.columns(3)

with col1:
    st.metric("Category", vendor["category"])

with col2:
    st.metric("Country", vendor["country"])

with col3:
    st.metric("Status", vendor["status"].capitalize())

tab1, tab2, tab3, tab4 = st.tabs([
    "Overview",
    "Quotes",
    "Contacts",
    "Documents"
])

with tab1:
    st.subheader("Vendor Information")
    st.table(vendor)

with tab2:
    st.subheader("Quote History")

with tab3:
    st.subheader("Contact List")

with tab4:
    st.subheader("Legal Documents")


if st.button("Return to Dashboard"):
    st.switch_page("pages/vendors.py")