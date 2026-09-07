from datetime import datetime
from pathlib import Path
import sqlite3


CURRENT_SCHEMA_VERSION = 22


MIGRATION_V1_SQL = r"""
DROP VIEW IF EXISTS invoice_drafts;
DROP VIEW IF EXISTS issued_invoices;
DROP VIEW IF EXISTS invoice_lifecycle;
DROP TRIGGER IF EXISTS trg_invoice_sites_only_ndc;
DROP TRIGGER IF EXISTS trg_invoices_ndc_single_line_article;

CREATE TABLE purchase_orders_v1_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    numero_bc TEXT NOT NULL UNIQUE,
    date_bc TEXT,
    mobilis_direction_id INTEGER NOT NULL,
    type_bc TEXT NOT NULL CHECK (
        type_bc IN ('ACQUISITION', 'CONSTRUCTION', 'CONST_ACQUIS', 'NDC', 'MGC')
    ),
    objet TEXT NOT NULL DEFAULT '',
    montant_ttc NUMERIC NOT NULL DEFAULT 0 CHECK (montant_ttc >= 0),
    attachment_path TEXT,
    created_by TEXT NOT NULL DEFAULT '',
    updated_by TEXT NOT NULL DEFAULT '',
    deleted_at TEXT,
    deleted_by TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (mobilis_direction_id) REFERENCES mobilis_directions (id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT
);

INSERT INTO purchase_orders_v1_new (
    id, numero_bc, date_bc, mobilis_direction_id, type_bc, objet,
    montant_ttc, attachment_path, created_by, updated_by, deleted_at,
    deleted_by, created_at, updated_at
)
SELECT
    id, numero_bc, date_bc, mobilis_direction_id, type_bc, objet,
    montant_ttc, attachment_path, created_by, updated_by, deleted_at,
    deleted_by, created_at, updated_at
FROM purchase_orders;

DROP TABLE purchase_orders;
ALTER TABLE purchase_orders_v1_new RENAME TO purchase_orders;

CREATE TRIGGER trg_updated_purchase_orders
AFTER UPDATE ON purchase_orders
FOR EACH ROW
BEGIN
    UPDATE purchase_orders SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TABLE invoices_v1_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_number TEXT,
    invoice_type TEXT CHECK (
        invoice_type IN ('ACQUISITION', 'CONSTRUCTION', 'CONST_ACQUIS', 'NDC')
    ),
    purchase_order_id INTEGER,
    site_id INTEGER,
    invoice_date TEXT,
    total_ht NUMERIC DEFAULT 0 CHECK (total_ht IS NULL OR total_ht >= 0),
    retenue_garantie NUMERIC DEFAULT 0 CHECK (
        retenue_garantie IS NULL OR retenue_garantie >= 0
    ),
    montant_ht_apres_rg NUMERIC DEFAULT 0 CHECK (
        montant_ht_apres_rg IS NULL OR montant_ht_apres_rg >= 0
    ),
    tva NUMERIC DEFAULT 0 CHECK (tva IS NULL OR tva >= 0),
    total_ttc NUMERIC DEFAULT 0 CHECK (total_ttc IS NULL OR total_ttc >= 0),
    montant_en_lettres TEXT NOT NULL DEFAULT '',
    remarque TEXT NOT NULL DEFAULT '',
    depos INTEGER NOT NULL DEFAULT 0 CHECK (depos IN (0, 1)),
    cancelled_at TEXT,
    cancelled_by TEXT NOT NULL DEFAULT '',
    cancellation_reason TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    updated_by TEXT NOT NULL DEFAULT '',
    deleted_at TEXT,
    deleted_by TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (purchase_order_id) REFERENCES purchase_orders (id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,
    FOREIGN KEY (site_id) REFERENCES sites (id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,
    CHECK (
        invoice_type IS NULL
        OR invoice_type <> 'NDC'
        OR site_id IS NULL
    ),
    CHECK (
        cancelled_at IS NULL
        OR length(trim(cancellation_reason)) > 0
    )
);

INSERT INTO invoices_v1_new (
    id, invoice_number, invoice_type, purchase_order_id, site_id,
    invoice_date, total_ht, retenue_garantie, montant_ht_apres_rg,
    tva, total_ttc, montant_en_lettres, remarque, depos,
    created_by, updated_by, deleted_at, deleted_by, created_at, updated_at
)
SELECT
    id, invoice_number, invoice_type, purchase_order_id, site_id,
    invoice_date, total_ht, retenue_garantie, montant_ht_apres_rg,
    tva, total_ttc, montant_en_lettres, remarque, depos,
    created_by, updated_by, deleted_at, deleted_by, created_at, updated_at
FROM invoices;

DROP TABLE invoices;
ALTER TABLE invoices_v1_new RENAME TO invoices;

CREATE UNIQUE INDEX idx_invoices_number_fiscal_year
ON invoices (invoice_number COLLATE NOCASE, substr(invoice_date, 1, 4))
WHERE invoice_number IS NOT NULL
  AND length(trim(invoice_number)) > 0
  AND invoice_date IS NOT NULL;

CREATE UNIQUE INDEX idx_invoices_site_type_regular
ON invoices (site_id, invoice_type)
WHERE invoice_type IN ('ACQUISITION', 'CONSTRUCTION', 'CONST_ACQUIS')
  AND deleted_at IS NULL;

CREATE TRIGGER trg_updated_invoices
AFTER UPDATE ON invoices
FOR EACH ROW
BEGIN
    UPDATE invoices SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TRIGGER trg_invoice_sites_only_ndc
BEFORE INSERT ON invoice_sites
FOR EACH ROW
BEGIN
    SELECT CASE
        WHEN (SELECT invoice_type FROM invoices WHERE id = NEW.invoice_id) <> 'NDC'
        THEN RAISE(ABORT, 'invoice_sites accepts only NDC invoices')
    END;
END;

CREATE TRIGGER trg_invoices_ndc_single_line_article
BEFORE INSERT ON invoice_lines
FOR EACH ROW
WHEN (SELECT invoice_type FROM invoices WHERE id = NEW.invoice_id) = 'NDC'
BEGIN
    SELECT CASE
        WHEN NEW.article_number <> 6
        THEN RAISE(ABORT, 'NDC invoices accept only article 6')
    END;
END;

CREATE TABLE invoice_tracking (
    invoice_id INTEGER PRIMARY KEY,
    date_depot_dtc TEXT,
    date_depot_mobilis TEXT,
    date_ov TEXT,
    legacy_depos INTEGER NOT NULL DEFAULT 0 CHECK (legacy_depos IN (0, 1)),
    migration_review_required INTEGER NOT NULL DEFAULT 0
        CHECK (migration_review_required IN (0, 1)),
    created_by TEXT NOT NULL DEFAULT '',
    updated_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (invoice_id) REFERENCES invoices (id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,
    CHECK (date_depot_dtc IS NULL OR date(date_depot_dtc) = date_depot_dtc),
    CHECK (date_depot_mobilis IS NULL OR date(date_depot_mobilis) = date_depot_mobilis),
    CHECK (date_ov IS NULL OR date(date_ov) = date_ov),
    CHECK (date_depot_mobilis IS NULL OR date_depot_dtc IS NOT NULL),
    CHECK (date_ov IS NULL OR date_depot_mobilis IS NOT NULL),
    CHECK (
        date_depot_mobilis IS NULL
        OR date_depot_mobilis >= date_depot_dtc
    ),
    CHECK (date_ov IS NULL OR date_ov >= date_depot_mobilis)
);

CREATE TABLE invoice_tracking_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    field_name TEXT NOT NULL DEFAULT '',
    old_value TEXT,
    new_value TEXT,
    actor TEXT NOT NULL DEFAULT '',
    device_name TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (invoice_id) REFERENCES invoices (id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT
);

INSERT INTO invoice_tracking (
    invoice_id, legacy_depos, migration_review_required, created_by, updated_by
)
SELECT
    id,
    depos,
    CASE WHEN depos = 1 THEN 1 ELSE 0 END,
    'migration:v1',
    'migration:v1'
FROM invoices;

INSERT INTO invoice_tracking_events (
    invoice_id, event_type, field_name, old_value, new_value,
    actor, reason
)
SELECT
    id,
    'LEGACY_DEPOSIT_IMPORTED',
    'depos',
    '0',
    '1',
    'migration:v1',
    'Date DTC inconnue; verification manuelle requise.'
FROM invoices
WHERE depos = 1;

CREATE INDEX idx_invoice_tracking_dtc ON invoice_tracking(date_depot_dtc);
CREATE INDEX idx_invoice_tracking_mobilis ON invoice_tracking(date_depot_mobilis);
CREATE INDEX idx_invoice_tracking_ov ON invoice_tracking(date_ov);
CREATE INDEX idx_invoice_tracking_events_invoice
ON invoice_tracking_events(invoice_id, created_at DESC);

CREATE TRIGGER trg_invoice_tracking_created
AFTER INSERT ON invoices
FOR EACH ROW
BEGIN
    INSERT OR IGNORE INTO invoice_tracking (
        invoice_id, legacy_depos, migration_review_required,
        created_by, updated_by
    )
    VALUES (
        NEW.id,
        NEW.depos,
        CASE WHEN NEW.depos = 1 THEN 1 ELSE 0 END,
        NEW.created_by,
        NEW.updated_by
    );
END;

CREATE TRIGGER trg_invoice_tracking_updated
AFTER UPDATE ON invoice_tracking
FOR EACH ROW
BEGIN
    UPDATE invoice_tracking
    SET updated_at = CURRENT_TIMESTAMP
    WHERE invoice_id = OLD.invoice_id;
END;

CREATE TRIGGER trg_invoice_tracking_validate_dtc_insert
BEFORE INSERT ON invoice_tracking
FOR EACH ROW
WHEN NEW.date_depot_dtc IS NOT NULL
BEGIN
    SELECT CASE
        WHEN EXISTS (
            SELECT 1 FROM invoices
            WHERE id = NEW.invoice_id
              AND invoice_date IS NOT NULL
              AND NEW.date_depot_dtc < invoice_date
        )
        THEN RAISE(ABORT, 'Date DTC must not precede invoice date')
    END;
END;

CREATE TRIGGER trg_invoice_tracking_validate_dtc_update
BEFORE UPDATE OF date_depot_dtc ON invoice_tracking
FOR EACH ROW
WHEN NEW.date_depot_dtc IS NOT NULL
BEGIN
    SELECT CASE
        WHEN EXISTS (
            SELECT 1 FROM invoices
            WHERE id = NEW.invoice_id
              AND invoice_date IS NOT NULL
              AND NEW.date_depot_dtc < invoice_date
        )
        THEN RAISE(ABORT, 'Date DTC must not precede invoice date')
    END;
END;

CREATE TRIGGER trg_invoice_tracking_sync_legacy_depos
AFTER UPDATE OF date_depot_dtc ON invoice_tracking
FOR EACH ROW
BEGIN
    UPDATE invoices
    SET depos = CASE WHEN NEW.date_depot_dtc IS NULL THEN 0 ELSE 1 END
    WHERE id = NEW.invoice_id;
END;

CREATE TRIGGER trg_invoices_sync_legacy_depos
AFTER UPDATE OF depos ON invoices
FOR EACH ROW
WHEN NEW.depos <> OLD.depos
  AND EXISTS (
      SELECT 1 FROM invoice_tracking
      WHERE invoice_id = NEW.id AND date_depot_dtc IS NULL
  )
BEGIN
    UPDATE invoice_tracking
    SET legacy_depos = NEW.depos,
        migration_review_required = CASE WHEN NEW.depos = 1 THEN 1 ELSE 0 END,
        updated_by = NEW.updated_by
    WHERE invoice_id = NEW.id;
END;

CREATE TRIGGER trg_invoice_tracking_events_no_update
BEFORE UPDATE ON invoice_tracking_events
BEGIN
    SELECT RAISE(ABORT, 'Invoice tracking events are append-only');
END;

CREATE TRIGGER trg_invoice_tracking_events_no_delete
BEFORE DELETE ON invoice_tracking_events
BEGIN
    SELECT RAISE(ABORT, 'Invoice tracking events are append-only');
END;

CREATE VIEW invoice_lifecycle AS
SELECT
    i.*,
    t.date_depot_dtc,
    t.date_depot_mobilis,
    t.date_ov,
    t.legacy_depos,
    t.migration_review_required,
    CASE
        WHEN i.cancelled_at IS NOT NULL THEN 'CANCELLED'
        WHEN i.purchase_order_id IS NULL
          OR i.invoice_type IS NULL
          OR i.invoice_number IS NULL
          OR length(trim(i.invoice_number)) = 0
          OR i.invoice_date IS NULL
          OR COALESCE(i.total_ttc, 0) <= 0
          OR (
              i.invoice_type = 'NDC'
              AND NOT EXISTS (
                  SELECT 1 FROM invoice_sites invoice_site
                  WHERE invoice_site.invoice_id = i.id
              )
          )
          OR (i.invoice_type <> 'NDC' AND i.site_id IS NULL)
        THEN 'BROUILLON'
        WHEN t.date_depot_dtc IS NULL THEN 'READY_DTC'
        WHEN t.date_depot_mobilis IS NULL THEN 'DEPOSITED_DTC'
        WHEN t.date_ov IS NULL THEN 'AWAITING_PAYMENT'
        ELSE 'PAID'
    END AS lifecycle_status,
    CASE WHEN t.date_depot_dtc IS NULL THEN 0 ELSE 1 END AS is_issued,
    CASE
        WHEN t.date_depot_mobilis IS NOT NULL
         AND t.date_ov IS NULL
         AND julianday('now') - julianday(t.date_depot_mobilis) > 60
        THEN 1 ELSE 0
    END AS is_overdue_60_days
FROM invoices i
JOIN invoice_tracking t ON t.invoice_id = i.id;

CREATE VIEW invoice_drafts AS
SELECT * FROM invoice_lifecycle
WHERE date_depot_dtc IS NULL;

CREATE VIEW issued_invoices AS
SELECT * FROM invoice_lifecycle
WHERE date_depot_dtc IS NOT NULL;
"""


