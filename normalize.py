"""Pure helpers that turn extracted quote data into comparable numbers.

No database, no network, no Streamlit. Everything here is deterministic, so the model
never does arithmetic: it reports what the document says and this code calculates.

The rule throughout: if something can't be worked out without guessing, return None and
say why, so a human sees the gap instead of a plausible-looking wrong number.
"""
import calendar
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

# ---------- Units ----------

_MASS_TO_G = {"mg": 0.001, "g": 1.0, "kg": 1000.0}
_UNIT_ALIASES = {
    "gram": "g", "grams": "g", "gm": "g", "gms": "g",
    "kilogram": "kg", "kilograms": "kg", "kgs": "kg",
    "milligram": "mg", "milligrams": "mg",
}


def normalize_unit(unit: str | None) -> str | None:
    if unit is None:
        return None
    u = unit.strip().lower().rstrip(".")
    return _UNIT_ALIASES.get(u, u) or None


def convert(value: float | None, from_unit: str | None, to_unit: str | None) -> float | None:
    """Convert a quantity between units. Only mass units convert; anything else must match exactly."""
    f, t = normalize_unit(from_unit), normalize_unit(to_unit)
    if value is None or f is None or t is None:
        return None
    if f == t:
        return float(value)
    if f in _MASS_TO_G and t in _MASS_TO_G:
        return value * _MASS_TO_G[f] / _MASS_TO_G[t]
    return None


# ---------- References and dates ----------

_REVISION_SUFFIX = re.compile(
    r"(?:[\s_\-./]*(?:revised|revision|rev\.?|r|v)[\s_\-.]*\d*)+$", re.IGNORECASE
)


def base_reference(ref: str | None) -> str | None:
    """Reduce a quote reference to the part shared by its revisions.

    'PWB-3307', 'PWB-3307-R2' and 'PWB-3307_v2_REVISED' all give 'PWB3307'.
    Returns None when nothing usable is left.
    """
    if not ref:
        return None
    stripped = _REVISION_SUFFIX.sub("", ref.strip())
    base = re.sub(r"[^A-Z0-9]", "", stripped.upper())
    return base or None


def derive_valid_until(quote_date: str | None, valid_for_days: int | None) -> str | None:
    if not quote_date or valid_for_days is None:
        return None
    try:
        return (date.fromisoformat(quote_date) + timedelta(days=valid_for_days)).isoformat()
    except ValueError:
        return None


def add_months(iso_date: str, months: int) -> str | None:
    try:
        d = date.fromisoformat(iso_date)
    except ValueError:
        return None
    year_offset, month_index = divmod(d.month - 1 + months, 12)
    year, month = d.year + year_offset, month_index + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1])).isoformat()


# ---------- Comparable price ----------

@dataclass
class Comparable:
    amount: float | None                    # in the quote's own currency
    basis: str                              # human-readable explanation, shown next to the number
    is_estimate: bool = False
    notes: list[str] = field(default_factory=list)


def _fmt(n: float) -> str:
    return f"{n:,.2f}"


def _item_type(item: dict) -> str:
    return item.get("item_type") or "main"


def _quantity_mismatch(items: list[dict], rfq: dict | None) -> str | None:
    """Note if the main line items clearly cover a different quantity than the RFQ asked for."""
    if not rfq or rfq.get("quantity") is None or not rfq.get("unit"):
        return None
    covered, seen = 0.0, False
    for item in items:
        if _item_type(item) != "main" or item.get("quantity") is None:
            continue
        converted = convert(item["quantity"], item.get("unit"), rfq["unit"])
        if converted is None:
            continue
        covered, seen = covered + converted, True
    if not seen:
        return None
    wanted = float(rfq["quantity"])
    if abs(covered - wanted) > 0.01 * wanted:
        return f"quote covers {covered:g} {rfq['unit']} but the RFQ asks for {wanted:g} {rfq['unit']}"
    return None


def _tier_label(t: dict) -> str:
    lo, hi, unit = t.get("min_quantity"), t.get("max_quantity"), t.get("quantity_unit") or ""
    if lo is not None and hi is not None:
        return f"{lo:g}-{hi:g} {unit}".strip()
    if lo is not None:
        return f">= {lo:g} {unit}".strip()
    if hi is not None:
        return f"<= {hi:g} {unit}".strip()
    return "unbounded"


