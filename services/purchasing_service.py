import json
import re
from collections import defaultdict
from datetime import date
import db
from services.review_service import usd_equivalent, quote_detail
import services.rfq_service

PO_NUMBER_RE = re.compile(r"PO-\d{4}-\d{3,4}")
AMOUNT_TOLERANCE = 0.01            # 1%: an invoice this close to the PO amount counts as matching


class PurchasingError(ValueError):
    """A problem the person can read: the message is safe to show."""


def _iso(value, label: str, required: bool = True) -> str | None:
    value = (value or "").strip() if isinstance(value, str) else value
    if not value:
        if required:
            raise PurchasingError(f"{label} is required")
        return None
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        raise PurchasingError(f"{label}: '{value}' is not a date in YYYY-MM-DD form") from None


def _amount(value, label: str, allow_zero: bool = False) -> float:
    try:
        n = float(str(value).replace(",", "").strip())
    except ValueError:
        raise PurchasingError(f"{label}: '{value}' is not a number (use plain digits, e.g. 19050.00)") from None
    if n < 0 or (n == 0 and not allow_zero):
        raise PurchasingError(f"{label} must be {'zero or more' if allow_zero else 'more than zero'}")
    return round(n, 2)


def _notes(raw: str | None) -> list[dict]:
    return json.loads(raw) if raw else []


def _add_note(conn, table: str, row_id: int, text: str) -> None:
    raw = conn.execute(f"SELECT {'notes' if table == 'purchase_orders' else 'review_notes'} AS n FROM {table} WHERE id = ?",
                       (row_id,)).fetchone()["n"]
    col = "notes" if table == "purchase_orders" else "review_notes"
    items = _notes(raw) + [{"by": "reviewer", "text": f"{db.TODAY}: {text}"}]
    conn.execute(f"UPDATE {table} SET {col} = ? WHERE id = ?", (json.dumps(items, ensure_ascii=False), row_id))


def to_usd(amount: float | None, currency: str | None) -> float | None:
    return usd_equivalent(amount, currency)



# Purchase orders

def next_po_number() -> str:
    year = db.TODAY[:4]
    prefix = f"PO-{year}-"
    row = db.query_one("SELECT COALESCE(MAX(CAST(SUBSTR(po_number, ?) AS INTEGER)), 0) + 1 AS n FROM purchase_orders "
                       "WHERE po_number LIKE ?", (len(prefix) + 1, f"{prefix}%"))
    return f"{prefix}{row['n']:03d}"


def quotes_ready_for_po() -> list[dict]:
    """Approved, current (not superseded) quotes that do not already have an active PO."""
    return db.query(
        """SELECT q.id, q.vendor_id, v.name AS vendor_name, q.reference_number, q.currency, q.total_amount,
                  q.comparable_amount, q.rfq_id, r.rfq_number
           FROM quotes q JOIN vendors v ON v.id = q.vendor_id LEFT JOIN rfqs r ON r.id = q.rfq_id
           WHERE q.review_status = 'approved' AND q.currency IS NOT NULL
             AND (q.comparable_amount IS NOT NULL OR q.total_amount IS NOT NULL)
             AND NOT EXISTS (SELECT 1 FROM quotes n WHERE n.supersedes_quote_id = q.id AND n.review_status != 'rejected')
             AND NOT EXISTS (SELECT 1 FROM purchase_orders p WHERE p.quote_id = q.id AND p.status != 'cancelled')
           ORDER BY v.name, q.id""")


def po_draft(quote_id: int) -> dict:
    """Suggested PO values for a quote, plus things the person must acknowledge."""
    q = quote_detail(quote_id)
    if q is None:
        raise PurchasingError("that quote does not exist")
    if q["review_status"] != "approved":
        raise PurchasingError("only an approved quote can be turned into a purchase order")
    existing = db.query_one("SELECT po_number FROM purchase_orders WHERE quote_id = ? AND status != 'cancelled'", (quote_id,))
    if existing:
        raise PurchasingError(f"this quote already has purchase order {existing['po_number']}")
    newer = db.query_one("SELECT reference_number FROM quotes WHERE supersedes_quote_id = ? AND review_status != 'rejected'", (quote_id,))
    if newer:
        raise PurchasingError(f"this quote was replaced by a newer revision ({newer['reference_number'] or 'no reference'}); use that one")
    amount = q["comparable_amount"] if q["comparable_amount"] is not None else q["total_amount"]
    if amount is None or not q["currency"]:
        raise PurchasingError("this quote has no price and currency to order against; fix it in the Review Queue first")
    return {"quote_id": quote_id, "vendor_id": q["vendor_id"], "vendor_name": q["vendor_name"], "rfq_id": q["rfq_id"],
            "rfq_number": q["rfq_number"], "po_number": next_po_number(), "issued_date": db.TODAY, "amount": amount,
            "currency": q["currency"], "basis": q["comparable_basis"], "warnings": q["warnings"]}