MIGRATION_V2_SQL = r"""
CREATE TABLE permissions (
    code TEXT PRIMARY KEY,
    description TEXT NOT NULL DEFAULT ''
);

CREATE TABLE role_permissions (
    role TEXT NOT NULL CHECK (role IN ('admin', 'editor', 'viewer')),
    permission_code TEXT NOT NULL,
    PRIMARY KEY (role, permission_code),
    FOREIGN KEY (permission_code) REFERENCES permissions(code)
        ON UPDATE CASCADE
        ON DELETE CASCADE
);

CREATE TABLE user_permissions (
    user_id INTEGER NOT NULL,
    permission_code TEXT NOT NULL,
    allowed INTEGER NOT NULL CHECK (allowed IN (0, 1)),
    updated_by TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, permission_code),
    FOREIGN KEY (user_id) REFERENCES users(id)
        ON UPDATE CASCADE
        ON DELETE CASCADE,
    FOREIGN KEY (permission_code) REFERENCES permissions(code)
        ON UPDATE CASCADE
        ON DELETE CASCADE
);

INSERT INTO permissions(code, description) VALUES
    ('invoice.read', 'Consulter les factures'),
    ('invoice.create', 'Creer une facture ou un brouillon'),
    ('invoice.edit_draft', 'Modifier une facture avant depot DTC'),
    ('invoice.deposit_dtc', 'Enregistrer le depot DTC'),
    ('invoice.deposit_mobilis', 'Enregistrer le depot Mobilis'),
    ('invoice.mark_paid', 'Enregistrer la date OV'),
    ('invoice.unlock', 'Modifier exceptionnellement une facture verrouillee'),
    ('invoice.cancel', 'Annuler ou restaurer une facture'),
    ('invoice.export', 'Exporter les factures'),
    ('audit.read', 'Consulter le journal audit');

INSERT INTO role_permissions(role, permission_code)
SELECT 'admin', code FROM permissions;

INSERT INTO role_permissions(role, permission_code) VALUES
    ('editor', 'invoice.read'),
    ('editor', 'invoice.create'),
    ('editor', 'invoice.edit_draft'),
    ('editor', 'invoice.deposit_dtc'),
    ('editor', 'invoice.deposit_mobilis'),
    ('editor', 'invoice.mark_paid'),
    ('editor', 'invoice.export'),
    ('viewer', 'invoice.read');

CREATE TABLE invoice_unlock_authorizations (
    invoice_id INTEGER PRIMARY KEY,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (invoice_id) REFERENCES invoices(id)
        ON UPDATE CASCADE
        ON DELETE CASCADE
);

CREATE TRIGGER trg_invoices_lock_issued_header
BEFORE UPDATE OF
    invoice_number, invoice_type, purchase_order_id, site_id, invoice_date,
    total_ht, retenue_garantie, montant_ht_apres_rg, tva, total_ttc,
    montant_en_lettres
ON invoices
FOR EACH ROW
WHEN EXISTS (
    SELECT 1 FROM invoice_tracking
    WHERE invoice_id = OLD.id AND date_depot_dtc IS NOT NULL
)
AND NOT EXISTS (
    SELECT 1 FROM invoice_unlock_authorizations
    WHERE invoice_id = OLD.id
)
BEGIN
    SELECT RAISE(ABORT, 'Issued invoice is locked');
END;

CREATE TRIGGER trg_invoices_no_delete_issued
BEFORE UPDATE OF deleted_at ON invoices
FOR EACH ROW
WHEN OLD.deleted_at IS NULL
  AND NEW.deleted_at IS NOT NULL
  AND EXISTS (
      SELECT 1 FROM invoice_tracking
      WHERE invoice_id = OLD.id AND date_depot_dtc IS NOT NULL
  )
BEGIN
    SELECT RAISE(ABORT, 'Issued invoice cannot be deleted; cancel it instead');
END;

CREATE TRIGGER trg_invoices_no_hard_delete_issued
BEFORE DELETE ON invoices
FOR EACH ROW
WHEN EXISTS (
    SELECT 1 FROM invoice_tracking
    WHERE invoice_id = OLD.id AND date_depot_dtc IS NOT NULL
)
BEGIN
    SELECT RAISE(ABORT, 'Issued invoice cannot be deleted; cancel it instead');
END;

CREATE TRIGGER trg_invoice_lines_lock_insert
BEFORE INSERT ON invoice_lines
FOR EACH ROW
WHEN EXISTS (
    SELECT 1 FROM invoice_tracking
    WHERE invoice_id = NEW.invoice_id AND date_depot_dtc IS NOT NULL
)
AND NOT EXISTS (
    SELECT 1 FROM invoice_unlock_authorizations
    WHERE invoice_id = NEW.invoice_id
)
BEGIN
    SELECT RAISE(ABORT, 'Issued invoice lines are locked');
END;

CREATE TRIGGER trg_invoice_lines_lock_update
BEFORE UPDATE ON invoice_lines
FOR EACH ROW
WHEN EXISTS (
    SELECT 1 FROM invoice_tracking
    WHERE invoice_id = OLD.invoice_id AND date_depot_dtc IS NOT NULL
)
AND NOT EXISTS (
    SELECT 1 FROM invoice_unlock_authorizations
    WHERE invoice_id = OLD.invoice_id
)
BEGIN
    SELECT RAISE(ABORT, 'Issued invoice lines are locked');
END;

CREATE TRIGGER trg_invoice_lines_lock_delete
BEFORE DELETE ON invoice_lines
FOR EACH ROW
WHEN EXISTS (
    SELECT 1 FROM invoice_tracking
    WHERE invoice_id = OLD.invoice_id AND date_depot_dtc IS NOT NULL
)
AND NOT EXISTS (
    SELECT 1 FROM invoice_unlock_authorizations
    WHERE invoice_id = OLD.invoice_id
)
BEGIN
    SELECT RAISE(ABORT, 'Issued invoice lines are locked');
END;

CREATE TRIGGER trg_invoice_sites_lock_insert
BEFORE INSERT ON invoice_sites
FOR EACH ROW
WHEN EXISTS (
    SELECT 1 FROM invoice_tracking
    WHERE invoice_id = NEW.invoice_id AND date_depot_dtc IS NOT NULL
)
AND NOT EXISTS (
    SELECT 1 FROM invoice_unlock_authorizations
    WHERE invoice_id = NEW.invoice_id
)
BEGIN
    SELECT RAISE(ABORT, 'Issued invoice sites are locked');
END;

CREATE TRIGGER trg_invoice_sites_lock_update
BEFORE UPDATE ON invoice_sites
FOR EACH ROW
WHEN EXISTS (
    SELECT 1 FROM invoice_tracking
    WHERE invoice_id = OLD.invoice_id AND date_depot_dtc IS NOT NULL
)
AND NOT EXISTS (
    SELECT 1 FROM invoice_unlock_authorizations
    WHERE invoice_id = OLD.invoice_id
)
BEGIN
    SELECT RAISE(ABORT, 'Issued invoice sites are locked');
END;

CREATE TRIGGER trg_invoice_sites_lock_delete
BEFORE DELETE ON invoice_sites
FOR EACH ROW
WHEN EXISTS (
    SELECT 1 FROM invoice_tracking
    WHERE invoice_id = OLD.invoice_id AND date_depot_dtc IS NOT NULL
)
AND NOT EXISTS (
    SELECT 1 FROM invoice_unlock_authorizations
    WHERE invoice_id = OLD.invoice_id
)
BEGIN
    SELECT RAISE(ABORT, 'Issued invoice sites are locked');
END;

CREATE TRIGGER trg_invoice_tracking_no_cancelled_update
BEFORE UPDATE OF date_depot_dtc, date_depot_mobilis, date_ov
ON invoice_tracking
FOR EACH ROW
WHEN EXISTS (
    SELECT 1 FROM invoices
    WHERE id = OLD.invoice_id AND cancelled_at IS NOT NULL
)
BEGIN
    SELECT RAISE(ABORT, 'Cancelled invoice tracking is locked');
END;
"""

