"""Turn extracted documents into database records.

    python loader.py                       # dry run over data/sample (reads cached results, no API calls)
    python loader.py --live --max-calls 5  # allow API calls for files without a cached result
    python loader.py path/to/file.pdf      # one file
    python loader.py --review              # list everything that needs a human (failed, unmatched, actions)
    python loader.py --raw 7               # show what the AI returned for extraction #7, and why it failed
    python loader.py --live --retry-invalid  # re-extract documents whose earlier result failed validation

Two phases, so a failure never loses work:
  1. REGISTER: store the file in `documents` and the AI result in `extractions`.
     This happens even when the result failed validation, so nothing silently disappears.
  2. APPLY: turn the stored result into records (quotes, legal documents, RFQ status).
     This runs in ONE transaction: it either all happens or none of it does. It can be
     re-run later, for example after a human picks the right vendor.

Principles
  * The AI proposes, a human approves: every quote and legal document is created 'pending'.
  * Nothing is guessed: no vendor is created from an extracted name, an unmatched vendor
    waits for a human, and anything ambiguous becomes a note on the record.
"""
import argparse
import json
import mimetypes
import re
import shutil
import sys
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

import db
import extraction.extract as extract
import extraction.normalize as normalize
from services.rfq_service import log_message
from services.vendor_service import normalize_vendor_name

DOCS_DIR = db.BASE_DIR / "data" / "documents"
# Files under these folders are referenced where they are. Anything else (an upload from a
# temp folder) is copied into DOCS_DIR.
ALLOWED_FILE_ROOTS = (DOCS_DIR, extract.SAMPLE_DIR)

_FREE_MAIL = {"gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com", "proton.me", "protonmail.com"}
_RFQ_RE = re.compile(r"RFQ[\s_-]*(\d{4})[\s_-]*(\d{1,4})", re.IGNORECASE)
_REVISION_NAME_RE = re.compile(r"revis|(?:^|[_\-\s])(?:v|rev|r)\.?\s*[2-9](?:$|[_\-\s.])", re.IGNORECASE)
RETRYABLE_STATUSES = ("new", "needs_vendor", "failed")


@dataclass
class Outcome:
    path: Path | None
    status: str                       # applied | needs_vendor | invalid | duplicate | info_only | failed |
                                      # dry-run | skipped | error
    message: str = ""
    document_id: int | None = None
    extraction_id: int | None = None
    quote_id: int | None = None
    legal_id: int | None = None
    notes: list[str] = field(default_factory=list)


# ---------- Small helpers ----------

def _rel(path: Path) -> str:
    try:
        return path.relative_to(db.BASE_DIR).as_posix()
    except ValueError:
        return str(path)


def _stored_path(path: Path, sha: str) -> str:
    """Path to record for a file: in place if it already lives under an allowed folder, else a copy."""
    path = path.resolve()
    for root in ALLOWED_FILE_ROOTS:
        if path.is_relative_to(Path(root).resolve()):
            return _rel(path)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", path.name)
    target = DOCS_DIR / f"{sha[:8]}_{safe}"
    if not target.exists():
        shutil.copy2(path, target)
    return _rel(target)


def _email_meta(path: Path) -> dict:
    """Sender address and sent date from an .eml's headers (reliable, unlike the AI's reading)."""
    if path.suffix.lower() != ".eml":
        return {}
    try:
        with open(path, "rb") as f:
            msg = BytesParser(policy=policy.default).parse(f)
    except OSError:
        return {}
    meta = {}
    sender = parseaddr(str(msg["From"] or ""))[1].strip().lower()
    if sender:
        meta["sender"] = sender
    try:
        meta["sent_on"] = parsedate_to_datetime(str(msg["Date"])).date().isoformat()
    except (TypeError, ValueError):
        pass
    return meta


def _notes_json(ai_flags: list[str] | None, loader_notes: list[str]) -> str | None:
    items = [{"by": "ai", "text": t} for t in (ai_flags or [])] + [{"by": "loader", "text": t} for t in loader_notes]
    return json.dumps(items, ensure_ascii=False) if items else None


# ---------- Matching: vendor and RFQ ----------

def resolve_vendor(conn, name: str | None, sender: str | None = None) -> tuple[int | None, str]:
    """Find the existing vendor a document belongs to. Never creates one.

    Evidence, strongest first: sender address of a known contact, exact normalised name,
    the sender's company domain, then a cautious fuzzy name match. If two kinds of
    evidence disagree the result is 'unresolved' so a human decides.
    Returns (vendor_id or None, explanation).
    """
    by_sender = by_name = None
    how_sender = how_name = ""

    if sender:
        rows = conn.execute("SELECT DISTINCT vendor_id FROM vendor_contacts WHERE lower(email) = ?", (sender,)).fetchall()
        if len(rows) == 1:
            by_sender, how_sender = rows[0][0], "sender address matches a known contact"
        elif "@" in sender and sender.split("@")[1] not in _FREE_MAIL:
            rows = conn.execute("SELECT DISTINCT vendor_id FROM vendor_contacts WHERE lower(email) LIKE ?",
                                (f"%@{sender.split('@')[1]}",)).fetchall()
            if len(rows) == 1:
                by_sender, how_sender = rows[0][0], "sender's company domain matches a known contact"

    if name and name.strip():
        key = normalize_vendor_name(name)
        row = conn.execute("SELECT id FROM vendors WHERE normalized_name = ?", (key,)).fetchone()
        if row:
            by_name, how_name = row["id"], "vendor name matches exactly"
        elif key:
            hits = []
            for v in conn.execute("SELECT id, normalized_name FROM vendors").fetchall():
                other = v["normalized_name"]
                short, long_ = sorted((key, other), key=len)
                if (len(short) >= 6 and long_.startswith(short)) or SequenceMatcher(None, key, other).ratio() >= 0.9:
                    hits.append(v["id"])
            if len(hits) == 1:
                by_name, how_name = hits[0], f"vendor name '{name}' is a close (fuzzy) match; verify"

    if by_sender and by_name and by_sender != by_name:
        return None, "conflict: the sender matches one vendor but the document names another"
    if by_sender:
        return by_sender, how_sender
    if by_name:
        return by_name, how_name
    return None, f"no vendor matched '{name or ''}'" + (f" or sender {sender}" if sender else "")


