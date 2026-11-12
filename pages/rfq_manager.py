"""RFQ Manager page: who has replied, who is late, follow-ups, history, and the quote comparison per RFQ.
All database work is in rfq_service.py. Same layout as the Review Queue: list | detail | activity."""
from datetime import date, timedelta

import streamlit as st

import db
import services.review_service as rv
import services.rfq_service as rs
from services.email_service import send_email, build_simple_msg
import services.purchasing_service as ps
from st_helpers.po_dialog import show_create_po_dialog

st.set_page_config(page_title="RFQ Manager", layout="wide")
st.title("RFQ Manager")
st.caption("Track every request for quotation: who replied, who is late, follow-ups and the quote comparison.")

LIST_HEIGHT = 640


def send_followup(contact_email: str, subject: str, body: str) -> None:
    if not db.SEND_ENABLED:
        raise SystemError("Sending mail on this app has been disabled.")
    else:
        send_email(build_simple_msg(contact_email, subject, body))


STATUS_COLOR = {"draft": "gray", "open": "green", "closed": "blue", "cancelled": "red",
                "sent": "orange", "replied": "green", "declined": "red"}


def _flash() -> None:
    msg = st.session_state.pop("rm_flash", None)
    if msg:
        st.toast(msg)


def _done(message: str, *clear: str) -> None:
    st.session_state["rm_flash"] = message
    for k in clear:
        st.session_state.pop(k, None)
    st.rerun()


def _try(fn, *args, success: str, clear: tuple = (), **kw) -> None:
    """Run a service call; show its message as an error instead of crashing, or refresh on success."""
    try:
        fn(*args, **kw)
    except rs.RfqError as e:
        st.error(str(e))
        return
    except Exception as e:
        st.error(f"Something went wrong: {type(e).__name__}: {e}")
        return
    _done(success, *clear)

# List view

def _pick(rows: list[dict]) -> dict | None:
    ids = [r["id"] for r in rows]
    if st.session_state.get("rm_rfq") not in ids:
        st.session_state["rm_rfq"] = ids[0] if ids else None
    with st.container(height=LIST_HEIGHT, border=True):
        for r in rows:
            late = f" · {r['late']} late" if r["late"] else ""
            label = (f"{r['rfq_number']}  [{r['status']}]\n{r['title']}\n"
                     f"{r['replied']}/{r['invited']} replied{late}")
            if st.button(label, key=f"rm_pick_{r['id']}", use_container_width=True,
                         type="primary" if r["id"] == st.session_state["rm_rfq"] else "secondary"):
                st.session_state["rm_rfq"] = r["id"]
                st.session_state.pop("rm_followup_vendor", None)
                st.rerun()
    return next((r for r in rows if r["id"] == st.session_state["rm_rfq"]), None)


# Detail view

