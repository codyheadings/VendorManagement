-- ---------- Vendors ----------

CREATE TABLE IF NOT EXISTS vendors (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  normalized_name TEXT NOT NULL UNIQUE,
  category TEXT,
  country TEXT,
  notes TEXT,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive','blocked')),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
) STRICT;

CREATE TABLE IF NOT EXISTS vendor_contacts (
  id INTEGER PRIMARY KEY,
  vendor_id INTEGER NOT NULL REFERENCES vendors(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  email TEXT CHECK (email LIKE '_%@_%._%'),
  phone TEXT, -- TODO: add format check after normalizing inputs
  role TEXT,
  is_primary INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (vendor_id, email)
) STRICT;

-- At most one primary contact per vendor
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_primary_contact
  ON vendor_contacts(vendor_id) WHERE is_primary = 1;


-- ---------- Documents ----------

CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY,
  vendor_id INTEGER REFERENCES vendors(id),
  file_name TEXT NOT NULL,
  file_path TEXT NOT NULL,
  sha256 TEXT NOT NULL UNIQUE,
  mime_type TEXT,
  category TEXT NOT NULL DEFAULT 'other'
    CHECK (category IN ('rfq','quote','legal','invoice','email','other')),
  source TEXT NOT NULL DEFAULT 'upload' CHECK (source IN ('upload','email')),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
) STRICT;


-- ---------- RFQs ----------

CREATE TABLE IF NOT EXISTS rfqs (
  id INTEGER PRIMARY KEY,
  rfq_number TEXT NOT NULL UNIQUE,
  document_id INTEGER REFERENCES documents(id),   -- source file, if any
  title TEXT NOT NULL,
  contact_email TEXT NOT NULL,
  deliver_to TEXT NOT NULL,
  issue_date TEXT NOT NULL CHECK (date(issue_date) = issue_date),
  due_date TEXT CHECK (due_date IS NULL OR date(due_date) = due_date),
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'open', 'closed', 'cancelled')),
  awarded_vendor_id INTEGER REFERENCES vendors(id),  -- vendor whose quote was accepted
  created_at TEXT NOT NULL DEFAULT (datetime('now')) CHECK (due_date IS NULL OR due_date >= issue_date),
) STRICT;

CREATE TABLE IF NOT EXISTS rfq_items (
  id INTEGER PRIMARY KEY,
  rfq_id INTEGER NOT NULL REFERENCES rfqs(id) ON DELETE CASCADE,
  line_number INTEGER NOT NULL,
  description TEXT NOT NULL,
  quantity REAL CHECK (quantity IS NULL OR quantity > 0),
  unit TEXT,
  requirement TEXT,
  UNIQUE (rfq_id, line_number)
) STRICT;

CREATE TABLE IF NOT EXISTS rfq_specifications (
  id INTEGER PRIMARY KEY,
  rfq_id INTEGER NOT NULL REFERENCES rfqs(id) ON DELETE CASCADE,
  line_number INTEGER NOT NULL,
  description TEXT NOT NULL,
  UNIQUE (rfq_id, line_number)
) STRICT;

CREATE TABLE IF NOT EXISTS rfq_quote_requirements (
  id INTEGER PRIMARY KEY,
  rfq_id INTEGER NOT NULL REFERENCES rfqs(id) ON DELETE CASCADE,
  line_number INTEGER NOT NULL,
  description TEXT NOT NULL,
  UNIQUE (rfq_id, line_number)
) STRICT;

CREATE TABLE IF NOT EXISTS rfq_invitations (
  id INTEGER PRIMARY KEY,
  rfq_id INTEGER NOT NULL REFERENCES rfqs(id) ON DELETE CASCADE,
  vendor_id INTEGER NOT NULL REFERENCES vendors(id),
  status TEXT NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft','sent','replied','declined')),
  sent_at TEXT,
  replied_at TEXT,
  last_followup_at TEXT,
  followup_count INTEGER NOT NULL DEFAULT 0 CHECK (followup_count >= 0),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (rfq_id, vendor_id)
) STRICT;


-- ---------- Quotes ----------