def _rfq_dict(conn, row) -> dict:
    """An RFQ row plus the quantity and unit of its requested item.

    Quantity lives on rfq_items. Only when the RFQ has exactly one item with both a quantity and
    a unit (a 500 g synthesis plus a unit-less certificate counts as one) is it safe to compare a quote against it; otherwise they stay None and the price
    comparison says so instead of guessing which item a quote total covers.
    """
    rfq = dict(row)
    items = [i for i in conn.execute("SELECT quantity, unit FROM rfq_items WHERE rfq_id = ?", (rfq["id"],)).fetchall()
             if i["quantity"] is not None and i["unit"]]       # unit-less lines (a CoA, a report) are not the priced quantity
    rfq["quantity"] = rfq["unit"] = None
    if len(items) == 1:
        rfq["quantity"], rfq["unit"] = items[0]["quantity"], items[0]["unit"]
    return rfq


def resolve_rfq(conn, ref: str | None, vendor_id: int, allow_infer: bool = True) -> tuple[dict | None, list[str]]:
    notes: list[str] = []
    if ref:
        m = _RFQ_RE.search(ref)
        if m:
            number = f"RFQ-{m.group(1)}-{int(m.group(2)):03d}"
            row = conn.execute("SELECT * FROM rfqs WHERE rfq_number = ?", (number,)).fetchone()
            if row:
                return _rfq_dict(conn, row), notes
            notes.append(f"RFQ reference '{ref}' is not in the RFQ list")
        else:
            notes.append(f"unrecognised RFQ reference '{ref}'")
    if allow_infer:
        rows = conn.execute(
            """SELECT r.* FROM rfqs r JOIN rfq_invitations i ON i.rfq_id = r.id
               WHERE i.vendor_id = ? AND r.status = 'open'""", (vendor_id,)).fetchall()
        if len(rows) == 1:
            notes.append(f"no usable RFQ reference; linked to {rows[0]['rfq_number']}, the only open RFQ this "
                         f"vendor was invited to (verify)")
            return _rfq_dict(conn, rows[0]), notes
        if not ref:
            notes.append("no RFQ reference in the document and it could not be inferred")
    return None, notes


# ---------- Applying: quotes ----------

def _link_revision(conn, new_id: int, vendor_id: int, rfq_id: int | None, reference: str | None,
                   quote_date: str | None, supersedes_ref: str | None, revision_hint: bool, notes: list[str]) -> None:
    """Set supersedes_quote_id when this quote is a newer (or older) version of one we already hold."""
    base = normalize.base_reference(supersedes_ref) or normalize.base_reference(reference)
    others = conn.execute(
        """SELECT id, reference_number, quote_date, supersedes_quote_id, rfq_id FROM quotes
           WHERE vendor_id = ? AND id != ? AND review_status != 'rejected'""", (vendor_id, new_id)).fetchall()

    same_family = [o for o in others
                   if base and normalize.base_reference(o["reference_number"]) == base
                   and (rfq_id is None or o["rfq_id"] is None or o["rfq_id"] == rfq_id)]
    if same_family:
        newest = max(same_family, key=lambda o: (o["quote_date"] or "", o["id"]))
        if revision_hint or (quote_date or "") >= (newest["quote_date"] or ""):
            conn.execute("UPDATE quotes SET supersedes_quote_id = ? WHERE id = ?", (newest["id"], new_id))
            notes.append(f"supersedes quote #{newest['id']} ({newest['reference_number'] or 'no reference'})")
        elif newest["supersedes_quote_id"] is None:
            conn.execute("UPDATE quotes SET supersedes_quote_id = ? WHERE id = ?", (new_id, newest["id"]))
            notes.append(f"is older than quote #{newest['id']}, which now supersedes it")
        else:
            notes.append(f"is older than quote #{newest['id']} (already superseded elsewhere); check the chain")
        return

    siblings = [o for o in others if rfq_id is not None and o["rfq_id"] == rfq_id]
    if siblings:
        ids = ", ".join(f"#{o['id']}" for o in siblings)
        notes.append((f"marked as a revision but no matching reference was found; " if revision_hint else "")
                     + f"this vendor already has quote {ids} for the same RFQ; link manually if this replaces one")
    elif revision_hint:
        notes.append("marked as a revision but no earlier quote from this vendor was found")


