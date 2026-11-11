import re

_COMPANY_SUFFIXES = (
    r"(?:l\.?l\.?c|llp|ltd|limited|inc|incorporated|corp|corporation|"
    r"co|company|plc|pvt|gmbh)"
)
_SUFFIX_RE = re.compile(rf"[\s,.\-]+{_COMPANY_SUFFIXES}\.?\s*$", re.IGNORECASE)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_COUNTRY_ALIASES = {
    "us": "United States",
    "usa": "United States",
    "united states": "United States",
    "united states of america": "United States",
    "america": "United States",
    "uk": "United Kingdom",
    "gb": "United Kingdom",
    "great britain": "United Kingdom",
    "united kingdom": "United Kingdom",
}


def normalize_vendor_name(name: str) -> str:
    """Build the duplicate-detection key for a vendor name.

    Strips company suffixes, then lowercases and removes spaces and punctuation.
    Internal key for comparison, not a display name.
    """
    key = re.sub(r"\s+", " ", name.strip())
    while True:
        stripped = _SUFFIX_RE.sub("", key).strip(" ,.-")
        if not stripped or stripped == key:
            break
        key = stripped
    return re.sub(r"[\W_]+", "", key.casefold())


def normalize_country(value: str | None) -> str | None:
    """Map common variants (US, USA, UK...) to one spelling; pass others through."""
    if not value or not value.strip():
        return None
    cleaned = value.strip()
    return _COUNTRY_ALIASES.get(cleaned.casefold().replace(".", ""), cleaned)


def clean_email(value: str | None) -> tuple[str | None, str | None]:
    """Return (email, problem). email is None when missing or invalid."""
    if not value or not value.strip():
        return None, "missing"
    email = value.strip().lower()
    if not _EMAIL_RE.match(email):
        return None, f"invalid ({value.strip()})"
    return email, None