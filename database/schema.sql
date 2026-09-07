-- Baseline schema V0. db.ensure_schema applies all versioned migrations after this script.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS company_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    nom TEXT NOT NULL DEFAULT '',
    logo_path TEXT,
    rgc TEXT NOT NULL DEFAULT '',
    nif TEXT NOT NULL DEFAULT '',
    art TEXT NOT NULL DEFAULT '',
    adresse TEXT NOT NULL DEFAULT '',
    numero_compte TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mobilis_directions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doit_nom TEXT NOT NULL,
    direction_regionale TEXT NOT NULL,
    adresse TEXT NOT NULL DEFAULT '',
    rgc TEXT NOT NULL DEFAULT '',
    nif TEXT NOT NULL DEFAULT '',
    logo_path TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mobilis_client_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    doit TEXT NOT NULL DEFAULT '',
    nif TEXT NOT NULL DEFAULT '',
    nis TEXT NOT NULL DEFAULT '',
    is_configured INTEGER NOT NULL DEFAULT 0 CHECK (is_configured IN (0, 1)),
    created_by TEXT NOT NULL DEFAULT '',
    updated_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS contract_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    reference_contrat TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bpu_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_number INTEGER NOT NULL UNIQUE,
    designation TEXT NOT NULL,
    unite TEXT NOT NULL,
    pu_ht NUMERIC NOT NULL CHECK (pu_ht >= 0),
    categorie TEXT NOT NULL CHECK (categorie IN ('acquisition', 'fourniture', 'prestation')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS purchase_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    numero_bc TEXT NOT NULL UNIQUE,
    date_bc TEXT,
    mobilis_direction_id INTEGER NOT NULL,
    type_bc TEXT NOT NULL CHECK (type_bc IN ('ACQUISITION', 'CONSTRUCTION', 'CONST_ACQUIS', 'NDC', 'MGC')),
    objet TEXT NOT NULL DEFAULT '',
    montant_ttc NUMERIC NOT NULL DEFAULT 0 CHECK (montant_ttc >= 0),
    attachment_path TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (mobilis_direction_id) REFERENCES mobilis_directions (id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS sites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    purchase_order_id INTEGER NOT NULL,
    code_site TEXT NOT NULL UNIQUE,
    nom_site TEXT NOT NULL,
    region TEXT NOT NULL DEFAULT '',
    typologie_site TEXT NOT NULL DEFAULT '',
    bet TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (purchase_order_id) REFERENCES purchase_orders (id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_number TEXT NOT NULL UNIQUE,
    invoice_type TEXT NOT NULL CHECK (invoice_type IN ('ACQUISITION', 'CONSTRUCTION', 'CONST_ACQUIS', 'NDC')),
    purchase_order_id INTEGER NOT NULL,
    site_id INTEGER,
    invoice_date TEXT,
    total_ht NUMERIC NOT NULL DEFAULT 0 CHECK (total_ht >= 0),
    retenue_garantie NUMERIC NOT NULL DEFAULT 0 CHECK (retenue_garantie >= 0),
    montant_ht_apres_rg NUMERIC NOT NULL DEFAULT 0 CHECK (montant_ht_apres_rg >= 0),
    tva NUMERIC NOT NULL DEFAULT 0 CHECK (tva >= 0),
    total_ttc NUMERIC NOT NULL DEFAULT 0 CHECK (total_ttc >= 0),
    montant_en_lettres TEXT NOT NULL DEFAULT '',
    remarque TEXT NOT NULL DEFAULT '',
    depos INTEGER NOT NULL DEFAULT 0 CHECK (depos IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (purchase_order_id) REFERENCES purchase_orders (id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,
    FOREIGN KEY (site_id) REFERENCES sites (id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,
    CHECK (
        (invoice_type = 'NDC' AND site_id IS NULL)
        OR (invoice_type <> 'NDC' AND site_id IS NOT NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_invoices_site_type_regular
ON invoices (site_id, invoice_type)
WHERE invoice_type IN ('ACQUISITION', 'CONSTRUCTION', 'CONST_ACQUIS');

CREATE TABLE IF NOT EXISTS invoice_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL,
    article_number INTEGER NOT NULL,
    designation_snapshot TEXT NOT NULL,
    unite_snapshot TEXT NOT NULL,
    pu_ht_snapshot NUMERIC NOT NULL CHECK (pu_ht_snapshot >= 0),
    categorie_snapshot TEXT NOT NULL CHECK (categorie_snapshot IN ('acquisition', 'fourniture', 'prestation', 'ndc')),
    quantite NUMERIC NOT NULL CHECK (quantite > 0),
    montant_ht NUMERIC NOT NULL CHECK (montant_ht >= 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (invoice_id) REFERENCES invoices (id)
        ON UPDATE CASCADE
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS invoice_sites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL,
    site_id INTEGER NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (invoice_id) REFERENCES invoices (id)
        ON UPDATE CASCADE
        ON DELETE CASCADE,
    FOREIGN KEY (site_id) REFERENCES sites (id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS template_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_by TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_by TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_invoice_sites_invoice_site
ON invoice_sites (invoice_id, site_id);

CREATE TRIGGER IF NOT EXISTS trg_invoice_sites_only_ndc
BEFORE INSERT ON invoice_sites
FOR EACH ROW
BEGIN
    SELECT CASE
        WHEN (SELECT invoice_type FROM invoices WHERE id = NEW.invoice_id) <> 'NDC'
        THEN RAISE(ABORT, 'invoice_sites accepts only NDC invoices')
    END;
END;

CREATE TRIGGER IF NOT EXISTS trg_invoices_ndc_single_line_article
BEFORE INSERT ON invoice_lines
FOR EACH ROW
WHEN (SELECT invoice_type FROM invoices WHERE id = NEW.invoice_id) = 'NDC'
BEGIN
    SELECT CASE
        WHEN NEW.article_number <> 6
        THEN RAISE(ABORT, 'NDC invoices accept only article 6')
    END;
END;

CREATE TRIGGER IF NOT EXISTS trg_updated_company_settings
AFTER UPDATE ON company_settings
FOR EACH ROW
BEGIN
    UPDATE company_settings SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_updated_mobilis_directions
AFTER UPDATE ON mobilis_directions
FOR EACH ROW
BEGIN
    UPDATE mobilis_directions SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_updated_mobilis_client_settings
AFTER UPDATE ON mobilis_client_settings
FOR EACH ROW
BEGIN
    UPDATE mobilis_client_settings SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_updated_contract_settings
AFTER UPDATE ON contract_settings
FOR EACH ROW
BEGIN
    UPDATE contract_settings SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_updated_bpu_items
AFTER UPDATE ON bpu_items
FOR EACH ROW
BEGIN
    UPDATE bpu_items SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_updated_purchase_orders
AFTER UPDATE ON purchase_orders
FOR EACH ROW
BEGIN
    UPDATE purchase_orders SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_updated_sites
AFTER UPDATE ON sites
FOR EACH ROW
BEGIN
    UPDATE sites SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_updated_invoices
AFTER UPDATE ON invoices
FOR EACH ROW
BEGIN
    UPDATE invoices SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

INSERT OR IGNORE INTO company_settings (id) VALUES (1);
INSERT OR IGNORE INTO contract_settings (id) VALUES (1);
