"""Everything the Review Queue page does to the database.

Rules
  * Approving is a person's decision. Nothing in the system approves a pending record by itself.
  * Edits and the decision are saved together in ONE transaction, and every change is written into
    the record's review_notes (who: 'reviewer', what changed, when), so the history travels with it.
  * A record that is missing something a later step relies on cannot be approved (no currency,
    no price, no signing status). The reviewer can still reject it or fix it and approve.
  * The comparable price is recomputed from the saved values whenever a quote is saved, so an edited
    total never leaves a stale 'comparable' number behind.
"""
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import db
import extraction.loader as loader
import extraction.normalize as normalize

LEGAL_STATUSES = ("draft", "sent", "signed", "expired", "terminated", "unknown")


@dataclass
class Result:
    ok: bool
    errors: list[str] = field(default_factory=list)
    message: str = ""


# ---------- Helpers ----------

def _notes(raw: str | None) -> list[dict]:
    return json.loads(raw) if raw else []


def _add_note(conn, table: str, row_id: int, text: str) -> None:
    raw = conn.execute(f"SELECT review_notes FROM {table} WHERE id = ?", (row_id,)).fetchone()["review_notes"]
    items = _notes(raw) + [{"by": "reviewer", "text": f"{db.TODAY}: {text}"}]
    conn.execute(f"UPDATE {table} SET review_notes = ? WHERE id = ?", (json.dumps(items, ensure_ascii=False), row_id))


def _opt(value) -> str | None:
    value = (value or "").strip() if isinstance(value, str) else value
    return value or None


def _iso(value: str | None, label: str, errors: list[str]) -> str | None:
    value = _opt(value)
    if value is None:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        errors.append(f"{label}: '{value}' is not a date in YYYY-MM-DD form")
        return None


def _number(value, label: str, errors: list[str], integer: bool = False) -> float | int | None:
    value = _opt(value if not isinstance(value, (int, float)) else str(value))
    if value is None:
        return None
    try:
        n = float(str(value).replace(",", ""))
    except ValueError:
        errors.append(f"{label}: '{value}' is not a number (use plain digits, e.g. 19050.00)")
        return None
    if n < 0:
        errors.append(f"{label}: must not be negative")
    if integer and n != int(n):
        errors.append(f"{label}: must be a whole number")
    return int(n) if integer else n


def usd_equivalent(amount: float | None, currency: str | None) -> float | None:
    if amount is None or not currency:
        return None
    row = db.query_one("SELECT rate_to_usd FROM exchange_rates WHERE currency = ?", (currency.upper(),))
    return round(amount * row["rate_to_usd"], 2) if row else None


def document_file(document_id: int | None) -> Path | None:
    """The stored file for a document, only if it lives in an approved folder."""
    if not document_id:
        return None
    row = db.query_one("SELECT file_path FROM documents WHERE id = ?", (document_id,))
    if not row:
        return None
    path = Path(row["file_path"])
    path = (path if path.is_absolute() else db.BASE_DIR / path).resolve()
    roots = [(db.BASE_DIR / "data" / sub).resolve() for sub in ("documents", "sample", "rfqs")]
    return path if path.is_file() and any(path.is_relative_to(r) for r in roots) else None


def vendors() -> list[dict]:
    return [dict(r) for r in db.query("SELECT id, name FROM vendors ORDER BY name")]


def rfqs() -> list[dict]:
    return [dict(r) for r in db.query("SELECT id, rfq_number, title FROM rfqs ORDER BY rfq_number")]


# ---------- Quotes ----------

def counts() -> dict:
    return {
        "quotes": db.scalar("SELECT COUNT(*) FROM quotes WHERE review_status = 'pending'"),
        "legal": db.scalar("SELECT COUNT(*) FROM legal_documents WHERE review_status = 'pending'"),
        "invoices": db.scalar("SELECT COUNT(*) FROM invoices WHERE review_status = 'pending'"),
        "attention": len(loader.review_queue()),
    }


def pending_quotes() -> list[dict]:
    return [dict(r) for r in db.query(
        """SELECT q.id, q.reference_number, q.currency, q.total_amount, q.comparable_amount, q.quote_date,
                  v.name AS vendor_name, r.rfq_number
           FROM quotes q JOIN vendors v ON v.id = q.vendor_id LEFT JOIN rfqs r ON r.id = q.rfq_id
           WHERE q.review_status = 'pending' ORDER BY q.created_at, q.id""")]


