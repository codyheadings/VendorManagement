"""Pydantic schemas for what we extract from vendor documents.

Generate the tool schema sent to Claude, and validate what comes back.
Validation is strict so that anything which passes can be inserted into 
the database directly. Anything that fails goes to human review instead of being guessed.
"""
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


def _iso_date(value):
    if value is None or str(value).strip() == "":
        return None
    try:
        return date.fromisoformat(str(value).strip()).isoformat()
    except ValueError:
        raise ValueError(f"not an ISO date (YYYY-MM-DD): {value!r}") from None


def _truncate_lists(cls, v):
    """Over-long flag / source lists are trimmed, not rejected: the tokens are already spent."""
    if isinstance(v, list):
        return v[:5] if len(v) > 5 and all(isinstance(x, str) for x in v) else v[:6]
    return v


class FieldSource(BaseModel):
    field: str = Field(description="Name of the extracted field this snippet supports")
    snippet: str = Field(description="Short verbatim excerpt (max 150 characters) from the document")


class LineItem(BaseModel):
    description: str = Field(description="As written in the document, in its original language")
    description_en: str | None = Field(None, description="Short English translation; null if already English")
    item_type: Literal["main", "shipping", "fee", "optional", "other"] = Field(
        "main",
        description="main = the requested goods or service; shipping / fee = other charges; "
        "optional = an offered extra NOT included in the total",
    )
    quantity: float | None = None
    unit: str | None = Field(None, description="Unit exactly as quoted, e.g. g, kg, study, hour. Never convert.")
    unit_price: float | None = None
    line_total: float | None = None

    @field_validator("quantity")
    @classmethod
    def _quantity_positive(cls, v):
        if v is not None and v <= 0:
            raise ValueError("quantity must be > 0")
        return v

    @field_validator("unit_price", "line_total")
    @classmethod
    def _non_negative(cls, v):
        if v is not None and v < 0:
            raise ValueError("must be >= 0")
        return v


class PriceTier(BaseModel):
    """One row of a price-break table, e.g. '500 g - 999 g: 36000 per kg'."""
    min_quantity: float | None = None
    max_quantity: float | None = None
    quantity_unit: str | None = Field(None, description="Unit the quantity range is written in, e.g. g or kg")
    unit_price: float = Field(description="Price per price_unit")
    price_unit: str | None = Field(None, description="What the price is per, e.g. kg. As quoted, never converted.")

    @field_validator("unit_price", "min_quantity", "max_quantity")
    @classmethod
    def _non_negative(cls, v):
        if v is not None and v < 0:
            raise ValueError("must be >= 0")
        return v


class QuoteExtraction(BaseModel):
    source_language: str | None = Field(None, description="ISO 639-1 code of the document language, e.g. en, de, zh")
    vendor_name: str | None = None
    reference_number: str | None = Field(None, description="The vendor's own quote or reference number")
    rfq_reference: str | None = Field(None, description="Our RFQ number if the document mentions one, e.g. RFQ-2026-014")
    quote_date: str | None = Field(None, description="ISO date YYYY-MM-DD")
    valid_until: str | None = Field(None, description="ISO date YYYY-MM-DD, only if stated as a date")
    valid_for_days: int | None = Field(None, description="Validity in days, only if stated as a duration")
    currency: str | None = Field(None, description="3-letter ISO 4217 code, e.g. USD, GBP, EUR")
    total_amount: float | None = Field(None, description="Total price as a plain number; null if no total is stated")
    delivery_time_days: int | None = Field(
        None, description="Calendar days (weeks x 7). For a range use the upper end."
    )
    delivery_time_text: str | None = Field(None, description="Delivery / lead time wording exactly as written")
    terms: str | None = Field(None, description="Payment terms, Incoterms, price-changing conditions; max 40 words")
    requires_nda: bool | None = Field(
        None, description="True if the quote is conditional on a signed NDA / confidentiality agreement"
    )
    line_items: list[LineItem] = Field(
        default_factory=list, description="Only what is actually charged. NOT price-break tiers (use price_tiers)."
    )
    price_tiers: list[PriceTier] = Field(
        default_factory=list, description="Quantity-dependent price table, if the quote has one"
    )
    answers_rfq: Literal["yes", "partial", "no", "unclear"] = Field(
        "unclear",
        description="yes = prices the requested item at the requested quantity/scope (a unit price or tier that "
        "covers it counts even without a stated total); partial = part of it missing or different; no = not addressed",
    )
    flags: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="Problems a reviewer must check; empty list if none. Never confirmations.",
    )
    field_sources: list[FieldSource] = Field(
        default_factory=list,
        max_length=6,
        description="Verbatim evidence for total_amount, quote_date, valid_until, delivery time",
    )
    confidence: float = Field(0.0, ge=0, le=1, description="0 to 1: how sure you are this extraction is right")

    _trim = field_validator("flags", "field_sources", mode="before", check_fields=False)(classmethod(_truncate_lists))

    @field_validator("quote_date", "valid_until")
    @classmethod
    def _dates(cls, v):
        return _iso_date(v)

    @field_validator("currency")
    @classmethod
    def _currency(cls, v):
        if v is None:
            return None
        v = v.strip().upper()
        if len(v) != 3 or not v.isalpha():
            raise ValueError("expected a 3-letter ISO 4217 code")
        return v

    @field_validator("total_amount")
    @classmethod
    def _amount(cls, v):
        if v is not None and v < 0:
            raise ValueError("must be >= 0")
        return v

    @field_validator("delivery_time_days", "valid_for_days")
    @classmethod
    def _days(cls, v):
        if v is not None and v < 0:
            raise ValueError("must be >= 0")
        return v

    @model_validator(mode="after")
    def _validity_order(self):
        if self.quote_date and self.valid_until and self.valid_until < self.quote_date:
            raise ValueError("valid_until is earlier than quote_date")
        return self


