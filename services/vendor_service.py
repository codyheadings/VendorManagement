import re
import db
import sqlite3

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

def add_vendor(name: str, country: str, category: str) -> int:
    normalized_name = normalize_vendor_name(name)
    
    try:
        inserted = db.execute(
            """
            INSERT INTO vendors (name, normalized_name, category, country)
            VALUES (?, ?, ?, ?)
            """,
            (
                name.strip(),
                normalized_name,
                category.strip(),
                country.strip()
            )
        )
        return inserted

    except sqlite3.IntegrityError as e:
            msg = str(e)
            if "vendors.normalized_name" in msg:
                raise ValueError(f"A vendor named '{name}' already exists.") from e
            raise ValueError(f"Could not add vendor: {msg}") from e

def add_vendor_with_contact(
    name: str, category: str, country: str,
    contact_name: str, email: str, role: str,
) -> int:
    """Insert a vendor and its primary contact atomically. Returns the vendor id."""
    try:
        with db.transaction() as conn:
            vendor_id = conn.execute(
                """
                INSERT INTO vendors (name, normalized_name, category, country)
                VALUES (?, ?, ?, ?)
                """,
                (name.strip(), normalize_vendor_name(name), category.strip(), country.strip()),
            ).lastrowid

            conn.execute(
                """
                INSERT INTO vendor_contacts (vendor_id, name, email, role, is_primary)
                VALUES (?, ?, ?, ?, 1)
                """,
                (vendor_id, contact_name.strip(), email.strip(), role.strip()),
            )
            return vendor_id
    except sqlite3.IntegrityError as e:
        msg = str(e)
        if "vendors.normalized_name" in msg:
            raise ValueError(f"A vendor named '{name}' already exists.") from e
        if "CHECK constraint failed" in msg and "email" in msg:
            raise ValueError("The submitted email address is incorrectly formatted.") from e
        raise ValueError(f"Could not save: {msg}") from e

def get_vendor_from_id(vendor_id: int) -> str:
    result = db.query_one(
        "SELECT name FROM vendors WHERE id = ?",
        (vendor_id,)
    )
    return result["name"]

def get_vendor_primary_contact(vendor_id: int) -> dict:
    """Retrieve vendor information and its primary contact."""

    return db.query_one(
        """
        SELECT
            v.id AS vendor_id,
            v.name AS vendor_name,
            c.name AS contact_name,
            c.email AS email,
            c.role AS contact_role
        FROM vendors v
        LEFT JOIN vendor_contacts c
            ON c.vendor_id = v.id
            AND c.is_primary = 1
        WHERE v.id = ?
        """,
        (vendor_id,)
    )

def get_vendor_quotes(vendor_id, search="", status_filter="All", sort="Date", order="Descending"):

    like = f"%{search.strip()}%"
    status = status_filter.lower()

    sort_columns = {
        "Date": "q.quote_date",
        "Price": "q.total_amount",
        "Delivery": "q.delivery_time_days",
    }

    sort_by = sort_columns[sort]
    order_by = "DESC" if order == "Descending" else "ASC"

    return db.query(
        f"""
        SELECT
            q.id,
            q.reference_number,
            q.quote_date,
            q.valid_until,
            q.currency,
            q.total_amount,
            q.delivery_time_days,
            q.review_status,
            q.confidence,

            v.name AS vendor_name,

            r.rfq_number,

            d.file_name,
            d.file_path,
            d.mime_type,

            er.rate_to_usd,

            EXISTS (
                SELECT 1
                FROM quotes q2
                WHERE q2.supersedes_quote_id = q.id
                  AND q2.review_status = 'approved'
            ) AS superseded

        FROM quotes q

        JOIN vendors v
            ON v.id = q.vendor_id

        LEFT JOIN rfqs r
            ON r.id = q.rfq_id

        LEFT JOIN documents d
            ON d.id = q.document_id

        LEFT JOIN exchange_rates er
            ON er.currency = q.currency

        WHERE q.vendor_id = ?

          AND (
              q.reference_number LIKE ?
              OR COALESCE(r.rfq_number, '') LIKE ?
              OR COALESCE(r.title, '') LIKE ?
          )

          AND (
              ? = 'all'
              OR q.review_status = ?
          )

        ORDER BY {sort_by} {order_by}, q.id DESC
        """,
        (
            vendor_id,
            like,
            like,
            like,
            status,
            status,
        ),
    )

