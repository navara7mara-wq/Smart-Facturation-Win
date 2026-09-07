from contextlib import closing
from pathlib import Path
import os
import sqlite3
import subprocess
import sys

import pytest

import db as db_module
from database import migrations


ROOT_DIR = Path(__file__).resolve().parents[1]
BASELINE_SCHEMA = (ROOT_DIR / "database" / "schema.sql").read_text(encoding="utf-8")
pytestmark = pytest.mark.migration


def legacy_database(path):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(BASELINE_SCHEMA)
    connection.execute(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('admin', 'editor', 'viewer')),
            is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db_module.add_audit_columns(connection)
    direction_id = connection.execute(
        """
        INSERT INTO mobilis_directions(doit_nom, direction_regionale)
        VALUES('Mobilis', 'Alger')
        """
    ).lastrowid
    po_id = connection.execute(
        """
        INSERT INTO purchase_orders(
            numero_bc, date_bc, mobilis_direction_id, type_bc, objet, montant_ttc
        )
        VALUES('BC-LEGACY', '2026-01-02', ?, 'CONSTRUCTION', 'Legacy', 1190)
        """,
        (direction_id,),
    ).lastrowid
    site_id = connection.execute(
        """
        INSERT INTO sites(purchase_order_id, code_site, nom_site)
        VALUES(?, 'SITE-LEGACY', 'Legacy site')
        """,
        (po_id,),
    ).lastrowid
    invoice_id = connection.execute(
        """
        INSERT INTO invoices(
            invoice_number, invoice_type, purchase_order_id, site_id,
            invoice_date, total_ht, retenue_garantie, montant_ht_apres_rg,
            tva, total_ttc, depos, created_by, updated_by
        )
        VALUES(
            'FAC-001', 'CONSTRUCTION', ?, ?, '2026-01-05',
            1000, 50, 950, 180.5, 1130.5, 1, 'legacy', 'legacy'
        )
        """,
        (po_id, site_id),
    ).lastrowid
    connection.commit()
    return connection, direction_id, po_id, site_id, invoice_id