MIGRATION_V3_SQL = r"""
CREATE TABLE bpu_st_mapping (
    st_article_number INTEGER PRIMARY KEY,
    general_article_number INTEGER NOT NULL UNIQUE,
    mapping_version TEXT NOT NULL DEFAULT 'BPU-ST-1',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (general_article_number) REFERENCES bpu_items(article_number)
        ON UPDATE CASCADE ON DELETE CASCADE
);

ALTER TABLE invoices ADD COLUMN numbering_system TEXT NOT NULL DEFAULT 'GENERAL'
    CHECK (numbering_system IN ('GENERAL', 'ST'));
ALTER TABLE invoice_lines ADD COLUMN source_reference_type TEXT NOT NULL DEFAULT 'GENERAL'
    CHECK (source_reference_type IN ('GENERAL', 'ST', 'GENERAL_FALLBACK'));
ALTER TABLE invoice_lines ADD COLUMN source_st_number INTEGER;

CREATE INDEX idx_bpu_st_mapping_general ON bpu_st_mapping(general_article_number);
CREATE INDEX idx_invoice_lines_source_st ON invoice_lines(source_st_number);

CREATE TRIGGER trg_invoice_line_source_insert
BEFORE INSERT ON invoice_lines
FOR EACH ROW
BEGIN
    SELECT CASE
        WHEN NEW.source_reference_type = 'ST' AND NEW.source_st_number IS NULL
            THEN RAISE(ABORT, 'ST source requires an ST article number')
        WHEN NEW.source_reference_type <> 'ST' AND NEW.source_st_number IS NOT NULL
            THEN RAISE(ABORT, 'Only ST source accepts an ST article number')
    END;
END;

CREATE TRIGGER trg_invoice_line_source_update
BEFORE UPDATE OF source_reference_type, source_st_number ON invoice_lines
FOR EACH ROW
BEGIN
    SELECT CASE
        WHEN NEW.source_reference_type = 'ST' AND NEW.source_st_number IS NULL
            THEN RAISE(ABORT, 'ST source requires an ST article number')
        WHEN NEW.source_reference_type <> 'ST' AND NEW.source_st_number IS NOT NULL
            THEN RAISE(ABORT, 'Only ST source accepts an ST article number')
    END;
END;
"""


