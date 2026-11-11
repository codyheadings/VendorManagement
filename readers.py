"""Turn files into text for the LLM, locally.

Prefers cheaper options. Only true scans (images, or PDFs with no text layer) fall back to vision input.
"""
import base64
import io
import re
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path

MAX_CHARS = 24_000            # ~6k tokens; longer text is truncated with a note
MAX_PDF_PAGES = 20
MAX_XLSX_ROWS = 200           # per sheet
MAX_VISION_PDF_PAGES = 8      # refuse to send huge scanned PDFs
MAX_VISION_FILE_MB = 10
MAX_IMAGE_SIDE = 1600         # px; images are downscaled before sending
SCAN_CHARS_PER_PAGE = 40      # fewer extracted characters than this per page => treat as scanned

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".gif"}


class ReaderError(Exception):
    """The file can't be read. The message is safe to show."""


@dataclass
class DocContent:
    kind: str                          # pdf | xlsx | docx | eml | text | image
    text: str = ""
    vision: str | None = None          # None | "pdf" | "image": needs vision input
    vision_tokens_est: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def est_tokens(self) -> int:
        return len(self.text) // 4 + self.vision_tokens_est


# Cut irrelevant interview lines
BOILERPLATE_LINES = [
    re.compile(r"^[^\n]*FICTIONAL DOCUMENT[^\n]*recruitment exercise[^\n]*$", re.IGNORECASE | re.MULTILINE),
]
_CID_RE = re.compile(r"\(cid:\d+\)")
CID_VISION_RATIO = 0.02       # more undecodable glyphs than this => the text layer is unreliable
UNREADABLE = "�"


def _cap(text: str, notes: list[str]) -> str:
    for pattern in BOILERPLATE_LINES:
        text = pattern.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > MAX_CHARS:
        notes.append(f"text truncated from {len(text):,} to {MAX_CHARS:,} characters")
        text = text[:MAX_CHARS] + "\n[... truncated ...]"
    return text


# ---------- PDF ----------

def _read_pdf(path: Path) -> DocContent:
    import pdfplumber

    try:
        with pdfplumber.open(path) as pdf:
            n_pages = len(pdf.pages)
            parts = [(page.extract_text() or "") for page in pdf.pages[:MAX_PDF_PAGES]]
    except Exception as e:
        raise ReaderError(f"could not open PDF ({type(e).__name__})") from None

    if n_pages == 0:
        raise ReaderError("PDF has no pages")

    notes = [f"{n_pages} page(s)"]
    if n_pages > MAX_PDF_PAGES:
        notes.append(f"only the first {MAX_PDF_PAGES} pages were read")
    text = "\n\n".join(p.strip() for p in parts).strip()

    # Some fonts have glyphs with no Unicode mapping, if a price digit were hit we must not guess it.
    n_cid = len(_CID_RE.findall(text))
    unreliable_text = bool(n_cid) and n_cid / max(len(text), 1) > CID_VISION_RATIO
    if n_cid:
        text = _CID_RE.sub(UNREADABLE, text)
        notes.append(f"{n_cid} glyph(s) could not be decoded and were replaced with {UNREADABLE}")

    if unreliable_text or len(text) < SCAN_CHARS_PER_PAGE * min(n_pages, MAX_PDF_PAGES):
        # No usable text layer: it's a scan. Send the PDF itself to a vision-capable model.
        if n_pages > MAX_VISION_PDF_PAGES:
            raise ReaderError(f"scanned PDF has {n_pages} pages (limit {MAX_VISION_PDF_PAGES})")
        if path.stat().st_size > MAX_VISION_FILE_MB * 1024 * 1024:
            raise ReaderError(f"scanned PDF is larger than {MAX_VISION_FILE_MB} MB")
        notes.append("text layer unreliable (undecodable glyphs): needs vision input" if unreliable_text
                     else "no text layer: needs vision input")
        return DocContent("pdf", text="" if unreliable_text else text, vision="pdf",
                          vision_tokens_est=n_pages * 1800, notes=notes)

    return DocContent("pdf", text=_cap(text, notes), notes=notes)


# ---------- Excel ----------

def _read_xlsx(path: Path) -> DocContent:
    import openpyxl

    try:
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    except Exception as e:
        raise ReaderError(f"could not open workbook ({type(e).__name__})") from None

    notes, parts = [], []
    for ws in wb.worksheets:
        rows = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i >= MAX_XLSX_ROWS:
                notes.append(f"sheet '{ws.title}' truncated at {MAX_XLSX_ROWS} rows")
                break
            cells = ["" if c is None else str(c).strip() for c in row]
            if any(cells):
                rows.append(" | ".join(cells).rstrip(" |"))
        if rows:
            parts.append(f"## Sheet: {ws.title}\n" + "\n".join(rows))
    wb.close()

    if not parts:
        raise ReaderError("workbook has no readable cells")
    notes.insert(0, f"{len(wb.worksheets)} sheet(s)")
    return DocContent("xlsx", text=_cap("\n\n".join(parts), notes), notes=notes)