def test_migrations_preserve_legacy_data_and_create_backup(tmp_path):
    database_path = tmp_path / "data" / "legacy.sqlite3"
    database_path.parent.mkdir()
    con, direction_id, _, site_id, invoice_id = legacy_database(database_path)
    try:
        assert migrations.run_migrations(con, database_path) == list(range(1, migrations.CURRENT_SCHEMA_VERSION + 1))

        backup_paths = list((database_path.parent / "migration_backups").glob("*.sqlite3"))
        assert len(backup_paths) == 1
        with closing(sqlite3.connect(backup_paths[0])) as backup:
            assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert backup.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 1
            assert backup.execute(
                "SELECT COUNT(*) FROM schema_migrations"
            ).fetchone()[0] == 0

        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        company_sigle = con.execute(
            "SELECT sigle FROM company_branches WHERE id=1"
        ).fetchone()[0]
        client_sigle = con.execute(
            "SELECT sigle FROM client_directions WHERE id=?", (direction_id,)
        ).fetchone()[0]
        assert company_sigle == "DG"
        assert client_sigle == f"DR-{direction_id:03d}"
        migrated_client = con.execute(
            "SELECT raison_sociale, sigle, reference_contrat FROM clients WHERE id=1"
        ).fetchone()
        assert migrated_client[1] == "ATM Mobilis"
        assert "adresse" not in {
            row[1] for row in con.execute("PRAGMA table_info(clients)")
        }
        a12 = con.execute(
            "SELECT id, libelle_complet FROM typologies WHERE sigle='A12'"
        ).fetchone()
        assert a12["libelle_complet"] == "A12 ( MAT 12M + BTS OUTDOOR )"
        assert "typologie_label_snapshot" in {
            row[1] for row in con.execute("PRAGMA table_info(invoices)")
        }
        assert "typology_id" in {
            row[1] for row in con.execute("PRAGMA table_info(sites)")
        }
        site_columns = {row[1] for row in con.execute("PRAGMA table_info(sites)")}
        assert {"subcontractor_id", "design_office_id"} <= site_columns
        assert {"subcontractors", "design_offices"} <= {
            row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"contact", "email"} <= {
            row[1] for row in con.execute("PRAGMA table_info(subcontractors)")
        }
        assert {"contact", "email"} <= {
            row[1] for row in con.execute("PRAGMA table_info(design_offices)")
        }
        assert con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='data_import_batches'"
        ).fetchone()
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO clients(raison_sociale, sigle) VALUES('Doublon', 'atm mobilis')"
            )
        legacy_client_id = con.execute(
            "INSERT INTO clients(raison_sociale) VALUES('Client historique')"
        ).lastrowid
        assert con.execute(
            "SELECT sigle FROM clients WHERE id=?", (legacy_client_id,)
        ).fetchone()[0] == f"CLIENT-{legacy_client_id:03d}"
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                """
                INSERT INTO client_directions(
                    client_id, company_branch_id, name, sigle
                ) VALUES(1, 1, 'Doublon', ?)
                """,
                (client_sigle.lower(),),
            )
        with pytest.raises(sqlite3.IntegrityError, match="Sigle"):
            con.execute(
                """
                INSERT INTO client_directions(
                    client_id, company_branch_id, name, sigle
                ) VALUES(1, 1, 'Invalide', 'dr@oran')
                """
            )
        assert con.execute(
            "SELECT invoice_number FROM invoices WHERE id=?", (invoice_id,)
        ).fetchone()[0] == "FAC-001"

        tracking = con.execute(
            """
            SELECT date_depot_dtc, legacy_depos, migration_review_required
            FROM invoice_tracking WHERE invoice_id=?
            """,
            (invoice_id,),
        ).fetchone()
        assert tuple(tracking) == (None, 1, 1)
        assert con.execute(
            "SELECT COUNT(*) FROM invoice_tracking_events WHERE invoice_id=?",
            (invoice_id,),
        ).fetchone()[0] == 1
        assert con.execute(
            "SELECT lifecycle_status FROM invoice_lifecycle WHERE id=?",
            (invoice_id,),
        ).fetchone()[0] == "READY_DTC"

        mgc_po_id = con.execute(
            """
            INSERT INTO purchase_orders(numero_bc, mobilis_direction_id, type_bc)
            VALUES('BC-MGC', ?, 'MGC')
            """,
            (direction_id,),
        ).lastrowid
        mgc_site_id = con.execute(
            """
            INSERT INTO sites(purchase_order_id, code_site, nom_site)
            VALUES(?, 'SITE-MGC', 'MGC site')
            """,
            (mgc_po_id,),
        ).lastrowid
        draft_id = con.execute(
            "INSERT INTO invoices(created_by, updated_by) VALUES('tester', 'tester')"
        ).lastrowid
        assert con.execute(
            "SELECT lifecycle_status FROM invoice_drafts WHERE id=?", (draft_id,)
        ).fetchone()[0] == "BROUILLON"
        assert con.execute(
            "SELECT COUNT(*) FROM invoice_tracking WHERE invoice_id=?", (draft_id,)
        ).fetchone()[0] == 1

        con.execute(
            """
            UPDATE invoices
            SET invoice_number='FAC-DRAFT', invoice_type='CONSTRUCTION',
                purchase_order_id=(SELECT id FROM purchase_orders WHERE numero_bc='BC-MGC'),
                site_id=?, invoice_date='2026-02-01', total_ttc=100
            WHERE id=?
            """,
            (mgc_site_id, draft_id),
        )
        assert con.execute(
            "SELECT lifecycle_status FROM invoice_drafts WHERE id=?", (draft_id,)
        ).fetchone()[0] == "READY_DTC"

        with pytest.raises(sqlite3.IntegrityError, match="Date DTC"):
            con.execute(
                "UPDATE invoice_tracking SET date_depot_dtc='2026-01-31' WHERE invoice_id=?",
                (draft_id,),
            )
        con.execute(
            "UPDATE invoice_tracking SET date_depot_dtc='2026-02-02' WHERE invoice_id=?",
            (draft_id,),
        )
        assert con.execute(
            "SELECT lifecycle_status FROM issued_invoices WHERE id=?", (draft_id,)
        ).fetchone()[0] == "DEPOSITED_DTC"
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "UPDATE invoice_tracking SET date_depot_mobilis='2026-02-01' WHERE invoice_id=?",
                (draft_id,),
            )
        con.execute(
            "UPDATE invoice_tracking SET date_depot_mobilis='2026-02-03' WHERE invoice_id=?",
            (draft_id,),
        )
        assert con.execute(
            "SELECT lifecycle_status FROM issued_invoices WHERE id=?", (draft_id,)
        ).fetchone()[0] == "AWAITING_PAYMENT"
        con.execute(
            "UPDATE invoice_tracking SET date_ov='2026-02-04' WHERE invoice_id=?",
            (draft_id,),
        )
        assert con.execute(
            "SELECT lifecycle_status FROM issued_invoices WHERE id=?", (draft_id,)
        ).fetchone()[0] == "PAID"

        con.execute(
            "INSERT INTO invoices(invoice_number, invoice_date) VALUES('FAC-001', '2027-01-01')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO invoices(invoice_number, invoice_date) VALUES('fac-001', '2026-12-31')"
            )

        backup_count = len(backup_paths)
        assert migrations.run_migrations(con, database_path) == []
        assert len(list((database_path.parent / "migration_backups").glob("*.sqlite3"))) == backup_count
    finally:
        con.close()


