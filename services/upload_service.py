"""What happens when someone uploads a file in the app.

    kind = "vendors" | "rfq" | "quote"* | "email"* | "legal"*      (chosen by the person, not assumed)
    saved = save_upload(name, data)             # safety checks, then a private temp copy
    pre   = preview(kind, saved)                # read-only: shows what WOULD happen
    out   = commit(kind, saved, allow_ai=True)  # does it

The chosen kind decides which code runs, so a quote is never mistaken for an RFQ.
*Only the three AI kinds can call an LLM, and only when there is no cached result for the file.
"""
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import db
import extract
import loader
import readers
import rfq_import
import seed

MAX_BYTES = 15 * 1024 * 1024
UPLOAD_KINDS = {
    "Vendor list (CSV)": ("vendors", {".csv"}),
    "RFQ we sent (PDF)": ("rfq", {".pdf"}),
    "Vendor quote": ("quote", readers.SUPPORTED_SUFFIXES - {".eml"}),
    "Vendor email (.eml)": ("email", {".eml"}),
    "Legal document (NDA / MSA / SOW)": ("legal", readers.SUPPORTED_SUFFIXES - {".eml"}),
}
AI_KINDS = {"quote", "email", "legal"}
_STAGING = Path(tempfile.gettempdir()) / "uploads"


class UploadError(ValueError):
    """A problem the person can fix; the message is safe to show."""


@dataclass
class Preview:
    ok: bool
    title: str = ""
    rows: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    needs_ai: bool = False
    already_loaded: bool = False


def save_upload(name: str, data: bytes, kind: str) -> Path:
    """Validate and stage an uploaded file. Raises UploadError."""
    allowed = next((s for k, s in UPLOAD_KINDS.values() if k == kind), None)
    if allowed is None:
        raise UploadError(f"unknown upload type {kind!r}")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).name).strip("._") or "upload"
    suffix = Path(safe).suffix.lower()
    if suffix not in allowed:
        raise UploadError(f"{suffix or 'this file'} is not accepted here; use one of: {', '.join(sorted(allowed))}")
    if not data:
        raise UploadError("the file is empty")
    if len(data) > MAX_BYTES:
        raise UploadError(f"the file is {len(data) / 1e6:.1f} MB; the limit is {MAX_BYTES // 1_000_000} MB")
    _STAGING.mkdir(parents=True, exist_ok=True)
    target = Path(tempfile.mkdtemp(dir=_STAGING)) / safe
    target.write_bytes(data)
    return target


class _Rollback(Exception):
    pass


def _vendor_preview(path: Path) -> Preview:
    """Run the real import inside a transaction that is then rolled back."""
    holder = {}
    try:
        with db.transaction() as conn:
            holder["r"] = seed.seed_vendors_from_csv(conn, path)
            raise _Rollback
    except _Rollback:
        pass
    except Exception as e:
        return Preview(False, "Could not read the vendor list", warnings=[f"{type(e).__name__}: {e}"])
    r = holder["r"]
    rows = [("Vendors to add", str(r.vendors_created)), ("Contacts to add", str(r.contacts_created)),
            ("Merged into existing", str(len(r.merged))), ("Rows rejected", str(len(r.rejected)))]
    return Preview(r.vendors_created + r.contacts_created > 0 or bool(r.merged), "Vendor list", rows,
                   r.warnings + r.rejected + [f"NDA claim to verify: {c}" for c in r.nda_claims])


def _rfq_preview(path: Path) -> Preview:
    try:
        d = rfq_import.parse_rfq_pdf(path)
    except rfq_import.RfqParseError as e:
        return Preview(False, "Not a readable RFQ", warnings=[str(e)])
    exists = db.query_one("SELECT 1 AS x FROM rfqs WHERE rfq_number = ?", (d["number"],)) is not None
    known, unknown = [], []
    for name in d["vendors"]:
        with db.transaction() as conn:
            vid, _ = loader.resolve_vendor(conn, name)
        (known if vid else unknown).append(name)
    rows = [("RFQ", d["number"]), ("Title", d["title"]), ("Issued / due", f"{d['issue_date']} / {d['due_date']}"),
            ("Items", "; ".join(f"{i['description']} ({i['quantity']:g}{' ' + i['unit'] if i['unit'] else ''})"
                                for i in d["items"])),
            ("Specifications", str(len(d["specifications"]))), ("Quote requirements", str(len(d["quote_requirements"]))),
            ("Vendors linked", f"{len(known)} of {len(d['vendors'])}")]
    warnings = [f"'{n}' is not in the vendor list and will not be linked" for n in unknown]
    if exists:
        warnings.insert(0, f"{d['number']} is already in the database; nothing will change")
    return Preview(not exists, d["number"], rows, warnings, already_loaded=exists)


def _ai_preview(kind: str, path: Path) -> Preview:
    sha = extract.file_sha256(path)
    existing = db.query_one(
        "SELECT e.status FROM documents d JOIN extractions e ON e.document_id = d.id WHERE d.sha256 = ?", (sha,))
    if existing:
        return Preview(False, path.name, [("Status", f"already loaded ({existing['status']})")],
                       ["This exact file was already uploaded."], already_loaded=True)
    ctx = extract.RunContext(live=False, max_calls=0, force_type=kind)
    res = extract.process_file(path, ctx)
    cached = res.status != "dry-run"
    if res.status == "error":
        return Preview(False, path.name, warnings=[res.message])
    rows = [("File", path.name), ("Type", kind), ("Extraction", "cached, free" if cached else "needs one AI call (about a tenth of a cent)")]
    return Preview(True, path.name, rows, list(res.notes or []), needs_ai=not cached)


def preview(kind: str, path: Path) -> Preview:
    """Read-only. Never writes to the database or calls the API."""
    if kind == "vendors":
        return _vendor_preview(path)
    if kind == "rfq":
        return _rfq_preview(path)
    if kind in AI_KINDS:
        return _ai_preview(kind, path)
    raise UploadError(f"unknown upload type {kind!r}")


def commit(kind: str, path: Path, allow_ai: bool = False, client=None) -> loader.Outcome | seed.ImportReport:
    """Do it. AI is used only when allow_ai is set (the person pressed the button)."""
    if kind == "vendors":
        with db.transaction() as conn:
            return seed.seed_vendors_from_csv(conn, path)
    if kind == "rfq":
        return rfq_import.import_rfq(path)
    if kind in AI_KINDS:
        ctx = extract.RunContext(live=allow_ai, max_calls=1, force_type=kind, client=client)
        return loader.ingest(path, ctx)
    raise UploadError(f"unknown upload type {kind!r}")