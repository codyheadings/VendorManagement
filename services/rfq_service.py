import db
from datetime import date
import sqlite3
from pathlib import Path
import hashlib

def get_next_rfq_number() -> str:
    year = date.today().year
    prefix = f"RFQ-{year}-"

    result = db.query_one(
        """
        SELECT COALESCE(
            MAX(CAST(SUBSTR(rfq_number, ?) AS INTEGER)),
            0
        ) + 1 AS next_number
        FROM rfqs
        WHERE rfq_number LIKE ?
        """,
        (len(prefix) + 1, f"{prefix}%")
    )

    return f"{prefix}{result['next_number']:03d}"


def add_rfq(rfq_data: dict, pdf_path: str) -> int:
    """Create an RFQ, its document record, and related records atomically."""

    pdf_file = Path(pdf_path)

    if not pdf_file.is_file():
        raise ValueError(f"RFQ PDF not found: {pdf_file}")

    pdf_bytes = pdf_file.read_bytes()
    sha256 = hashlib.sha256(pdf_bytes).hexdigest()

    rfq_number = get_next_rfq_number()

    try:
        with db.transaction() as conn:
            # Register the generated PDF in documents
            document_id = conn.execute(
                """
                INSERT INTO documents (
                    file_name,
                    file_path,
                    sha256,
                    mime_type,
                    category
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    f"{rfq_data['rfq_id']}.pdf",
                    pdf_path,
                    sha256,
                    "application/pdf",
                    "rfq"
                )
            ).lastrowid

            # Insert the main RFQ record
            rfq_id = conn.execute(
                """
                INSERT INTO rfqs (
                    rfq_number,
                    document_id,
                    title,
                    contact_email,
                    deliver_to,
                    issue_date,
                    due_date,
                    status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rfq_number,
                    document_id,
                    rfq_data["rfq_subtitle"].strip(),
                    rfq_data["rfq_contact"].strip(),
                    rfq_data["deliver_to"].strip(),
                    rfq_data["issued_date"].isoformat(),
                    rfq_data["rfq_deadline"].isoformat(),
                    "draft"
                )
            ).lastrowid

            # Insert requested items
            for item in rfq_data["items"]:
                quantity = item["quantity"].strip()

                conn.execute(
                    """
                    INSERT INTO rfq_items (
                        rfq_id,
                        line_number,
                        description,
                        quantity,
                        unit,
                        requirement
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        rfq_id,
                        item["line"],
                        item["description"],
                        float(quantity) if quantity else None,
                        item["unit"] or None,
                        item["required"] or None
                    )
                )

            # Insert specifications
            for index, specification in enumerate(
                rfq_data["specifications"], start=1
            ):
                conn.execute(
                    """
                    INSERT INTO rfq_specifications (
                        rfq_id,
                        line_number,
                        description
                    )
                    VALUES (?, ?, ?)
                    """,
                    (rfq_id, index, specification)
                )

            # Insert quote requirements
            for index, requirement in enumerate(
                rfq_data["quote_requirements"], start=1
            ):
                conn.execute(
                    """
                    INSERT INTO rfq_quote_requirements (
                        rfq_id,
                        line_number,
                        description
                    )
                    VALUES (?, ?, ?)
                    """,
                    (rfq_id, index, requirement)
                )

            # Create an invitation for each selected vendor
            for vendor_id in rfq_data["vendors"]:
                conn.execute(
                    """
                    INSERT INTO rfq_invitations (
                        rfq_id,
                        vendor_id,
                        status
                    )
                    VALUES (?, ?, ?)
                    """,
                    (rfq_id, vendor_id, "draft")
                )

            return rfq_id

    except sqlite3.IntegrityError as e:
        raise ValueError(f"Could not create RFQ: {e}") from e

    except (ValueError, TypeError) as e:
        raise ValueError(f"Invalid RFQ data: {e}") from e

class RfqError(ValueError):
    """A problem the person can read: the message is safe to show."""
 
 
def mark_rfq_sent(rfq_id: int | None, vendor_ids: list[int]) -> None:
    """Record that these vendors were emailed: their invitations become 'sent' and a draft RFQ becomes 'open'.
 
    One transaction, and it raises instead of silently doing nothing (an UPDATE that matches no row looks
    like success), so a missing RFQ id can never slip through unnoticed.
    """
    if not rfq_id:
        raise RfqError("no RFQ id: the RFQ must be created before it can be marked as sent")
    if not vendor_ids:
        return
    with db.transaction() as conn:
        rfq = conn.execute("SELECT id, status FROM rfqs WHERE id = ?", (rfq_id,)).fetchone()
        if rfq is None:
            raise RfqError(f"RFQ #{rfq_id} does not exist")
        if rfq["status"] in ("closed", "cancelled"):
            raise RfqError(f"RFQ #{rfq_id} is {rfq['status']}; it cannot be sent")
        for vendor_id in vendor_ids:
            conn.execute(
                """INSERT INTO rfq_invitations (rfq_id, vendor_id, status, sent_at) VALUES (?, ?, 'sent', ?)
                   ON CONFLICT(rfq_id, vendor_id) DO UPDATE SET
                       status = CASE WHEN status IN ('draft') THEN 'sent' ELSE status END,
                       sent_at = CASE WHEN status IN ('draft') THEN excluded.sent_at ELSE sent_at END""",
                (rfq_id, vendor_id, db.TODAY))
        conn.execute("UPDATE rfqs SET status = 'open' WHERE id = ? AND status = 'draft'", (rfq_id,))
 
 
def vendors_not_yet_sent(rfq_id: int | None, vendor_ids: list[int]) -> list[int]:
    """Of the selected vendors, those who have not already been sent this RFQ (stops double emailing)."""
    if not rfq_id or not vendor_ids:
        return list(vendor_ids)
    marks = ",".join("?" * len(vendor_ids))
    done = {r["vendor_id"] for r in db.query(
        f"SELECT vendor_id FROM rfq_invitations WHERE rfq_id = ? AND vendor_id IN ({marks}) "
        "AND status IN ('sent','replied','declined')", (rfq_id, *vendor_ids))}
    return [v for v in vendor_ids if v not in done]