def test_legacy_migration_matrix_preserves_relations_and_financial_values(tmp_path):
    database_path = tmp_path / "data" / "legacy-matrix.sqlite3"
    database_path.parent.mkdir()
    con, direction_id, po_id, site_id, issued_id = legacy_database(database_path)
    try:
        con.execute(
            "UPDATE purchase_orders SET attachment_path='uploads/purchase_orders/legacy.pdf' WHERE id=?",
            (po_id,),
        )
        con.execute(
            """
            INSERT INTO invoice_lines(
                invoice_id, article_number, designation_snapshot, unite_snapshot,
                pu_ht_snapshot, categorie_snapshot, quantite, montant_ht
            ) VALUES(?, 1, 'Article historique', 'U', 250.25, 'fourniture', 4, 1001)
            """,
            (issued_id,),
        )
        regular_po_id = con.execute(
            """
            INSERT INTO purchase_orders(
                numero_bc, date_bc, mobilis_direction_id, type_bc, objet, montant_ttc,
                created_by, updated_by
            ) VALUES('BC-READY-LEGACY', '2026-02-01', ?, 'ACQUISITION', 'Ready', 595,
                     'legacy-user', 'legacy-user')
            """,
            (direction_id,),
        ).lastrowid
        regular_site_id = con.execute(
            """
            INSERT INTO sites(
                purchase_order_id, code_site, nom_site, region, typologie_site,
                created_by, updated_by
            ) VALUES(?, 'SITE-READY-LEGACY', 'Ready site', 'Oran', 'A12',
                     'legacy-user', 'legacy-user')
            """,
            (regular_po_id,),
        ).lastrowid
        ready_id = con.execute(
            """
            INSERT INTO invoices(
                invoice_number, invoice_type, purchase_order_id, site_id,
                invoice_date, total_ht, retenue_garantie, montant_ht_apres_rg,
                tva, total_ttc, montant_en_lettres, remarque, depos,
                created_by, updated_by
            ) VALUES(
                'FAC-READY-LEGACY', 'ACQUISITION', ?, ?, '2026-02-03',
                500, 25, 475, 90.25, 565.25, 'CINQ CENT SOIXANTE-CINQ',
                'Valeur historique', 0, 'legacy-user', 'legacy-user'
            )
            """,
            (regular_po_id, regular_site_id),
        ).lastrowid
        con.execute(
            """
            INSERT INTO invoice_lines(
                invoice_id, article_number, designation_snapshot, unite_snapshot,
                pu_ht_snapshot, categorie_snapshot, quantite, montant_ht
            ) VALUES(?, 2, 'Acquisition historique', 'U', 125, 'acquisition', 4, 500)
            """,
            (ready_id,),
        )
        ndc_po_id = con.execute(
            """
            INSERT INTO purchase_orders(
                numero_bc, date_bc, mobilis_direction_id, type_bc, objet, montant_ttc,
                created_by, updated_by
            ) VALUES('BC-NDC-LEGACY', '2026-03-01', ?, 'NDC', 'Multi-sites', 2261,
                     'legacy-user', 'legacy-user')
            """,
            (direction_id,),
        ).lastrowid
        ndc_site_ids = [
            con.execute(
                """
                INSERT INTO sites(
                    purchase_order_id, code_site, nom_site, created_by, updated_by
                ) VALUES(?, ?, ?, 'legacy-user', 'legacy-user')
                """,
                (ndc_po_id, f"NDC-LEGACY-{index}", f"NDC legacy {index}"),
            ).lastrowid
            for index in (1, 2)
        ]
        ndc_id = con.execute(
            """
            INSERT INTO invoices(
                invoice_number, invoice_type, purchase_order_id, invoice_date,
                total_ht, retenue_garantie, montant_ht_apres_rg, tva, total_ttc,
                montant_en_lettres, depos, created_by, updated_by
            ) VALUES(
                'FAC-NDC-LEGACY', 'NDC', ?, '2026-03-02',
                2000, 100, 1900, 361, 2261, 'DEUX MILLE DEUX CENT SOIXANTE',
                0, 'legacy-user', 'legacy-user'
            )
            """,
            (ndc_po_id,),
        ).lastrowid
        con.execute(
            """
            INSERT INTO invoice_lines(
                invoice_id, article_number, designation_snapshot, unite_snapshot,
                pu_ht_snapshot, categorie_snapshot, quantite, montant_ht
            ) VALUES(?, 6, 'Forfait NDC historique', 'Site', 1000, 'ndc', 2, 2000)
            """,
            (ndc_id,),
        )
        con.executemany(
            "INSERT INTO invoice_sites(invoice_id, site_id) VALUES(?, ?)",
            ((ndc_id, current_site_id) for current_site_id in ndc_site_ids),
        )
        con.commit()

        invoice_snapshot = {
            row["id"]: tuple(row)
            for row in con.execute(
                """
                SELECT id, invoice_number, invoice_type, purchase_order_id, site_id,
                       invoice_date, total_ht, retenue_garantie,
                       montant_ht_apres_rg, tva, total_ttc, montant_en_lettres,
                       remarque, depos, created_by, updated_by
                FROM invoices ORDER BY id
                """
            )
        }
        line_snapshot = [
            tuple(row)
            for row in con.execute(
                """
                SELECT invoice_id, article_number, designation_snapshot,
                       unite_snapshot, pu_ht_snapshot, categorie_snapshot,
                       quantite, montant_ht
                FROM invoice_lines ORDER BY invoice_id, id
                """
            )
        ]
        relation_snapshot = [
            tuple(row)
            for row in con.execute(
                "SELECT invoice_id, site_id FROM invoice_sites ORDER BY invoice_id, site_id"
            )
        ]

        assert migrations.run_migrations(con, database_path) == list(range(1, migrations.CURRENT_SCHEMA_VERSION + 1))

        migrated_snapshot = {
            row["id"]: tuple(row)
            for row in con.execute(
                """
                SELECT id, invoice_number, invoice_type, purchase_order_id, site_id,
                       invoice_date, total_ht, retenue_garantie,
                       montant_ht_apres_rg, tva, total_ttc, montant_en_lettres,
                       remarque, depos, created_by, updated_by
                FROM invoices ORDER BY id
                """
            )
        }
        assert migrated_snapshot == invoice_snapshot
        assert [
            tuple(row)
            for row in con.execute(
                """
                SELECT invoice_id, article_number, designation_snapshot,
                       unite_snapshot, pu_ht_snapshot, categorie_snapshot,
                       quantite, montant_ht
                FROM invoice_lines ORDER BY invoice_id, id
                """
            )
        ] == line_snapshot
        assert [
            tuple(row)
            for row in con.execute(
                "SELECT invoice_id, site_id FROM invoice_sites ORDER BY invoice_id, site_id"
            )
        ] == relation_snapshot
        assert con.execute(
            "SELECT attachment_path FROM purchase_orders WHERE id=?", (po_id,)
        ).fetchone()[0] == "uploads/purchase_orders/legacy.pdf"
        assert con.execute("SELECT COUNT(*) FROM invoice_tracking").fetchone()[0] == 3
        assert con.execute(
            "SELECT COUNT(*) FROM invoice_tracking WHERE migration_review_required=1"
        ).fetchone()[0] == 1
        assert con.execute(
            "SELECT lifecycle_status FROM invoice_lifecycle WHERE id=?", (ndc_id,)
        ).fetchone()[0] == "READY_DTC"
        assert con.execute(
            "SELECT lifecycle_status FROM invoice_lifecycle WHERE id=?", (ready_id,)
        ).fetchone()[0] == "READY_DTC"
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []

        backup_path = next((database_path.parent / "migration_backups").glob("*.sqlite3"))
        with closing(sqlite3.connect(backup_path)) as backup:
            assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert backup.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 3
            assert backup.execute("SELECT COUNT(*) FROM invoice_lines").fetchone()[0] == 3
            assert backup.execute("SELECT COUNT(*) FROM invoice_sites").fetchone()[0] == 2
    finally:
        con.close()


