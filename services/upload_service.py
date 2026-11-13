"""What happens when someone uploads a file in the app. No Streamlit in here, so it is testable.

    kind = "vendors" | "rfq" | "quote" | "email" | "legal"      (chosen by the person, never guessed)
    saved = save_upload(name, data)             # safety checks, then a private temp copy
    pre   = preview(kind, saved)                # read-only: shows what WOULD happen
    out   = commit(kind, saved, allow_ai=True)  # does it

The chosen kind decides which code runs, so a quote is never mistaken for an RFQ.
Only the three AI kinds can cost money, and only when there is no cached result for the file.
"""
import re
import tempfile
from email.message import EmailMessage
from dataclasses import dataclass, field
from pathlib import Path
import extraction.doctype_check as doctype_check
import db
import extraction.extract as extract
import extraction.loader as loader
import extraction.readers as readers
import extraction.rfq_import as rfq_import
import extraction.seed as seed

MAX_BYTES = 15 * 1024 * 1024
UPLOAD_KINDS = {
    "Vendor list (CSV)": ("vendors", {".csv"}),
    "RFQ we sent (PDF)": ("rfq", {".pdf"}),
    "Vendor quote": ("quote", readers.SUPPORTED_SUFFIXES - {".eml"}),
    "Vendor email (.eml)": ("email", {".eml"}),
    "Legal document (NDA / MSA / SOW)": ("legal", readers.SUPPORTED_SUFFIXES - {".eml"}),
    "Vendor invoice": ("invoice", readers.SUPPORTED_SUFFIXES - {".eml"}),
}
AI_KINDS = {"quote", "email", "legal", "invoice"}
_STAGING = Path(tempfile.gettempdir()) / "uploads"


PASTE_KINDS = {"vendors", "email", "quote", "legal"}      # types that also accept pasted text
MAX_PASTE_CHARS = 100_000
_HEADER_RE = re.compile(r"^(From|To|Date|Subject|Cc):[ \t]*(.*)$", re.IGNORECASE)


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
    type_warning: str | None = None
    replacing: bool = False


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


def _email_from_text(text: str) -> tuple[str, str, bool]:
    """Pasted email -> (file name, content, had_sender). Leading 'From:/Date:/Subject:' lines become real
    headers, so the sender address can identify the vendor exactly as it does for an .eml file."""
    headers, body_lines, lines = {}, [], text.splitlines()
    i = 0
    while i < len(lines) and _HEADER_RE.match(lines[i].strip()):
        key, value = _HEADER_RE.match(lines[i].strip()).groups()
        headers[key.title()] = value.strip()
        i += 1
    if "From" in headers:
        try:
            msg = EmailMessage()
            for key, value in headers.items():
                msg[key] = value
            msg.set_content("\n".join(lines[i:]).strip() or "(empty)")
            return "pasted_email.eml", msg.as_string(), True
        except Exception:
            pass
    return "pasted_email.txt", text, False


def save_pasted(text: str, kind: str) -> tuple[Path, list[str]]:
    """Stage pasted text as a file so it goes through exactly the same pipeline as an upload.
 
    Returns (path, notes to show in the preview)."""
    if kind not in PASTE_KINDS:
        raise UploadError("pasting text is not available for this type")
    text = (text or "").strip()
    if not text:
        raise UploadError("nothing was pasted")
    if len(text) > MAX_PASTE_CHARS:
        raise UploadError(f"the pasted text is {len(text):,} characters; the limit is {MAX_PASTE_CHARS:,}")
    notes: list[str] = []
    if kind == "vendors":
        name, content = "pasted_vendors.csv", text
    elif kind == "email":
        name, content, had_sender = _email_from_text(text)
        if not had_sender:
            notes.append("No 'From:' line found, so the vendor is matched by name only. "
                         "Paste the From: / Date: / Subject: lines too for a more reliable match.")
    else:
        name, content = f"pasted_{kind}.txt", text
    _STAGING.mkdir(parents=True, exist_ok=True)
    target = Path(tempfile.mkdtemp(dir=_STAGING)) / name
    target.write_text(content, encoding="utf-8")
    return target, notes


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


def _replacement_info(sha: str) -> tuple[bool, str | None]:
    existing = db.query_one(
        """
        SELECT id
        FROM documents
        WHERE sha256 = ?
        """,
        (sha,),
    )

    if existing is None:
        return False, None

    with db.transaction() as conn:
        reason = loader.replacement_reason(
            conn,
            existing["id"],
        )

    return reason is not None, reason


def _ai_preview(kind: str, path: Path) -> Preview:
    sha = extract.file_sha256(path)

    existing = db.query_one(
        "SELECT id FROM documents WHERE sha256 = ?",
        (sha,),
    )

    replacing = False
    reason = None

    if existing:
        replacing, reason = _replacement_info(sha)

        if not replacing:
            return Preview(
                False,
                path.name,
                [("Status", "Already uploaded")],
                [
                    "This exact file has already been uploaded "
                    "and cannot be replaced automatically."
                ],
                already_loaded=True,
            )

    ctx = extract.RunContext(
        live=False,
        max_calls=0,
        force_type=kind,
    )
    res = extract.process_file(path, ctx)

    if res.status == "error":
        return Preview(False, path.name, warnings=[res.message])

    cached = res.from_cache

    rows = [
        ("File", path.name),
        ("Type", kind),
        (
            "Extraction",
            "fresh extraction (replacing previous result)"
            if replacing
            else "cached, does not require AI" if cached
            else "will be extracted by AI",
        ),
    ]

    warnings = list(res.notes or [])

    if replacing:
        warnings.insert(
            0,
            f"{reason} Re-uploading will replace the previous "
            "extraction and, if applicable, its rejected document."
        )

    check = doctype_check.check_type(kind, path)

    return Preview(
        True,
        path.name,
        rows,
        warnings,
        needs_ai=replacing or not cached,
        type_warning=check.message if check else None,
        replacing=replacing,
    )


def preview(kind: str, path: Path) -> Preview:
    """Read-only. Never writes to the database or calls the API."""
    if kind == "vendors":
        return _vendor_preview(path)
    if kind == "rfq":
        return _rfq_preview(path)
    if kind in AI_KINDS:
        return _ai_preview(kind, path)
    raise UploadError(f"unknown upload type {kind!r}")


def commit(
    kind: str,
    path: Path,
    allow_ai: bool = False,
    client=None,
    type_confirmed: bool = False,
    replace_existing: bool = False,
) -> loader.Outcome | seed.ImportReport:

    if kind in doctype_check.CHECKED_KINDS and not type_confirmed:
        check = doctype_check.check_type(kind, path)
        if check:
            raise UploadError(check.message)

    if kind == "vendors":
        with db.transaction() as conn:
            return seed.seed_vendors_from_csv(conn, path)

    if kind == "rfq":
        return rfq_import.import_rfq(path)

    if kind in AI_KINDS:
        ctx = extract.RunContext(
            live=allow_ai,
            max_calls=1,
            force_type=kind,
            client=client,
            retry_invalid=replace_existing,
            refresh_cache=replace_existing,
        )

        return loader.ingest(path, ctx)

    raise UploadError(f"unknown upload type {kind!r}")