def get_vendor_documents(vendor_id, category=None, search="", sort="Date",
                         order="Descending"):
    sort_columns = {
        "Date": "d.created_at",
        "Name": "d.file_name",
        "Category": "d.category",
    }

    sort_by = sort_columns[sort]
    order_by = "DESC" if order == "Descending" else "ASC"

    like = f"%{search.strip()}%"
    category_filter = category if category else None

    return db.query(
        f"""
        SELECT
            d.id,
            d.file_name,
            d.file_path,
            d.mime_type,
            d.category,
            d.source,
            d.created_at,

            v.name AS vendor_name,

            (SELECT r.rfq_number
             FROM rfqs r
             WHERE r.document_id = d.id
             LIMIT 1) AS rfq_number,

            (SELECT r.title
             FROM rfqs r
             WHERE r.document_id = d.id
             LIMIT 1) AS rfq_title,

            (SELECT r.status
             FROM rfqs r
             WHERE r.document_id = d.id
             LIMIT 1) AS rfq_status,

            (SELECT r.due_date
             FROM rfqs r
             WHERE r.document_id = d.id
             LIMIT 1) AS rfq_due_date,

            (SELECT l.doc_type
             FROM legal_documents l
             WHERE l.document_id = d.id
             LIMIT 1) AS legal_type,

            (SELECT l.status
             FROM legal_documents l
             WHERE l.document_id = d.id
             LIMIT 1) AS legal_status,

            (SELECT l.expiry_date
             FROM legal_documents l
             WHERE l.document_id = d.id
             LIMIT 1) AS expiry_date,

            (SELECT i.invoice_number
             FROM invoices i
             WHERE i.document_id = d.id
             LIMIT 1) AS invoice_number,

            (SELECT i.amount
             FROM invoices i
             WHERE i.document_id = d.id
             LIMIT 1) AS invoice_amount,

            (SELECT i.currency
             FROM invoices i
             WHERE i.document_id = d.id
             LIMIT 1) AS invoice_currency,

            (SELECT i.due_date
             FROM invoices i
             WHERE i.document_id = d.id
             LIMIT 1) AS invoice_due_date,

            (SELECT i.paid_date
             FROM invoices i
             WHERE i.document_id = d.id
             LIMIT 1) AS paid_date

        FROM documents d

        LEFT JOIN vendors v ON v.id = d.vendor_id

        WHERE d.category != 'quote'

        AND (
            d.vendor_id = ?

            OR EXISTS (
                SELECT 1
                FROM legal_documents l
                WHERE l.document_id = d.id
                  AND l.vendor_id = ?
            )

            OR EXISTS (
                SELECT 1
                FROM invoices i
                WHERE i.document_id = d.id
                  AND i.vendor_id = ?
            )
        )

        AND (? IS NULL OR d.category = ?)

        AND (
            d.file_name LIKE ?
            OR d.category LIKE ?
            OR d.source LIKE ?
            OR COALESCE(v.name, '') LIKE ?

            OR EXISTS (
                SELECT 1
                FROM rfqs r
                WHERE r.document_id = d.id
                  AND (
                      r.rfq_number LIKE ?
                      OR r.title LIKE ?
                  )
            )

            OR EXISTS (
                SELECT 1
                FROM legal_documents l
                WHERE l.document_id = d.id
                  AND l.doc_type LIKE ?
            )

            OR EXISTS (
                SELECT 1
                FROM invoices i
                WHERE i.document_id = d.id
                  AND i.invoice_number LIKE ?
            )
        )

        ORDER BY {sort_by} {order_by}, d.id DESC
        """,
        (
            vendor_id,
            vendor_id,
            vendor_id,
            category_filter,
            category_filter,
            like,
            like,
            like,
            like,
            like,
            like,
            like,
            like,
        ),
    )

