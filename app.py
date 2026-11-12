# App entry point
import streamlit as st
import db

@st.cache_resource
def _setup():
    db.ensure_db()

_setup()

# Define the pages
pages = {
    "": [
        st.Page("pages/vendors.py", title="Dashboard", icon=":material/analytics:", default=True),
        # st.Page("pages/request_quotes.py", title="Request Quotes", icon=":material/request_quote:"),
        st.Page("pages/vendor_profile.py", title="Vendor Profile", icon=":material/business:", visibility="hidden"),
    ],
    "Resources": [
        st.Page("pages/quotes.py", title="View Quotes", icon=":material/document_search:"),
        st.Page("pages/documents.py", title="Other Documents", icon=":material/folder_open:"),
    ],
}


# Set up navigation
pg = st.navigation(pages, position="top")

# Run the selected page
pg.run()