MIGRATION_V4_SQL = r"""
ALTER TABLE invoices ADD COLUMN typologie_snapshot TEXT NOT NULL DEFAULT '';

UPDATE invoices
SET typologie_snapshot = CASE
    WHEN invoice_type = 'NDC' THEN 'NDC'
    WHEN EXISTS (
        SELECT 1 FROM purchase_orders po
        WHERE po.id=invoices.purchase_order_id AND po.type_bc='MGC'
    ) THEN 'MGC'
    ELSE COALESCE((SELECT typologie_site FROM sites WHERE id=invoices.site_id), '')
END;

CREATE UNIQUE INDEX idx_invoices_one_active_ndc_per_po
ON invoices(purchase_order_id)
WHERE invoice_type='NDC' AND deleted_at IS NULL AND cancelled_at IS NULL;

CREATE INDEX idx_invoices_typologie_snapshot
ON invoices(typologie_snapshot);
"""


MIGRATION_V5_SQL = r"""
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    details TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE company_branches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    code TEXT NOT NULL UNIQUE,
    address TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_by TEXT NOT NULL DEFAULT '',
    updated_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO company_branches(id, name, code, created_by, updated_by)
VALUES(1, 'Direction générale', 'DG', 'migration:v5', 'migration:v5');

CREATE TABLE clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raison_sociale TEXT NOT NULL,
    rgc TEXT NOT NULL DEFAULT '',
    nif TEXT NOT NULL DEFAULT '',
    adresse TEXT NOT NULL DEFAULT '',
    logo_path TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_by TEXT NOT NULL DEFAULT '',
    updated_by TEXT NOT NULL DEFAULT '',
    deleted_at TEXT,
    deleted_by TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO clients(
    id, raison_sociale, rgc, nif, adresse, logo_path, created_by, updated_by
)
SELECT
    1,
    COALESCE(NULLIF((SELECT doit FROM mobilis_client_settings WHERE id=1), ''),
             NULLIF((SELECT doit_nom FROM mobilis_directions WHERE deleted_at IS NULL ORDER BY id LIMIT 1), ''),
             'Mobilis'),
    COALESCE((SELECT rgc FROM mobilis_directions WHERE deleted_at IS NULL AND trim(rgc)<>'' ORDER BY id LIMIT 1), ''),
    COALESCE(NULLIF((SELECT nif FROM mobilis_client_settings WHERE id=1), ''),
             (SELECT nif FROM mobilis_directions WHERE deleted_at IS NULL AND trim(nif)<>'' ORDER BY id LIMIT 1), ''),
    COALESCE((SELECT adresse FROM mobilis_directions WHERE deleted_at IS NULL AND trim(adresse)<>'' ORDER BY id LIMIT 1), ''),
    COALESCE((SELECT logo_path FROM mobilis_directions WHERE deleted_at IS NULL AND trim(COALESCE(logo_path,''))<>'' ORDER BY id LIMIT 1), ''),
    'migration:v5', 'migration:v5';

CREATE TABLE client_directions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    company_branch_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    address TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    legacy_mobilis_direction_id INTEGER UNIQUE,
    created_by TEXT NOT NULL DEFAULT '',
    updated_by TEXT NOT NULL DEFAULT '',
    deleted_at TEXT,
    deleted_by TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(client_id) REFERENCES clients(id) ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY(company_branch_id) REFERENCES company_branches(id) ON UPDATE CASCADE ON DELETE RESTRICT
);

INSERT INTO client_directions(
    id, client_id, company_branch_id, name, address,
    legacy_mobilis_direction_id, created_by, updated_by, deleted_at, deleted_by,
    created_at, updated_at
)
SELECT
    id, 1, 1, direction_regionale, adresse, id,
    created_by, updated_by, deleted_at, deleted_by, created_at, updated_at
FROM mobilis_directions;

ALTER TABLE purchase_orders ADD COLUMN client_direction_id INTEGER REFERENCES client_directions(id);
ALTER TABLE purchase_orders ADD COLUMN company_branch_id INTEGER REFERENCES company_branches(id);
UPDATE purchase_orders
SET client_direction_id=mobilis_direction_id,
    company_branch_id=COALESCE((SELECT company_branch_id FROM client_directions WHERE id=mobilis_direction_id), 1);
CREATE INDEX idx_purchase_orders_client_direction ON purchase_orders(client_direction_id);
CREATE INDEX idx_purchase_orders_company_branch ON purchase_orders(company_branch_id);

ALTER TABLE audit_log ADD COLUMN company_branch_id INTEGER REFERENCES company_branches(id);

CREATE TABLE users_v5_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('super_admin', 'admin', 'editor', 'viewer')),
    company_branch_id INTEGER,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    must_change_password INTEGER NOT NULL DEFAULT 0 CHECK (must_change_password IN (0, 1)),
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT,
    last_login_at TEXT,
    FOREIGN KEY(company_branch_id) REFERENCES company_branches(id) ON UPDATE CASCADE ON DELETE RESTRICT,
    CHECK (role='super_admin' OR company_branch_id IS NOT NULL)
);

INSERT INTO users_v5_new(
    id, username, password_hash, role, company_branch_id, is_active,
    created_at, updated_at, must_change_password, failed_attempts,
    locked_until, last_login_at
)
SELECT
    id, username, password_hash,
    CASE WHEN role='admin' THEN 'super_admin' ELSE role END,
    CASE WHEN role='admin' THEN NULL ELSE 1 END,
    is_active, created_at, updated_at, must_change_password, failed_attempts,
    locked_until, last_login_at
FROM users;
DROP TABLE users;
ALTER TABLE users_v5_new RENAME TO users;

CREATE TABLE role_permissions_v5_new (
    role TEXT NOT NULL CHECK (role IN ('super_admin', 'admin', 'editor', 'viewer')),
    permission_code TEXT NOT NULL,
    PRIMARY KEY(role, permission_code),
    FOREIGN KEY(permission_code) REFERENCES permissions(code) ON UPDATE CASCADE ON DELETE CASCADE
);
INSERT INTO role_permissions_v5_new(role, permission_code)
SELECT CASE WHEN role='admin' THEN 'super_admin' ELSE role END, permission_code
FROM role_permissions;
DROP TABLE role_permissions;
ALTER TABLE role_permissions_v5_new RENAME TO role_permissions;

INSERT OR IGNORE INTO role_permissions(role, permission_code)
SELECT 'admin', permission_code FROM role_permissions WHERE role='super_admin';

INSERT OR IGNORE INTO permissions(code, description) VALUES
    ('client.manage', 'Gérer les clients et leurs directions'),
    ('company_branch.manage', 'Gérer les directions de l’entreprise'),
    ('purchase_order.edit', 'Modifier les bons de commande'),
    ('bpu_st.manage', 'Importer et activer les versions BPU ST');
INSERT OR IGNORE INTO role_permissions(role, permission_code)
SELECT 'super_admin', code FROM permissions;
INSERT OR IGNORE INTO role_permissions(role, permission_code) VALUES
    ('admin', 'purchase_order.edit');

CREATE TRIGGER trg_company_branches_updated
AFTER UPDATE ON company_branches BEGIN
    UPDATE company_branches SET updated_at=CURRENT_TIMESTAMP WHERE id=OLD.id;
END;
CREATE TRIGGER trg_clients_updated
AFTER UPDATE ON clients BEGIN
    UPDATE clients SET updated_at=CURRENT_TIMESTAMP WHERE id=OLD.id;
END;
CREATE TRIGGER trg_client_directions_updated
AFTER UPDATE ON client_directions BEGIN
    UPDATE client_directions SET updated_at=CURRENT_TIMESTAMP WHERE id=OLD.id;
END;
"""