def tier_consistency_notes(tiers: list[dict]) -> list[str]:
    """Spot price-tier tables whose ranges don't line up (often a misread unit)."""
    rows = []
    for t in tiers:
        unit = t.get("quantity_unit")
        lo = convert(t.get("min_quantity"), unit, "g") if t.get("min_quantity") is not None else None
        hi = convert(t.get("max_quantity"), unit, "g") if t.get("max_quantity") is not None else None
        if (t.get("min_quantity") is not None and lo is None) or (t.get("max_quantity") is not None and hi is None):
            return []                      # a unit we can't compare: say nothing rather than guess
        rows.append((lo if lo is not None else 0.0, hi, t))
    rows.sort(key=lambda r: r[0])
    notes = []
    for (_, prev_hi, prev), (next_lo, _, nxt) in zip(rows, rows[1:]):
        if prev_hi is None:
            notes.append(f"price tiers overlap: '{_tier_label(prev)}' has no upper limit but '{_tier_label(nxt)}' follows")
            continue
        tolerance = max(1.0, 0.01 * prev_hi)
        if next_lo - prev_hi > tolerance:
            notes.append(f"price tiers are not contiguous: gap between '{_tier_label(prev)}' and "
                         f"'{_tier_label(nxt)}'; check the units in the source")
        elif prev_hi - next_lo > tolerance:
            notes.append(f"price tiers overlap: '{_tier_label(prev)}' and '{_tier_label(nxt)}'")
    return notes


def _from_tiers(q: dict, rfq: dict | None, extras: float) -> Comparable:
    tiers = q["price_tiers"]
    if not rfq or rfq.get("quantity") is None or not rfq.get("unit"):
        return Comparable(None, "price tiers found but the RFQ quantity is unknown")
    qty, unit = float(rfq["quantity"]), rfq["unit"]

    matches = []
    for t in tiers:
        in_tier_unit = convert(qty, unit, t.get("quantity_unit"))
        if in_tier_unit is None:
            continue
        lo, hi = t.get("min_quantity"), t.get("max_quantity")
        if (lo is None or in_tier_unit >= lo - 1e-9) and (hi is None or in_tier_unit <= hi + 1e-9):
            matches.append(t)
    if len(matches) != 1:
        return Comparable(None, f"{len(matches)} price tiers match {qty:g} {unit}; cannot choose one")

    tier = matches[0]
    if not normalize_unit(tier.get("price_unit")):
        return Comparable(None, "the matching price tier does not state what the price is per")
    per_unit = convert(qty, unit, tier["price_unit"])
    if per_unit is None:
        return Comparable(None, f"cannot convert {unit} to the tier's price unit '{tier['price_unit']}'")

    product = tier["unit_price"] * per_unit
    currency = q.get("currency") or ""
    basis = (f"{qty:g} {unit} at the {_tier_label(tier)} tier "
             f"({currency} {tier['unit_price']:,.2f}/{tier['price_unit']}) = {_fmt(product)}".replace("  ", " "))
    if extras:
        basis += f" + {_fmt(extras)} shipping/fees"
    basis += "; estimate, excludes duties and optional items"
    return Comparable(product + extras, basis, is_estimate=True)


def compute_comparable(q: dict, rfq: dict | None) -> Comparable:
    """Work out the price for the quantity the RFQ asked about.

    Order of preference:
      1. the stated total (unless the line items clearly cover a different quantity)
      2. a price tier that matches the RFQ quantity, plus shipping/fee lines
      3. the sum of the line items (main + shipping + fees), when every main line has a total
    """
    items = q.get("line_items") or []
    tiers = q.get("price_tiers") or []
    extras = sum(i["line_total"] for i in items
                 if _item_type(i) in ("shipping", "fee") and i.get("line_total") is not None)

    mismatch = _quantity_mismatch(items, rfq)
    if mismatch:
        return Comparable(None, f"not comparable: {mismatch}", notes=[mismatch])

    if q.get("total_amount") is not None:
        return Comparable(float(q["total_amount"]), "stated total")

    if tiers:
        result = _from_tiers(q, rfq, extras)
        result.notes.extend(tier_consistency_notes(tiers))
        return result

    main = [i for i in items if _item_type(i) == "main"]
    if main and all(i.get("line_total") is not None for i in main):
        amount = sum(i["line_total"] for i in main) + extras
        return Comparable(amount, "sum of line items (excludes optional items)")

    return Comparable(None, "no total and no price that can be worked out from the document")