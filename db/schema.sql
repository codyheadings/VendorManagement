-- ---------- Vendors ----------

CREATE TABLE vendors (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  normalized_name TEXT NOT NULL UNIQUE,
  category TEXT,
  country TEXT,
  notes TEXT,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive','blocked')),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
) STRICT;

CREATE TABLE vendor_contacts (
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
CREATE UNIQUE INDEX idx_one_primary_contact
  ON vendor_contacts(vendor_id) WHERE is_primary = 1;


-- ---------- Documents ----------

CREATE TABLE documents (
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

CREATE TABLE rfqs (
  id INTEGER PRIMARY KEY,
  rfq_number TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  description TEXT,
  quantity REAL CHECK (quantity > 0),
  unit TEXT,
  issue_date TEXT NOT NULL CHECK (date(issue_date) = issue_date),
  due_date TEXT CHECK (due_date IS NULL OR date(due_date) = due_date),
  status TEXT NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft','open','closed','cancelled')),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  CHECK (due_date IS NULL OR due_date >= issue_date)
) STRICT;

CREATE TABLE rfq_invitations (
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

CREATE TABLE quotes (
  id INTEGER PRIMARY KEY,
  vendor_id INTEGER NOT NULL REFERENCES vendors(id),
  rfq_id INTEGER REFERENCES rfqs(id),
  document_id INTEGER REFERENCES documents(id),
  reference_number TEXT,
  quote_date TEXT CHECK (quote_date IS NULL OR date(quote_date) = quote_date),
  valid_until TEXT CHECK (valid_until IS NULL OR date(valid_until) = valid_until),
  currency TEXT NOT NULL CHECK (length(currency) = 3 AND currency = upper(currency)),
  total_amount REAL CHECK (total_amount >= 0),
  delivery_time_days INTEGER CHECK (delivery_time_days >= 0),
  terms TEXT,
  supersedes_quote_id INTEGER REFERENCES quotes(id),
  review_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (review_status IN ('pending','approved','rejected')),
  confidence REAL CHECK (confidence BETWEEN 0 AND 1),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  CHECK (supersedes_quote_id IS NULL OR supersedes_quote_id != id),
  CHECK (valid_until IS NULL OR quote_date IS NULL OR valid_until >= quote_date)
) STRICT;

CREATE TABLE quote_line_items (
  id INTEGER PRIMARY KEY,
  quote_id INTEGER NOT NULL REFERENCES quotes(id) ON DELETE CASCADE,
  description TEXT NOT NULL,
  quantity REAL CHECK (quantity > 0),
  unit TEXT,
  unit_price REAL CHECK (unit_price >= 0),
  line_total REAL CHECK (line_total >= 0)
) STRICT;


-- ---------- Legal documents ----------

CREATE TABLE legal_documents (
  id INTEGER PRIMARY KEY,
  vendor_id INTEGER NOT NULL REFERENCES vendors(id),
  document_id INTEGER REFERENCES documents(id),
  parent_id INTEGER REFERENCES legal_documents(id),
  doc_type TEXT NOT NULL CHECK (doc_type IN ('NDA','MSA','SOW')),
  status TEXT NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft','sent','signed','expired','terminated')),
  signer TEXT,
  vendor_signer TEXT,
  effective_date TEXT CHECK (effective_date IS NULL OR date(effective_date) = effective_date),
  expiry_date TEXT CHECK (expiry_date IS NULL OR date(expiry_date) = expiry_date),
  auto_renews INTEGER NOT NULL DEFAULT 0 CHECK (auto_renews IN (0, 1)),
  notice_days INTEGER CHECK (notice_days >= 0),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  CHECK (expiry_date IS NULL OR effective_date IS NULL OR expiry_date >= effective_date)
) STRICT;


-- ---------- Vendor hub ----------

CREATE TABLE purchase_orders (
  id INTEGER PRIMARY KEY,
  vendor_id INTEGER NOT NULL REFERENCES vendors(id),
  quote_id INTEGER REFERENCES quotes(id),
  po_number TEXT NOT NULL UNIQUE,
  issued_date TEXT NOT NULL CHECK (date(issued_date) = issued_date),
  amount REAL NOT NULL CHECK (amount >= 0),
  currency TEXT NOT NULL CHECK (length(currency) = 3 AND currency = upper(currency)),
  status TEXT NOT NULL DEFAULT 'open'
    CHECK (status IN ('open','received','closed','cancelled')),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
) STRICT;

CREATE TABLE invoices (
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
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (vendor_id, invoice_number)
) STRICT;


-- ---------- Misc ----------

CREATE TABLE exchange_rates (
  currency TEXT PRIMARY KEY CHECK (length(currency) = 3 AND currency = upper(currency)),
  rate_to_usd REAL NOT NULL CHECK (rate_to_usd > 0),
  as_of_date TEXT NOT NULL CHECK (date(as_of_date) = as_of_date)
) STRICT;


-- ---------- Indexes on foreign keys ----------

CREATE INDEX idx_documents_vendor        ON documents(vendor_id);
CREATE INDEX idx_invitations_vendor      ON rfq_invitations(vendor_id);
CREATE INDEX idx_quotes_vendor           ON quotes(vendor_id);
CREATE INDEX idx_quotes_rfq              ON quotes(rfq_id);
CREATE INDEX idx_quotes_supersedes       ON quotes(supersedes_quote_id);
CREATE INDEX idx_line_items_quote        ON quote_line_items(quote_id);
CREATE INDEX idx_legal_vendor            ON legal_documents(vendor_id);
CREATE INDEX idx_legal_expiry            ON legal_documents(expiry_date);
CREATE INDEX idx_po_vendor               ON purchase_orders(vendor_id);
CREATE INDEX idx_invoices_vendor         ON invoices(vendor_id);
CREATE INDEX idx_invoices_po             ON invoices(po_id);