import streamlit as st
import extraction.readers as readers
import services.review_service as rv
import db

db.ensure_db()
st.set_page_config(page_title="Review Queue", layout="wide")
st.title("Review Queue")
st.caption("Accept and reject pending documents. Nothing extracted by AI is released until a person approves it.")

LIST_HEIGHT = 640


# ---------- Shared pieces ----------

def _flash() -> None:
    """Show the result of the previous action (set just before st.rerun())."""
    msg = st.session_state.pop("review_flash", None)
    if msg:
        st.toast(msg)


def _done(message: str, *clear_keys: str) -> None:
    st.session_state["review_flash"] = message
    for k in clear_keys:
        st.session_state.pop(k, None)
    st.rerun()


def _show_errors(result: rv.Result) -> None:
    for e in result.errors:
        st.error(e)


def _source_preview(document_id: int | None) -> None:
    st.subheader("Source document")
    path = rv.document_file(document_id)
    if path is None:
        st.info("The original file is not available.")
        return
    st.caption(path.name)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        st.pdf(path.read_bytes(), height=LIST_HEIGHT - 60)
    elif suffix in readers.IMAGE_SUFFIXES:
        st.image(str(path))
    else:                                              # docx / xlsx / eml / txt: show the text we read from it
        try:
            st.text_area("Extracted text", readers.read_document(path).text, height=LIST_HEIGHT - 90, disabled=True,
                         label_visibility="collapsed")
        except readers.ReaderError as e:
            st.warning(f"Cannot show this file: {e}")
    st.download_button("Download original", path.read_bytes(), file_name=path.name, key=f"dl_{document_id}")


def _pick(items: list[dict], state_key: str, label_fn) -> dict | None:
    """Left-hand list. Returns the selected item (the first one by default)."""
    ids = [i["id"] for i in items]
    if st.session_state.get(state_key) not in ids:
        st.session_state[state_key] = ids[0] if ids else None
    with st.container(height=LIST_HEIGHT, border=True):
        for item in items:
            selected = item["id"] == st.session_state[state_key]
            if st.button(label_fn(item), key=f"{state_key}_{item['id']}", use_container_width=True,
                         type="primary" if selected else "secondary"):
                st.session_state[state_key] = item["id"]
                st.rerun()
    return next((i for i in items if i["id"] == st.session_state[state_key]), None)


def _notes(notes: list[dict]) -> None:
    who = {"ai": "Flagged by the AI", "loader": "Flagged by the loader", "reviewer": "Reviewer history"}
    for n in notes:
        st.caption(n["text"], help=who.get(n["by"]))


def _decision_buttons(prefix: str):
    """Approve / Save / Reject row inside a form. Returns (clicked, reason)."""
    reason = st.text_input("Reason (required to reject)", key=f"{prefix}_reason")
    a, b, c = st.columns(3)
    approve = a.form_submit_button("Approve", type="primary", use_container_width=True)
    save = b.form_submit_button("Save edits", use_container_width=True)
    reject = c.form_submit_button("Reject", use_container_width=True)
    return ("approve" if approve else "save" if save else "reject" if reject else None), reason


# ---------- Quotes ----------

