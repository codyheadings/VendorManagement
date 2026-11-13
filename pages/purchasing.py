from datetime import date
import streamlit as st
import db
import services.purchasing_service as ps
from st_helpers.po_dialog import show_create_po_dialog

db.ensure_db()
st.set_page_config(page_title="Purchasing", layout="wide")
st.title("Purchasing")
st.caption("Purchase orders, invoices and amount spent. Amounts are shown in USD where converted. "
           "Recording a payment here is a record only; no money moves.")

LIST_HEIGHT = 560
COLOR = {"open": "orange", "received": "blue", "closed": "green", "cancelled": "red",
         "paid": "green", "unpaid": "orange", "overdue": "red", "pending": "gray", "approved": "green", "rejected": "red"}


def _flash() -> None:
    msg = st.session_state.pop("purchasing_flash", None)
    st.session_state["refresh_insights"] = True
    if msg:
        st.toast(msg)


def _usd(v: float | None) -> str:
    return f"${v:,.0f}" if v is not None else "-"


def _try(fn, *args, success: str, **kw) -> None:
    try:
        fn(*args, **kw)
    except ps.PurchasingError as e:
        st.error(str(e))
        return
    except Exception as e:  # noqa: BLE001
        st.error(f"Something went wrong: {type(e).__name__}: {e}")
        return
    st.session_state["purchasing_flash"] = success
    st.rerun()


# ---------- purchase orders ----------

def pos_tab() -> None:
    show = st.segmented_control("Show", ["Active", "Closed", "Cancelled", "All"], default="Active", key="po_filter") or "Active"
    rows = ps.list_pos()
    keep = {"Active": ("open", "received"), "Closed": ("closed",), "Cancelled": ("cancelled",), "All": ("open", "received", "closed", "cancelled")}[show]
    rows = [r for r in rows if r["status"] in keep]
    if not rows:
        st.info("No purchase orders here yet. Use 'New purchase order' above to order from an approved quote.")
        return
    ids = [r["id"] for r in rows]
    if st.session_state.get("po_sel") not in ids:
        st.session_state["po_sel"] = ids[0]
    left, right = st.columns([1, 2])
    with left:
        with st.container(height=LIST_HEIGHT, border=True):
            for r in rows:
                label = f"{r['po_number']}  [{r['status']}]\n{r['vendor_name']}\n{r['currency']} {r['amount']:,.2f} · invoiced {r['invoiced']:,.2f}"
                if st.button(label, key=f"po_pick_{r['id']}", use_container_width=True,
                             type="primary" if r["id"] == st.session_state["po_sel"] else "secondary"):
                    st.session_state["po_sel"] = r["id"]
                    st.rerun()
    d = ps.po_detail(st.session_state["po_sel"])
    with right:
        with st.container(height=LIST_HEIGHT, border=True):
            st.subheader(f"{d['po_number']} · {d['vendor_name']}")
            st.badge(d["status"], color=COLOR.get(d["status"], "gray"))
            usd = ps.to_usd(d["amount"], d["currency"])
            c1, c2, c3 = st.columns(3)
            c1.metric("PO amount", f"{d['currency']} {d['amount']:,.2f}", help=f"About ${usd:,.2f} USD" if usd and d["currency"] != "USD" else None)
            c2.metric("Invoiced", f"{d['currency']} {d['invoiced']:,.2f}")
            c3.metric("Left on PO", f"{d['currency']} {d['remaining']:,.2f}")
            q = d["quote"]
            st.caption(f"Issued {d['issued_date']}" + (f" · from quote {q['reference_number'] or ''} ({q['rfq_number']})" if q and q["rfq_number"] else ""))

            a, b, c = st.columns(3)
            nxt = {"open": ("received", "closed", "cancelled"), "received": ("closed",)}.get(d["status"], ())
            if "received" in nxt and a.button("Mark received", key=f"po_rec_{d['id']}", use_container_width=True):
                _try(ps.set_po_status, d["id"], "received", success="Marked received.")
            if "closed" in nxt:
                with b.popover("Close PO", use_container_width=True):
                    note = st.text_input("Note (optional)", key=f"po_cl_note_{d['id']}")
                    if st.button("Close PO", key=f"po_cl_{d['id']}", type="primary"):
                        _try(ps.set_po_status, d["id"], "closed", note, success="PO closed.")
            if "cancelled" in nxt:
                with c.popover("Cancel PO", use_container_width=True):
                    why = st.text_input("Reason (required)", key=f"po_cx_why_{d['id']}")
                    if st.button("Cancel PO", key=f"po_cx_{d['id']}"):
                        _try(ps.set_po_status, d["id"], "cancelled", why, success="PO cancelled.")

            st.markdown("**Invoices on this PO**")
            if d["invoices"]:
                st.dataframe([{"Invoice": i["invoice_number"], "Date": i["invoice_date"], "Due": i["due_date"],
                               "Amount": f"{i['currency']} {i['amount']:,.2f}", "Status": ps.invoice_status(i)} for i in d["invoices"]],
                             hide_index=True)
            else:
                st.caption("No invoices yet. Upload one from the dashboard (Vendor invoice).")
            if d["notes"]:
                with st.expander(f"History ({len(d['notes'])})"):
                    for n in d["notes"]:
                        st.caption(n["text"])