def _mark_replied(conn, rfq_id: int, vendor_id: int, on: str | None, notes: list[str]) -> None:
    inv = conn.execute("SELECT id, status FROM rfq_invitations WHERE rfq_id = ? AND vendor_id = ?",
                       (rfq_id, vendor_id)).fetchone()
    if inv is None:
        notes.append("this vendor was not invited to the RFQ")
        return
    if inv["status"] == "draft":
        notes.append("the invitation was still a draft (never sent)")
    if inv["status"] != "replied":
        conn.execute("UPDATE rfq_invitations SET status = 'replied', replied_at = COALESCE(replied_at, ?) WHERE id = ?",
                     (on or db.TODAY, inv["id"]))


def _apply_quote(conn, doc_id: int, q: dict, vendor_id: int, *, replied_on: str | None,
                 revision_hint: bool, supersedes_ref: str | None, extra_notes: list[str]) -> tuple[int | None, list[str]]:
    """Insert a quote with its line items and price tiers. Returns (quote_id or None if duplicate, notes)."""
    notes = list(extra_notes)
    rfq, rfq_notes = resolve_rfq(conn, q.get("rfq_reference"), vendor_id)
    notes += rfq_notes
    rfq_id = rfq["id"] if rfq else None

    if q.get("reference_number") is not None or q.get("total_amount") is not None:
        dup = conn.execute(
            """SELECT id FROM quotes WHERE vendor_id = ? AND reference_number IS ? AND total_amount IS ?
                  AND currency IS ? AND quote_date IS ?""",
            (vendor_id, q.get("reference_number"), q.get("total_amount"), q.get("currency"), q.get("quote_date"))).fetchone()
        if dup:
            return None, [f"same vendor, reference, amount and date as quote #{dup['id']}"]

    valid_until = q.get("valid_until")
    if valid_until is None and q.get("valid_for_days") is not None:
        valid_until = normalize.derive_valid_until(q.get("quote_date"), q["valid_for_days"])
        if valid_until:
            notes.append(f"valid_until derived: quote date + {q['valid_for_days']} days")
        else:
            notes.append(f"valid for {q['valid_for_days']} days, but no quote date to count from")

    if q.get("currency") is None:
        notes.append("currency not stated: set it before approving")

    comparable = normalize.compute_comparable(q, rfq)
    notes += comparable.notes

    quote_id = conn.execute(
        """INSERT INTO quotes (vendor_id, rfq_id, document_id, reference_number, quote_date, valid_until, currency,
               total_amount, delivery_time_days, delivery_time_text, terms, requires_nda, answers_rfq, source_language,
               comparable_amount, comparable_basis, comparable_is_estimate, review_notes, field_sources,
               confidence, review_status)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'pending')""",
        (vendor_id, rfq_id, doc_id, q.get("reference_number"), q.get("quote_date"), valid_until, q.get("currency"),
         q.get("total_amount"), q.get("delivery_time_days"), q.get("delivery_time_text"), q.get("terms"),
         None if q.get("requires_nda") is None else int(q["requires_nda"]), q.get("answers_rfq"),
         q.get("source_language"), comparable.amount, comparable.basis, int(comparable.is_estimate),
         None, json.dumps(q.get("field_sources") or [], ensure_ascii=False), q.get("confidence"))).lastrowid

    for item in q.get("line_items") or []:
        conn.execute(
            """INSERT INTO quote_line_items (quote_id, description, description_en, item_type, quantity, unit,
                   unit_price, line_total) VALUES (?,?,?,?,?,?,?,?)""",
            (quote_id, item["description"], item.get("description_en"), item.get("item_type") or "main",
             item.get("quantity"), item.get("unit"), item.get("unit_price"), item.get("line_total")))
    for tier in q.get("price_tiers") or []:
        conn.execute(
            """INSERT INTO quote_price_tiers (quote_id, min_quantity, max_quantity, quantity_unit, unit_price, price_unit)
               VALUES (?,?,?,?,?,?)""",
            (quote_id, tier.get("min_quantity"), tier.get("max_quantity"), tier.get("quantity_unit"),
             tier["unit_price"], tier.get("price_unit")))

    _link_revision(conn, quote_id, vendor_id, rfq_id, q.get("reference_number"), q.get("quote_date"),
                   supersedes_ref, revision_hint, notes)
    if rfq_id is not None:
        _mark_replied(conn, rfq_id, vendor_id, q.get("quote_date") or replied_on, notes)

    conn.execute("UPDATE quotes SET review_notes = ? WHERE id = ?", (_notes_json(q.get("flags"), notes), quote_id))
    return quote_id, notes


# ---------- Applying: legal documents ----------