def _actions(d: dict) -> None:
    c1, c2, c3 = st.columns(3)
    if d["status"] in ("draft", "open"):
        with c1.popover("Extend deadline", use_container_width=True):
            current = date.fromisoformat(d["due_date"]) if d["due_date"] else date.fromisoformat(db.TODAY)
            new_due = st.date_input("New deadline", value=current + timedelta(days=7), key=f"rm_due_{d['id']}")
            why = st.text_input("Reason (optional)", key=f"rm_due_why_{d['id']}")
            if st.button("Save deadline", key=f"rm_due_go_{d['id']}", type="primary"):
                _try(rs.extend_deadline, d["id"], new_due.isoformat(), why, success="Deadline updated.")
    if d["status"] == "open":
        with c2.popover("Close RFQ", use_container_width=True):
            st.caption(
                "Select the vendor whose quote you want to accept. "
                "Only invited vendors with approved, current quotes are shown."
            )

            candidates = rs.award_candidates(d["id"])

            if candidates:
                vendor_options = {
                    v["vendor_id"]: v["vendor_name"]
                    for v in candidates
                }

                awarded_vendor_id = st.selectbox(
                    "Award to vendor",
                    options=list(vendor_options.keys()),
                    format_func=lambda vendor_id: vendor_options[vendor_id],
                    key=f"rm_award_vendor_{d['id']}",
                )

                selected = next(
                    v for v in candidates
                    if v["vendor_id"] == awarded_vendor_id
                )

                st.caption(
                    f"{selected['quote_count']} eligible quote(s) "
                    f"from {selected['vendor_name']}."
                )

            else:
                awarded_vendor_id = None
                st.info(
                    "No invited vendors currently have an approved, "
                    "non-superseded quote for this RFQ."
                )

            note = st.text_input(
                "Closing note (optional)",
                key=f"rm_close_note_{d['id']}",
            )

            if st.button(
                "Close RFQ",
                key=f"rm_close_go_{d['id']}",
                type="primary",
                use_container_width=True,
            ):
                _try(
                    rs.close_rfq,
                    d["id"],
                    note,
                    awarded_vendor_id,
                    success=(
                        f"RFQ closed and awarded to "
                        f"{vendor_options[awarded_vendor_id]}."
                        if awarded_vendor_id is not None
                        else "RFQ closed without an award."
                    ),
                )
    if d["status"] == "closed":
        if c2.button("Reopen", key=f"rm_reopen_{d['id']}", use_container_width=True):
            _try(rs.reopen_rfq, d["id"], success="RFQ reopened.")
    if d["status"] == "draft":
            if c2.button("Open", key=f"rm_open_{d['id']}", use_container_width=True):
                _try(rs.open_rfq, d["id"], success="RFQ opened.")
    if d["status"] in ("draft", "open"):
        with c3.popover("Cancel RFQ", use_container_width=True):
            st.caption("Cancelling cannot be undone.")
            why = st.text_input("Reason (required)", key=f"rm_cancel_why_{d['id']}")
            if st.button("Cancel RFQ", key=f"rm_cancel_go_{d['id']}"):
                _try(rs.cancel_rfq, d["id"], why, success="RFQ cancelled.")


def _details(d: dict) -> None:
    with st.expander("Request details"):
        if d["items"]:
            st.dataframe([{"Line": i["line_number"], "Item": i["description"], "Quantity": i["quantity"],
                           "Unit": i["unit"], "Requirement": i["requirement"]} for i in d["items"]], hide_index=True)
        if d["specifications"]:
            st.markdown("**Specifications**\n" + "\n".join(f"- {s}" for s in d["specifications"]))
        if d["quote_requirements"]:
            st.markdown("**Quote requirements**\n" + "\n".join(f"- {s}" for s in d["quote_requirements"]))
        path = rv.document_file(d["document_id"])
        if path:
            st.download_button("Download RFQ PDF", path.read_bytes(), file_name=path.name, key=f"rm_pdf_{d['id']}")


def _vendor_rows(d: dict) -> None:
    st.markdown("**Vendors**")
    for inv in d["invitations"]:
        with st.container(border=True):
            left, right = st.columns([2.2, 1.4])
            with left:
                st.markdown(f"**{inv['vendor_name']}**")
                st.badge(inv["status"], color=STATUS_COLOR.get(inv["status"], "gray"))
                if inv["days_late"]:
                    st.badge(f"{inv['days_late']} days late", color="red")
                bits = []
                if inv["sent_at"]:
                    bits.append(f"sent {inv['sent_at']}")
                if inv["followup_count"]:
                    bits.append(f"{inv['followup_count']} follow-up(s), last {inv['last_followup_at']}")
                if inv["quotes"]:
                    bits.append(f"{inv['quotes']} quote(s) received")
                if bits:
                    st.caption(" · ".join(bits))
            with right:
                fu = inv["followup"]
                if inv["status"] == "sent":
                    if st.button("Follow up", key=f"rm_fu_{d['id']}_{inv['vendor_id']}", disabled=not fu["allowed"],
                                 help=None if fu["allowed"] else f"Not possible: {fu['reason']}", use_container_width=True):
                        st.session_state["rm_followup_vendor"] = inv["vendor_id"]
                        st.rerun()
                    if not fu["allowed"]:
                        st.caption(fu["reason"])
                    with st.popover("Mark as...", use_container_width=True):
                        st.caption("For a reply that did not come through the app.")
                        note = st.text_input("Note", key=f"rm_mk_note_{d['id']}_{inv['vendor_id']}")
                        a, _, b = st.columns(3)
                        if a.button("Replied", key=f"rm_mk_r_{d['id']}_{inv['vendor_id']}"):
                            _try(rs.set_invitation_status, d["id"], inv["vendor_id"], "replied", note, success="Marked as replied.")
                        if b.button("Declined", key=f"rm_mk_d_{d['id']}_{inv['vendor_id']}"):
                            _try(rs.set_invitation_status, d["id"], inv["vendor_id"], "declined", note, success="Marked as declined.")


