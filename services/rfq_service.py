import db
from datetime import date
import sqlite3
from pathlib import Path
import hashlib
from datetime import date

def get_next_rfq_number() -> str:
    year = date.today().year
    prefix = f"RFQ-{year}-"

    result = db.query_one(
        """
        SELECT COALESCE(
            MAX(CAST(SUBSTR(rfq_number, ?) AS INTEGER)),
            0
        ) + 1 AS next_number
        FROM rfqs
        WHERE rfq_number LIKE ?
        """,
        (len(prefix) + 1, f"{prefix}%")
    )

    return f"{prefix}{result['next_number']:03d}"


def add_rfq(rfq_data: dict, pdf_path: str) -> int:
    """Create an RFQ, its document record, and related records atomically."""

    pdf_file = Path(pdf_path)

    if not pdf_file.is_file():
        raise ValueError(f"RFQ PDF not found: {pdf_file}")

    pdf_bytes = pdf_file.read_bytes()
    sha256 = hashlib.sha256(pdf_bytes).hexdigest()

    rfq_number = get_next_rfq_number()

    try:
        with db.transaction() as conn:
            # Register the generated PDF in documents
            document_id = conn.execute(
                """
                INSERT INTO documents (
                    file_name,
                    file_path,
                    sha256,
                    mime_type,
                    category
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    f"{rfq_data['rfq_id']}.pdf",
                    pdf_path,
                    sha256,
                    "application/pdf",
                    "rfq"
                )
            ).lastrowid

            # Insert the main RFQ record
            rfq_id = conn.execute(
                """
                INSERT INTO rfqs (
                    rfq_number,
                    document_id,
                    title,
                    contact_email,
                    deliver_to,
                    issue_date,
                    due_date,
                    status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rfq_number,
                    document_id,
                    rfq_data["rfq_subtitle"].strip(),
                    rfq_data["rfq_contact"].strip(),
                    rfq_data["deliver_to"].strip(),
                    rfq_data["issued_date"].isoformat(),
                    rfq_data["rfq_deadline"].isoformat(),
                    "draft"
                )
            ).lastrowid

            # Insert requested items
            for item in rfq_data["items"]:
                quantity = item["quantity"].strip()

                conn.execute(
                    """
                    INSERT INTO rfq_items (
                        rfq_id,
                        line_number,
                        description,
                        quantity,
                        unit,
                        requirement
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        rfq_id,
                        item["line"],
                        item["description"],
                        float(quantity) if quantity else None,
                        item["unit"] or None,
                        item["required"] or None
                    )
                )

            # Insert specifications
            for index, specification in enumerate(
                rfq_data["specifications"], start=1
            ):
                conn.execute(
                    """
                    INSERT INTO rfq_specifications (
                        rfq_id,
                        line_number,
                        description
                    )
                    VALUES (?, ?, ?)
                    """,
                    (rfq_id, index, specification)
                )

            # Insert quote requirements
            for index, requirement in enumerate(
                rfq_data["quote_requirements"], start=1
            ):
                conn.execute(
                    """
                    INSERT INTO rfq_quote_requirements (
                        rfq_id,
                        line_number,
                        description
                    )
                    VALUES (?, ?, ?)
                    """,
                    (rfq_id, index, requirement)
                )

            # Create an invitation for each selected vendor
            for vendor_id in rfq_data["vendors"]:
                conn.execute(
                    """
                    INSERT INTO rfq_invitations (
                        rfq_id,
                        vendor_id,
                        status
                    )
                    VALUES (?, ?, ?)
                    """,
                    (rfq_id, vendor_id, "draft")
                )

            return rfq_id

    except sqlite3.IntegrityError as e:
        raise ValueError(f"Could not create RFQ: {e}") from e

    except (ValueError, TypeError) as e:
        raise ValueError(f"Invalid RFQ data: {e}") from e

class RfqError(ValueError):
    """A problem the person can read: the message is safe to show."""
 
 
def mark_rfq_sent(rfq_id: int | None, vendor_ids: list[int]) -> None:
    """Record that these vendors were emailed: their invitations become 'sent' and a draft RFQ becomes 'open'.
 
    One transaction, and it raises instead of silently doing nothing (an UPDATE that matches no row looks
    like success), so a missing RFQ id can never slip through unnoticed.
    """
    if not rfq_id:
        raise RfqError("no RFQ id: the RFQ must be created before it can be marked as sent")
    if not vendor_ids:
        return
    with db.transaction() as conn:
        rfq = conn.execute("SELECT id, status FROM rfqs WHERE id = ?", (rfq_id,)).fetchone()
        if rfq is None:
            raise RfqError(f"RFQ #{rfq_id} does not exist")
        if rfq["status"] in ("closed", "cancelled"):
            raise RfqError(f"RFQ #{rfq_id} is {rfq['status']}; it cannot be sent")
        for vendor_id in vendor_ids:
            conn.execute(
                """INSERT INTO rfq_invitations (rfq_id, vendor_id, status, sent_at) VALUES (?, ?, 'sent', ?)
                   ON CONFLICT(rfq_id, vendor_id) DO UPDATE SET
                       status = CASE WHEN status IN ('draft') THEN 'sent' ELSE status END,
                       sent_at = CASE WHEN status IN ('draft') THEN excluded.sent_at ELSE sent_at END""",
                (rfq_id, vendor_id, db.TODAY))
        conn.execute("UPDATE rfqs SET status = 'open' WHERE id = ? AND status = 'draft'", (rfq_id,))
 
 
def vendors_not_yet_sent(rfq_id: int | None, vendor_ids: list[int]) -> list[int]:
    """Of the selected vendors, those who have not already been sent this RFQ (stops double emailing)."""
    if not rfq_id or not vendor_ids:
        return list(vendor_ids)
    marks = ",".join("?" * len(vendor_ids))
    done = {r["vendor_id"] for r in db.query(
        f"SELECT vendor_id FROM rfq_invitations WHERE rfq_id = ? AND vendor_id IN ({marks}) "
        "AND status IN ('sent','replied','declined')", (rfq_id, *vendor_ids))}
    return [v for v in vendor_ids if v not in done]
 
  
FOLLOWUP_MIN_DAYS = 3             # a second msg sooner than this needs a confirmation
FOLLOWUP_SOFT_LIMIT = 3           # after this many, suggest calling or closing instead
 
MESSAGES_DDL = (
    """CREATE TABLE IF NOT EXISTS rfq_messages (
         id INTEGER PRIMARY KEY,
         rfq_id INTEGER NOT NULL REFERENCES rfqs(id) ON DELETE CASCADE,
         vendor_id INTEGER REFERENCES vendors(id),
         document_id INTEGER REFERENCES documents(id),
         direction TEXT NOT NULL CHECK (direction IN ('outbound','inbound','internal')),
         kind TEXT NOT NULL CHECK (kind IN ('followup','question','decline','reply','notice','note','status')),
         channel TEXT NOT NULL DEFAULT 'system' CHECK (channel IN ('email','manual','system')),
         subject TEXT,
         body TEXT NOT NULL,
         occurred_on TEXT NOT NULL CHECK (date(occurred_on) = occurred_on),
         created_at TEXT NOT NULL DEFAULT (datetime('now'))
       ) STRICT""",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_rfq_messages_doc ON rfq_messages(rfq_id, document_id) WHERE document_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_rfq_messages_rfq ON rfq_messages(rfq_id, occurred_on)",
)
 
 
def ensure_tables(conn=None) -> None:
    """Create the message log if the database predates it. Safe to call any number of times."""
    if conn is not None:
        for ddl in MESSAGES_DDL:
            conn.execute(ddl)
        return
    with db.transaction() as c:
        ensure_tables(c)
 
 
def log_message(conn, rfq_id: int, direction: str, kind: str, body: str, *, vendor_id: int | None = None,
                document_id: int | None = None, channel: str = "system", subject: str | None = None,
                occurred_on: str | None = None) -> None:
    """Add one line to an RFQ's history. Logging the same document twice updates the vendor only (no duplicate line)."""
    ensure_tables(conn)
    conn.execute(
        """INSERT INTO rfq_messages (rfq_id, vendor_id, document_id, direction, kind, channel, subject, body, occurred_on)
           VALUES (?,?,?,?,?,?,?,?,?)
           ON CONFLICT(rfq_id, document_id) WHERE document_id IS NOT NULL
           DO UPDATE SET vendor_id = COALESCE(excluded.vendor_id, rfq_messages.vendor_id)""",
        (rfq_id, vendor_id, document_id, direction, kind, channel, subject, body, occurred_on or db.TODAY))
 
 
def _days_between(later: str, earlier: str) -> int:
    return (date.fromisoformat(later) - date.fromisoformat(earlier)).days
 
 
def list_rfqs(status: str | None = None) -> list[dict]:
    """One row per RFQ with reply counts. `late` = invited vendors still silent after the deadline."""
    sql = """SELECT r.id, r.rfq_number, r.title, r.status, r.issue_date, r.due_date,
                    COUNT(i.id) AS invited,
                    COALESCE(SUM(i.status = 'sent'), 0)     AS awaiting,
                    COALESCE(SUM(i.status = 'replied'), 0)  AS replied,
                    COALESCE(SUM(i.status = 'declined'), 0) AS declined,
                    COALESCE(SUM(i.status = 'draft'), 0)    AS unsent
             FROM rfqs r LEFT JOIN rfq_invitations i ON i.rfq_id = r.id"""
    params: tuple = ()
    if status:
        sql += " WHERE r.status = ?"
        params = (status,)
    sql += " GROUP BY r.id ORDER BY r.issue_date DESC, r.id DESC"
    rows = []
    for r in db.query(sql, params):
        d = dict(r)
        overdue = d["status"] == "open" and d["due_date"] is not None and d["due_date"] < db.TODAY
        d["late"] = d["awaiting"] if overdue else 0
        rows.append(d)
    return rows
 
 
def summary() -> dict:
    rows = list_rfqs()
    open_ = [r for r in rows if r["status"] == "open"]
    return {"open": len(open_), "draft": sum(r["status"] == "draft" for r in rows),
            "awaiting": sum(r["awaiting"] for r in open_), "late": sum(r["late"] for r in open_)}
 
 
def rfq_detail(rfq_id: int) -> dict | None:
    row = db.query_one("SELECT * FROM rfqs WHERE id = ?", (rfq_id,))
    if row is None:
        return None
    d = dict(row)
    d["items"] = [dict(r) for r in db.query("SELECT * FROM rfq_items WHERE rfq_id = ? ORDER BY line_number", (rfq_id,))]
    d["specifications"] = [r["description"] for r in db.query(
        "SELECT description FROM rfq_specifications WHERE rfq_id = ? ORDER BY line_number", (rfq_id,))]
    d["quote_requirements"] = [r["description"] for r in db.query(
        "SELECT description FROM rfq_quote_requirements WHERE rfq_id = ? ORDER BY line_number", (rfq_id,))]
    d["overdue"] = d["status"] == "open" and d["due_date"] is not None and d["due_date"] < db.TODAY
    d["awarded_vendor_name"] = None
    if d.get("awarded_vendor_id") is not None:
        vendor = db.query_one(
            "SELECT name FROM vendors WHERE id = ?",
            (d["awarded_vendor_id"],),
        )
        if vendor:
            d["awarded_vendor_name"] = vendor["name"]
    d["invitations"] = invitation_rows(d)
    return d
 
 
def invitation_rows(rfq: dict) -> list[dict]:
    out = []
    for r in db.query(
            """SELECT i.*, v.name AS vendor_name,
                      (SELECT c.name  FROM vendor_contacts c WHERE c.vendor_id = v.id ORDER BY c.is_primary DESC, c.id LIMIT 1) AS contact_name,
                      (SELECT c.email FROM vendor_contacts c WHERE c.vendor_id = v.id AND c.email IS NOT NULL
                                                           ORDER BY c.is_primary DESC, c.id LIMIT 1) AS contact_email,
                      (SELECT COUNT(*) FROM quotes q WHERE q.rfq_id = i.rfq_id AND q.vendor_id = i.vendor_id
                                                      AND q.review_status != 'rejected') AS quotes
               FROM rfq_invitations i JOIN vendors v ON v.id = i.vendor_id WHERE i.rfq_id = ? ORDER BY v.name""", (rfq["id"],)):
        d = dict(r)
        d["days_late"] = (_days_between(db.TODAY, rfq["due_date"])
                          if d["status"] == "sent" and rfq["status"] == "open" and rfq["due_date"] and rfq["due_date"] < db.TODAY else 0)
        d["followup"] = followup_state(rfq, d)
        out.append(d)
    return out
 
 
def followup_state(rfq: dict, inv: dict) -> dict:
    """Can we nudge this vendor? {'allowed': bool, 'reason': why not, 'warnings': [things to confirm]}"""
    if rfq["status"] != "open":
        return {"allowed": False, "reason": f"the RFQ is {rfq['status']}", "warnings": []}
    if inv["status"] != "sent":
        why = {"draft": "the RFQ was never sent to this vendor", "replied": "the vendor already replied",
               "declined": "the vendor declined"}.get(inv["status"], inv["status"])
        return {"allowed": False, "reason": why, "warnings": []}
    if not inv.get("contact_email"):
        return {"allowed": False, "reason": "no contact email on file for this vendor", "warnings": []}
    warnings = []
    if rfq["due_date"] and rfq["due_date"] >= db.TODAY:
        warnings.append(f"the deadline ({rfq['due_date']}) has not passed yet")
    if inv.get("last_followup_at") and _days_between(db.TODAY, inv["last_followup_at"]) < FOLLOWUP_MIN_DAYS:
        warnings.append(f"you followed up on {inv['last_followup_at']}, less than {FOLLOWUP_MIN_DAYS} days ago")
    if inv.get("followup_count", 0) >= FOLLOWUP_SOFT_LIMIT:
        warnings.append(f"already followed up {inv['followup_count']} times; consider phoning, or closing the RFQ for this vendor")
    return {"allowed": True, "reason": "", "warnings": warnings}
 
 
def draft_followup(rfq_id: int, vendor_id: int) -> tuple[str, str]:
    """A plain, polite follow-up the person can edit."""
    rfq = rfq_detail(rfq_id)
    inv = next((i for i in (rfq or {}).get("invitations", []) if i["vendor_id"] == vendor_id), None)
    if inv is None:
        raise RfqError("that vendor was not invited to this RFQ")
    subject = f"Follow-up: {rfq['rfq_number']} {rfq['title']}"
    greeting = f"Hello {inv['contact_name']}," if inv.get("contact_name") else "Hello,"
    sent = f" on {inv['sent_at']}" if inv.get("sent_at") else ""
    due = f"The deadline was {rfq['due_date']}." if rfq.get("due_date") else ""
    body = (f"{greeting}\n\nWe sent you request for quotation {rfq['rfq_number']} ({rfq['title']}){sent} and have not yet received "
            f"your reply. {due}\n\nPlease send your quote, or let us know if you would rather pass at this time. "
            f"If you need more time or more information, tell us and we will help.\n\nThank you,\n{rfq['contact_email']}")
    return subject, body
 
 
def record_followup(rfq_id: int, vendor_id: int, subject: str, body: str, *, channel: str = "email",
                    confirmed_warnings: bool = False) -> None:
    """Record that a follow-up went out (or was sent by hand). Call this AFTER the email was actually sent."""
    if channel not in ("email", "manual"):
        raise RfqError(f"unknown channel {channel!r}")
    if not (subject or "").strip() or not (body or "").strip():
        raise RfqError("a follow-up needs a subject and a message")
    rfq = rfq_detail(rfq_id)
    if rfq is None:
        raise RfqError("that RFQ does not exist")
    inv = next((i for i in rfq["invitations"] if i["vendor_id"] == vendor_id), None)
    if inv is None:
        raise RfqError("that vendor was not invited to this RFQ")
    state = inv["followup"]
    if not state["allowed"]:
        raise RfqError(f"cannot follow up: {state['reason']}")
    if state["warnings"] and not confirmed_warnings:
        raise RfqError("please confirm: " + "; ".join(state["warnings"]))
    with db.transaction() as conn:
        conn.execute("UPDATE rfq_invitations SET last_followup_at = ?, followup_count = followup_count + 1 "
                     "WHERE rfq_id = ? AND vendor_id = ?", (db.TODAY, rfq_id, vendor_id))
        log_message(conn, rfq_id, "outbound", "followup", body.strip(), vendor_id=vendor_id, channel=channel,
                    subject=subject.strip())
 
 
def _status_note(conn, rfq_id: int, text: str, vendor_id: int | None = None) -> None:
    log_message(conn, rfq_id, "internal", "status", text, vendor_id=vendor_id, channel="manual")
 
 
def _get(conn, rfq_id: int):
    row = conn.execute("SELECT * FROM rfqs WHERE id = ?", (rfq_id,)).fetchone()
    if row is None:
        raise RfqError("that RFQ does not exist")
    return row
 
 
def close_rfq(rfq_id: int, note: str | None = None, awarded_vendor_id: int | None = None,) -> None:
    with db.transaction() as conn:
        rfq = _get(conn, rfq_id)

        if rfq["status"] != "open":
            raise RfqError(
                f"only an open RFQ can be closed (this one is {rfq['status']})"
            )

        # If a vendor is selected, verify they are eligible for this RFQ.
        if awarded_vendor_id is not None:
            candidate = conn.execute(
                """
                SELECT 1
                FROM rfq_invitations i
                JOIN quotes q
                    ON q.rfq_id = i.rfq_id
                   AND q.vendor_id = i.vendor_id
                WHERE i.rfq_id = ?
                  AND i.vendor_id = ?
                  AND q.review_status = 'approved'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM quotes newer
                      WHERE newer.supersedes_quote_id = q.id
                        AND newer.review_status != 'rejected'
                  )
                LIMIT 1
                """,
                (rfq_id, awarded_vendor_id),
            ).fetchone()

            if candidate is None:
                raise RfqError(
                    "That vendor does not have an eligible approved quote for this RFQ."
                )

        conn.execute(
            """
            UPDATE rfqs
            SET status = 'closed',
                awarded_vendor_id = ?
            WHERE id = ?
            """,
            (awarded_vendor_id, rfq_id),
        )

        if awarded_vendor_id is not None:
            vendor = conn.execute("SELECT name FROM vendors WHERE id = ?", (awarded_vendor_id,),).fetchone()
            status_message = f"RFQ closed and awarded to {vendor['name']}"
        else:
            status_message = "RFQ closed"

        _status_note(conn, rfq_id, status_message + (f": {note.strip()}" if note and note.strip() else ""),)
 
 
def cancel_rfq(rfq_id: int, reason: str) -> None:
    if not (reason or "").strip():
        raise RfqError("give a reason for cancelling; it is kept in the RFQ history")
    with db.transaction() as conn:
        rfq = _get(conn, rfq_id)
        if rfq["status"] not in ("draft", "open"):
            raise RfqError(f"a {rfq['status']} RFQ cannot be cancelled")
        conn.execute("UPDATE rfqs SET status = 'cancelled' WHERE id = ?", (rfq_id,))
        _status_note(conn, rfq_id, f"RFQ cancelled: {reason.strip()}")
 
 
def reopen_rfq(rfq_id: int) -> None:
    with db.transaction() as conn:
        rfq = _get(conn, rfq_id)
        if rfq["status"] != "closed":
            raise RfqError("only a closed RFQ can be reopened (a cancelled one cannot)")
        conn.execute("UPDATE rfqs SET status = 'open' WHERE id = ?", (rfq_id,))
        _status_note(conn, rfq_id, "RFQ reopened")


def open_rfq(rfq_id: int) -> None:
    with db.transaction() as conn:
        rfq = _get(conn, rfq_id)
        if rfq["status"] != "draft":
            raise RfqError("only an RFQ draft can be opened (a cancelled one cannot)")
        conn.execute("UPDATE rfqs SET status = 'open' WHERE id = ?", (rfq_id,))
        _status_note(conn, rfq_id, "RFQ draft opened")
 
 
def extend_deadline(rfq_id: int, new_due: str, reason: str | None = None) -> None:
    try:
        new_due = date.fromisoformat((new_due or "").strip()).isoformat()
    except ValueError:
        raise RfqError("the new deadline must be a date like 2026-11-30") from None
    with db.transaction() as conn:
        rfq = _get(conn, rfq_id)
        if rfq["status"] not in ("draft", "open"):
            raise RfqError(f"the deadline of a {rfq['status']} RFQ cannot be changed")
        if rfq["due_date"] and new_due <= rfq["due_date"]:
            raise RfqError(f"the new deadline must be later than the current one ({rfq['due_date']})")
        if new_due < rfq["issue_date"]:
            raise RfqError("the deadline cannot be before the issue date")
        conn.execute("UPDATE rfqs SET due_date = ? WHERE id = ?", (new_due, rfq_id))
        _status_note(conn, rfq_id, f"deadline moved from {rfq['due_date']} to {new_due}"
                     + (f": {reason.strip()}" if reason and reason.strip() else ""))
 
 
def set_invitation_status(rfq_id: int, vendor_id: int, status: str, note: str | None = None) -> None:
    """Record a reply that did not arrive through the app (a phone call, a hallway chat): 'replied' or 'declined'."""
    if status not in ("replied", "declined"):
        raise RfqError("status must be 'replied' or 'declined'")
    with db.transaction() as conn:
        _get(conn, rfq_id)
        inv = conn.execute("SELECT * FROM rfq_invitations WHERE rfq_id = ? AND vendor_id = ?", (rfq_id, vendor_id)).fetchone()
        if inv is None:
            raise RfqError("that vendor was not invited to this RFQ")
        if inv["status"] != "sent":
            raise RfqError(f"this invitation is already '{inv['status']}'")
        conn.execute("UPDATE rfq_invitations SET status = ?, replied_at = ? WHERE id = ?", (status, db.TODAY, inv["id"]))
        _status_note(conn, rfq_id, f"marked {status} by hand" + (f": {note.strip()}" if note and note.strip() else ""),
                     vendor_id=vendor_id)
 
 
def add_note(rfq_id: int, text: str, vendor_id: int | None = None) -> None:
    if not (text or "").strip():
        raise RfqError("the note is empty")
    with db.transaction() as conn:
        _get(conn, rfq_id)
        log_message(conn, rfq_id, "internal", "note", text.strip(), vendor_id=vendor_id, channel="manual")
 
 
def comparison(rfq_id: int) -> list[dict]:
    """Quotes received for this RFQ, cheapest first. Unpriced quotes come last."""
    rows = [dict(r) for r in db.query(
        "SELECT * FROM v_quote_comparison WHERE rfq_id = ? ORDER BY comparable_usd IS NULL, comparable_usd", (rfq_id,))]
    return rows
 
 
def activity(rfq_id: int) -> list[dict]:
    """Newest first: logged messages plus events worked out from the data (issued, sent, quote received)."""
    ensure_tables()
    rfq = db.query_one("SELECT * FROM rfqs WHERE id = ?", (rfq_id,))
    if rfq is None:
        return []
    events = [{"on": rfq["issue_date"], "icon": "created", "who": "", "title": "RFQ issued", "body": rfq["title"], "ord": 0}]
    for r in db.query("SELECT i.sent_at, v.name FROM rfq_invitations i JOIN vendors v ON v.id = i.vendor_id "
                      "WHERE i.rfq_id = ? AND i.sent_at IS NOT NULL", (rfq_id,)):
        events.append({"on": r["sent_at"], "icon": "sent", "who": r["name"], "title": "RFQ sent", "body": "", "ord": 1})
    logged_docs = {r["document_id"] for r in db.query(
        "SELECT document_id FROM rfq_messages WHERE rfq_id = ? AND document_id IS NOT NULL", (rfq_id,))}
    for r in db.query("SELECT q.quote_date, q.created_at, q.reference_number, q.review_status, q.document_id, v.name FROM quotes q "
                      "JOIN vendors v ON v.id = q.vendor_id WHERE q.rfq_id = ?", (rfq_id,)):
        if r["document_id"] in logged_docs:
            continue
        events.append({"on": r["quote_date"] or r["created_at"][:10], "icon": "quote", "who": r["name"],
                       "title": "Quote received", "body": f"{r['reference_number'] or 'no reference'} ({r['review_status']})", "ord": 2})
    for m in db.query("SELECT m.*, v.name AS vendor FROM rfq_messages m LEFT JOIN vendors v ON v.id = m.vendor_id WHERE m.rfq_id = ?", (rfq_id,)):
        title = {"followup": "Follow-up sent", "question": "Vendor question", "decline": "Vendor declined",
                 "reply": "Vendor reply", "notice": "Vendor message", "note": "Note", "status": "Status"}[m["kind"]]
        events.append({"on": m["occurred_on"], "icon": m["kind"], "who": m["vendor"] or "", "title": title,
                       "body": (f"**{m['subject']}**\n\n" if m["subject"] else "") + m["body"], "ord": 3 + m["id"] / 1e6,
                       "channel": m["channel"]})
    events.sort(key=lambda e: (e["on"], e["ord"]), reverse=True)
    return events

def award_candidates(rfq_id: int) -> list[dict]:
    """Return invited vendors with approved, current quotes for an RFQ."""
    return db.query(
        """
        SELECT
            v.id AS vendor_id,
            v.name AS vendor_name,
            COUNT(q.id) AS quote_count
        FROM rfq_invitations i
        JOIN vendors v
            ON v.id = i.vendor_id
        JOIN quotes q
            ON q.rfq_id = i.rfq_id
            AND q.vendor_id = i.vendor_id
        WHERE i.rfq_id = ?
          AND q.review_status = 'approved'
          AND NOT EXISTS (
              SELECT 1
              FROM quotes newer
              WHERE newer.supersedes_quote_id = q.id
                AND newer.review_status != 'rejected'
          )
        GROUP BY v.id, v.name
        ORDER BY v.name
        """,
        (rfq_id,),
    )