MIGRATION_V6_SQL = r"""
CREATE TABLE bpu_st_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    source_filename TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'ACTIVE', 'ARCHIVED')),
    total_rows INTEGER NOT NULL DEFAULT 0,
    mapped_rows INTEGER NOT NULL DEFAULT 0,
    imported_by TEXT NOT NULL DEFAULT '',
    activated_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    activated_at TEXT,
    CHECK (mapped_rows >= 0 AND total_rows >= mapped_rows)
);

INSERT INTO bpu_st_versions(code, status, total_rows, mapped_rows, imported_by, activated_by, activated_at)
SELECT COALESCE(MAX(mapping_version), 'BPU-ST-1'), 'ACTIVE', COUNT(*), COUNT(*),
       'migration:v6', 'migration:v6', CURRENT_TIMESTAMP
FROM bpu_st_mapping;

CREATE TABLE bpu_st_items (
    version_id INTEGER NOT NULL,
    st_article_number INTEGER NOT NULL,
    designation TEXT NOT NULL DEFAULT '',
    unite TEXT NOT NULL DEFAULT '',
    source_price NUMERIC,
    general_article_number INTEGER,
    confidence NUMERIC NOT NULL DEFAULT 0,
    review_status TEXT NOT NULL DEFAULT 'PENDING' CHECK (review_status IN ('PENDING', 'CONFIRMED')),
    PRIMARY KEY(version_id, st_article_number),
    FOREIGN KEY(version_id) REFERENCES bpu_st_versions(id) ON DELETE CASCADE,
    FOREIGN KEY(general_article_number) REFERENCES bpu_items(article_number) ON UPDATE CASCADE ON DELETE RESTRICT
);

INSERT INTO bpu_st_items(
    version_id, st_article_number, designation, unite, source_price,
    general_article_number, confidence, review_status
)
SELECT v.id, m.st_article_number, b.designation, b.unite, NULL,
       m.general_article_number, 1.0, 'CONFIRMED'
FROM bpu_st_mapping m
JOIN bpu_st_versions v ON v.code=m.mapping_version
JOIN bpu_items b ON b.article_number=m.general_article_number;

DROP TABLE bpu_st_mapping;
CREATE VIEW bpu_st_mapping AS
SELECT item.st_article_number, item.general_article_number, version.code AS mapping_version,
       version.created_at
FROM bpu_st_items item
JOIN bpu_st_versions version ON version.id=item.version_id
WHERE version.status='ACTIVE' AND item.review_status='CONFIRMED';

ALTER TABLE invoice_lines ADD COLUMN mapping_version TEXT NOT NULL DEFAULT '';
UPDATE invoice_lines
SET mapping_version=COALESCE((SELECT code FROM bpu_st_versions WHERE status='ACTIVE' LIMIT 1), '')
WHERE source_reference_type='ST';

CREATE UNIQUE INDEX idx_bpu_st_one_active_version
ON bpu_st_versions(status) WHERE status='ACTIVE';
CREATE INDEX idx_bpu_st_items_general
ON bpu_st_items(version_id, general_article_number);
"""


MIGRATION_V7_SQL = r"""
UPDATE sites
SET typologie_site = CASE
    WHEN upper(trim(typologie_site)) IN ('A9', 'A12', 'A15', 'PYLONE', 'COLLOC')
        THEN upper(trim(typologie_site))
    WHEN upper(typologie_site) LIKE '%COLO%' THEN 'COLLOC'
    WHEN upper(typologie_site) LIKE '%PYL%' THEN 'PYLONE'
    WHEN upper(typologie_site) LIKE '%A15%' THEN 'A15'
    WHEN upper(typologie_site) LIKE '%A12%' THEN 'A12'
    WHEN upper(typologie_site) LIKE '%A9%' THEN 'A9'
    ELSE ''
END;

UPDATE invoices
SET typologie_snapshot = CASE
    WHEN invoice_type='NDC' THEN 'NDC'
    WHEN EXISTS (
        SELECT 1 FROM purchase_orders po
        WHERE po.id=invoices.purchase_order_id AND po.type_bc='MGC'
    ) THEN 'MGC'
    ELSE COALESCE((SELECT typologie_site FROM sites WHERE id=invoices.site_id), '')
END;
"""


MIGRATION_V8_SQL = r"""
UPDATE sites
SET typologie_site='COLLOC'
WHERE upper(typologie_site) LIKE '%COLO%';

UPDATE invoices
SET typologie_snapshot='COLLOC'
WHERE invoice_type<>'NDC'
  AND EXISTS (
      SELECT 1 FROM sites
      WHERE sites.id=invoices.site_id AND sites.typologie_site='COLLOC'
  );
"""