def _comparison(d: dict) -> None:
    rows = rs.comparison(d["id"])
    st.markdown("**Quote comparison**")
    if not rows:
        st.caption("No quotes received for this RFQ yet.")
        return
    table = []
    for r in rows:
        price = f"{r['currency']} {r['comparable_amount']:,.2f}" if r["comparable_amount"] is not None else "no comparable price"
        table.append({"Vendor": r["vendor_name"], "Price": price + (" (estimate)" if r["comparable_is_estimate"] else ""),
                      "USD": r["comparable_usd"], "Delivery (days)": r["delivery_time_days"], "Valid until": r["valid_until"],
                      "Answers RFQ": r["answers_rfq"], "NDA needed": bool(r["requires_nda"]), "Review": r["review_status"]})
    st.dataframe(table, hide_index=True, column_config={"USD": st.column_config.NumberColumn("USD", format="$%.2f")})
    if any(r["review_status"] == "pending" for r in rows):
        st.caption("Some quotes are still pending review; approve them in the Review Queue before relying on the numbers.")
    ready = {q["id"]: q for q in ps.quotes_ready_for_po()}
    for r in rows:
        if r["quote_id"] in ready and st.button(f"Create PO for {r['vendor_name']}", key=f"rm_po_{d['id']}_{r['quote_id']}"):
            show_create_po_dialog(r["quote_id"])


def _detail(d: dict) -> None:
    st.subheader(f"{d['rfq_number']} · {d['title']}")
    st.badge(d["status"], color=STATUS_COLOR.get(d["status"], "gray"))
    if d["overdue"]:
        st.badge("deadline passed", color="red")
    st.caption(f"Issued {d['issue_date']} · quotes due {d['due_date'] or 'not set'} · deliver to {d['deliver_to']} · "
               f"contact {d['contact_email']}")
    if d.get("awarded_vendor_name"):
        st.success(f"Awarded to: {d['awarded_vendor_name']}")
    _actions(d)
    _details(d)
    _vendor_rows(d)
    _comparison(d)


# Action col

