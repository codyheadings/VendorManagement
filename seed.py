import csv
from dataclasses import dataclass, field
from pathlib import Path
 
import db
import services.rates_service
from services.vendor_service import clean_email, normalize_country, normalize_vendor_name
 
SAMPLE_DIR = db.BASE_DIR / "data" / "sample"
VENDOR_CSV = SAMPLE_DIR / "02_Vendor_List" / "vendor_contacts_export.csv"
 
# Fallback rates to not waste API tokens
MOCK_RATES = services.rates_service.to_usd_rates({
  "date": "2026-10-20",
  "base": "EUR",
  "rates": {
    "USD": 1.122649,
    "GBP": 0.8395,
    "INR": 106.333563,
    "CNY": 7.497107,
    "CHF": 0.926921,
    "JPY": 177.690099,
    "CAD": 1.579197,
    "AUD": 1.602346
  }
})
 
 
@dataclass
class ImportReport:
    rates_source: str = ""
    vendors_created: int = 0
    contacts_created: int = 0
    merged: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    nda_claims: list[str] = field(default_factory=list)
 
    def summary(self) -> str:
        lines = [
            f"Exchange rates:   {self.rates_source}",
            f"Vendors created:  {self.vendors_created}",
            f"Contacts created: {self.contacts_created}",
        ]
        sections = (
            ("Merged / skipped", self.merged),
            ("Warnings", self.warnings),
            ("Rejected rows", self.rejected),
            ("NDA claims to verify against actual legal documents", self.nda_claims),
        )
        for title, items in sections:
            if items:
                lines += ["", f"{title} ({len(items)}):"]
                lines += [f"  - {item}" for item in items]
        return "\n".join(lines)
 
 
def seed_exchange_rates(conn, allow_network: bool = False) -> str:
    """Load rates for db.TODAY: cached API response, else one live call, else mock.
 
    Returns a one-line description of where the rates came from.
    """
    rates, source = services.rates_service.load_rates(db.TODAY, allow_network=allow_network)
    if rates is None:
        rates = MOCK_RATES
        source = f"MOCK fallback rates ({source})"
    conn.executemany(
        "INSERT INTO exchange_rates (currency, rate_to_usd, as_of_date) VALUES (?, ?, ?)",
        [(cur, rate, db.TODAY) for cur, rate in rates.items()],
    )
    return source
 
 
def seed_vendors_from_csv(conn, csv_path: Path) -> ImportReport:
    """Import the legacy vendor export: clean it, merge duplicates, report everything."""
    report = ImportReport()
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
 
    for line_no, row in enumerate(rows, start=2):
        name = " ".join((row.get("Vendor name") or "").split())
        if not name:
            report.rejected.append(f"line {line_no}: missing vendor name")
            continue
 
        key = normalize_vendor_name(name)
        country = normalize_country(row.get("Country"))
        category = (row.get("Category") or "").strip() or None
        row_notes = (row.get("Notes") or "").strip()
 
        # Vendor: create, or merge into an existing one with the same key
        existing = conn.execute(
            "SELECT id, name, category, country FROM vendors WHERE normalized_name = ?",
            (key,),
        ).fetchone()
 
        if existing:
            vendor_id = existing["id"]
            note = f" (row note: '{row_notes}')" if row_notes else ""
            report.merged.append(
                f"line {line_no}: '{name}' merged into '{existing['name']}'{note}"
            )
            # Fill blanks on the existing record; never overwrite, just flag conflicts.
            for col, new in (("category", category), ("country", country)):
                old = existing[col]
                if new and not old:
                    conn.execute(f"UPDATE vendors SET {col} = ? WHERE id = ?", (new, vendor_id))
                elif new and old and new != old:
                    report.warnings.append(
                        f"line {line_no}: {col} conflict for '{existing['name']}' "
                        f"('{old}' vs '{new}'); kept '{old}'"
                    )
        else:
            vendor_id = conn.execute(
                """
                INSERT INTO vendors (name, normalized_name, category, country, notes)
                VALUES (?, ?, ?, ?, ?)
                """,
                (name, key, category, country, row_notes or None),
            ).lastrowid
            report.vendors_created += 1
 
        # NDA column: record the claim, don't trust it
        raw_nda = (row.get("NDA on file") or "").strip()
        if raw_nda:
            report.nda_claims.append(f"{name}: '{raw_nda}'")
 
        # Contact info
        contact_name = " ".join((row.get("Main contact") or "").split())
        email, problem = clean_email(row.get("Email"))
        phone = (row.get("Phone") or "").strip() or None
 
        if problem == "missing":
            report.warnings.append(
                f"line {line_no}: {name}: no email on file (cannot receive RFQs by email)"
            )
        elif problem:
            report.warnings.append(f"line {line_no}: {name}: email {problem}; saved without email")
 
        if not contact_name:
            if not email:
                report.warnings.append(f"line {line_no}: {name}: no contact name or email; contact skipped")
                continue
            contact_name = email
 
        already = conn.execute(
            """
            SELECT 1 FROM vendor_contacts
            WHERE vendor_id = ?
              AND (email = ? OR (email IS NULL AND ? IS NULL AND name = ?))
            """,
            (vendor_id, email, email, contact_name),
        ).fetchone()
        if already:
            report.merged.append(f"line {line_no}: contact '{contact_name}' already exists; skipped")
            continue
 
        has_primary = conn.execute(
            "SELECT 1 FROM vendor_contacts WHERE vendor_id = ? AND is_primary = 1", (vendor_id,)
        ).fetchone()
        role = "Billing" if "billing" in row_notes.casefold() else None
 
        conn.execute(
            """
            INSERT INTO vendor_contacts (vendor_id, name, email, phone, role, is_primary)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (vendor_id, contact_name, email, phone, role, 0 if has_primary else 1),
        )
        report.contacts_created += 1
 
    return report
 
 
def run(fetch_rates: bool = False) -> ImportReport:
    """Seed everything. fetch_rates=True allows ONE live exchange-rate API call
    (only if no cached response exists). The app's startup path leaves it False
    so a deployed app can never spend API quota."""
    with db.transaction() as conn:
        rates_source = seed_exchange_rates(conn, allow_network=fetch_rates)
        report = seed_vendors_from_csv(conn, VENDOR_CSV)
        report.rates_source = rates_source
    return report
 
 
if __name__ == "__main__":
    db.init_db(reset=True)
    print(run(fetch_rates=True).summary())