def quotes_tab() -> None:
    items = rv.pending_quotes()
    if not items:
        st.success("No quotes waiting for review.")
        return

    def label(q):
        amount = f"{q['currency'] or '?'} {q['total_amount']:,.2f}" if q["total_amount"] is not None else "no total"
        return f"{q['vendor_name']}\n{q['reference_number'] or 'no reference'} · {amount}"

    list_col, detail_col, source_col = st.columns([1, 1.5, 1.5])
    with list_col:
        picked = _pick(items, "rv_quote", label)
    if picked is None:
        return
    d = rv.quote_detail(picked["id"])

    with detail_col:
        with st.container(height=LIST_HEIGHT, border=True):
            st.subheader(d["vendor_name"])
            st.badge(f"answers RFQ: {d['answers_rfq'] or 'unclear'}",
                     color="green" if d["answers_rfq"] == "yes" else "orange")
            if d["confidence"] is not None:
                st.caption(f"AI confidence {d['confidence']:.0%}")
            if d["source_language"] and d["source_language"] != "en":
                st.info(f"Original document language: {d['source_language'].upper()}. Descriptions below are shown "
                        "as written with an English translation; the delivery wording is kept as written.")
            for w in d["warnings"]:
                st.warning(w)

            if d["comparable_amount"] is not None:
                usd = f" (about ${d['comparable_usd']:,.2f} USD)" if d["comparable_usd"] and d["currency"] != "USD" else ""
                st.metric("Comparable price", f"{d['currency'] or ''} {d['comparable_amount']:,.2f}{usd}")
                st.caption(d["comparable_basis"] or "")
            else:
                st.warning(d["comparable_basis"] or "No comparable price could be worked out.")
            if d["price_tiers"] or d["line_items"]:
                with st.expander("Line items and price tiers"):
                    if d["line_items"]:
                        # For a document in another language: the original wording AND the English translation,
                        # side by side, so the reviewer can check one against the other.
                        translated = any(li.get("description_en") for li in d["line_items"])
                        cols = ["description"] + (["description_en"] if translated else []) + [
                            "item_type", "quantity", "unit", "unit_price", "line_total"]
                        st.dataframe([{k: li.get(k) for k in cols} for li in d["line_items"]], hide_index=True,
                                     column_config={"description": "Description (as written)",
                                                    "description_en": "English translation"})
                    if d["price_tiers"]:
                        st.dataframe([{k: v for k, v in t.items() if k not in ("id", "quote_id")} for t in d["price_tiers"]],
                                     hide_index=True)
            if d["notes"]:
                with st.expander(f"Notes ({len(d['notes'])})", expanded=True):
                    _notes(d["notes"])

            vendors, rfqs = rv.vendors(), rv.rfqs()
            k = f"q{d['id']}"
            with st.form(f"form_{k}", enter_to_submit=False):
                vendor_ids = [v["id"] for v in vendors]
                vendor_id = st.selectbox("Vendor", vendor_ids, index=vendor_ids.index(d["vendor_id"]),
                                         format_func=lambda i: next(v["name"] for v in vendors if v["id"] == i), key=f"{k}_vendor")
                rfq_ids = [None] + [r["id"] for r in rfqs]
                rfq_id = st.selectbox("RFQ", rfq_ids, index=rfq_ids.index(d["rfq_id"]),
                                      format_func=lambda i: "(none)" if i is None else
                                      next(f"{r['rfq_number']}: {r['title']}" for r in rfqs if r["id"] == i), key=f"{k}_rfq")
                c1, c2 = st.columns(2)
                ref = c1.text_input("Vendor reference", d["reference_number"] or "", key=f"{k}_ref")
                currency = c2.text_input("Currency", d["currency"] or "", max_chars=3, key=f"{k}_cur",
                                         help="3-letter code. Required to approve.")
                total = c1.text_input("Total", "" if d["total_amount"] is None else f"{d['total_amount']:.2f}", key=f"{k}_total")
                days = c2.text_input("Delivery (days)", "" if d["delivery_time_days"] is None else str(d["delivery_time_days"]), key=f"{k}_days")
                qdate = c1.text_input("Quote date", d["quote_date"] or "", placeholder="YYYY-MM-DD", key=f"{k}_qd")
                valid = c2.text_input("Valid until", d["valid_until"] or "", placeholder="YYYY-MM-DD", key=f"{k}_vu")
                dtext = st.text_input("Delivery wording", d["delivery_time_text"] or "", key=f"{k}_dtext")
                nda = st.checkbox("Requires an NDA", value=bool(d["requires_nda"]), key=f"{k}_nda")
                if d["terms"]:
                    st.caption(f"Terms: {d['terms']}")
                decision, reason = _decision_buttons(k)

            if decision:
                result = rv.review_quote(d["id"], {
                    "vendor_id": vendor_id, "rfq_id": rfq_id, "reference_number": ref, "currency": currency,
                    "total_amount": total, "delivery_time_days": days, "quote_date": qdate, "valid_until": valid,
                    "delivery_time_text": dtext, "requires_nda": nda}, decision, reason)
                if result.ok:
                    _done(result.message, "rv_quote")
                _show_errors(result)

    with source_col:
        _source_preview(d["document_id"])


# ---------- Legal documents ----------