def _followup_composer(d: dict) -> None:
    vendor_id = st.session_state.get("rm_followup_vendor")
    inv = next((i for i in d["invitations"] if i["vendor_id"] == vendor_id), None)
    if inv is None:
        st.session_state.pop("rm_followup_vendor", None)
        return
    k = f"rm_fu_{d['id']}_{vendor_id}"
    with st.container(border=True):
        st.markdown(f"**Follow up with {inv['vendor_name']}**")
        st.caption(f"To: {inv['contact_name'] or 'contact'} <{inv['contact_email']}>")
        if f"{k}_subject" not in st.session_state:
            subject, body = rs.draft_followup(d["id"], vendor_id)
            st.session_state[f"{k}_subject"], st.session_state[f"{k}_body"] = subject, body
        warnings = inv["followup"]["warnings"]
        for w in warnings:
            st.warning(w.capitalize())
        confirmed = st.checkbox("I understand, send anyway", key=f"{k}_ok") if warnings else True
        subject = st.text_input("Subject", key=f"{k}_subject")
        body = st.text_area("Message", key=f"{k}_body", height=230)
        a, b, c = st.columns(3)
        if a.button("Send", type="primary", disabled=not confirmed, key=f"{k}_send", use_container_width=True):
            try:
                send_followup(inv["contact_email"], subject, body)
            except Exception as e:
                st.error(f"The email could not be sent, so nothing was recorded: {e}")
            else:
                _try(rs.record_followup, d["id"], vendor_id, subject, body, channel="email", confirmed_warnings=confirmed,
                     success="Follow-up sent.", clear=("rm_followup_vendor",))
        if b.button("Record as sent" if not db.SEND_ENABLED else "I sent it myself", disabled=not confirmed, key=f"{k}_manual",
                    help="Use this if you sent the message from your own mail. It is logged on the RFQ.", use_container_width=True):
            _try(rs.record_followup, d["id"], vendor_id, subject, body, channel="manual", confirmed_warnings=confirmed,
                 success="Follow-up recorded.", clear=("rm_followup_vendor",))
        if c.button("Cancel", key=f"{k}_cancel", use_container_width=True):
            st.session_state.pop("rm_followup_vendor", None)
            st.rerun()
        if not db.SEND_ENABLED:
            st.caption("Sending from the app is currently disabled. Copy the message, send it from your mail, then record it as sent.")


ICON = {"created": ":material/flag:", "sent": ":material/send:", "quote": ":material/request_quote:",
        "followup": ":material/reply:", "question": ":material/help:", "decline": ":material/block:",
        "reply": ":material/mail:", "notice": ":material/info:", "note": ":material/sticky_note_2:",
        "status": ":material/sync_alt:"}


def _activity(d: dict) -> None:
    st.markdown("**History**")
    with st.container(height=300 if st.session_state.get("rm_followup_vendor") else 430, border=True):
        for e in rs.activity(d["id"]):
            who = f" · {e['who']}" if e["who"] else ""
            via = " (sent by hand)" if e.get("channel") == "manual" and e["icon"] == "followup" else ""
            st.markdown(f"{ICON.get(e['icon'], '')} **{e['title']}**{who}{via}  \n:gray[{e['on']}]")
            if e["body"]:
                st.caption(e["body"])
    with st.form(f"rm_note_form_{d['id']}", clear_on_submit=True):
        note = st.text_input("Add a note", placeholder="e.g. Called Englewood Pharmaceuticals, quote coming Friday", label_visibility="collapsed")
        if st.form_submit_button("Add note"):
            _try(rs.add_note, d["id"], note, success="Note added.")


# Page layout

_flash()
s = rs.summary()
m1, m2, m3, m4 = st.columns(4)
m1.metric("Open RFQs", s["open"])
m2.metric("Waiting for a reply", s["awaiting"])
m3.metric("Late replies", s["late"])
m4.metric("Drafts (not sent)", s["draft"])

FILTERS = {"Open": "open", "Draft": "draft", "Closed": "closed", "Cancelled": "cancelled", "All": None}
choice = st.segmented_control("Show", list(FILTERS), default="Open", key="rm_filter") or "Open"
rows = rs.list_rfqs(FILTERS[choice])
if not rows:
    st.info(f"No {choice.lower()} RFQs." if choice != "All" else "No RFQs yet. Create one from the Vendors page.")
    st.stop()

list_col, detail_col, side_col = st.columns([1, 1.7, 1.3])
with list_col:
    picked = _pick(rows)
d = rs.rfq_detail(picked["id"]) if picked else None
if d:
    with detail_col:
        with st.container(height=LIST_HEIGHT, border=True):
            _detail(d)
    with side_col:
        with st.container(height=640):
            _followup_composer(d)
            _activity(d)