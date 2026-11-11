
import sqlite3
import streamlit as st
import db
from services.vendor_service import normalize_vendor_name

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

@st.dialog("Request a Quote", width="large")
def show_rfq_form():
    with st.form("send_rfq"):
        col1, divider, col2 = st.columns([3, 0.1, 6])
        with col1:
            st.subheader("Requesting From:")
            for v_id in selected_ids:
                st.write(get_vendor_from_id(v_id))

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
            st.subheader("Quote Order")
            st.grid()

        submit = st.form_submit_button("Submit", 
            type="primary",
            use_container_width=True)

    if submit:
        st.rerun()

def get_vendor_from_id(id: int) -> str:
    return next(iter(db.query_one(f"SELECT name FROM vendors WHERE id = {id}").values()))

def update_vendor_selection(vendor_id):
    selected = st.session_state.selected_vendor_ids
    checkbox_key = f"select_vendor_{vendor_id}"

    if st.session_state[checkbox_key]:
        selected.add(vendor_id)
    else:
        selected.discard(vendor_id)

def reset_vendor_selection():
    for vendor_id in list(st.session_state.selected_vendor_ids):
        checkbox_key = f"select_vendor_{vendor_id}"
        st.session_state[checkbox_key] = False

    st.session_state.selected_vendor_ids.clear()

if "selected_vendor_ids" not in st.session_state:
    st.session_state.selected_vendor_ids = set()

vendors_col, insights_col = st.columns([2, 1], gap="large")

with vendors_col:
    STATUS_COLORS = {"active": "green", "inactive": "gray", "blocked": "red"}
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

    with st.container(height=480, border=True):
        for v in vendors:
            with st.container(key=f"row_{v['id']}", border=True):
                cols = st.columns(COL_WIDTHS, vertical_alignment="center")

                cols[0].checkbox(
                    "Select",
                    key=f"select_vendor_{v['id']}",
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

    add_col, rfq_col, deselect_col, spacer = st.columns([1, 1.5, 1, 4])

    with add_col:
        st.button(
            "Add Vendor",
            on_click=show_vendor_form,
            use_container_width=True,
        )

    with rfq_col:
        if selected_ids:
            if st.button(
                f"Send RFQ ({len(selected_ids)})",
                type="primary",
                use_container_width=True,
            ):
                st.session_state["rfq_vendor_ids"] = list(selected_ids)
                show_rfq_form()

    with deselect_col:
        if selected_ids:
            st.button(
                "Deselect All",
                on_click=reset_vendor_selection,
                use_container_width=True,
            )

with insights_col:
    with st.container(horizontal_alignment="center", height="stretch"):
        st.subheader("Insights")

        col1, col2 = st.columns(2)
        with col1:
            total_vendors = db.scalar("SELECT COUNT(*) FROM vendors")
            st.metric("Total Vendors", total_vendors)

        with col2:
            active_vendors = db.scalar("SELECT COUNT(*) FROM vendors WHERE status LIKE 'active'")
            st.metric("Active Vendors", active_vendors)
            
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


# Helpers
def add_vendor(name: str, country: str, category: str) -> int:
    normalized_name = normalize_vendor_name(name)
    
    try:
        inserted = db.execute(
            """
            INSERT INTO vendors (name, normalized_name, category, country)
            VALUES (?, ?, ?, ?)
            """,
            (
                name.strip(),
                normalized_name,
                category.strip(),
                country.strip()
            )
        )
        return inserted

    except sqlite3.IntegrityError as e:
            msg = str(e)
            if "vendors.normalized_name" in msg:
                raise ValueError(f"A vendor named '{name}' already exists.") from e
            raise ValueError(f"Could not add vendor: {msg}") from e

def add_vendor_contact(vendor_id: int, name: str, email: str, role: str, is_primary: bool = True):
    try:
        db.execute(
            """
            INSERT INTO vendor_contacts (vendor_id, name, email, role, is_primary)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                vendor_id,
                name.strip(),
                email,
                role,
                is_primary
            )
        )
        st.rerun()

    except sqlite3.IntegrityError as e:
            msg = str(e)
            if "CHECK constraint failed" in msg and "email" in msg:
                raise ValueError("The submitted email address is incorrectly formatted.") from e
            raise ValueError(f"Could not add contact: {msg}") from e

def add_vendor_with_contact(
    name: str, category: str, country: str,
    contact_name: str, email: str, role: str,
) -> int:
    """Insert a vendor and its primary contact atomically. Returns the vendor id."""
    try:
        with db.transaction() as conn:
            vendor_id = conn.execute(
                """
                INSERT INTO vendors (name, normalized_name, category, country)
                VALUES (?, ?, ?, ?)
                """,
                (name.strip(), normalize_vendor_name(name), category.strip(), country.strip()),
            ).lastrowid

            conn.execute(
                """
                INSERT INTO vendor_contacts (vendor_id, name, email, role, is_primary)
                VALUES (?, ?, ?, ?, 1)
                """,
                (vendor_id, contact_name.strip(), email.strip(), role.strip()),
            )
            return vendor_id
    except sqlite3.IntegrityError as e:
        msg = str(e)
        if "vendors.normalized_name" in msg:
            raise ValueError(f"A vendor named '{name}' already exists.") from e
        if "CHECK constraint failed" in msg and "email" in msg:
            raise ValueError("The submitted email address is incorrectly formatted.") from e
        raise ValueError(f"Could not save: {msg}") from e