def create_po(quote_id: int, *, po_number: str | None = None, issued_date: str | None = None,
              amount: float | str | None = None, close_rfq: bool = False, confirmed_warnings: bool = False) -> dict:
    """Create a purchase order from an approved quote. Returns {'po_id', 'po_number', 'notes'}."""
    draft = po_draft(quote_id)
    if draft["warnings"] and not confirmed_warnings:
        raise PurchasingError("please confirm: " + " ".join(draft["warnings"]))
    number = (po_number or draft["po_number"]).strip().upper()
    if not PO_NUMBER_RE.fullmatch(number):
        raise PurchasingError("PO number must look like PO-2026-001")
    issued = _iso(issued_date or draft["issued_date"], "Issue date")
    value = _amount(amount if amount not in (None, "") else draft["amount"], "Amount")
    try:
        with db.transaction() as conn:
            po_id = conn.execute(
                "INSERT INTO purchase_orders (vendor_id, quote_id, po_number, issued_date, amount, currency, status) "
                "VALUES (?,?,?,?,?,?, 'open')",
                (draft["vendor_id"], quote_id, number, issued, value, draft["currency"])).lastrowid
            what = f"created from quote {quote_id}"
            if abs(value - draft["amount"]) > 0.005:
                what += f"; amount changed from {draft['amount']:,.2f} to {value:,.2f}"
            _add_note(conn, "purchase_orders", po_id, what)
    except Exception as e:  # noqa: BLE001
        if "UNIQUE" in str(e) and "po_number" in str(e):
            raise PurchasingError(f"PO number {number} is already used") from None
        raise PurchasingError(f"could not create the purchase order: {e}") from None
    notes = []
    if close_rfq and draft["rfq_id"]:
        try:
            services.rfq_service.close_rfq(draft["rfq_id"], f"{number} issued to {draft['vendor_name']}")
            notes.append(f"{draft['rfq_number']} closed")
        except services.rfq_service.RfqError as e:
            notes.append(f"the RFQ was not closed: {e}")
    return {"po_id": po_id, "po_number": number, "notes": notes}


_PO_NEXT = {"open": ("received", "closed", "cancelled"), "received": ("closed",), "closed": (), "cancelled": ()}


def set_po_status(po_id: int, status: str, note: str | None = None) -> None:
    with db.transaction() as conn:
        po = conn.execute("SELECT * FROM purchase_orders WHERE id = ?", (po_id,)).fetchone()
        if po is None:
            raise PurchasingError("that purchase order does not exist")
        if status not in _PO_NEXT[po["status"]]:
            raise PurchasingError(f"a {po['status']} purchase order cannot become {status}")
        invoices = conn.execute("SELECT * FROM invoices WHERE po_id = ? AND review_status = 'approved'", (po_id,)).fetchall()
        if status == "cancelled":
            if invoices:
                raise PurchasingError(f"{len(invoices)} approved invoice(s) are linked to this PO, so it cannot be cancelled")
            if not (note or "").strip():
                raise PurchasingError("give a reason for cancelling; it is kept on the record")
        if status == "closed":
            unpaid = [i for i in invoices if i["paid_date"] is None]
            if unpaid:
                raise PurchasingError(f"{len(unpaid)} invoice(s) on this PO are still unpaid")
        conn.execute("UPDATE purchase_orders SET status = ? WHERE id = ?", (status, po_id))
        _add_note(conn, "purchase_orders", po_id, f"marked {status}" + (f": {note.strip()}" if note and note.strip() else ""))


def list_pos(status: str | None = None) -> list[dict]:
    sql = """SELECT p.*, v.name AS vendor_name,
                    COALESCE((SELECT SUM(i.amount) FROM invoices i WHERE i.po_id = p.id AND i.review_status = 'approved'), 0) AS invoiced,
                    COALESCE((SELECT SUM(i.amount) FROM invoices i WHERE i.po_id = p.id AND i.review_status = 'approved'
                                                                    AND i.paid_date IS NOT NULL), 0) AS paid,
                    (SELECT COUNT(*) FROM invoices i WHERE i.po_id = p.id AND i.review_status = 'pending') AS pending_invoices
             FROM purchase_orders p JOIN vendors v ON v.id = p.vendor_id"""
    params: tuple = ()
    if status:
        sql += " WHERE p.status = ?"
        params = (status,)
    sql += " ORDER BY p.issued_date DESC, p.id DESC"
    rows = db.query(sql, params)
    for r in rows:
        r["remaining"] = round(r["amount"] - r["invoiced"], 2)
        r["amount_usd"] = to_usd(r["amount"], r["currency"])
    return rows


