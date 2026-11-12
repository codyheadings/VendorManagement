# App entry point
import streamlit as st
import db

# remove excess whitespace at top of page
st.markdown(
    """
    <style>
        .block-container {
            padding-top: 3rem;
            padding-bottom: 1rem;
        }
    </style>
    """,
    unsafe_allow_html=True
)

@st.cache_resource
def _setup():
    db.ensure_db()

_setup()

# Define the pages
pages = {
    "": [
        st.Page("pages/vendors.py", title="Dashboard", icon=":material/analytics:", default=True),
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