CREATE TABLE IF NOT EXISTS quotes (
  id INTEGER PRIMARY KEY,
  vendor_id INTEGER NOT NULL REFERENCES vendors(id),
  rfq_id INTEGER REFERENCES rfqs(id),
  document_id INTEGER REFERENCES documents(id),   -- source file, if any
  reference_number TEXT,
  quote_date TEXT CHECK (quote_date IS NULL OR date(quote_date) = quote_date),
  valid_until TEXT CHECK (valid_until IS NULL OR date(valid_until) = valid_until),
  -- NULL currency = the document didn't state one; a human must fill it in before approving
  currency TEXT CHECK (currency IS NULL OR (length(currency) = 3 AND currency = upper(currency))),
  total_amount REAL CHECK (total_amount >= 0),
  delivery_time_days INTEGER CHECK (delivery_time_days >= 0),
  delivery_time_text TEXT,
  terms TEXT,
  requires_nda INTEGER CHECK (requires_nda IN (0, 1)),
  answers_rfq TEXT CHECK (answers_rfq IN ('yes','partial','no','unclear')),
  source_language TEXT,
  -- Price for the quantity we asked about, in the quote's own currency (see normalize.py).
  -- comparable_basis says how it was worked out; NULL amount = could not be worked out.
  comparable_amount REAL CHECK (comparable_amount >= 0),
  comparable_basis TEXT,
  comparable_is_estimate INTEGER NOT NULL DEFAULT 0 CHECK (comparable_is_estimate IN (0, 1)),
  review_notes TEXT CHECK (review_notes IS NULL OR json_valid(review_notes)),   -- [{"by":"ai"|"loader","text":...}]
  field_sources TEXT CHECK (field_sources IS NULL OR json_valid(field_sources)),
  supersedes_quote_id INTEGER REFERENCES quotes(id),
  review_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (review_status IN ('pending','approved','rejected')),
  confidence REAL CHECK (confidence BETWEEN 0 AND 1),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  CHECK (supersedes_quote_id IS NULL OR supersedes_quote_id != id),
  CHECK (valid_until IS NULL OR quote_date IS NULL OR valid_until >= quote_date)
) STRICT;

CREATE TABLE IF NOT EXISTS quote_line_items (
  id INTEGER PRIMARY KEY,
  quote_id INTEGER NOT NULL REFERENCES quotes(id) ON DELETE CASCADE,
  description TEXT NOT NULL,              -- original language
  description_en TEXT,
  item_type TEXT NOT NULL DEFAULT 'main'
    CHECK (item_type IN ('main','shipping','fee','optional','other')),
  quantity REAL CHECK (quantity > 0),
  unit TEXT,
  unit_price REAL CHECK (unit_price >= 0),
  line_total REAL CHECK (line_total >= 0)
) STRICT;


-- Quantity-dependent price tables ("500 g - 999 g: 36000 per kg"), kept as quoted.
CREATE TABLE IF NOT EXISTS quote_price_tiers (
  id INTEGER PRIMARY KEY,
  quote_id INTEGER NOT NULL REFERENCES quotes(id) ON DELETE CASCADE,
  min_quantity REAL CHECK (min_quantity >= 0),
  max_quantity REAL CHECK (max_quantity >= 0),
  quantity_unit TEXT,
  unit_price REAL NOT NULL CHECK (unit_price >= 0),
  price_unit TEXT
) STRICT;


-- ---------- Legal documents ----------

CREATE TABLE IF NOT EXISTS legal_documents (
  id INTEGER PRIMARY KEY,
  vendor_id INTEGER NOT NULL REFERENCES vendors(id),
  document_id INTEGER REFERENCES documents(id),
  parent_id INTEGER REFERENCES legal_documents(id),
  doc_type TEXT NOT NULL CHECK (doc_type IN ('NDA','MSA','SOW','OTHER')),
  other_description TEXT,
  status TEXT NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft','sent','signed','expired','terminated','unknown')),
  signer TEXT,
  vendor_signer TEXT,
  effective_date TEXT CHECK (effective_date IS NULL OR date(effective_date) = effective_date),
  expiry_date TEXT CHECK (expiry_date IS NULL OR date(expiry_date) = expiry_date),
  auto_renews INTEGER NOT NULL DEFAULT 0 CHECK (auto_renews IN (0, 1)),
  notice_days INTEGER CHECK (notice_days >= 0),
  review_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (review_status IN ('pending','approved','rejected')),
  review_notes TEXT CHECK (review_notes IS NULL OR json_valid(review_notes)),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  CHECK (expiry_date IS NULL OR effective_date IS NULL OR expiry_date >= effective_date)
) STRICT;