# ---------- Word ----------

def _read_docx(path: Path) -> DocContent:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        document = docx.Document(path)
    except Exception as e:
        raise ReaderError(f"could not open Word file ({type(e).__name__})") from None

    parts = []
    for child in document.element.body.iterchildren():
        if child.tag.endswith("}p"):
            text = Paragraph(child, document).text.strip()
            if text:
                parts.append(text)
        elif child.tag.endswith("}tbl"):
            for row in Table(child, document).rows:
                cells, previous = [], None
                for cell in row.cells:
                    t = cell.text.strip()
                    if t != previous:
                        cells.append(t)
                    previous = t
                if any(cells):
                    parts.append(" | ".join(cells))

    if not parts:
        raise ReaderError("Word file has no text")
    notes: list[str] = []
    return DocContent("docx", text=_cap("\n".join(parts), notes), notes=notes)


# ---------- Email ----------

class _HtmlText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("br", "p", "div", "tr", "li"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def _html_to_text(html: str) -> str:
    parser = _HtmlText()
    parser.feed(html)
    return re.sub(r"\n\s*\n+", "\n\n", "".join(parser.parts))


def _read_eml(path: Path) -> DocContent:
    try:
        with open(path, "rb") as f:
            msg = BytesParser(policy=policy.default).parse(f)
        part = msg.get_body(preferencelist=("plain", "html"))
        body = ""
        if part is not None:
            body = part.get_content()
            if part.get_content_type() == "text/html":
                body = _html_to_text(body)
        attachments = [p.get_filename() for p in msg.iter_attachments() if p.get_filename()]
    except Exception as e:
        raise ReaderError(f"could not parse email ({type(e).__name__})") from None

    notes: list[str] = []
    # Quoted reply history (lines starting with ">") is mostly repeated text: drop it to save tokens.
    kept = [ln for ln in body.splitlines() if not ln.lstrip().startswith(">")]
    if len(kept) < len(body.splitlines()):
        notes.append("quoted reply lines removed")

    header = [f"{h}: {msg[h]}" for h in ("From", "To", "Cc", "Date", "Subject") if msg[h]]
    if attachments:
        header.append("Attachments: " + ", ".join(attachments))
    text = "\n".join(header) + "\n\n" + "\n".join(kept)
    return DocContent("eml", text=_cap(text, notes), notes=notes)


# ---------- Plain text and images ----------

def _read_text(path: Path) -> DocContent:
    notes: list[str] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise ReaderError(f"could not read file ({type(e).__name__})") from None
    if not text.strip():
        raise ReaderError("file is empty")
    return DocContent("text", text=_cap(text, notes), notes=notes)


def _read_image(path: Path) -> DocContent:
    from PIL import Image

    try:
        with Image.open(path) as im:
            w, h = im.size
    except Exception as e:
        raise ReaderError(f"could not open image ({type(e).__name__})") from None
    scale = min(1.0, MAX_IMAGE_SIDE / max(w, h))
    est = max(1, int(w * scale * h * scale / 750))  # rough image-token estimate
    return DocContent("image", vision="image", vision_tokens_est=est,
                      notes=[f"{w}x{h}px image: needs vision input"])


_READERS = {
    ".pdf": _read_pdf,
    ".xlsx": _read_xlsx,
    ".xlsm": _read_xlsx,
    ".docx": _read_docx,
    ".eml": _read_eml,
    ".txt": _read_text,
    ".md": _read_text,
    **{suffix: _read_image for suffix in IMAGE_SUFFIXES},
}
SUPPORTED_SUFFIXES = set(_READERS)


def read_document(path: Path) -> DocContent:
    reader = _READERS.get(path.suffix.lower())
    if reader is None:
        raise ReaderError(f"unsupported file type '{path.suffix}'")
    return reader(path)


def build_media_blocks(path: Path, content: DocContent) -> list[dict]:
    """Content blocks for scans. Built only at the moment of an API call."""
    if content.vision == "pdf":
        data = base64.standard_b64encode(path.read_bytes()).decode()
        return [{"type": "document",
                 "source": {"type": "base64", "media_type": "application/pdf", "data": data}}]
    if content.vision == "image":
        from PIL import Image

        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
            buf = io.BytesIO()
            im.save(buf, format="PNG")
        data = base64.standard_b64encode(buf.getvalue()).decode()
        return [{"type": "image",
                 "source": {"type": "base64", "media_type": "image/png", "data": data}}]
    return []