MIGRATION_V9_SQL = r"""
INSERT INTO client_directions(client_id, company_branch_id, name, created_by, updated_by)
SELECT 1, 1, 'Direction principale', 'migration:v9', 'migration:v9'
WHERE NOT EXISTS (SELECT 1 FROM client_directions);

DROP TRIGGER IF EXISTS trg_updated_purchase_orders;

CREATE TABLE purchase_orders_v9_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    numero_bc TEXT NOT NULL UNIQUE,
    date_bc TEXT,
    mobilis_direction_id INTEGER,
    client_direction_id INTEGER NOT NULL DEFAULT 1,
    company_branch_id INTEGER NOT NULL DEFAULT 1,
    type_bc TEXT NOT NULL CHECK (type_bc IN ('ACQUISITION', 'CONSTRUCTION', 'CONST_ACQUIS', 'NDC', 'MGC')),
    objet TEXT NOT NULL DEFAULT '',
    montant_ttc NUMERIC NOT NULL DEFAULT 0 CHECK (montant_ttc >= 0),
    attachment_path TEXT,
    created_by TEXT NOT NULL DEFAULT '',
    updated_by TEXT NOT NULL DEFAULT '',
    deleted_at TEXT,
    deleted_by TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(client_direction_id) REFERENCES client_directions(id) ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY(company_branch_id) REFERENCES company_branches(id) ON UPDATE CASCADE ON DELETE RESTRICT
);

INSERT INTO purchase_orders_v9_new(
    id, numero_bc, date_bc, mobilis_direction_id, client_direction_id,
    company_branch_id, type_bc, objet, montant_ttc, attachment_path,
    created_by, updated_by, deleted_at, deleted_by, created_at, updated_at
)
SELECT id, numero_bc, date_bc, mobilis_direction_id, client_direction_id,
       company_branch_id, type_bc, objet, montant_ttc, attachment_path,
       created_by, updated_by, deleted_at, deleted_by, created_at, updated_at
FROM purchase_orders;

DROP TABLE purchase_orders;
ALTER TABLE purchase_orders_v9_new RENAME TO purchase_orders;
CREATE INDEX idx_purchase_orders_client_direction ON purchase_orders(client_direction_id);
CREATE INDEX idx_purchase_orders_company_branch ON purchase_orders(company_branch_id);
CREATE TRIGGER trg_updated_purchase_orders
AFTER UPDATE ON purchase_orders
FOR EACH ROW BEGIN
    UPDATE purchase_orders SET updated_at=CURRENT_TIMESTAMP WHERE id=OLD.id;
END;
"""


MIGRATION_V10_SQL = r"""
UPDATE clients
SET raison_sociale = replace(
    replace(
      replace(lower(raison_sociale), 'alg�rie', 'Algérie'),
      't�l�com', 'Télécom'
    ),
    'mobil/mobilis', 'Mobile / Mobilis'
)
WHERE raison_sociale LIKE '%�%';

UPDATE client_directions
SET name = replace(
    replace(name, 'r�gionale', 'régionale'),
    'cit�', 'cité'
)
WHERE name LIKE '%�%';
"""


MIGRATION_V11_SQL = r"""
UPDATE clients
SET raison_sociale='Algérie Télécom Mobile / Mobilis'
WHERE lower(raison_sociale) LIKE 'alg_rie t_l_com mobil/mobilis';
"""


MIGRATION_V12_SQL = r"""
ALTER TABLE company_branches ADD COLUMN sigle TEXT NOT NULL DEFAULT '';
UPDATE company_branches
SET sigle=upper(trim(COALESCE(NULLIF(code, ''), 'DIR-' || printf('%03d', id))));

ALTER TABLE client_directions ADD COLUMN sigle TEXT NOT NULL DEFAULT '';
UPDATE client_directions
SET sigle='DR-' || printf('%03d', id);

CREATE UNIQUE INDEX idx_company_branches_sigle_unique
ON company_branches(sigle COLLATE NOCASE);
CREATE UNIQUE INDEX idx_client_directions_sigle_unique
ON client_directions(client_id, sigle COLLATE NOCASE)
WHERE deleted_at IS NULL;

CREATE TRIGGER trg_company_branches_sigle_insert
BEFORE INSERT ON company_branches
WHEN trim(NEW.sigle)='' OR NEW.sigle<>upper(NEW.sigle)
     OR NEW.sigle GLOB '*[^A-Z0-9 -]*'
BEGIN
    SELECT RAISE(ABORT, 'Sigle direction entreprise invalide');
END;
CREATE TRIGGER trg_company_branches_sigle_update
BEFORE UPDATE OF sigle ON company_branches
WHEN trim(NEW.sigle)='' OR NEW.sigle<>upper(NEW.sigle)
     OR NEW.sigle GLOB '*[^A-Z0-9 -]*'
BEGIN
    SELECT RAISE(ABORT, 'Sigle direction entreprise invalide');
END;
CREATE TRIGGER trg_client_directions_sigle_insert
BEFORE INSERT ON client_directions
WHEN trim(NEW.sigle)='' OR NEW.sigle<>upper(NEW.sigle)
     OR NEW.sigle GLOB '*[^A-Z0-9 -]*'
BEGIN
    SELECT RAISE(ABORT, 'Sigle direction client invalide');
END;
CREATE TRIGGER trg_client_directions_sigle_update
BEFORE UPDATE OF sigle ON client_directions
WHEN trim(NEW.sigle)='' OR NEW.sigle<>upper(NEW.sigle)
     OR NEW.sigle GLOB '*[^A-Z0-9 -]*'
BEGIN
    SELECT RAISE(ABORT, 'Sigle direction client invalide');
END;
"""


MIGRATION_V13_SQL = r"""
UPDATE clients
SET raison_sociale='Algérie Télécom Mobile / Mobilis'
WHERE upper(raison_sociale) LIKE '%MOBILIS%';

UPDATE company_branches
SET name='Direction générale'
WHERE upper(code)='DG';
UPDATE company_branches
SET name='Direction Régionale Ouest', sigle='DRO', code='DRO'
WHERE upper(COALESCE(code,''))='DRO' OR upper(name) LIKE '%OUEST%';

UPDATE client_directions
SET name='Direction Régionale Mobilis Chlef', sigle='DR CHLEF'
WHERE upper(name) LIKE '%CHLEF%';
UPDATE client_directions
SET name='Direction Régionale Mobilis Oran', sigle='DR ORAN'
WHERE upper(name) LIKE '%ORAN%';
UPDATE client_directions
SET name='Direction Régionale Mobilis Cité Ben Souna', sigle='DR CBS'
WHERE upper(name) LIKE '%BEN SOUNA%';
"""


MIGRATION_V14_SQL = r"""
ALTER TABLE clients ADD COLUMN sigle TEXT NOT NULL DEFAULT '';
UPDATE clients
SET sigle = CASE
    WHEN upper(raison_sociale) LIKE '%MOBILIS%' THEN 'ATM Mobilis'
    ELSE 'CLIENT-' || printf('%03d', id)
END;

CREATE UNIQUE INDEX idx_clients_sigle_unique
ON clients(sigle COLLATE NOCASE)
WHERE deleted_at IS NULL;

CREATE TRIGGER trg_clients_sigle_insert_length
BEFORE INSERT ON clients
WHEN length(trim(NEW.sigle))>30
BEGIN
    SELECT RAISE(ABORT, 'Sigle client invalide');
END;

CREATE TRIGGER trg_clients_sigle_insert_default
AFTER INSERT ON clients
WHEN trim(NEW.sigle)=''
BEGIN
    UPDATE clients
    SET sigle='CLIENT-' || printf('%03d', NEW.id)
    WHERE id=NEW.id;
END;

CREATE TRIGGER trg_clients_sigle_update
BEFORE UPDATE OF sigle ON clients
WHEN trim(NEW.sigle)='' OR length(trim(NEW.sigle))>30
BEGIN
    SELECT RAISE(ABORT, 'Sigle client invalide');
END;
"""