def _apply_legal(conn, doc_id: int, d: dict, vendor_id: int) -> tuple[int, list[str]]:
    notes: list[str] = []
    doc_type = d["doc_type"] if d["doc_type"] in ("NDA", "MSA", "SOW") else "OTHER"
    status = {"signed": "signed", "draft": "draft"}.get(d.get("status"), "unknown")
    if status == "unknown":
        notes.append("signing status not clear from the document; confirm before relying on it")

    expiry = d.get("expiry_date")
    if expiry is None and d.get("effective_date") and d.get("term_months") is not None:
        expiry = normalize.add_months(d["effective_date"], d["term_months"])
        if expiry:
            notes.append(f"expiry derived: effective date + {d['term_months']} months; check the contract wording")
    if expiry is None:
        notes.append("no expiry date found; no renewal reminder can be set")

    parent_id = None
    if doc_type == "SOW" or d.get("parent_agreement_reference"):
        msas = conn.execute("SELECT id FROM legal_documents WHERE vendor_id = ? AND doc_type = 'MSA'", (vendor_id,)).fetchall()
        if len(msas) == 1:
            parent_id = msas[0]["id"]
            notes.append(f"linked to MSA #{parent_id}")
        elif doc_type == "SOW":
            notes.append("SOW not linked to an MSA (" + ("none" if not msas else "several") + " on file for this vendor)")

    same = conn.execute("SELECT id FROM legal_documents WHERE vendor_id = ? AND doc_type = ?", (vendor_id, doc_type)).fetchall()
    if same and doc_type != "OTHER":
        notes.append(f"this vendor already has {doc_type} #{', #'.join(str(s['id']) for s in same)}; check which is current")

    legal_id = conn.execute(
        """INSERT INTO legal_documents (vendor_id, document_id, parent_id, doc_type, other_description, status, signer,
               vendor_signer, effective_date, expiry_date, auto_renews, notice_days, review_status, review_notes)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'pending', ?)""",
        (vendor_id, doc_id, parent_id, doc_type, d.get("other_type_description") if doc_type == "OTHER" else None,
         status, d.get("our_signer"), d.get("counterparty_signer"), d.get("effective_date"), expiry,
         int(bool(d.get("auto_renews"))), d.get("renewal_notice_days"), None)).lastrowid
    conn.execute("UPDATE legal_documents SET review_notes = ? WHERE id = ?", (_notes_json(d.get("flags"), notes), legal_id))
    return legal_id, notes


_PO_RE = re.compile(r"PO[-\s]?(\d{4})[-\s]?(\d{3,4})", re.IGNORECASE)


def _apply_invoice(conn, doc_id: int, d: dict, vendor_id: int) -> tuple[int | None, list[str], str, str]:
    """Insert an invoice as pending. Returns (invoice_id, notes, status, detail); status is applied | duplicate | invalid."""
    notes: list[str] = []
    missing = [label for key, label in (("invoice_number", "invoice number"), ("total_amount", "total amount"),
                                         ("currency", "currency"), ("invoice_date", "invoice date")) if d.get(key) is None]
    if missing:
        return None, notes, "invalid", "the invoice is missing " + ", ".join(missing) + "; needs manual entry"

    dup = conn.execute("SELECT id FROM invoices WHERE vendor_id = ? AND invoice_number = ?",
                       (vendor_id, d["invoice_number"])).fetchone()
    if dup:
        return None, notes, "duplicate", f"invoice {d['invoice_number']} is already recorded (#{dup['id']})"

    due = d.get("due_date")
    if due is None and d.get("payment_terms_days") is not None:
        due = normalize.derive_valid_until(d["invoice_date"], d["payment_terms_days"])
        if due:
            notes.append(f"due date derived: invoice date + {d['payment_terms_days']} days")
    if due is None:
        notes.append("no due date or payment term found; overdue reminders will not work for this invoice")

    if d.get("subtotal") is not None and d.get("tax_amount") is not None \
            and abs(d["subtotal"] + d["tax_amount"] - d["total_amount"]) > 0.01:
        notes.append(f"subtotal {d['subtotal']:,.2f} + tax {d['tax_amount']:,.2f} does not equal the total "
                     f"{d['total_amount']:,.2f}; check the document")

    po_id = None
    ref = d.get("po_reference")
    if ref:
        m = _PO_RE.search(ref)
        if not m:
            notes.append(f"unrecognised PO reference '{ref}'")
        else:
            number = f"PO-{m.group(1)}-{int(m.group(2)):03d}"
            po = conn.execute("SELECT id, vendor_id FROM purchase_orders WHERE po_number = ?", (number,)).fetchone()
            if po is None:
                notes.append(f"the invoice cites {number}, which is not in the purchase order list")
            elif po["vendor_id"] != vendor_id:
                notes.append(f"the invoice cites {number}, which belongs to a different vendor; not linked")
            else:
                po_id = po["id"]
    else:
        notes.append("the invoice cites no purchase order")

    invoice_id = conn.execute(
        """INSERT INTO invoices (vendor_id, po_id, document_id, invoice_number, amount, currency, invoice_date, due_date,
               po_reference, review_status, review_notes) VALUES (?,?,?,?,?,?,?,?,?, 'pending', ?)""",
        (vendor_id, po_id, doc_id, d["invoice_number"], d["total_amount"], d["currency"], d["invoice_date"], due, ref,
         _notes_json(d.get("flags"), notes))).lastrowid
    return invoice_id, notes, "applied", "invoice recorded as pending review"


# ---------- Applying: dispatch ----------

def _finish(conn, ext_id: int, status: str, detail: str | None = None, summary: str | None = None,
            action: str | None = None) -> None:
    conn.execute(
        """UPDATE extractions SET status = ?, status_detail = ?, summary = COALESCE(?, summary),
               action_needed = COALESCE(?, action_needed),
               action_status = CASE WHEN ? IS NOT NULL THEN 'open'
                                    WHEN ? = 'applied' AND action_status = 'open' THEN 'done'
                                    ELSE action_status END,
               applied_at = CASE WHEN ? = 'applied' THEN datetime('now') ELSE applied_at END
           WHERE id = ?""", (status, detail, summary, action, action, status, status, ext_id))


_EMAIL_KIND_TO_LOG = {"quote": "reply", "revision": "reply", "question": "question", "decline": "decline",
                      "notice": "notice", "other": "notice"}