def test_failed_migration_rolls_back_all_schema_changes(tmp_path, monkeypatch):
    database_path = tmp_path / "rollback.sqlite3"
    with closing(sqlite3.connect(database_path)) as con:
        con.execute("CREATE TABLE original_data(id INTEGER PRIMARY KEY)")
        monkeypatch.setattr(
            migrations,
            "MIGRATIONS",
            ((99, "broken", "CREATE TABLE partial_change(id INTEGER); INVALID SQL;"),),
        )

        with pytest.raises(sqlite3.OperationalError):
            migrations.run_migrations(con, database_path)

        assert con.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='partial_change'"
        ).fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 0


def test_tracking_events_are_append_only(tmp_path):
    database_path = tmp_path / "append-only.sqlite3"
    con, _, _, _, invoice_id = legacy_database(database_path)
    try:
        migrations.run_migrations(con, database_path)
        event_id = con.execute(
            "SELECT id FROM invoice_tracking_events WHERE invoice_id=?", (invoice_id,)
        ).fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            con.execute(
                "UPDATE invoice_tracking_events SET reason='changed' WHERE id=?", (event_id,)
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            con.execute("DELETE FROM invoice_tracking_events WHERE id=?", (event_id,))
    finally:
        con.close()


def test_init_script_applies_current_schema(tmp_path):
    database_path = tmp_path / "initialized.sqlite3"
    environment = os.environ.copy()
    environment["PHOENIX_DB_PATH"] = str(database_path)

    result = subprocess.run(
        [sys.executable, str(ROOT_DIR / "scripts" / "init_db.py")],
        cwd=ROOT_DIR,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    with closing(sqlite3.connect(database_path)) as con:
        assert con.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == migrations.CURRENT_SCHEMA_VERSION
        views = {
            row[0]
            for row in con.execute("SELECT name FROM sqlite_master WHERE type='view'")
        }
        assert {"invoice_lifecycle", "invoice_drafts", "issued_invoices"} <= views


def test_v18_upgrade_forces_legacy_admin_password_change_without_changing_role(tmp_path, monkeypatch):
    database_path = tmp_path / "v17-upgrade.sqlite3"
    with closing(sqlite3.connect(database_path)) as con:
        con.executescript(
            """
            CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, name TEXT NOT NULL,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT NOT NULL, role TEXT NOT NULL,
                is_active INTEGER NOT NULL, must_change_password INTEGER NOT NULL,
                updated_at TEXT, last_login_at TEXT);
            CREATE TABLE user_sessions(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
                session_token_hash TEXT NOT NULL);
            """
        )
        con.executemany(
            "INSERT INTO schema_migrations(version, name) VALUES(?, ?)",
            [(version, name) for version, name, _ in migrations.MIGRATIONS if version < 18],
        )
        con.execute("INSERT INTO users VALUES(1, 'admin', 'super_admin', 1, 0, NULL, '2026-08-01')")
        con.execute("INSERT INTO users VALUES(2, 'operator', 'editor', 1, 0, NULL, '2026-08-01')")
        con.execute("INSERT INTO user_sessions VALUES(1, 1, 'admin-session')")
        con.execute("INSERT INTO user_sessions VALUES(2, 2, 'operator-session')")

        monkeypatch.setattr(
            migrations,
            "MIGRATIONS",
            tuple(migration for migration in migrations.MIGRATIONS if migration[0] <= 18),
        )
        assert migrations.run_migrations(con, database_path) == [18]
        assert tuple(con.execute(
            "SELECT role, must_change_password FROM users WHERE username='admin'"
        ).fetchone()) == ("super_admin", 1)
        assert con.execute("SELECT COUNT(*) FROM user_sessions WHERE user_id=1").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM user_sessions WHERE user_id=2").fetchone()[0] == 1
        assert tuple(con.execute(
            "SELECT role, must_change_password FROM users WHERE username='operator'"
        ).fetchone()) == ("editor", 0)


def test_v19_backfills_invoice_rate_snapshots_without_changing_financial_values(tmp_path, monkeypatch):
    database_path = tmp_path / "v18-financial-upgrade.sqlite3"
    con, _, _, _, invoice_id = legacy_database(database_path)
    try:
        migrations.ensure_migration_table(con)
        con.executemany(
            "INSERT INTO schema_migrations(version, name) VALUES(?, ?)",
            [(version, name) for version, name, _ in migrations.MIGRATIONS if version < 19],
        )
        monkeypatch.setattr(
            migrations,
            "MIGRATIONS",
            tuple(migration for migration in migrations.MIGRATIONS if migration[0] <= 19),
        )
        before = tuple(con.execute(
            "SELECT total_ht, retenue_garantie, montant_ht_apres_rg, tva, total_ttc FROM invoices WHERE id=?",
            (invoice_id,),
        ).fetchone())

        assert migrations.run_migrations(con, database_path) == [19]
        row = con.execute(
            "SELECT rg_rate, tva_rate, total_ht, retenue_garantie, montant_ht_apres_rg, tva, total_ttc FROM invoices WHERE id=?",
            (invoice_id,),
        ).fetchone()
        assert tuple(row[:2]) == (5, 19)
        assert tuple(row[2:]) == before
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        assert migrations.run_migrations(con, database_path) == []
    finally:
        con.close()


def test_v20_adds_payment_transfer_reference_without_changing_tracking_dates(tmp_path, monkeypatch):
    database_path = tmp_path / "v19-payment-reference-upgrade.sqlite3"
    con, _, _, _, invoice_id = legacy_database(database_path)
    all_migrations = migrations.MIGRATIONS
    try:
        migrations.ensure_migration_table(con)
        monkeypatch.setattr(
            migrations,
            "MIGRATIONS",
            tuple(migration for migration in all_migrations if migration[0] <= 19),
        )
        assert migrations.run_migrations(con, database_path) == list(range(1, 20))
        before = tuple(con.execute(
            "SELECT date_depot_dtc, date_depot_mobilis, date_ov FROM invoice_tracking WHERE invoice_id=?",
            (invoice_id,),
        ).fetchone())

        monkeypatch.setattr(
            migrations,
            "MIGRATIONS",
            tuple(migration for migration in all_migrations if migration[0] <= 20),
        )
        assert migrations.run_migrations(con, database_path) == [20]
        tracking = con.execute(
            """
            SELECT date_depot_dtc, date_depot_mobilis, date_ov, numero_ordre_virement
            FROM invoice_tracking WHERE invoice_id=?
            """,
            (invoice_id,),
        ).fetchone()
        assert tuple(tracking[:3]) == before
        assert tracking["numero_ordre_virement"] == ""
        assert "numero_ordre_virement" in {
            row[1] for row in con.execute("PRAGMA table_info(invoice_tracking)")
        }
        assert "numero_ordre_virement" in {
            row[1] for row in con.execute("PRAGMA table_info(invoice_lifecycle)")
        }
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        con.close()


def test_v21_adds_bpu_active_marker_without_deleting_existing_catalog(tmp_path, monkeypatch):
    database_path = tmp_path / "v20-bpu-catalog.sqlite3"
    with closing(sqlite3.connect(database_path)) as con:
        con.row_factory = sqlite3.Row
        con.executescript("""
            CREATE TABLE bpu_items(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                article_number INTEGER NOT NULL UNIQUE,
                designation TEXT NOT NULL,
                unite TEXT NOT NULL,
                pu_ht NUMERIC NOT NULL,
                categorie TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO bpu_items(article_number,designation,unite,pu_ht,categorie)
            VALUES(1,'Article historique','U',10,'fourniture');
        """)
        migrations.ensure_migration_table(con)
        con.executemany(
            "INSERT INTO schema_migrations(version, name) VALUES(?, ?)",
            [(version, name) for version, name, _ in migrations.MIGRATIONS if version < 21],
        )
        monkeypatch.setattr(
            migrations,
            "MIGRATIONS",
            tuple(migration for migration in migrations.MIGRATIONS if migration[0] == 21),
        )

        assert migrations.run_migrations(con, database_path) == [21]
        row = con.execute(
            "SELECT article_number,is_active FROM bpu_items WHERE article_number=1"
        ).fetchone()
        assert tuple(row) == (1, 1)
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []


def test_v22_moves_global_contract_reference_to_each_client_and_drops_address(tmp_path, monkeypatch):
    database_path = tmp_path / "v21-client-contract.sqlite3"
    con, _, _, _, _ = legacy_database(database_path)
    all_migrations = migrations.MIGRATIONS
    try:
        migrations.ensure_migration_table(con)
        monkeypatch.setattr(
            migrations,
            "MIGRATIONS",
            tuple(migration for migration in all_migrations if migration[0] <= 21),
        )
        assert migrations.run_migrations(con, database_path) == list(range(1, 22))
        con.execute(
            "UPDATE contract_settings SET reference_contrat='CTR-LEGACY-2026' WHERE id=1"
        )
        con.execute("UPDATE clients SET adresse='Adresse client à supprimer'")
        con.execute(
            "INSERT INTO clients(raison_sociale, sigle, adresse) VALUES('Second client', 'SECOND', 'Autre adresse')"
        )
        con.commit()

        monkeypatch.setattr(
            migrations,
            "MIGRATIONS",
            tuple(migration for migration in all_migrations if migration[0] == 22),
        )
        assert migrations.run_migrations(con, database_path) == [22]

        columns = {row[1] for row in con.execute("PRAGMA table_info(clients)")}
        assert "adresse" not in columns
        assert "reference_contrat" in columns
        assert {
            row[0]
            for row in con.execute("SELECT reference_contrat FROM clients")
        } == {"CTR-LEGACY-2026"}
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        con.close()