def po_detail(po_id: int) -> dict | None:
    po = db.query_one("SELECT p.*, v.name AS vendor_name FROM purchase_orders p JOIN vendors v ON v.id = p.vendor_id WHERE p.id = ?", (po_id,))
    if po is None:
        return None
    po["notes"] = _notes(po["notes"])
    po["invoices"] = db.query("SELECT * FROM invoices WHERE po_id = ? ORDER BY invoice_date, id", (po_id,))
    approved = [i for i in po["invoices"] if i["review_status"] == "approved"]
    po["invoiced"] = round(sum(i["amount"] for i in approved), 2)
    po["paid"] = round(sum(i["amount"] for i in approved if i["paid_date"]), 2)
    po["remaining"] = round(po["amount"] - po["invoiced"], 2)
    q = db.query_one("SELECT q.reference_number, q.rfq_id, r.rfq_number FROM quotes q LEFT JOIN rfqs r ON r.id = q.rfq_id WHERE q.id = ?",
                     (po["quote_id"],)) if po["quote_id"] else None
    po["quote"] = q
    return po


# Invoices

def invoice_status(inv: dict) -> str:
    """pending | rejected | paid | overdue | unpaid  (for display and filtering)."""
    if inv["review_status"] != "approved":
        return inv["review_status"]
    if inv["paid_date"]:
        return "paid"
    return "overdue" if inv["due_date"] and inv["due_date"] < db.TODAY else "unpaid"


def invoice_warnings(inv: dict) -> list[str]:
    """What a person should check about an invoice. Computed fresh from the data each time."""
    out = []
    po = db.query_one("SELECT * FROM purchase_orders WHERE id = ?", (inv["po_id"],)) if inv.get("po_id") else None
    if inv.get("po_id") is None:
        out.append("Not linked to a purchase order" + (f" (the invoice cites {inv['po_reference']})." if inv.get("po_reference") else "."))
    if po:
        if po["status"] == "cancelled":
            out.append(f"{po['po_number']} was cancelled.")
        if po["currency"] != inv["currency"]:
            out.append(f"Currency {inv['currency']} differs from the PO ({po['currency']}).")
        else:
            others = db.query_one("SELECT COALESCE(SUM(amount), 0) AS s FROM invoices WHERE po_id = ? AND id != ? AND review_status = 'approved'",
                                  (po["id"], inv["id"]))["s"]
            room = po["amount"] - others
            if inv["amount"] > room * (1 + AMOUNT_TOLERANCE) + 0.005:
                out.append(f"Amount {inv['amount']:,.2f} exceeds what is left on {po['po_number']} ({room:,.2f} of {po['amount']:,.2f}).")
        if inv["invoice_date"] < po["issued_date"]:
            out.append(f"Invoice date {inv['invoice_date']} is before the PO was issued ({po['issued_date']}).")
    if inv.get("due_date") is None:
        out.append("No due date, so payment reminders will not work.")
    return out


def list_invoices(status: str | None = None) -> list[dict]:
    """status: pending | unpaid | overdue | paid | None (everything except rejected)."""
    
    rows = db.query("""SELECT i.*, v.name AS vendor_name, p.po_number FROM invoices i JOIN vendors v ON v.id = i.vendor_id
                       LEFT JOIN purchase_orders p ON p.id = i.po_id ORDER BY i.invoice_date DESC, i.id DESC""")
    for r in rows:
        r["status"] = invoice_status(r)
        r["amount_usd"] = to_usd(r["amount"], r["currency"])
        r["days_overdue"] = ((date.fromisoformat(db.TODAY) - date.fromisoformat(r["due_date"])).days
                             if r["status"] == "overdue" else 0)
    return [r for r in rows if (r["status"] == status if status else r["status"] != "rejected")]