class LegalDocExtraction(BaseModel):
    source_language: str | None = Field(None, description="ISO 639-1 code of the document language")
    doc_type: Literal["NDA", "MSA", "SOW", "OTHER"]
    other_type_description: str | None = Field(None, description="If OTHER, what it is, e.g. certificate of insurance")
    counterparty_name: str | None = Field(None, description="The vendor / other party")
    our_signer: str | None = Field(None, description="Person who signed or is to sign for ODDITY Labs")
    counterparty_signer: str | None = Field(None, description="Person who signed or is to sign for the other party")
    status: Literal["draft", "signed", "unknown"] = Field(
        "unknown",
        description="'signed' only if signatures or signing dates are visibly present; 'draft' if marked draft "
        "or unsigned; otherwise 'unknown'",
    )
    effective_date: str | None = Field(None, description="ISO date YYYY-MM-DD")
    expiry_date: str | None = Field(None, description="ISO date YYYY-MM-DD, only if stated or directly derivable "
                                    "from an explicit end date; do not calculate from a term")
    term_months: int | None = Field(None, description="Stated term length in months")
    auto_renews: bool | None = None
    renewal_notice_days: int | None = Field(None, description="Days of notice required to cancel or renew")
    governing_law: str | None = None
    parent_agreement_reference: str | None = Field(None, description="e.g. the MSA a SOW falls under")
    flags: list[str] = Field(default_factory=list, max_length=5, description="Problems only; empty if none")
    field_sources: list[FieldSource] = Field(default_factory=list, max_length=6)
    confidence: float = Field(0.0, ge=0, le=1)

    _trim = field_validator("flags", "field_sources", mode="before", check_fields=False)(classmethod(_truncate_lists))

    @field_validator("effective_date", "expiry_date")
    @classmethod
    def _dates(cls, v):
        return _iso_date(v)

    @field_validator("term_months", "renewal_notice_days")
    @classmethod
    def _non_negative(cls, v):
        if v is not None and v < 0:
            raise ValueError("must be >= 0")
        return v

    @model_validator(mode="after")
    def _date_order(self):
        if self.effective_date and self.expiry_date and self.expiry_date < self.effective_date:
            raise ValueError("expiry_date is earlier than effective_date")
        return self


class EmailExtraction(BaseModel):
    kind: Literal["quote", "question", "decline", "revision", "notice", "other"] = Field(
        description="quote: price given in the email; question: vendor asks us something; decline: vendor "
        "will not quote; revision: updated version of an earlier quote; notice: administrative or legal notice"
    )
    vendor_name: str | None = None
    rfq_reference: str | None = None
    summary: str = Field(description="One sentence describing what the email says")
    action_needed: str | None = Field(None, description="What our team should do next, if anything")
    supersedes_reference: str | None = Field(None, description="For a revision: reference of the quote it replaces")
    quote: QuoteExtraction | None = Field(None, description="Fill in only if the email body itself contains a quote")
    source_language: str | None = Field(None, description="ISO 639-1 code of the email language")
    flags: list[str] = Field(default_factory=list, max_length=5, description="At most 5 short English notes")
    confidence: float = Field(0.0, ge=0, le=1)

    _trim = field_validator("flags", "field_sources", mode="before", check_fields=False)(classmethod(_truncate_lists))