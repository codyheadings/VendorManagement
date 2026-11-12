import streamlit as st

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