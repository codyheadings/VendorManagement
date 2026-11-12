"""Load an RFQ from its PDF into rfqs / rfq_items / specifications / requirements / invitations.

Rules
  * An RFQ number that already exists is left alone.
  * Nothing is guessed or invented: a field that is missing makes the import fail with a clear
    message instead of storing a placeholder. A vendor named in the PDF that is not in the
    vendor list is reported and skipped; vendors are never created from a document.
  * Everything is written in ONE transaction, so a failure leaves no half-created RFQ.
  * This module never sends email. An imported RFQ records that it was sent (the PDF says so);
    it does not trigger the mail code.
"""
import argparse
import re
import sys
from datetime import datetime
from pathlib import Path
import pdfplumber
import db
import extraction.extract as extract
import extraction.loader as loader

_NUMBER_RE = re.compile(r"RFQ-\d{4}-\d{3,4}")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_END_MARKERS = ("fictional document",)


class RfqParseError(ValueError):
    pass


def _lines(page) -> list[str]:
    return [ln.strip() for ln in (page.extract_text() or "").splitlines() if ln.strip()]


def _date(text: str, label: str) -> str:
    for fmt in ("%d %b %Y", "%d %B %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    raise RfqParseError(f"{label}: cannot read the date {text!r}")


def _quantity(cell: str) -> tuple[float | None, str | None]:
    """'500 g' -> (500, 'g'); '1' -> (1, None); '' -> (None, None)."""
    m = re.fullmatch(r"\s*([\d][\d,]*(?:\.\d+)?)\s*(.*?)\s*", (cell or "").replace("\n", " "))
    if not m:
        raise RfqParseError(f"cannot read the quantity {cell!r}")
    return float(m.group(1).replace(",", "")), (m.group(2) or None)


def _bullets(lines: list[str]) -> list[str]:
    """Group lines into bullet entries. A line starting with a symbol begins a new entry;
    anything else continues the previous one (wrapped text)."""
    out: list[str] = []
    for ln in lines:
        if not (ln[0].isalnum() or ln[0] in "(\"'"):
            out.append(ln[1:].strip())
        elif out:
            out[-1] += " " + ln
        else:
            out.append(ln)
    return [b for b in out if b]


def looks_like_rfq(path: Path) -> bool:
    try:
        with pdfplumber.open(path) as pdf:
            text = pdf.pages[0].extract_text() or ""
    except Exception:
        return False
    return "Request for Quotation" in text and bool(_NUMBER_RE.search(text))


def parse_rfq_pdf(path: Path) -> dict:
    """Return {number, title, contact_email, deliver_to, issue_date, due_date, vendors, items,
    specifications, quote_requirements}. Raises RfqParseError if anything required is missing."""
    try:
        with pdfplumber.open(path) as pdf:
            lines = [ln for page in pdf.pages for ln in _lines(page)]
            tables = [t for page in pdf.pages for t in page.extract_tables()]
    except Exception as e:
        raise RfqParseError(f"cannot read the PDF: {e}") from e

    start = next((i for i, ln in enumerate(lines) if ln.startswith("Request for Quotation")), None)
    if start is None:
        raise RfqParseError("this is not an ODDITY RFQ (no 'Request for Quotation' heading)")

    fields = {}
    for table in tables:
        if table and table[0] and table[0][0] == "RFQ number":
            fields = {row[0]: (row[1] or "").replace("\n", " ").strip() for row in table if row and len(row) >= 2}
    items_table = next((t for t in tables if t and t[0][:2] == ["Line", "Description"]), None)
    for need in ("RFQ number", "Issued", "Quotes due", "Contact", "Deliver to"):
        if not fields.get(need):
            raise RfqParseError(f"missing '{need}'")
    if items_table is None or len(items_table) < 2:
        raise RfqParseError("no 'Requested items' table found")

    number = fields["RFQ number"]
    if not _NUMBER_RE.fullmatch(number):
        raise RfqParseError(f"unrecognised RFQ number {number!r}")
    email = _EMAIL_RE.search(fields["Contact"])
    if not email:
        raise RfqParseError(f"no email address in Contact {fields['Contact']!r}")

    title_lines = []
    for ln in lines[start + 1:]:
        if ln.startswith("RFQ number"):
            break
        title_lines.append(ln)
    title = " ".join(title_lines).strip()
    if not title:
        raise RfqParseError("missing the RFQ title")

    items = []
    for row in items_table[1:]:
        line_no, desc, qty_cell, required = (row + [None] * 4)[:4]
        quantity, unit = _quantity(qty_cell or "")
        items.append({"line": int(line_no), "description": (desc or "").replace("\n", " ").strip(),
                      "quantity": quantity, "unit": unit,
                      "requirement": (required or "").replace("\n", " ").strip() or None})
    if any(not i["description"] for i in items):
        raise RfqParseError("an item has no description")

    def section(heading_startswith: str, stop: tuple[str, ...]) -> list[str]:
        try:
            begin = next(i for i, ln in enumerate(lines) if ln.startswith(heading_startswith)) + 1
        except StopIteration:
            return []
        body = []
        for ln in lines[begin:]:
            if ln.startswith(stop) or ln.lower().startswith(_END_MARKERS):
                break
            body.append(ln)
        return _bullets(body)

    specs = section("Specifications", ("Please include",))
    reqs = section("Please include", ())

    vendors = [v.strip() for v in re.split(r",", fields.get("Vendors invited", "")) if v.strip()]
    return {"number": number, "title": title, "contact_email": email.group(0),
            "deliver_to": fields["Deliver to"], "issue_date": _date(fields["Issued"], "Issued"),
            "due_date": _date(fields["Quotes due"], "Quotes due"), "vendors": vendors, "items": items,
            "specifications": specs, "quote_requirements": reqs}


def import_rfq(path: Path) -> loader.Outcome:
    """Parse and store one RFQ PDF. Returns an Outcome (applied / duplicate / invalid)."""
    path = Path(path)
    try:
        data = parse_rfq_pdf(path)
    except RfqParseError as e:
        return loader.Outcome(path, "invalid", f"RFQ not imported: {e}")

    sha = extract.file_sha256(path)
    notes: list[str] = []
    try:
        with db.transaction() as conn:
            if conn.execute("SELECT 1 FROM rfqs WHERE rfq_number = ?", (data["number"],)).fetchone():
                return loader.Outcome(path, "duplicate", f"{data['number']} already exists")

            row = conn.execute("SELECT id FROM documents WHERE sha256 = ?", (sha,)).fetchone()
            if row:
                doc_id = row["id"]
                conn.execute("UPDATE documents SET category = 'rfq' WHERE id = ?", (doc_id,))
            else:
                doc_id = conn.execute(
                    "INSERT INTO documents (file_name, file_path, sha256, mime_type, category, source) "
                    "VALUES (?,?,?,?,'rfq','upload')",
                    (path.name, loader._stored_path(path, sha), sha, "application/pdf")).lastrowid

            rfq_id = conn.execute(
                """INSERT INTO rfqs (rfq_number, document_id, title, contact_email, deliver_to, issue_date, due_date, status)
                   VALUES (?,?,?,?,?,?,?, 'open')""",
                (data["number"], doc_id, data["title"], data["contact_email"], data["deliver_to"],
                 data["issue_date"], data["due_date"])).lastrowid
            for i in data["items"]:
                conn.execute("INSERT INTO rfq_items (rfq_id, line_number, description, quantity, unit, requirement) "
                             "VALUES (?,?,?,?,?,?)",
                             (rfq_id, i["line"], i["description"], i["quantity"], i["unit"], i["requirement"]))
            for n, text in enumerate(data["specifications"], 1):
                conn.execute("INSERT INTO rfq_specifications (rfq_id, line_number, description) VALUES (?,?,?)",
                             (rfq_id, n, text))
            for n, text in enumerate(data["quote_requirements"], 1):
                conn.execute("INSERT INTO rfq_quote_requirements (rfq_id, line_number, description) VALUES (?,?,?)",
                             (rfq_id, n, text))

            invited = 0
            for name in data["vendors"]:
                vendor_id, how = loader.resolve_vendor(conn, name)
                if vendor_id is None:
                    notes.append(f"invited vendor '{name}' is not in the vendor list: add them, then link the invitation by hand")
                    continue
                if how.startswith("vendor name '"):
                    notes.append(f"'{name}' matched loosely ({how.split(';')[0]}); verify")
                # The PDF is the record that the RFQ was sent on its issue date.
                conn.execute("INSERT OR IGNORE INTO rfq_invitations (rfq_id, vendor_id, status, sent_at) VALUES (?,?, 'sent', ?)",
                             (rfq_id, vendor_id, data["issue_date"]))
                invited += 1
    except Exception as e:
        return loader.Outcome(path, "invalid", f"RFQ not imported: {type(e).__name__}: {e}")

    relinked = loader.relink_quotes()
    if relinked:
        notes.append(f"{relinked} existing quote(s) linked to {data['number']}")
    return loader.Outcome(path, "applied", f"{data['number']} created: {len(data['items'])} item(s), "
                          f"{invited} of {len(data['vendors'])} invited vendor(s) linked", document_id=doc_id, notes=notes)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Import RFQ PDFs.")
    ap.add_argument("paths", nargs="+", type=Path)
    args = ap.parse_args(argv)
    db.ensure_db()
    code = 0
    for p in args.paths:
        out = import_rfq(p)
        print(f"[{out.status:<9}] {p.name}: {out.message}")
        for n in out.notes:
            print(f"             - {n}")
        code |= out.status == "invalid"
    return int(code)


if __name__ == "__main__":
    sys.exit(main())