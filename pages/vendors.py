import streamlit as st
import db
import notify
from services.review_service import attention_items
from services.rfq_service import *
from st_helpers.add_vendor_dialog import show_rfq_form
from st_helpers.upload_dialog import show_upload_dialog
from st_helpers.vendor_dialogs import *

st.set_page_config(
    page_title="Dashboard",
    layout="wide"
)

notify.run_if_due()

# Main page content
st.markdown("# Vendor Overview")
st.caption("View and manage your vendors.")
        

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

def get_insights_data():
    """Fetch the latest dashboard metrics."""

    return {
        "total_vendors": db.scalar(
            "SELECT COUNT(*) FROM vendors"
        ),
        "active_vendors": db.scalar(
            "SELECT COUNT(*) FROM vendors WHERE status = 'active'"
        ),
        "vendor_countries": db.scalar(
            "SELECT COUNT(DISTINCT country) FROM vendors WHERE country != ''"
        ),
        "open_rfqs": db.scalar(
            "SELECT COUNT(*) FROM rfqs WHERE status = 'open'"
        ),
        "draft_rfqs": db.scalar(
            "SELECT COUNT(*) FROM rfqs WHERE status = 'draft'"
        ),
        "payment_orders": db.scalar("SELECT COUNT(*) FROM purchase_orders"),
        "quotes": db.scalar(
            "SELECT COUNT(*) FROM quotes WHERE review_status = 'pending'"
        ),
        "legal_docs": db.scalar(
            "SELECT COUNT(*) FROM legal_documents WHERE review_status = 'pending'"
        ),
        "attention": len(attention_items()),
    }


@st.fragment(run_every="2s")
def render_insights():
    # Refresh metrics only when requested, or on first page load.
    if ("insights_data" not in st.session_state or st.session_state.get("refresh_insights", False)):
        st.session_state.insights_data = get_insights_data()
        st.session_state.refresh_insights = False

    data = st.session_state.insights_data

    with st.container(horizontal_alignment="right", height="stretch"):
        st.subheader("Insights")

        st.caption("Vendors")

        col1, col2, col3 = st.columns(3)

        col1.metric("Total Vendors", data["total_vendors"])
        col2.metric("Active Vendors", data["active_vendors"])
        col3.metric("Countries", data["vendor_countries"])

        st.divider()

        st.caption("Quotes and Payment")

        col1, col2, col3 = st.columns(3)

        col1.metric("Open Quote Requests", data["open_rfqs"])
        col2.metric("Quote Request Drafts", data["draft_rfqs"])
        col3.metric("Payment Orders", data["payment_orders"])

        st.divider()

        st.caption("Review")

        col1, col2, col3 = st.columns(3)

        col1.metric("Pending Quotations", data["quotes"])
        col2.metric("Pending Legal Documents", data["legal_docs"])
        col3.metric("Unresolved Items", data["attention"])

        needs_attention = (
            data["quotes"] > 0
            or data["legal_docs"] > 0
            or data["attention"] > 0
        )

        with st.container(horizontal=True, vertical_alignment="center"):
            if needs_attention:
                st.warning(
                    "Review actions needed, view your queue to get started."
                )
            else:
                st.success(
                    "No review actions needed at this time."
                )

            if st.button(
                "View Queue",
                type="primary" if needs_attention else "secondary",
            ):
                st.switch_page("pages/review_queue.py")

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
        with st.spinner():
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
                st.session_state.new_rfq_id = None
                show_rfq_form()

    with deselect_col:
        if selected_ids:
            st.button("Deselect All", on_click=reset_vendor_selection, use_container_width=True)

with insights_col:
    render_insights()