"""Cheap, local check that an upload is the kind of document the uploader said it is.

Runs BEFORE any AI call. It only ever warns; the person can still go ahead.

How it avoids false alarms
  * A quote that says "NDA required" or "confidentiality agreement" in its requirements must not be
    flagged. So wording in the BODY is weak evidence and can never be enough on its own: body hits are
    capped at 2 points and a warning needs 3.
  * Strong evidence is a document TITLE: the file name plus the first few SHORT lines (headings, not
    sentences). 'Non-Disclosure Agreement' up there is how a document names itself, whereas
    a sentence about an NDA further down is just content.
  * The suspected kind must also beat the kind the person chose by a clear margin (2 points).
"""
import re
from dataclasses import dataclass

import readers

TITLE_LINES = 5                    # how many of the first non-empty lines count as the title area
TITLE_LINE_MAX = 80                # ...and only lines this short (a heading, not a sentence)
MARGIN = 2
THRESHOLD = 3
TITLE_POINTS, BODY_POINTS, BODY_CAP = 3, 1, 2

# kind -> (title patterns, body patterns). Several languages for the ones that matter here.
SIGNALS: dict[str, tuple[list[str], list[str]]] = {
    "legal": (
        [r"non[- ]?disclosure", r"confidentiality agreement", r"master services? agreement", r"statement of work",
         r"\bNDA\b", r"\bMSA\b", r"\bSOW\b", r"certificate of insurance", r"geheimhaltung", r"vertraulichkeitsvereinbarung",
         r"rahmenvertrag", r"accord de confidentialit"],
        [r"hereby agree", r"governing law", r"in witness whereof", r"\bthe parties\b", r"indemnif", r"jurisdiction",
         r"signature"]),
    "quote": (
        [r"(?<!request for )quotation", r"\bquote\b", r"price (sheet|list|offer)", r"\bangebot\b", r"\boffre\b",
         r"cotizaci", r"报价", r"\bproforma\b"],
        [r"unit price", r"total (price|amount)", r"valid until", r"lead time", r"delivery time", r"lieferzeit",
         r"\bpreis", r"per (kg|g|unit|study)"]),
    "rfq": (
        [r"request for (quotation|quote|proposal)"],
        [r"quotes? due", r"vendors? invited", r"please include in your quote"]),
    "invoice": (
        [r"\binvoice\b", r"\brechnung\b", r"\bfacture\b"],
        [r"amount due", r"bill to", r"payment due", r"invoice (no|number)"]),
}
LABEL = {"legal": "a legal document (NDA / MSA / SOW)", "quote": "a vendor quote", "rfq": "one of our own RFQs",
         "invoice": "an invoice"}
CHOSEN_LABEL = {"quote": "Vendor quote", "legal": "Legal document"}
CHECKED_KINDS = {"quote", "legal"}                  # emails are identified by their headers, not by wording


@dataclass
class TypeCheck:
    suspected: str
    evidence: list[str]
    message: str


def _score(kind: str, title: str, body: str) -> tuple[int, list[str]]:
    title_pats, body_pats = SIGNALS[kind]
    evidence, points = [], 0
    for pat in title_pats:
        m = re.search(pat, title, re.IGNORECASE)
        if m:
            points += TITLE_POINTS
            evidence.append(m.group(0))
    body_points = 0
    for pat in body_pats:
        if re.search(pat, body, re.IGNORECASE):
            body_points += BODY_POINTS
    return points + min(body_points, BODY_CAP), evidence


def _split(text: str) -> tuple[str, str]:
    """(title area, everything else). The title area is the first few short non-empty lines."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    head = [ln for ln in lines[:TITLE_LINES] if len(ln) <= TITLE_LINE_MAX]
    return "\n".join(head), "\n".join(lines)


def check_type(kind: str, path) -> TypeCheck | None:
    """Return a warning if the file looks like a different kind of document, else None."""
    if kind not in CHECKED_KINDS:
        return None
    try:
        content = readers.read_document(path)
    except readers.ReaderError:
        return None                                  # unreadable: the normal pipeline reports that
    text = content.text or ""
    if content.vision or len(text.strip()) < 40:     # a scan: no text to judge by
        return None
    name = re.sub(r"[_\-.]+", " ", path.stem)
    heading, whole = _split(text)
    title = f"{name}\n{heading}"
    scores = {k: _score(k, title, whole) for k in SIGNALS}
    chosen_score = scores[kind][0]
    best = max((k for k in SIGNALS if k != kind), key=lambda k: scores[k][0])
    best_score, evidence = scores[best]
    if best_score < THRESHOLD or best_score < chosen_score + MARGIN:
        return None
    # An evidence word from the title is what a person can verify at a glance
    shown = ", ".join(f"'{e}'" for e in dict.fromkeys(evidence)) or "its wording"
    note = " Invoices cannot be loaded as quotes." if best == "invoice" else ""
    return TypeCheck(best, evidence, f"This looks like {LABEL[best]} (found {shown} in the title area), but you chose "
                                     f"'{CHOSEN_LABEL[kind]}'. Check the document type.{note}")