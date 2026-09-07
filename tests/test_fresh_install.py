import db as db_module
from database import migrations
from services import auth


BUSINESS_TABLES = (
    "bpu_items",
    "bpu_st_items",
    "bpu_st_versions",
    "clients",
    "client_directions",
    "company_branches",
    "purchase_orders",
    "sites",
    "invoices",
    "subcontractors",
    "design_offices",
    "typologies",
)


def test_packaged_fresh_install_is_empty_and_requires_secure_admin_setup(tmp_path, monkeypatch):
    database_path = tmp_path / "fresh-install" / "pos_ai.sqlite3"
    monkeypatch.setattr(db_module, "DB_PATH", database_path)
    monkeypatch.setenv("PHOENIX_FRESH_INSTALL_EMPTY", "1")
    monkeypatch.delenv("PHOENIX_DEFAULT_ADMIN_KEEP_PASSWORD", raising=False)
    db_module.invalidate_schema_cache(database_path)

    try:
        auth.ensure_default_admin()
        with db_module.db() as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert connection.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0] == migrations.CURRENT_SCHEMA_VERSION == 22
            invoice_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(invoices)")
            }
            assert {"rg_rate", "tva_rate"} <= invoice_columns
            assert "is_active" in {
                row[1] for row in connection.execute("PRAGMA table_info(bpu_items)")
            }
            tracking_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(invoice_tracking)")
            }
            assert "numero_ordre_virement" in tracking_columns
            assert all(
                connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] == 0
                for table in BUSINESS_TABLES
            )
            admin = connection.execute(
                "SELECT password_hash, must_change_password, role FROM users WHERE username='admin'"
            ).fetchone()
            assert admin["role"] == "super_admin"
            assert admin["must_change_password"] == 1
            assert not auth.verify_password("admin123", admin["password_hash"])
            assert connection.execute(
                "SELECT value FROM app_settings WHERE key='skip_builtin_bpu_seed'"
            ).fetchone()[0] == "1"

        db_module.invalidate_schema_cache(database_path)
        with db_module.db() as connection:
            assert all(
                connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] == 0
                for table in BUSINESS_TABLES
            )
        assert auth.first_run_required() is True
    finally:
        db_module.invalidate_schema_cache(database_path)