def legal_tab() -> None:
    items = rv.pending_legal()
    if not items:
        st.success("No legal documents waiting for review.")
        return
    list_col, detail_col, source_col = st.columns([1, 1.5, 1.5])
    with list_col:
        picked = _pick(items, "rv_legal", lambda l: f"{l['vendor_name']}\n{l['doc_type']} · {l['status']}")
    if picked is None:
        return
    d = rv.legal_detail(picked["id"])
    k = f"l{d['id']}"
    with detail_col:
        with st.container(height=LIST_HEIGHT, border=True):
            st.subheader(f"{d['doc_type']} · {d['vendor_name']}")
            if d["notes"]:
                with st.expander(f"Notes ({len(d['notes'])})", expanded=True):
                    _notes(d["notes"])
            vendors = rv.vendors()
            with st.form(f"form_{k}", enter_to_submit=False):
                vendor_ids = [v["id"] for v in vendors]
                vendor_id = st.selectbox("Vendor", vendor_ids, index=vendor_ids.index(d["vendor_id"]),
                                         format_func=lambda i: next(v["name"] for v in vendors if v["id"] == i), key=f"{k}_vendor")
                types = ["NDA", "MSA", "SOW", "OTHER"]
                c1, c2 = st.columns(2)
                doc_type = c1.selectbox("Type", types, index=types.index(d["doc_type"]), key=f"{k}_type")
                status = c2.selectbox("Status", rv.LEGAL_STATUSES, index=rv.LEGAL_STATUSES.index(d["status"]), key=f"{k}_status",
                                      help="Must not be 'unknown' to approve.")
                other = st.text_input("If OTHER, what is it?", d["other_description"] or "", key=f"{k}_other")
                signer = c1.text_input("Signed for us by", d["signer"] or "", key=f"{k}_signer")
                vsigner = c2.text_input("Signed for the vendor by", d["vendor_signer"] or "", key=f"{k}_vsigner")
                eff = c1.text_input("Effective date", d["effective_date"] or "", placeholder="YYYY-MM-DD", key=f"{k}_eff")
                exp = c2.text_input("Expiry date", d["expiry_date"] or "", placeholder="YYYY-MM-DD", key=f"{k}_exp")
                renew = c1.checkbox("Auto-renews", value=bool(d["auto_renews"]), key=f"{k}_renew")
                notice = c2.text_input("Notice days", "" if d["notice_days"] is None else str(d["notice_days"]), key=f"{k}_notice")
                decision, reason = _decision_buttons(k)
            if decision:
                result = rv.review_legal(d["id"], {
                    "vendor_id": vendor_id, "doc_type": doc_type, "other_description": other, "status": status,
                    "signer": signer, "vendor_signer": vsigner, "effective_date": eff, "expiry_date": exp,
                    "auto_renews": renew, "notice_days": notice}, decision, reason)
                if result.ok:
                    _done(result.message, "rv_legal")
                _show_errors(result)
    with source_col:
        _source_preview(d["document_id"])


# ---------- Invoices ----------

def invoices_tab() -> None:
    items = rv.pending_invoices()
    if not items:
        st.success("No invoices waiting for review.")
        return
    list_col, detail_col, source_col = st.columns([1, 1.5, 1.5])
    with list_col:
        picked = _pick(items, "rv_invoice", lambda i: f"{i['vendor_name']}\n{i['invoice_number']} · {i['currency'] or '?'} {i['amount']:,.2f}")
    if picked is None:
        return
    d = rv.invoice_detail(picked["id"])
    k = f"i{d['id']}"
    with detail_col:
        with st.container(height=LIST_HEIGHT, border=True):
            st.subheader(f"Invoice {d['invoice_number']} · {d['vendor_name']}")
            for w in d["warnings"]:
                st.warning(w)
            usd = f" (about ${d['amount_usd']:,.2f} USD)" if d["amount_usd"] and d["currency"] != "USD" else ""
            st.metric("Amount", f"{d['currency']} {d['amount']:,.2f}{usd}")
            st.caption("Approving makes this invoice count as spend. Paying it is recorded separately on the Purchasing page.")
            if d["notes"]:
                with st.expander(f"Notes ({len(d['notes'])})", expanded=True):
                    _notes(d["notes"])
            vendors = rv.vendors()
            pos = rv.vendor_pos(d["vendor_id"])
            with st.form(f"form_{k}"):
                vendor_ids = [v["id"] for v in vendors]
                vendor_id = st.selectbox("Vendor", vendor_ids, index=vendor_ids.index(d["vendor_id"]),
                                         format_func=lambda i: next(v["name"] for v in vendors if v["id"] == i), key=f"{k}_vendor",
                                         help="If you change the vendor, save first, then pick the purchase order.")
                po_ids = [None] + [p["id"] for p in pos]
                po_id = st.selectbox("Purchase order", po_ids, index=po_ids.index(d["po_id"]) if d["po_id"] in po_ids else 0,
                                     format_func=lambda i: "(none)" if i is None else next(
                                         f"{p['po_number']} · {p['currency']} {p['amount']:,.2f} ({p['status']})" for p in pos if p["id"] == i),
                                     key=f"{k}_po")
                if d["po_reference"]:
                    st.caption(f"The invoice cites: {d['po_reference']}")
                c1, c2 = st.columns(2)
                number = c1.text_input("Invoice number", d["invoice_number"] or "", key=f"{k}_num")
                currency = c2.text_input("Currency", d["currency"] or "", max_chars=3, key=f"{k}_cur")
                amount = c1.text_input("Amount", "" if d["amount"] is None else f"{d['amount']:.2f}", key=f"{k}_amt")
                idate = c2.text_input("Invoice date", d["invoice_date"] or "", placeholder="YYYY-MM-DD", key=f"{k}_idate")
                due = c1.text_input("Due date", d["due_date"] or "", placeholder="YYYY-MM-DD", key=f"{k}_due")
                decision, reason = _decision_buttons(k)
            if decision:
                result = rv.review_invoice(d["id"], {"vendor_id": vendor_id, "po_id": po_id, "invoice_number": number,
                                                     "amount": amount, "currency": currency, "invoice_date": idate,
                                                     "due_date": due}, decision, reason)
                if result.ok:
                    _done(result.message, "rv_invoice")
                _show_errors(result)
    with source_col:
        _source_preview(d["document_id"])


