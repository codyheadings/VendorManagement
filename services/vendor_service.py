import re

def normalize_vendor_name(name: str) -> str:
    """Remove whitespace, set to lowercase, and strip suffixes like LLC, LTD, etc."""
    # Normalize whitespace
    name = re.sub(r"\s+", " ", name.strip())
    # Remove trailing company suffixes
    name = re.sub(
        r"\s*[,.\-]?\s*(?:L\.L\.C\.?|LLC\.?|LTD\.?|INC\.?|CORP\.?|CO\.?|COMPANY\.?|LLP\.?|PLC\.?|P\.?C\.?|DBA|GMBH)\s*$",
        "",
        name,
        flags=re.IGNORECASE
    )
    return name.strip().casefold()