def add_vendor_contact(
    vendor_id: int,
    name: str,
    email: str = "",
    phone: str = "",
    role: str = "",
    is_primary: bool = False,
):
    """Add a vendor contact, optionally replacing the primary contact."""
    try:
        with db.transaction() as conn:
            # Unset the existing primary contact if requested
            if is_primary:
                conn.execute(
                    """
                    UPDATE vendor_contacts
                    SET is_primary = 0
                    WHERE vendor_id = ?
                      AND is_primary = 1
                    """,
                    (vendor_id,),
                )

            # Insert the new contact
            conn.execute(
                """
                INSERT INTO vendor_contacts (
                    vendor_id,
                    name,
                    email,
                    phone,
                    role,
                    is_primary
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    vendor_id,
                    name.strip(),
                    email.strip() or None,
                    phone.strip() or None,
                    role.strip() or None,
                    int(is_primary),
                ),
            )

    except sqlite3.IntegrityError as e:
        msg = str(e)

        if "CHECK constraint failed" in msg and "email" in msg:
            raise ValueError(
                "The submitted email address is incorrectly formatted."
            ) from e

        if "UNIQUE constraint failed" in msg:
            raise ValueError(
                "A contact with this email already exists for this vendor."
            ) from e

        raise ValueError(f"Could not add contact: {msg}") from e


def delete_vendor_contact(vendor_id: int, contact_id: int):
    """Delete a non-primary vendor contact."""

    try:
        with db.transaction() as conn:

            result = conn.execute(
                """
                DELETE FROM vendor_contacts
                WHERE id = ?
                  AND vendor_id = ?
                  AND is_primary = 0
                """,
                (contact_id, vendor_id),
            )

            if result.rowcount == 0:
                raise ValueError(
                    "Contact could not be deleted. "
                    "It may be the primary contact or it no longer exists."
                )

    except sqlite3.IntegrityError as e:
        raise ValueError(
            f"Could not delete contact: {e}"
        ) from e


def update_vendor_contact(
    vendor_id: int,
    contact_id: int,
    name: str,
    email: str = "",
    phone: str = "",
    role: str = "",
    is_primary: bool = False,
):
    """Update a vendor contact, optionally replacing the primary contact."""

    try:
        with db.transaction() as conn:

            if is_primary:
                conn.execute(
                    """
                    UPDATE vendor_contacts
                    SET is_primary = 0
                    WHERE vendor_id = ?
                      AND id != ?
                      AND is_primary = 1
                    """,
                    (vendor_id, contact_id),
                )

            result = conn.execute(
                """
                UPDATE vendor_contacts
                SET
                    name = ?,
                    email = ?,
                    phone = ?,
                    role = ?,
                    is_primary = ?
                WHERE id = ?
                  AND vendor_id = ?
                """,
                (
                    name.strip(),
                    email.strip() or None,
                    phone.strip() or None,
                    role.strip() or None,
                    int(is_primary),
                    contact_id,
                    vendor_id,
                ),
            )

            if result.rowcount == 0:
                raise ValueError("Contact no longer exists.")

    except sqlite3.IntegrityError as e:
        msg = str(e)

        if "CHECK constraint failed" in msg and "email" in msg:
            raise ValueError(
                "The submitted email address is incorrectly formatted."
            ) from e

        if "UNIQUE constraint failed" in msg:
            raise ValueError(
                "A contact with this email already exists for this vendor."
            ) from e

        raise ValueError(f"Could not update contact: {msg}") from e