def _log_email(conn, d: dict, doc, vendor_id: int | None, meta: dict) -> None:
    """Put a vendor's email into the history of the RFQ it refers to (nothing if it names no known RFQ)."""
    emb = d.get("quote") or {}
    rfq, _ = resolve_rfq(conn, emb.get("rfq_reference") or d.get("rfq_reference"), vendor_id or 0, allow_infer=False)
    if rfq is None:
        return
    body = d.get("summary") or "(no summary)"
    if d.get("action_needed"):
        body += f"\n\nAction needed: {d['action_needed']}"
    log_message(conn, rfq["id"], "inbound", _EMAIL_KIND_TO_LOG.get(d.get("kind"), "notice"), body,
                            vendor_id=vendor_id, document_id=doc["id"], channel="email",
                            occurred_on=meta.get("sent_on") or db.TODAY)


def _apply(conn, ext_id: int, vendor_override: int | None) -> Outcome:
    ext = conn.execute("SELECT * FROM extractions WHERE id = ?", (ext_id,)).fetchone()
    if ext is None:
        return Outcome(None, "error", f"no extraction #{ext_id}")
    doc = conn.execute("SELECT * FROM documents WHERE id = ?", (ext["document_id"],)).fetchone()
    path = db.BASE_DIR / doc["file_path"] if not Path(doc["file_path"]).is_absolute() else Path(doc["file_path"])
    out = Outcome(path, ext["status"], document_id=doc["id"], extraction_id=ext_id)

    if ext["status"] not in RETRYABLE_STATUSES:
        out.message = f"already {ext['status']}; nothing to do"
        return out
    if ext["validity"] != "ok":
        out.status, out.message = "invalid", "extraction failed validation; needs manual entry"
        return out

    d = json.loads(ext["data_json"])
    meta = _email_meta(path)
    kind = ext["doc_type"]
    emb = d.get("quote") if kind == "email" else None
    name = (emb or {}).get("vendor_name") or d.get("vendor_name") or d.get("counterparty_name")

    if vendor_override:
        vendor_id, how = vendor_override, "assigned by a person"
    else:
        vendor_id, how = resolve_vendor(conn, name, meta.get("sender"))
    loose = bool(vendor_id) and how.startswith(("vendor name '", "sender's company domain"))
    loader_notes = [f"vendor matched loosely: {how}"] if loose else []

    if kind == "email":
        _log_email(conn, d, doc, vendor_id, meta)

    # Emails that create no record need no vendor.
    if kind == "email" and d["kind"] in ("question", "notice", "other"):
        if vendor_id:
            conn.execute("UPDATE documents SET vendor_id = ? WHERE id = ?", (vendor_id, doc["id"]))
        action = d.get("action_needed")
        _finish(conn, ext_id, "info_only", "email recorded; no record created", d.get("summary"), action)
        out.status, out.message = "info_only", d.get("summary") or "email recorded"
        return out

    if vendor_id is None:
        _finish(conn, ext_id, "needs_vendor", how, action=f"Choose the vendor for '{doc['file_name']}' ({how})")
        out.status, out.message = "needs_vendor", how
        return out
    conn.execute("UPDATE documents SET vendor_id = ? WHERE id = ?", (vendor_id, doc["id"]))
    out.notes = loader_notes

    if kind == "legal":
        legal_id, notes = _apply_legal(conn, doc["id"], d, vendor_id)
        out.legal_id, out.notes = legal_id, out.notes + notes
        _finish(conn, ext_id, "applied", "legal document recorded as pending review",
                f"{d['doc_type']} with {name or 'vendor'}")
        out.status, out.message = "applied", f"legal document #{legal_id} created (pending review)"
        return out

    if kind == "invoice":
        invoice_id, notes, status, detail = _apply_invoice(conn, doc["id"], d, vendor_id)
        out.notes = out.notes + notes
        what = f"invoice {d.get('invoice_number') or ''} from {name or 'vendor'}".replace("  ", " ")
        _finish(conn, ext_id, status, detail, what)
        out.status = status
        out.message = f"invoice #{invoice_id} created (pending review)" if status == "applied" else detail
        return out

    if kind == "email" and d["kind"] == "decline":
        rfq, notes = resolve_rfq(conn, (emb or {}).get("rfq_reference") or d.get("rfq_reference"), vendor_id, allow_infer=False)
        inv = None
        if rfq:
            inv = conn.execute("SELECT id, status FROM rfq_invitations WHERE rfq_id = ? AND vendor_id = ?",
                               (rfq["id"], vendor_id)).fetchone()
        if inv is None or inv["status"] == "replied":
            why = ("no RFQ number was found in the email" if rfq is None else
                   "the vendor was not invited to that RFQ" if inv is None else
                   "the vendor already replied with a quote")
            _finish(conn, ext_id, "info_only", f"decline not applied: {why}", d.get("summary"),
                    f"Decline from {name}: {why}. Update the RFQ status by hand if appropriate.")
            out.status, out.message = "info_only", f"decline not applied: {why}"
            return out
        conn.execute("UPDATE rfq_invitations SET status = 'declined', replied_at = COALESCE(replied_at, ?) WHERE id = ?",
                     (meta.get("sent_on") or db.TODAY, inv["id"]))
        _finish(conn, ext_id, "applied", f"{rfq['rfq_number']} invitation marked declined", d.get("summary"))
        out.status, out.message = "applied", f"{name} declined {rfq['rfq_number']}"
        return out

    # quote documents, and emails of kind quote / revision
    if kind == "email":
        if not emb:
            _finish(conn, ext_id, "info_only", "email says it holds a quote but none was extracted", d.get("summary"),
                    "The email looks like a quote but no quote data was extracted. Enter it by hand or re-run.")
            out.status, out.message = "info_only", "no quote data in a quote email"
            return out
        q = {**emb, "rfq_reference": emb.get("rfq_reference") or d.get("rfq_reference")}
        q["flags"] = list(emb.get("flags") or []) + list(d.get("flags") or [])
        hint, supersedes_ref = d["kind"] == "revision", d.get("supersedes_reference")
    else:
        q, supersedes_ref = d, None
        hint = bool(_REVISION_NAME_RE.search(doc["file_name"]))

    quote_id, notes = _apply_quote(conn, doc["id"], q, vendor_id, replied_on=meta.get("sent_on"),
                                   revision_hint=hint, supersedes_ref=supersedes_ref, extra_notes=loader_notes)
    out.notes = notes
    if quote_id is None:
        _finish(conn, ext_id, "duplicate", notes[0], f"Duplicate quote from {name}")
        out.status, out.message = "duplicate", notes[0]
        return out
    out.quote_id = quote_id
    amount = f"{q.get('currency') or '?'} {q['total_amount']:,.2f}" if q.get("total_amount") is not None else "no total"
    _finish(conn, ext_id, "applied", "quote recorded as pending review",
            f"Quote {q.get('reference_number') or ''} from {name}: {amount}".replace("  ", " "))
    out.status, out.message = "applied", f"quote #{quote_id} created (pending review)"
    return out