def quote_detail(quote_id: int) -> dict | None:
    row = db.query_one(
        """SELECT q.*, v.name AS vendor_name, r.rfq_number FROM quotes q JOIN vendors v ON v.id = q.vendor_id
           LEFT JOIN rfqs r ON r.id = q.rfq_id WHERE q.id = ?""", (quote_id,))
    if row is None:
        return None
    d = dict(row)
    d["notes"] = _notes(d["review_notes"])
    d["line_items"] = [dict(r) for r in db.query("SELECT * FROM quote_line_items WHERE quote_id = ? ORDER BY id", (quote_id,))]
    d["price_tiers"] = [dict(r) for r in db.query("SELECT * FROM quote_price_tiers WHERE quote_id = ? ORDER BY id", (quote_id,))]
    d["comparable_usd"] = usd_equivalent(d["comparable_amount"], d["currency"])
    d["total_usd"] = usd_equivalent(d["total_amount"], d["currency"])
    d["warnings"] = quote_warnings(d)
    return d


def quote_warnings(d: dict) -> list[str]:
    """Things a reviewer should know that are not stored as notes: computed from the current data."""
    out = []
    if d.get("requires_nda"):
        signed = db.scalar(
            """SELECT COUNT(*) FROM legal_documents WHERE vendor_id = ? AND doc_type = 'NDA' AND status = 'signed'
                  AND review_status != 'rejected' AND (expiry_date IS NULL OR expiry_date >= ?)""",
            (d["vendor_id"], db.TODAY))
        if not signed:
            out.append("This quote requires an NDA and no signed, unexpired NDA is on file for this vendor.")
    if d.get("valid_until") and d["valid_until"] < db.TODAY:
        out.append(f"This quote expired on {d['valid_until']}.")
    if d.get("rfq_id") is None:
        out.append("Not linked to an RFQ, so the price cannot be checked against the quantity requested.")
    return out


def _recompute_comparable(conn, quote_id: int) -> None:
    q = dict(conn.execute("SELECT * FROM quotes WHERE id = ?", (quote_id,)).fetchone())
    q["line_items"] = [dict(r) for r in conn.execute("SELECT * FROM quote_line_items WHERE quote_id = ?", (quote_id,))]
    q["price_tiers"] = [dict(r) for r in conn.execute("SELECT * FROM quote_price_tiers WHERE quote_id = ?", (quote_id,))]
    rfq = None
    if q["rfq_id"]:
        rfq = loader._rfq_dict(conn, conn.execute("SELECT * FROM rfqs WHERE id = ?", (q["rfq_id"],)).fetchone())
    c = normalize.compute_comparable(q, rfq)
    conn.execute("UPDATE quotes SET comparable_amount = ?, comparable_basis = ?, comparable_is_estimate = ? WHERE id = ?",
                 (c.amount, c.basis, int(c.is_estimate), quote_id))


QUOTE_FIELDS = ("vendor_id", "rfq_id", "reference_number", "quote_date", "valid_until", "currency",
                "total_amount", "delivery_time_days", "delivery_time_text", "requires_nda")


def _clean_quote(values: dict) -> tuple[dict, list[str]]:
    errors: list[str] = []
    out = {
        "vendor_id": values.get("vendor_id"),
        "rfq_id": values.get("rfq_id") or None,
        "reference_number": _opt(values.get("reference_number")),
        "quote_date": _iso(values.get("quote_date"), "Quote date", errors),
        "valid_until": _iso(values.get("valid_until"), "Valid until", errors),
        "total_amount": _number(values.get("total_amount"), "Total", errors),
        "delivery_time_days": _number(values.get("delivery_time_days"), "Delivery days", errors, integer=True),
        "delivery_time_text": _opt(values.get("delivery_time_text")),
        "requires_nda": None if values.get("requires_nda") is None else int(bool(values["requires_nda"])),
    }
    cur = _opt(values.get("currency"))
    if cur is not None:
        cur = cur.upper()
        if not re.fullmatch(r"[A-Z]{3}", cur):
            errors.append(f"Currency: '{cur}' is not a 3-letter code such as USD, GBP or EUR")
    out["currency"] = cur
    if not out["vendor_id"]:
        errors.append("Vendor is required")
    if out["quote_date"] and out["valid_until"] and out["valid_until"] < out["quote_date"]:
        errors.append("Valid until is earlier than the quote date")
    return out, errors