-- ---------- Document Extractions: ----------
-- One row per doc. Keeps the raw result (including FAILED ones, so nothing silently
-- disappears), and carries emails that create no record (questions, notices) as open actions.

CREATE TABLE IF NOT EXISTS extractions (
  id INTEGER PRIMARY KEY,
  document_id INTEGER NOT NULL UNIQUE REFERENCES documents(id) ON DELETE CASCADE,
  doc_type TEXT NOT NULL CHECK (doc_type IN ('quote','legal','email','invoice','other')),
  model TEXT,
  schema_version INTEGER,
  validity TEXT NOT NULL CHECK (validity IN ('ok','invalid')),
  data_json TEXT NOT NULL CHECK (json_valid(data_json)),
  errors_json TEXT CHECK (errors_json IS NULL OR json_valid(errors_json)),
  status TEXT NOT NULL DEFAULT 'new'
    CHECK (status IN ('new','invalid','needs_vendor','applied','duplicate','info_only','failed')),
  status_detail TEXT,
  summary TEXT,
  action_needed TEXT,
  action_status TEXT CHECK (action_status IN ('open','done','dismissed')),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  applied_at TEXT
) STRICT;


-- ---------- RFQ history: follow-ups, vendor questions, notes ----------

CREATE TABLE IF NOT EXISTS rfq_messages (
  id INTEGER PRIMARY KEY,
  rfq_id INTEGER NOT NULL REFERENCES rfqs(id) ON DELETE CASCADE,
  vendor_id INTEGER REFERENCES vendors(id),
  document_id INTEGER REFERENCES documents(id),
  direction TEXT NOT NULL CHECK (direction IN ('outbound','inbound','internal')),
  kind TEXT NOT NULL CHECK (kind IN ('followup','question','decline','reply','notice','note','status')),
  channel TEXT NOT NULL DEFAULT 'system' CHECK (channel IN ('email','manual','system')),
  subject TEXT,
  body TEXT NOT NULL,
  occurred_on TEXT NOT NULL CHECK (date(occurred_on) = occurred_on),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
) STRICT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_rfq_messages_doc ON rfq_messages(rfq_id, document_id) WHERE document_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_rfq_messages_rfq ON rfq_messages(rfq_id, occurred_on);

-- ---------- Vendor hub ----------

CREATE TABLE IF NOT EXISTS purchase_orders (
  id INTEGER PRIMARY KEY,
  vendor_id INTEGER NOT NULL REFERENCES vendors(id),
  quote_id INTEGER REFERENCES quotes(id),
  po_number TEXT NOT NULL UNIQUE,
  issued_date TEXT NOT NULL CHECK (date(issued_date) = issued_date),
  amount REAL NOT NULL CHECK (amount >= 0),
  currency TEXT NOT NULL CHECK (length(currency) = 3 AND currency = upper(currency)),
  status TEXT NOT NULL DEFAULT 'open'
    CHECK (status IN ('open','received','closed','cancelled')),
  notes TEXT CHECK (notes IS NULL OR json_valid(notes)),            -- history: who did what, when
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
) STRICT;