# ---------- Needs attention ----------

def attention_tab() -> None:
    items = rv.attention_items()
    if not items:
        st.success("Nothing needs attention.")
        return
    st.caption("Documents the system could not turn into records by itself, and emails that ask for a follow-up.")
    vendors = rv.vendors()
    for it in items:
        with st.container(border=True):
            left, right = st.columns([6, 1], gap="medium")
            with left:
                st.markdown(f"**{it['file_name']}**  ·  {it['doc_type']}")
                st.badge(it["status"].replace("_", " "), color="red" if it["status"] in ("invalid", "failed") else "orange")
                if it["summary"]:
                    st.write(it["summary"])
                for e in it["errors"]:
                    st.error(e)
                if it["status_detail"] and not it["errors"]:
                    st.caption(it["status_detail"])
                if it["action_needed"] and it["action_status"] == "open":
                    st.info(it["action_needed"])
                if it["status"] == "invalid":
                    st.caption("The AI result failed checks. Re-run it from the upload dialog or the command line "
                               "(`python loader.py --live --retry-invalid`), or dismiss it.")
            with right:
                if it["status"] == "needs_vendor":
                    vid = st.selectbox("Assign to vendor", [v["id"] for v in vendors], index=None,
                                       format_func=lambda i: next(v["name"] for v in vendors if v["id"] == i),
                                       key=f"att_v_{it['id']}", placeholder="Choose a vendor")
                    if st.button("Assign and load", key=f"att_assign_{it['id']}", disabled=vid is None, type="primary", use_container_width=True):
                        r = rv.assign_vendor(it["id"], vid)
                        _done(r.message) if r.ok else _show_errors(r)
                if it["status"] == "failed" and st.button("Retry", key=f"att_retry_{it['id']}", type="primary", use_container_width=True):
                    r = rv.retry_apply(it["id"])
                    _done(r.message or "Loaded.") if r.ok else _show_errors(r)
                if it["status"] == "info_only" and st.button("Mark as handled", key=f"att_done_{it['id']}", type="primary", use_container_width=True):
                    rv.resolve_action(it["id"], "done")
                    _done("Marked as handled.")
                if st.button("Dismiss", key=f"att_dismiss_{it['id']}",
                             help="Remove from this list without loading anything. The file stays in Documents.", use_container_width=True):
                    rv.resolve_action(it["id"], "dismissed")
                    _done("Dismissed.")


_flash()
n = rv.counts()
tab_q, tab_l, tab_i, tab_a = st.tabs([f"Quotes ({n['quotes']})", f"Legal documents ({n['legal']})",
                                      f"Invoices ({n['invoices']})", f"Needs attention ({n['attention']})"])
with tab_q:
    quotes_tab()
with tab_l:
    legal_tab()
with tab_i:
    invoices_tab()
with tab_a:
    attention_tab()