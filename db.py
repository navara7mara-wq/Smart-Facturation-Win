import os
import sqlite3
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
DB_PATH = ROOT_DIR / "data" / "pos_ai.sqlite3"
SCHEMA_PATH = ROOT_DIR / "database" / "schema.sql"

DEFAULT_TEMPLATE_SETTINGS = {
    "logo_width_px": "340",
    "logo_height_px": "170",
    "company_logo_anchor": "E1",
    "company_logo_offset_px": "118",
    "client_logo_anchor": "",
    "client_logo_offset_px": "0",
    "line_height_normal": "30",
    "line_height_wrapped": "50",
    "designation_wrap_chars": "52",
    "amount_words_font_size": "24",
    "col_a_width": "9",
    "col_b_width": "103",
    "col_c_width": "13.5",
    "col_d_width": "19",
    "col_e_width": "31",
    "col_f_width": "33",
    "hide_empty_sections": "1",
    "visual_blocks_json": "",
    "visual_blocks_facture_json": "",
    "visual_blocks_devis_quantitatif_json": "",
    "visual_blocks_devis_estimatif_json": "",
}


def current_actor():
    return os.environ.get("PHOENIX_CURRENT_USER") or os.environ.get("USERNAME") or os.environ.get("USER") or "User"


def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON;")
    if not connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='company_settings'").fetchone():
        connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    ensure_schema(connection)
    return connection


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('admin', 'editor', 'viewer')),
            is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS user_sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
                ON UPDATE CASCADE
                ON DELETE CASCADE
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS template_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_by TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    connection.execute("""
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
        )
    """)
    add_audit_columns(connection)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(purchase_orders)")}
    additions = {
        "objet": "ALTER TABLE purchase_orders ADD COLUMN objet TEXT NOT NULL DEFAULT ''",
        "montant_ttc": "ALTER TABLE purchase_orders ADD COLUMN montant_ttc NUMERIC NOT NULL DEFAULT 0 CHECK (montant_ttc >= 0)",
        "attachment_path": "ALTER TABLE purchase_orders ADD COLUMN attachment_path TEXT",
    }
    for column, statement in additions.items():
        if column not in columns:
            connection.execute(statement)
    site_columns = {row[1] for row in connection.execute("PRAGMA table_info(sites)")}
    if "bet" not in site_columns:
        connection.execute("ALTER TABLE sites ADD COLUMN bet TEXT NOT NULL DEFAULT ''")
    invoice_columns = {row[1] for row in connection.execute("PRAGMA table_info(invoices)")}
    invoice_additions = {
        "remarque": "ALTER TABLE invoices ADD COLUMN remarque TEXT NOT NULL DEFAULT ''",
        "depos": "ALTER TABLE invoices ADD COLUMN depos INTEGER NOT NULL DEFAULT 0 CHECK (depos IN (0, 1))",
    }
    for column, statement in invoice_additions.items():
        if column not in invoice_columns:
            connection.execute(statement)
    connection.execute("DROP INDEX IF EXISTS idx_invoices_site_type_regular")
    connection.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_invoices_site_type_regular
        ON invoices (site_id, invoice_type)
        WHERE invoice_type IN ('ACQUISITION', 'CONSTRUCTION', 'CONST_ACQUIS')
        AND deleted_at IS NULL
    """)
    ensure_template_defaults(connection)
    connection.commit()


def ensure_template_defaults(connection):
    for key, value in DEFAULT_TEMPLATE_SETTINGS.items():
        connection.execute(
            "INSERT OR IGNORE INTO template_settings(key, value, updated_by) VALUES (?, ?, ?)",
            (key, value, current_actor()),
        )


def add_audit_columns(connection):
    audit_tables = ["mobilis_directions", "purchase_orders", "sites", "invoices"]
    audit_columns = {
        "created_by": "TEXT NOT NULL DEFAULT ''",
        "updated_by": "TEXT NOT NULL DEFAULT ''",
        "deleted_at": "TEXT",
        "deleted_by": "TEXT",
    }
    for table in audit_tables:
        existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        for column, definition in audit_columns.items():
            if column not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