CREATE TABLE IF NOT EXISTS invoices (
  id INTEGER PRIMARY KEY,
  vendor_id INTEGER NOT NULL REFERENCES vendors(id),
  po_id INTEGER REFERENCES purchase_orders(id),
  document_id INTEGER REFERENCES documents(id),
  invoice_number TEXT NOT NULL,
  amount REAL NOT NULL CHECK (amount >= 0),
  currency TEXT NOT NULL CHECK (length(currency) = 3 AND currency = upper(currency)),
  invoice_date TEXT NOT NULL CHECK (date(invoice_date) = invoice_date),
  due_date TEXT CHECK (due_date IS NULL OR date(due_date) = due_date),
  paid_date TEXT CHECK (paid_date IS NULL OR date(paid_date) = paid_date),
  po_reference TEXT,
  review_status TEXT NOT NULL DEFAULT 'approved'
    CHECK (review_status IN ('pending','approved','rejected')),
  review_notes TEXT CHECK (review_notes IS NULL OR json_valid(review_notes)),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (vendor_id, invoice_number)
) STRICT;


-- ---------- Support ----------

CREATE TABLE IF NOT EXISTS exchange_rates (
  currency TEXT PRIMARY KEY CHECK (length(currency) = 3 AND currency = upper(currency)),
  rate_to_usd REAL NOT NULL CHECK (rate_to_usd > 0),
  as_of_date TEXT NOT NULL CHECK (date(as_of_date) = as_of_date)
) STRICT;


-- ---------- Views ----------

-- Current quotes only: not rejected, and not replaced by a newer non-rejected revision.
-- comparable_usd is converted at display time from the stored native amount.
CREATE VIEW v_quote_comparison AS
SELECT q.id AS quote_id, q.vendor_id, v.name AS vendor_name, q.rfq_id, r.rfq_number,
       q.reference_number, q.quote_date, q.valid_until, q.currency, q.total_amount,
       q.comparable_amount, q.comparable_basis, q.comparable_is_estimate,
       ROUND(q.comparable_amount * er.rate_to_usd, 2) AS comparable_usd,
       q.delivery_time_days, q.delivery_time_text, q.requires_nda, q.answers_rfq,
       q.review_status, q.confidence
FROM quotes q
JOIN vendors v              ON v.id = q.vendor_id
LEFT JOIN rfqs r            ON r.id = q.rfq_id
LEFT JOIN exchange_rates er ON er.currency = q.currency
WHERE q.review_status != 'rejected'
  AND NOT EXISTS (SELECT 1 FROM quotes n
                  WHERE n.supersedes_quote_id = q.id AND n.review_status != 'rejected');


-- ---------- Indexes on foreign keys ----------

CREATE INDEX IF NOT EXISTS idx_documents_vendor           ON documents(vendor_id);
CREATE INDEX IF NOT EXISTS idx_invitations_vendor         ON rfq_invitations(vendor_id);
CREATE INDEX IF NOT EXISTS idx_quotes_vendor              ON quotes(vendor_id);
CREATE INDEX IF NOT EXISTS idx_quotes_rfq                 ON quotes(rfq_id);
CREATE INDEX IF NOT EXISTS idx_rfq_vendor                 ON rfqs(awarded_vendor_id);
CREATE INDEX IF NOT EXISTS idx_rfq_items_rfq_id           ON rfq_items(rfq_id);
CREATE INDEX IF NOT EXISTS idx_rfq_invitations_rfq_id     ON rfq_invitations(rfq_id);
CREATE INDEX IF NOT EXISTS idx_rfq_invitations_vendor_id  ON rfq_invitations(vendor_id);
CREATE INDEX IF NOT EXISTS idx_quotes_supersedes          ON quotes(supersedes_quote_id);
CREATE INDEX IF NOT EXISTS idx_line_items_quote           ON quote_line_items(quote_id);
CREATE INDEX IF NOT EXISTS idx_tiers_quote                ON quote_price_tiers(quote_id);
CREATE INDEX IF NOT EXISTS idx_extractions_status         ON extractions(status);
CREATE INDEX IF NOT EXISTS idx_legal_vendor               ON legal_documents(vendor_id);
CREATE INDEX IF NOT EXISTS idx_legal_expiry               ON legal_documents(expiry_date);
CREATE INDEX IF NOT EXISTS idx_po_vendor                  ON purchase_orders(vendor_id);
CREATE INDEX IF NOT EXISTS idx_invoices_vendor            ON invoices(vendor_id);
CREATE INDEX IF NOT EXISTS idx_invoices_po                ON invoices(po_id);