def mark_paid(invoice_id: int, paid_date: str | None = None, note: str | None = None) -> None:
    """Record that an approved invoice was paid. This records a payment made elsewhere; it moves no money."""
    paid = _iso(paid_date or db.TODAY, "Payment date")
    with db.transaction() as conn:
        inv = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        if inv is None:
            raise PurchasingError("that invoice does not exist")
        if inv["review_status"] != "approved":
            raise PurchasingError("only an approved invoice can be marked as paid")
        if inv["paid_date"]:
            raise PurchasingError(f"this invoice was already marked paid on {inv['paid_date']}")
        if paid < inv["invoice_date"]:
            raise PurchasingError(f"the payment date cannot be before the invoice date ({inv['invoice_date']})")
        if paid > db.TODAY:
            raise PurchasingError("the payment date cannot be in the future")
        conn.execute("UPDATE invoices SET paid_date = ? WHERE id = ?", (paid, invoice_id))
        _add_note(conn, "invoices", invoice_id, f"marked paid on {paid}" + (f": {note.strip()}" if note and note.strip() else ""))


def undo_paid(invoice_id: int, reason: str) -> None:
    """Correct a payment recorded by mistake. A reason is required and kept."""
    if not (reason or "").strip():
        raise PurchasingError("give a reason; it is kept on the record")
    with db.transaction() as conn:
        inv = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        if inv is None or not inv["paid_date"]:
            raise PurchasingError("this invoice is not marked as paid")
        conn.execute("UPDATE invoices SET paid_date = NULL WHERE id = ?", (invoice_id,))
        _add_note(conn, "invoices", invoice_id, f"payment record ({inv['paid_date']}) removed: {reason.strip()}")


# Amount Spent

def _usd_or_none(amount, currency, unconverted: list) -> float:
    v = to_usd(amount, currency)
    if v is None:
        unconverted.append((amount, currency))
        return 0.0
    return v


def spent_summary(vendor_id: int | None = None) -> dict:
    """Totals in USD from approved invoices, plus open commitments on POs. One vendor, or all."""
    where, params = ("WHERE i.vendor_id = ?", (vendor_id,)) if vendor_id else ("", ())
    invoices = db.query(f"SELECT i.* FROM invoices i {where} {'AND' if where else 'WHERE'} i.review_status = 'approved'", params)
    unconverted: list = []
    by_month: dict[str, dict] = defaultdict(lambda: {"invoiced": 0.0, "paid": 0.0})
    total = {"invoiced": 0.0, "paid": 0.0, "outstanding": 0.0, "overdue": 0.0}
    for i in invoices:
        usd = _usd_or_none(i["amount"], i["currency"], unconverted)
        total["invoiced"] += usd
        by_month[i["invoice_date"][:7]]["invoiced"] += usd
        if i["paid_date"]:
            total["paid"] += usd
            by_month[i["paid_date"][:7]]["paid"] += usd
        else:
            total["outstanding"] += usd
            if i["due_date"] and i["due_date"] < db.TODAY:
                total["overdue"] += usd
    pwhere, pparams = ("AND p.vendor_id = ?", (vendor_id,)) if vendor_id else ("", ())
    committed = 0.0
    for p in db.query(f"SELECT p.*, COALESCE((SELECT SUM(i.amount) FROM invoices i WHERE i.po_id = p.id AND i.review_status = 'approved'), 0) AS invoiced "
                      f"FROM purchase_orders p WHERE p.status IN ('open','received') {pwhere}", pparams):
        committed += _usd_or_none(max(p["amount"] - p["invoiced"], 0), p["currency"], unconverted)
    months = [{"month": m, "invoiced": round(v["invoiced"], 2), "paid": round(v["paid"], 2)} for m, v in sorted(by_month.items())]
    return {**{k: round(v, 2) for k, v in total.items()}, "committed": round(committed, 2), "by_month": months,
            "unconverted": len(unconverted)}


def spent_by_vendor() -> list[dict]:
    """One row per vendor that has any approved invoice or open PO, largest amount first (USD)."""
    rows = []
    for v in db.query("SELECT id, name FROM vendors ORDER BY name"):
        s = spent_summary(v["id"])
        if s["invoiced"] or s["committed"] or s["unconverted"]:
            rows.append({"vendor_id": v["id"], "vendor": v["name"], **{k: s[k] for k in ("invoiced", "paid", "outstanding", "overdue", "committed", "unconverted")}})
    return sorted(rows, key=lambda r: -r["invoiced"])


def vendor_summary(vendor_id: int) -> dict:
    """Everything the vendor profile needs about money: totals, monthly history, POs and invoices."""
    s = spent_summary(vendor_id)
    s["pos"] = [p for p in list_pos() if p["vendor_id"] == vendor_id]
    s["invoices"] = [i for i in list_invoices() if i["vendor_id"] == vendor_id]
    return s