MIGRATION_V15_SQL = r"""
CREATE TABLE typologies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sigle TEXT NOT NULL COLLATE NOCASE UNIQUE,
    libelle_complet TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_by TEXT NOT NULL DEFAULT '',
    updated_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (length(trim(sigle)) BETWEEN 1 AND 30),
    CHECK (length(trim(libelle_complet)) BETWEEN 1 AND 250)
);

INSERT INTO typologies(sigle, libelle_complet, created_by, updated_by) VALUES
('A9', 'A9', 'migration:v15', 'migration:v15'),
('A12', 'A12 ( MAT 12M + BTS OUTDOOR )', 'migration:v15', 'migration:v15'),
('A15', 'A15', 'migration:v15', 'migration:v15'),
('PYLONE', 'PYLONE', 'migration:v15', 'migration:v15'),
('COLLOC', 'COLLOC', 'migration:v15', 'migration:v15'),
('MGC', 'MGC', 'migration:v15', 'migration:v15'),
('NDC', 'NDC', 'migration:v15', 'migration:v15');

ALTER TABLE sites ADD COLUMN typology_id INTEGER REFERENCES typologies(id)
    ON UPDATE CASCADE ON DELETE RESTRICT;
UPDATE sites
SET typology_id=(
    SELECT t.id FROM typologies t
    WHERE t.sigle=upper(trim(sites.typologie_site)) COLLATE NOCASE
)
WHERE trim(COALESCE(typologie_site, ''))<>'';
CREATE INDEX idx_sites_typology ON sites(typology_id);

ALTER TABLE invoices ADD COLUMN typologie_label_snapshot TEXT NOT NULL DEFAULT '';
UPDATE invoices
SET typologie_label_snapshot=COALESCE(
    (SELECT t.libelle_complet FROM typologies t
     WHERE t.sigle=invoices.typologie_snapshot COLLATE NOCASE),
    typologie_snapshot,
    ''
);

CREATE TRIGGER trg_typologies_updated
AFTER UPDATE ON typologies
FOR EACH ROW BEGIN
    UPDATE typologies SET updated_at=CURRENT_TIMESTAMP WHERE id=OLD.id;
END;
"""


MIGRATION_V16_SQL = r"""
CREATE TABLE subcontractors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raison_sociale TEXT NOT NULL,
    sigle TEXT NOT NULL COLLATE NOCASE UNIQUE,
    telephone TEXT NOT NULL DEFAULT '',
    adresse TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_by TEXT NOT NULL DEFAULT '',
    updated_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (length(trim(raison_sociale)) BETWEEN 1 AND 160),
    CHECK (length(trim(sigle)) BETWEEN 1 AND 40)
);

CREATE TABLE design_offices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raison_sociale TEXT NOT NULL,
    sigle TEXT NOT NULL COLLATE NOCASE UNIQUE,
    telephone TEXT NOT NULL DEFAULT '',
    adresse TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_by TEXT NOT NULL DEFAULT '',
    updated_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (length(trim(raison_sociale)) BETWEEN 1 AND 160),
    CHECK (length(trim(sigle)) BETWEEN 1 AND 40)
);

INSERT INTO design_offices(raison_sociale, sigle, created_by, updated_by)
SELECT bet, 'BET-' || printf('%03d', ROW_NUMBER() OVER (ORDER BY upper(trim(bet)))),
       'migration:v16', 'migration:v16'
FROM (SELECT DISTINCT trim(bet) AS bet FROM sites WHERE trim(COALESCE(bet, ''))<>'')
ORDER BY upper(bet);

ALTER TABLE sites ADD COLUMN subcontractor_id INTEGER REFERENCES subcontractors(id)
    ON UPDATE CASCADE ON DELETE RESTRICT;
ALTER TABLE sites ADD COLUMN design_office_id INTEGER REFERENCES design_offices(id)
    ON UPDATE CASCADE ON DELETE RESTRICT;

UPDATE sites
SET design_office_id=(
    SELECT d.id FROM design_offices d
    WHERE d.raison_sociale=trim(sites.bet) COLLATE NOCASE
)
WHERE trim(COALESCE(bet, ''))<>'';

CREATE INDEX idx_sites_subcontractor ON sites(subcontractor_id);
CREATE INDEX idx_sites_design_office ON sites(design_office_id);

CREATE TRIGGER trg_subcontractors_updated AFTER UPDATE ON subcontractors
FOR EACH ROW BEGIN
    UPDATE subcontractors SET updated_at=CURRENT_TIMESTAMP WHERE id=OLD.id;
END;
CREATE TRIGGER trg_design_offices_updated AFTER UPDATE ON design_offices
FOR EACH ROW BEGIN
    UPDATE design_offices SET updated_at=CURRENT_TIMESTAMP WHERE id=OLD.id;
END;
"""


MIGRATION_V17_SQL = r"""
ALTER TABLE subcontractors ADD COLUMN contact TEXT NOT NULL DEFAULT '';
ALTER TABLE subcontractors ADD COLUMN email TEXT NOT NULL DEFAULT '';
ALTER TABLE design_offices ADD COLUMN contact TEXT NOT NULL DEFAULT '';
ALTER TABLE design_offices ADD COLUMN email TEXT NOT NULL DEFAULT '';

CREATE TABLE data_import_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token TEXT NOT NULL UNIQUE,
    import_type TEXT NOT NULL CHECK(import_type IN ('subcontractors', 'design_offices', 'purchase_orders')),
    source_filename TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL,
    error_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'preview' CHECK(status IN ('preview', 'applied', 'rejected')),
    created_by TEXT NOT NULL DEFAULT '',
    applied_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    applied_at TEXT
);

CREATE INDEX idx_data_import_batches_created_at
ON data_import_batches(created_at);
"""


MIGRATION_V18_SQL = r"""
UPDATE users
SET must_change_password=1,
    updated_at=CURRENT_TIMESTAMP
WHERE username='admin'
  AND role='super_admin'
  AND is_active=1;
"""


MIGRATION_V19_SQL = r"""
ALTER TABLE invoices ADD COLUMN rg_rate NUMERIC NOT NULL DEFAULT 5.00
    CHECK (rg_rate >= 0 AND rg_rate <= 100);
ALTER TABLE invoices ADD COLUMN tva_rate NUMERIC NOT NULL DEFAULT 19.00
    CHECK (tva_rate >= 0 AND tva_rate <= 100);

UPDATE invoices SET rg_rate=5.00, tva_rate=19.00;
"""