def apply_extraction(extraction_id: int, vendor_id: int | None = None) -> Outcome:
    """Phase 2. Safe to call again: finished extractions are left alone.

    Pass vendor_id to assign an unmatched document to a vendor chosen by a person.
    """
    try:
        with db.transaction() as conn:
            return _apply(conn, extraction_id, vendor_id)
    except Exception as e:
        detail = f"{type(e).__name__}: {e}"[:300]
        db.execute("UPDATE extractions SET status = 'failed', status_detail = ? WHERE id = ?", (detail, extraction_id))
        return Outcome(None, "failed", detail, extraction_id=extraction_id)


def replacement_reason(conn, doc_id: int) -> str | None:
    """
    A document can be replaced if:
    - Its extraction is invalid or dismissed and has no business records.
    - OR it has exactly one rejected quote, legal document, or invoice.

    Never replace records that other business records depend on.
    """
    ext = conn.execute(
        """
        SELECT id, status, action_status
        FROM extractions
        WHERE document_id = ?
        """,
        (doc_id,),
    ).fetchone()

    records = []

    for table, kind in (
        ("quotes", "quote"),
        ("legal_documents", "legal document"),
        ("invoices", "invoice"),
    ):
        rows = conn.execute(
            f"""
            SELECT id, review_status
            FROM {table}
            WHERE document_id = ?
            """,
            (doc_id,),
        ).fetchall()

        for row in rows:
            records.append((kind, row["id"], row["review_status"]))

    # Other references to this source file must be protected.
    other_links = conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM rfqs WHERE document_id = ?) +
            (SELECT COUNT(*) FROM rfq_messages WHERE document_id = ?)
            AS total
        """,
        (doc_id, doc_id),
    ).fetchone()

    if other_links["total"]:
        return None

    if not records:
        if ext is None:
            return "The document has no previous extraction."

        if ext["status"] == "invalid":
            return "The previous extraction was invalid."

        if ext["action_status"] == "dismissed":
            return "The previous extraction was dismissed."

        return None

    # Only one generated business record, and it must be rejected.
    if len(records) != 1:
        return None

    kind, record_id, review_status = records[0]

    if review_status != "rejected" or ext is None:
        return None

    if kind == "quote":
        dependencies = conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM quotes
                 WHERE supersedes_quote_id = ?) +
                (SELECT COUNT(*) FROM purchase_orders
                 WHERE quote_id = ?)
                AS total
            """,
            (record_id, record_id),
        ).fetchone()

        if dependencies["total"]:
            return None

    elif kind == "legal document":
        # Prevent deletion of an agreement used as another
        # legal document's parent agreement.
        dependencies = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM legal_documents
            WHERE parent_id = ?
            """,
            (record_id,),
        ).fetchone()

        if dependencies["total"]:
            return None

    return f"The previous {kind} was rejected."


def _remove_replaceable_extraction(conn, doc_id: int) -> None:
    """
    Remove the previous extraction and its rejected business record.
    Must be called inside an existing database transaction.
    """
    reason = replacement_reason(conn, doc_id)

    if reason is None:
        raise ValueError(
            "Cannot replace this document: it is not rejected, "
            "or it has dependent records."
        )

    quote = conn.execute(
        "SELECT id FROM quotes WHERE document_id = ?",
        (doc_id,),
    ).fetchone()

    legal = conn.execute(
        "SELECT id FROM legal_documents WHERE document_id = ?",
        (doc_id,),
    ).fetchone()

    invoice = conn.execute(
        "SELECT id FROM invoices WHERE document_id = ?",
        (doc_id,),
    ).fetchone()

    if quote:
        quote_id = quote["id"]

        # Delete dependent rows explicitly.
        conn.execute(
            "DELETE FROM quote_line_items WHERE quote_id = ?",
            (quote_id,),
        )
        conn.execute(
            "DELETE FROM quote_price_tiers WHERE quote_id = ?",
            (quote_id,),
        )
        conn.execute(
            "DELETE FROM quotes WHERE id = ?",
            (quote_id,),
        )

    if legal:
        conn.execute(
            "DELETE FROM legal_documents WHERE id = ?",
            (legal["id"],),
        )

    if invoice:
        conn.execute(
            "DELETE FROM invoices WHERE id = ?",
            (invoice["id"],),
        )

    conn.execute(
        "DELETE FROM extractions WHERE document_id = ?",
        (doc_id,),
    )


# ---------- Phase 1: register ----------

def _register(path: Path, sha: str, category: str, source: str, ext_row: dict | None,
              replace: bool = False) -> tuple[int, int | None]:
    """Insert the document (once) and, if given, its extraction. Returns (document_id, extraction_id).

    replace=True first removes an earlier extraction that never produced a record (status invalid).
    """
    with db.transaction() as conn:
        row = conn.execute("SELECT id FROM documents WHERE sha256 = ?", (sha,)).fetchone()
        doc_id = row["id"] if row else conn.execute(
            """INSERT INTO documents (file_name, file_path, sha256, mime_type, category, source)
               VALUES (?,?,?,?,?,?)""",
            (path.name, _stored_path(path, sha), sha, mimetypes.guess_type(path.name)[0], category, source)).lastrowid
        ext_id = None
        if ext_row:
            if replace:
                _remove_replaceable_extraction(conn, doc_id)
            ext_id = conn.execute(
                """INSERT INTO extractions (document_id, doc_type, model, schema_version, validity, data_json,
                       errors_json, status, status_detail) VALUES (?,?,?,?,?,?,?,?,?)""",
                (doc_id, ext_row["doc_type"], extract.MODEL, extract.SCHEMA_VERSION, ext_row["validity"],
                 json.dumps(ext_row["data"], ensure_ascii=False, default=str),
                 json.dumps(ext_row["errors"], ensure_ascii=False) if ext_row["errors"] else None,
                 ext_row["status"], ext_row.get("detail"))).lastrowid
        return doc_id, ext_id


def ingest(path: Path, ctx: extract.RunContext) -> Outcome:
    """Read, extract (cached when possible), store, and apply one file."""
    path = Path(path)
    if not path.is_file():
        return Outcome(path, "error", "file not found")
    sha = extract.file_sha256(path)

    existing = db.query_one(
        """
        SELECT d.id AS document_id,
            e.id AS extraction_id,
            e.status
        FROM documents d
        LEFT JOIN extractions e ON e.document_id = d.id
        WHERE d.sha256 = ?
        """,
        (sha,),
    )

    replace = False

    if existing and existing["extraction_id"]:
        doc_id = existing["document_id"]

        with db.transaction() as conn:
            reason = replacement_reason(conn, doc_id)

        if reason is not None and ctx.retry_invalid:
            replace = True
        else:
            return Outcome(
                path,
                "duplicate",
                f"already ingested (status: {existing['status']})",
                existing["document_id"],
                existing["extraction_id"],
            )

    res = extract.process_file(path, ctx)      # cache first; calls the API only when allowed

    if res.status == "dry-run":
        return Outcome(path, "dry-run", res.message, notes=res.notes)
    if res.status == "skipped":
        if res.doc_type == "rfq":               # our own RFQs: parsed by rule, no AI
            import extraction.rfq_import as rfq_import
            return rfq_import.import_rfq(path)
        return Outcome(path, "skipped", res.message)
    if res.status == "error":
        if res.message.startswith("unreadable"):  # permanent: keep the file visible for manual handling
            doc_id, ext_id = _register(path, sha, "other", "upload", {
                "doc_type": res.doc_type if res.doc_type in ("quote", "legal", "email") else "other",
                "validity": "invalid", "data": {}, "errors": [res.message], "status": "invalid",
                "detail": res.message}, replace)
            return Outcome(path, "invalid", res.message, doc_id, ext_id)
        return Outcome(path, "error", res.message)  # transient (API trouble): nothing stored, retry later

    category = res.doc_type
    source = "email" if path.suffix.lower() == ".eml" else "upload"
    if res.status == "invalid":
        doc_id, ext_id = _register(path, sha, category, source, {
            "doc_type": res.doc_type, "validity": "invalid", "data": res.data or {}, "errors": res.errors,
            "status": "invalid", "detail": "; ".join(res.errors)[:300]}, replace)
        return Outcome(path, "invalid", "failed validation; needs manual entry", doc_id, ext_id, notes=res.errors)

    doc_id, ext_id = _register(path, sha, category, source, {
        "doc_type": res.doc_type, "validity": "ok", "data": res.data, "errors": [], "status": "new"}, replace)
    out = apply_extraction(ext_id)
    out.path, out.document_id, out.extraction_id = path, doc_id, ext_id
    return out


def relink_quotes() -> int:
    """Link quotes stored before their RFQ existed, and work out their comparable price now.

    Returns how many were linked. Safe to repeat. Uses the same matching rules as new quotes.
    """
    linked = 0
    with db.transaction() as conn:
        rows = conn.execute(
            """SELECT q.id, q.vendor_id, q.quote_date, e.data_json, e.doc_type FROM quotes q
               JOIN extractions e ON e.document_id = q.document_id
               WHERE q.rfq_id IS NULL AND e.validity = 'ok'""").fetchall()
        for r in rows:
            data = json.loads(r["data_json"])
            q = data.get("quote") if r["doc_type"] == "email" else data
            if not q:
                continue
            rfq, _ = resolve_rfq(conn, q.get("rfq_reference") or data.get("rfq_reference"), r["vendor_id"])
            if rfq is None:
                continue
            cmp_ = normalize.compute_comparable(q, rfq)
            conn.execute("UPDATE quotes SET rfq_id = ?, comparable_amount = ?, comparable_basis = ?, "
                         "comparable_is_estimate = ? WHERE id = ?",
                         (rfq["id"], cmp_.amount, cmp_.basis, int(cmp_.is_estimate), r["id"]))
            _mark_replied(conn, rfq["id"], r["vendor_id"], r["quote_date"], [])
            linked += 1
    return linked


# ---------- Review: see what needs a human ----------

REVIEW_STATUSES = ("invalid", "needs_vendor", "failed")


def review_queue() -> list[dict]:
    """Extractions that need a person: failed validation, no vendor, crashed, or an open action."""
    marks = ",".join("?" * len(REVIEW_STATUSES))
    rows = db.query(
        f"""SELECT e.id, e.status, e.status_detail, e.summary, e.action_needed, e.action_status, e.doc_type,
                   e.errors_json, d.file_name
            FROM extractions e JOIN documents d ON d.id = e.document_id
            WHERE (e.status IN ({marks}) AND COALESCE(e.action_status, '') != 'dismissed')
               OR (e.action_needed IS NOT NULL AND e.action_status = 'open')
            ORDER BY e.id""", REVIEW_STATUSES)
    return [dict(r) for r in rows]


def format_review(rows: list[dict]) -> str:
    if not rows:
        return "Nothing needs review."
    lines = []
    for r in rows:
        lines.append(f"#{r['id']:<3} [{r['status']}] {r['file_name']}  ({r['doc_type']})")
        if r["errors_json"]:
            for err in json.loads(r["errors_json"]):
                lines.append(f"       - {err}")
        elif r["status_detail"]:
            lines.append(f"       - {r['status_detail']}")
        if r["action_needed"] and r["action_status"] == "open":
            lines.append(f"       action: {r['action_needed']}")
    lines.append("\nSee exactly what the AI returned with: python loader.py --raw <id>")
    return "\n".join(lines)


def raw_extraction(extraction_id: int) -> str:
    """The stored AI output for one extraction, as readable JSON, plus its errors."""
    r = db.query_one("""SELECT e.*, d.file_name FROM extractions e JOIN documents d ON d.id = e.document_id
                        WHERE e.id = ?""", (extraction_id,))
    if r is None:
        return f"No extraction #{extraction_id}."
    out = [f"#{r['id']} {r['file_name']}  type={r['doc_type']}  status={r['status']}  validity={r['validity']}"]
    if r["errors_json"]:
        out += ["errors:"] + [f"  - {e}" for e in json.loads(r["errors_json"])]
    out += ["data:", json.dumps(json.loads(r["data_json"]), indent=2, ensure_ascii=False)]
    return "\n".join(out)


# ---------- Command line ----------

def main(argv: list[str] | None = None, client=None) -> int:
    ap = argparse.ArgumentParser(description="Load documents into the database.")
    ap.add_argument("paths", nargs="*", type=Path, help=f"files or folders (default: {extract.SAMPLE_DIR})")
    ap.add_argument("--live", action="store_true", help="allow API calls for files without a cached result")
    ap.add_argument("--max-calls", type=int, default=5)
    ap.add_argument("--type", dest="force_type", choices=sorted(extract.DOC_TYPES))
    ap.add_argument("--retry-invalid", action="store_true", help="re-extract documents whose result failed validation")
    ap.add_argument("--review", action="store_true", help="list extractions that need a person, then exit")
    ap.add_argument("--raw", type=int, metavar="ID", help="show the stored AI output for one extraction, then exit")
    args = ap.parse_args(argv)

    db.ensure_db()
    if args.raw is not None:
        print(raw_extraction(args.raw))
        return 0
    if args.review:
        print(format_review(review_queue()))
        return 0
    if db.query_one("SELECT 1 AS x FROM rfqs LIMIT 1") is None:
        print("Warning: the rfqs table is empty, so quotes cannot be linked to an RFQ and comparable "
              "prices cannot be checked against the requested quantity. Load RFQs first.\n", file=sys.stderr)
    ctx = extract.RunContext(live=args.live, max_calls=args.max_calls, force_type=args.force_type,
                             retry_invalid=args.retry_invalid, client=client)
    paths = args.paths or [extract.SAMPLE_DIR]
    root = paths[0] if paths[0].is_dir() else paths[0].parent
    try:
        results = [ingest(f, ctx) for f in extract.collect_files(paths)]
    except (extract.SetupError, extract.AbortRun) as e:
        print(f"\nStopped: {e}", file=sys.stderr)
        return 1
    if not results:
        print("No supported files found.")
        return 1

    for r in results:
        try:
            label = r.path.relative_to(root)
        except (ValueError, AttributeError):
            label = r.path.name if r.path else "?"
        print(f"[{r.status:<12}] {str(label):<58} {r.message}")
        for n in r.notes:
            print(f"                 - {n}")
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    print("\n" + " | ".join(f"{n} {s}" for s, n in sorted(counts.items())))
    if ctx.live:
        print(f"API calls: {ctx.calls}")
    return 0


if __name__ == "__main__":
    sys.exit(main())