# ---------- invoices ----------

def invoices_tab() -> None:
    choice = st.segmented_control("Show", ["All", "Unpaid", "Overdue", "Paid", "Pending review"], default="All", key="inv_filter") or "All"
    status = {"All": None, "Unpaid": "unpaid", "Overdue": "overdue", "Paid": "paid", "Pending review": "pending"}[choice]
    rows = ps.list_invoices(status)
    if choice == "Unpaid":
        rows = ps.list_invoices("unpaid") + ps.list_invoices("overdue")
    if not rows:
        st.info("No invoices here.")
        return
    with st.container(height=LIST_HEIGHT + 20, border=True):
        for i in rows:
            with st.container(border=True):
                a, b, c = st.columns([2.2, 1.6, 1.4])
                with a:
                    st.markdown(f"**{i['invoice_number']}** · {i['vendor_name']}")
                    st.badge(i["status"], color=COLOR.get(i["status"], "gray"))
                    if i["days_overdue"]:
                        st.badge(f"{i['days_overdue']} days overdue", color="red")
                    st.caption(f"{i['po_number'] or 'no PO'} · dated {i['invoice_date']} · due {i['due_date'] or 'not set'}"
                               + (f" · paid {i['paid_date']}" if i["paid_date"] else ""))
                with b:
                    st.markdown(f"**{i['currency']} {i['amount']:,.2f}**")
                    if i["amount_usd"] and i["currency"] != "USD":
                        st.caption(f"about ${i['amount_usd']:,.2f}")
                with c:
                    if i["status"] in ("unpaid", "overdue"):
                        with st.popover("Mark paid", use_container_width=True):
                            when = st.date_input("Paid on", value=date.fromisoformat(db.TODAY), max_value=date.fromisoformat(db.TODAY),
                                                 key=f"pay_date_{i['id']}")
                            note = st.text_input("Reference (optional)", key=f"pay_note_{i['id']}", placeholder="e.g. wire ref")
                            if st.button("Record payment", key=f"pay_go_{i['id']}", type="primary"):
                                _try(ps.mark_paid, i["id"], when.isoformat(), note, success="Payment recorded.")
                    elif i["status"] == "paid":
                        with st.popover("Undo payment", use_container_width=True):
                            why = st.text_input("Reason (required)", key=f"undo_why_{i['id']}")
                            if st.button("Remove payment record", key=f"undo_go_{i['id']}"):
                                _try(ps.undo_paid, i["id"], why, success="Payment record removed.")
                    elif i["status"] == "pending":
                        st.caption("Approve it in the Review Queue first.")


# Amount Spent

def spent_tab() -> None:
    s = ps.spent_summary()
    if s["unconverted"]:
        st.warning(f"{s['unconverted']} amount(s) are in a currency with no stored exchange rate and are left out of the USD totals.")
    rows = ps.spent_by_vendor()
    if not rows:
        st.info("No approved invoices yet, so there is no amount to show.")
        return
    left, right = st.columns([1.3, 1])
    with left:
        st.markdown("**By vendor (USD)**")
        st.dataframe([{"Vendor": r["vendor"], "Invoiced": r["invoiced"], "Paid": r["paid"], "Outstanding": r["outstanding"],
                       "Overdue": r["overdue"], "Committed on open POs": r["committed"]} for r in rows], hide_index=True,
                     column_config={k: st.column_config.NumberColumn(k, format="$%,.0f") for k in
                                    ("Invoiced", "Paid", "Outstanding", "Overdue", "Committed on open POs")})
    with right:
        st.markdown("**By month (USD)**")
        if s["by_month"]:
            st.bar_chart({"Invoiced": {m["month"]: m["invoiced"] for m in s["by_month"]},
                          "Paid": {m["month"]: m["paid"] for m in s["by_month"]}})
    st.caption("Invoiced counts approved invoices by invoice date; paid counts by payment date. "
               "Committed is what open purchase orders have not yet been invoiced for. Converted at the stored exchange rates.")


# ---------- page ----------

_flash()
s = ps.spent_summary()
top = st.columns([1, 1, 1, 1, 1, 1.2])
top[0].metric("Committed (open POs)", _usd(s["committed"]))
top[1].metric("Invoiced", _usd(s["invoiced"]))
top[2].metric("Paid", _usd(s["paid"]))
top[3].metric("Outstanding", _usd(s["outstanding"]))
top[4].metric("Overdue", _usd(s["overdue"]))
if top[5].button("New purchase order", type="primary", use_container_width=True):
    show_create_po_dialog()

tab_p, tab_i, tab_s = st.tabs(["Purchase orders", "Invoices", "Amount Spent"])
with tab_p:
    pos_tab()
with tab_i:
    invoices_tab()
with tab_s:
    spent_tab()