def review_quote(quote_id: int, values: dict, decision: str, reason: str | None = None) -> Result:
    """Save edits and approve or reject, atomically. decision: 'approve' | 'reject' | 'save'."""
    if decision not in ("approve", "reject", "save"):
        return Result(False, [f"unknown decision {decision!r}"])
    reason = _opt(reason)
    if decision == "reject" and not reason:
        return Result(False, ["Give a short reason for rejecting; it is kept on the record."])
    clean, errors = _clean_quote(values)
    if errors:
        return Result(False, errors)
    try:
        with db.transaction() as conn:
            old = conn.execute("SELECT * FROM quotes WHERE id = ?", (quote_id,)).fetchone()
            if old is None:
                return Result(False, ["That quote no longer exists."])
            if old["review_status"] != "pending":
                return Result(False, [f"This quote was already {old['review_status']}."])
            changes = [f"{k}: {old[k]!r} -> {v!r}" for k, v in clean.items() if old[k] != v]
            sets = ", ".join(f"{k} = ?" for k in clean)
            conn.execute(f"UPDATE quotes SET {sets} WHERE id = ?", (*clean.values(), quote_id))
            _recompute_comparable(conn, quote_id)
            if changes:
                _add_note(conn, "quotes", quote_id, "edited " + "; ".join(changes))
            if decision == "approve":
                new = conn.execute("SELECT currency, total_amount, comparable_amount FROM quotes WHERE id = ?", (quote_id,)).fetchone()
                problems = []
                if not new["currency"]:
                    problems.append("Set the currency before approving.")
                if new["total_amount"] is None and new["comparable_amount"] is None:
                    problems.append("This quote has no price that can be compared. Enter the total, or reject it.")
                if problems:
                    raise _Blocked(problems)
                conn.execute("UPDATE quotes SET review_status = 'approved' WHERE id = ?", (quote_id,))
                _add_note(conn, "quotes", quote_id, "approved")
            elif decision == "reject":
                conn.execute("UPDATE quotes SET review_status = 'rejected' WHERE id = ?", (quote_id,))
                _add_note(conn, "quotes", quote_id, f"rejected: {reason}")
    except _Blocked as b:
        return Result(False, b.problems)
    except Exception as e:
        return Result(False, [f"Could not save: {e}"])
    return Result(True, message={"approve": "Quote approved.", "reject": "Quote rejected.", "save": "Changes saved."}[decision])


class _Blocked(Exception):
    def __init__(self, problems):
        self.problems = problems


# ---------- Legal documents ----------

def pending_legal() -> list[dict]:
    return [dict(r) for r in db.query(
        """SELECT l.id, l.doc_type, l.status, l.effective_date, l.expiry_date, v.name AS vendor_name
           FROM legal_documents l JOIN vendors v ON v.id = l.vendor_id
           WHERE l.review_status = 'pending' ORDER BY l.created_at, l.id""")]


def legal_detail(legal_id: int) -> dict | None:
    row = db.query_one("SELECT l.*, v.name AS vendor_name FROM legal_documents l JOIN vendors v ON v.id = l.vendor_id "
                       "WHERE l.id = ?", (legal_id,))
    if row is None:
        return None
    d = dict(row)
    d["notes"] = _notes(d["review_notes"])
    return d