MIGRATION_V20_SQL = r"""
DROP VIEW IF EXISTS invoice_drafts;
DROP VIEW IF EXISTS issued_invoices;
DROP VIEW IF EXISTS invoice_lifecycle;

ALTER TABLE invoice_tracking
ADD COLUMN numero_ordre_virement TEXT NOT NULL DEFAULT ''
    CHECK (length(numero_ordre_virement) <= 120);

CREATE VIEW invoice_lifecycle AS
SELECT
    i.*,
    t.date_depot_dtc,
    t.date_depot_mobilis,
    t.date_ov,
    t.numero_ordre_virement,
    t.legacy_depos,
    t.migration_review_required,
    CASE
        WHEN i.cancelled_at IS NOT NULL THEN 'CANCELLED'
        WHEN i.purchase_order_id IS NULL
          OR i.invoice_type IS NULL
          OR i.invoice_number IS NULL
          OR length(trim(i.invoice_number)) = 0
          OR i.invoice_date IS NULL
          OR COALESCE(i.total_ttc, 0) <= 0
          OR (
              i.invoice_type = 'NDC'
              AND NOT EXISTS (
                  SELECT 1 FROM invoice_sites invoice_site
                  WHERE invoice_site.invoice_id = i.id
              )
          )
          OR (i.invoice_type <> 'NDC' AND i.site_id IS NULL)
        THEN 'BROUILLON'
        WHEN t.date_depot_dtc IS NULL THEN 'READY_DTC'
        WHEN t.date_depot_mobilis IS NULL THEN 'DEPOSITED_DTC'
        WHEN t.date_ov IS NULL THEN 'AWAITING_PAYMENT'
        ELSE 'PAID'
    END AS lifecycle_status,
    CASE WHEN t.date_depot_dtc IS NULL THEN 0 ELSE 1 END AS is_issued,
    CASE
        WHEN t.date_depot_mobilis IS NOT NULL
         AND t.date_ov IS NULL
         AND julianday('now') - julianday(t.date_depot_mobilis) > 60
        THEN 1 ELSE 0
    END AS is_overdue_60_days
FROM invoices i
JOIN invoice_tracking t ON t.invoice_id = i.id;

CREATE VIEW invoice_drafts AS
SELECT * FROM invoice_lifecycle
WHERE date_depot_dtc IS NULL;

CREATE VIEW issued_invoices AS
SELECT * FROM invoice_lifecycle
WHERE date_depot_dtc IS NOT NULL;
"""


MIGRATION_V21_SQL = r"""
ALTER TABLE bpu_items ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1
    CHECK (is_active IN (0, 1));

CREATE INDEX idx_bpu_items_active_article
ON bpu_items(is_active, article_number);
"""


MIGRATION_V22_SQL = r"""
ALTER TABLE clients ADD COLUMN reference_contrat TEXT NOT NULL DEFAULT '';

UPDATE clients
SET reference_contrat=COALESCE(
    (SELECT reference_contrat FROM contract_settings WHERE id=1),
    ''
)
WHERE trim(reference_contrat)='';

ALTER TABLE clients DROP COLUMN adresse;
"""


MIGRATIONS = (
    (1, "invoice_lifecycle_tracking", MIGRATION_V1_SQL),
    (2, "invoice_permissions_and_locking", MIGRATION_V2_SQL),
    (3, "bpu_st_numbering", MIGRATION_V3_SQL),
    (4, "invoice_nature_typology_ndc", MIGRATION_V4_SQL),
    (5, "clients_branches_super_admin", MIGRATION_V5_SQL),
    (6, "bpu_st_versioning", MIGRATION_V6_SQL),
    (7, "normalize_invoice_typologies", MIGRATION_V7_SQL),
    (8, "normalize_colocation_typology", MIGRATION_V8_SQL),
    (9, "purchase_orders_use_clients", MIGRATION_V9_SQL),
    (10, "repair_legacy_french_encoding", MIGRATION_V10_SQL),
    (11, "normalize_legacy_mobilis_name", MIGRATION_V11_SQL),
    (12, "direction_sigles", MIGRATION_V12_SQL),
    (13, "normalize_known_organization_labels", MIGRATION_V13_SQL),
    (14, "client_sigles", MIGRATION_V14_SQL),
    (15, "typology_catalog_and_invoice_labels", MIGRATION_V15_SQL),
    (16, "site_subcontractors_and_design_offices", MIGRATION_V16_SQL),
    (17, "partner_contacts_and_excel_imports", MIGRATION_V17_SQL),
    (18, "secure_initial_administrator", MIGRATION_V18_SQL),
    (19, "invoice_financial_rate_snapshots", MIGRATION_V19_SQL),
    (20, "invoice_payment_transfer_reference", MIGRATION_V20_SQL),
    (21, "bpu_catalog_history_safe_replacement", MIGRATION_V21_SQL),
    (22, "move_contract_reference_to_clients_and_remove_client_address", MIGRATION_V22_SQL),
)


def ensure_migration_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.commit()


def create_pre_migration_backup(
    connection: sqlite3.Connection,
    database_path: Path,
    target_version: int,
) -> Path | None:
    database_path = Path(database_path)
    if not database_path.exists() or not database_path.is_file():
        return None
    backup_dir = database_path.parent / "migration_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    target = backup_dir / f"phoenix_pre_migration_v{target_version}_{timestamp}.sqlite3"
    backup_connection = sqlite3.connect(target)
    try:
        connection.backup(backup_connection)
        result = backup_connection.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"Migration backup integrity check failed: {result}")
    except Exception:
        backup_connection.close()
        target.unlink(missing_ok=True)
        raise
    else:
        backup_connection.close()
    return target


def run_migrations(
    connection: sqlite3.Connection,
    database_path: Path | None = None,
) -> list[int]:
    ensure_migration_table(connection)
    applied = {
        row[0]
        for row in connection.execute("SELECT version FROM schema_migrations")
    }
    pending = [migration for migration in MIGRATIONS if migration[0] not in applied]
    if not pending:
        return []

    connection.commit()
    if database_path is not None:
        create_pre_migration_backup(connection, database_path, pending[-1][0])

    foreign_keys_enabled = connection.execute("PRAGMA foreign_keys").fetchone()[0]
    connection.execute("PRAGMA foreign_keys = OFF")
    completed = []
    try:
        for version, name, script in pending:
            try:
                if version == 5:
                    user_columns = {
                        row[1] for row in connection.execute("PRAGMA table_info(users)")
                    }
                    legacy_auth_columns = {
                        "must_change_password": "INTEGER NOT NULL DEFAULT 0",
                        "failed_attempts": "INTEGER NOT NULL DEFAULT 0",
                        "locked_until": "TEXT",
                        "last_login_at": "TEXT",
                    }
                    for column, definition in legacy_auth_columns.items():
                        if column not in user_columns:
                            connection.execute(
                                f"ALTER TABLE users ADD COLUMN {column} {definition}"
                            )
                    connection.commit()
                connection.executescript("BEGIN IMMEDIATE;\n" + script)
                if version == 18 and connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='user_sessions'"
                ).fetchone():
                    connection.execute(
                        """
                        DELETE FROM user_sessions
                        WHERE user_id IN (
                            SELECT id FROM users
                            WHERE username='admin' AND role='super_admin' AND is_active=1
                        )
                        """
                    )
                violations = connection.execute("PRAGMA foreign_key_check").fetchall()
                if violations:
                    raise RuntimeError(
                        f"Foreign key check failed during migration {version}: {violations[:5]}"
                    )
                connection.execute(
                    "INSERT INTO schema_migrations(version, name) VALUES(?, ?)",
                    (version, name),
                )
                connection.commit()
                completed.append(version)
            except Exception:
                connection.rollback()
                raise
    finally:
        connection.execute(f"PRAGMA foreign_keys = {1 if foreign_keys_enabled else 0}")
    return completed
