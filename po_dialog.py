"""Create-a-purchase-order dialog. Used by the Purchasing page and the RFQ Manager.

    from po_dialog import show_create_po_dialog
    show_create_po_dialog(quote_id=None)       # None: the person picks an approved quote
"""
import streamlit as st

import services.purchasing_service as ps


@st.dialog("Create purchase order", width="large")
def show_create_po_dialog(quote_id: int | None = None):
    ready = ps.quotes_ready_for_po()
    if not ready:
        st.info("There are no approved quotes waiting for a purchase order. Approve a quote in the Review Queue first.")
        return
    ids = [q["id"] for q in ready]
    if quote_id not in ids:
        quote_id = None
    label = lambda i: next(f"{q['vendor_name']} · {q['reference_number'] or 'no reference'} · {q['rfq_number'] or 'no RFQ'} · "
                           f"{q['currency']} {(q['comparable_amount'] if q['comparable_amount'] is not None else q['total_amount']):,.2f}"
                           for q in ready if q["id"] == i)
    chosen = st.selectbox("Approved quote", ids, index=ids.index(quote_id) if quote_id else None, format_func=label,
                          placeholder="Choose the quote you are ordering from", key="po_quote")
    if chosen is None:
        return
    try:
        draft = ps.po_draft(chosen)
    except ps.PurchasingError as e:
        st.error(str(e))
        return

    left, right = st.columns(2)
    with left:
        po_number = st.text_input("PO number", draft["po_number"], key=f"po_num_{chosen}")
        issued = st.text_input("Issue date", draft["issued_date"], placeholder="YYYY-MM-DD", key=f"po_date_{chosen}")
        amount = st.text_input(f"Amount ({draft['currency']})", f"{draft['amount']:.2f}", key=f"po_amt_{chosen}")
    with right:
        st.markdown(f"**{draft['vendor_name']}**")
        if draft["basis"]:
            st.caption(f"Price basis: {draft['basis']}")
        usd = ps.to_usd(draft["amount"], draft["currency"])
        if usd and draft["currency"] != "USD":
            st.caption(f"About ${usd:,.2f} USD")
        close = st.checkbox(f"Close {draft['rfq_number']} (the decision is made)", value=False, key=f"po_close_{chosen}") \
            if draft["rfq_number"] else False
        st.caption("Creating a PO records the decision. It does not email anyone.")

    for w in draft["warnings"]:
        st.warning(w)
    confirmed = st.checkbox("I have checked the warnings above", key=f"po_ok_{chosen}") if draft["warnings"] else True

    if st.button("Create purchase order", type="primary", disabled=not confirmed, use_container_width=True, key=f"po_go_{chosen}"):
        try:
            result = ps.create_po(chosen, po_number=po_number, issued_date=issued, amount=amount, close_rfq=close,
                                  confirmed_warnings=confirmed)
        except ps.PurchasingError as e:
            st.error(str(e))
            return
        except Exception as e:
            st.error(f"Could not create the purchase order: {type(e).__name__}: {e}")
            return
        st.session_state["purchasing_flash"] = f"{result['po_number']} created." + (" " + " ".join(result["notes"]) if result["notes"] else "")
        st.rerun()