def review_legal(legal_id: int, values: dict, decision: str, reason: str | None = None) -> Result:
    if decision not in ("approve", "reject", "save"):
        return Result(False, [f"unknown decision {decision!r}"])
    reason = _opt(reason)
    if decision == "reject" and not reason:
        return Result(False, ["Give a short reason for rejecting; it is kept on the record."])
    errors: list[str] = []
    doc_type = values.get("doc_type")
    status = values.get("status")
    clean = {
        "vendor_id": values.get("vendor_id"),
        "doc_type": doc_type,
        "other_description": _opt(values.get("other_description")) if doc_type == "OTHER" else None,
        "status": status,
        "signer": _opt(values.get("signer")),
        "vendor_signer": _opt(values.get("vendor_signer")),
        "effective_date": _iso(values.get("effective_date"), "Effective date", errors),
        "expiry_date": _iso(values.get("expiry_date"), "Expiry date", errors),
        "auto_renews": int(bool(values.get("auto_renews"))),
        "notice_days": _number(values.get("notice_days"), "Notice days", errors, integer=True),
    }
    if not clean["vendor_id"]:
        errors.append("Vendor is required")
    if doc_type not in ("NDA", "MSA", "SOW", "OTHER"):
        errors.append("Choose a document type")
    if status not in LEGAL_STATUSES:
        errors.append("Choose a status")
    if clean["effective_date"] and clean["expiry_date"] and clean["expiry_date"] < clean["effective_date"]:
        errors.append("Expiry date is earlier than the effective date")
    if decision == "approve":
        if status == "unknown":
            errors.append("Set the signing status (draft, sent, signed...) before approving.")
        if status == "signed" and not clean["effective_date"]:
            errors.append("A signed document needs an effective date.")
        if doc_type == "OTHER" and not clean["other_description"]:
            errors.append("Describe what this OTHER document is.")
    if errors:
        return Result(False, errors)
    try:
        with db.transaction() as conn:
            old = conn.execute("SELECT * FROM legal_documents WHERE id = ?", (legal_id,)).fetchone()
            if old is None:
                return Result(False, ["That document no longer exists."])
            if old["review_status"] != "pending":
                return Result(False, [f"This document was already {old['review_status']}."])
            changes = [f"{k}: {old[k]!r} -> {v!r}" for k, v in clean.items() if old[k] != v]
            conn.execute(f"UPDATE legal_documents SET {', '.join(f'{k} = ?' for k in clean)} WHERE id = ?",
                         (*clean.values(), legal_id))
            if changes:
                _add_note(conn, "legal_documents", legal_id, "edited " + "; ".join(changes))
            if decision == "approve":
                conn.execute("UPDATE legal_documents SET review_status = 'approved' WHERE id = ?", (legal_id,))
                _add_note(conn, "legal_documents", legal_id, "approved")
            elif decision == "reject":
                conn.execute("UPDATE legal_documents SET review_status = 'rejected' WHERE id = ?", (legal_id,))
                _add_note(conn, "legal_documents", legal_id, f"rejected: {reason}")
    except Exception as e:
        return Result(False, [f"Could not save: {e}"])
    return Result(True, message={"approve": "Document approved.", "reject": "Document rejected.", "save": "Changes saved."}[decision])


# ---------- Needs attention (failed / unmatched / open actions) ----------

def attention_items() -> list[dict]:
    items = []
    for r in loader.review_queue():
        row = db.query_one("SELECT document_id, summary FROM extractions WHERE id = ?", (r["id"],))
        r["document_id"], r["summary"] = row["document_id"], row["summary"]
        r["errors"] = json.loads(r["errors_json"]) if r["errors_json"] else []
        items.append(r)
    return items


def assign_vendor(extraction_id: int, vendor_id: int) -> Result:
    out = loader.apply_extraction(extraction_id, vendor_id)
    ok = out.status in ("applied", "info_only", "duplicate")
    return Result(ok, [] if ok else [out.message or out.status], out.message)


def retry_apply(extraction_id: int) -> Result:
    out = loader.apply_extraction(extraction_id)
    ok = out.status in ("applied", "info_only", "duplicate")
    return Result(ok, [] if ok else [out.message or out.status], out.message)


def resolve_action(extraction_id: int, how: str) -> None:
    """Close an item without applying it. how: 'done' (handled elsewhere) or 'dismissed' (not needed)."""
    if how not in ("done", "dismissed"):
        raise ValueError(how)
    db.execute("UPDATE extractions SET action_status = ? WHERE id = ?", (how, extraction_id))


# ---------- invoices ----------

def pending_invoices() -> list[dict]:
    return [dict(r) for r in db.query(
        """SELECT i.id, i.invoice_number, i.amount, i.currency, i.invoice_date, v.name AS vendor_name
           FROM invoices i JOIN vendors v ON v.id = i.vendor_id WHERE i.review_status = 'pending'
           ORDER BY i.created_at, i.id""")]


def vendor_pos(vendor_id: int) -> list[dict]:
    """Purchase orders an invoice from this vendor could belong to."""
    return [dict(r) for r in db.query(
        "SELECT id, po_number, amount, currency, status FROM purchase_orders WHERE vendor_id = ? AND status != 'cancelled' "
        "ORDER BY po_number", (vendor_id,))]


def invoice_detail(invoice_id: int) -> dict | None:
    import services.purchasing_service
    row = db.query_one("SELECT i.*, v.name AS vendor_name FROM invoices i JOIN vendors v ON v.id = i.vendor_id WHERE i.id = ?",
                       (invoice_id,))
    if row is None:
        return None
    d = dict(row)
    d["notes"] = _notes(d["review_notes"])
    d["amount_usd"] = usd_equivalent(d["amount"], d["currency"])
    d["warnings"] = services.purchasing_service.invoice_warnings(d)
    return d


def review_invoice(invoice_id: int, values: dict, decision: str, reason: str | None = None) -> Result:
    """Save edits and approve or reject an invoice, atomically. Approving makes it count as spend."""
    if decision not in ("approve", "reject", "save"):
        return Result(False, [f"unknown decision {decision!r}"])
    reason = _opt(reason)
    if decision == "reject" and not reason:
        return Result(False, ["Give a short reason for rejecting; it is kept on the record."])
    errors: list[str] = []
    cur = (_opt(values.get("currency")) or "").upper()
    if cur and not re.fullmatch(r"[A-Z]{3}", cur):
        errors.append(f"Currency: '{cur}' is not a 3-letter code such as USD, GBP or EUR")
    clean = {
        "vendor_id": values.get("vendor_id"),
        "po_id": values.get("po_id") or None,
        "invoice_number": _opt(values.get("invoice_number")),
        "amount": _number(values.get("amount"), "Amount", errors),
        "currency": cur or None,
        "invoice_date": _iso(values.get("invoice_date"), "Invoice date", errors),
        "due_date": _iso(values.get("due_date"), "Due date", errors),
    }
    if not clean["vendor_id"]:
        errors.append("Vendor is required")
    if clean["invoice_date"] and clean["due_date"] and clean["due_date"] < clean["invoice_date"]:
        errors.append("Due date is earlier than the invoice date")
    if decision == "approve":
        for key, label in (("invoice_number", "invoice number"), ("amount", "amount"), ("currency", "currency"),
                           ("invoice_date", "invoice date")):
            if clean[key] is None:
                errors.append(f"Set the {label} before approving.")
    if errors:
        return Result(False, errors)
    try:
        with db.transaction() as conn:
            old = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
            if old is None:
                return Result(False, ["That invoice no longer exists."])
            if old["review_status"] != "pending":
                return Result(False, [f"This invoice was already {old['review_status']}."])
            if clean["po_id"]:
                po = conn.execute("SELECT vendor_id FROM purchase_orders WHERE id = ?", (clean["po_id"],)).fetchone()
                if po is None or po["vendor_id"] != clean["vendor_id"]:
                    return Result(False, ["That purchase order does not belong to this vendor."])
            changes = [f"{k}: {old[k]!r} -> {v!r}" for k, v in clean.items() if old[k] != v]
            conn.execute(f"UPDATE invoices SET {', '.join(f'{k} = ?' for k in clean)} WHERE id = ?", (*clean.values(), invoice_id))
            if changes:
                _add_note(conn, "invoices", invoice_id, "edited " + "; ".join(changes))
            if decision == "approve":
                conn.execute("UPDATE invoices SET review_status = 'approved' WHERE id = ?", (invoice_id,))
                _add_note(conn, "invoices", invoice_id, "approved")
            elif decision == "reject":
                conn.execute("UPDATE invoices SET review_status = 'rejected' WHERE id = ?", (invoice_id,))
                _add_note(conn, "invoices", invoice_id, f"rejected: {reason}")
    except Exception as e:
        msg = str(e)
        if "UNIQUE" in msg and "invoice_number" in msg:
            return Result(False, ["This vendor already has an invoice with that number."])
        return Result(False, [f"Could not save: {e}"])
    return Result(True, message={"approve": "Invoice approved.", "reject": "Invoice rejected.", "save": "Changes saved."}[decision])