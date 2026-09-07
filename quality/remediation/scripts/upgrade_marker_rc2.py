"""Seed and verify isolated 2.5.1 -> 2.5.2 upgrade security/data markers."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.auth import hash_password, verify_password


MARKER = "QA_RC2_UPGRADE_251_TO_252"
UPGRADE_PASSWORD = "Upgrade251Pass123"
INVOICE_MARKER = "QA-RC2-HIST-001"
HISTORICAL_FINANCIALS = (1000, 50, 950, 180.5, 1130.5)


def has_column(connection, table, column):
    return column in {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def seed_historical_invoice(connection, client_id):
    branch_id = connection.execute(
        """
        INSERT INTO company_branches(name, code, address, sigle, created_by, updated_by)
        VALUES('QA RC2 Upgrade Branch', 'QA-RC2-UP', 'QA only', 'QA-UP',
               'qa-remediation', 'qa-remediation')
        """
    ).lastrowid
    direction_id = connection.execute(
        """
        INSERT INTO client_directions(
            client_id, company_branch_id, name, address, sigle, created_by, updated_by
        ) VALUES(?, ?, 'QA RC2 Upgrade Direction', 'QA only', 'QA-DIR',
                 'qa-remediation', 'qa-remediation')
        """,
        (client_id, branch_id),
    ).lastrowid
    purchase_order_id = connection.execute(
        """
        INSERT INTO purchase_orders(
            numero_bc, date_bc, client_direction_id, company_branch_id,
            type_bc, objet, montant_ttc, created_by, updated_by
        ) VALUES('QA-RC2-BC-UPGRADE', '2026-08-01', ?, ?, 'ACQUISITION',
                 'QA historical upgrade invoice', 1130.5,
                 'qa-remediation', 'qa-remediation')
        """,
        (direction_id, branch_id),
    ).lastrowid
    site_id = connection.execute(
        """
        INSERT INTO sites(
            purchase_order_id, code_site, nom_site, region, typologie_site,
            bet, created_by, updated_by
        ) VALUES(?, 'QA-RC2-SITE-UP', 'QA historical site', 'QA', 'A12',
                 'QA BET', 'qa-remediation', 'qa-remediation')
        """,
        (purchase_order_id,),
    ).lastrowid
    invoice_id = connection.execute(
        """
        INSERT INTO invoices(
            invoice_number, invoice_type, purchase_order_id, site_id,
            invoice_date, total_ht, retenue_garantie, montant_ht_apres_rg,
            tva, total_ttc, montant_en_lettres, remarque, depos,
            numbering_system, typologie_snapshot, typologie_label_snapshot,
            created_by, updated_by
        ) VALUES(?, 'ACQUISITION', ?, ?, '2026-08-02', ?, ?, ?, ?, ?,
                 'MILLE CENT TRENTE DINARS ET CINQUANTE CENTIMES',
                 'QA historical upgrade marker', 0, 'GENERAL', 'A12',
                 'A12 (QA)', 'qa-remediation', 'qa-remediation')
        """,
        (INVOICE_MARKER, purchase_order_id, site_id, *HISTORICAL_FINANCIALS),
    ).lastrowid
    connection.execute(
        """
        INSERT INTO invoice_lines(
            invoice_id, article_number, designation_snapshot, unite_snapshot,
            pu_ht_snapshot, categorie_snapshot, quantite, montant_ht,
            source_reference_type, mapping_version
        ) VALUES(?, 9902, 'QA historical upgrade line', 'U', 100, 'acquisition',
                 10, 1000, 'GENERAL', 'QA-UPGRADE-251')
        """,
        (invoice_id,),
    )
    return invoice_id


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("seed", "verify"))
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    database = args.database.resolve()
    if "build\\upgrade-251-to-252" not in str(database):
        raise RuntimeError("Non-QA upgrade database refused")

    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        if args.action == "seed":
            client_id = connection.execute(
                """
                INSERT INTO clients(
                    raison_sociale, sigle, rgc, nif, adresse, logo_path,
                    is_active, created_by, updated_by
                ) VALUES(?,?,?,?,?,'',1,'qa-remediation','qa-remediation')
                """,
                (MARKER, "QA-RC2", "QA-RGC-RC2", "QA-NIF-RC2", "QA RC-2 upgrade address"),
            ).lastrowid
            seed_historical_invoice(connection, client_id)
            admin = connection.execute(
                "SELECT id FROM users WHERE username='admin'"
            ).fetchone()
            connection.execute(
                """
                UPDATE users
                SET password_hash=?, role='super_admin', company_branch_id=NULL,
                    is_active=1, must_change_password=0, failed_attempts=0,
                    locked_until=NULL, last_login_at='2026-08-24 12:00:00'
                WHERE id=?
                """,
                (hash_password(UPGRADE_PASSWORD), admin["id"]),
            )
            connection.execute(
                """
                INSERT INTO user_sessions(token, user_id, expires_at, csrf_token)
                VALUES('qa-pre-upgrade-session', ?, '2099-01-01T00:00:00', 'qa-csrf')
                """,
                (admin["id"],),
            )
            connection.commit()

        marker = connection.execute(
            "SELECT raison_sociale, sigle, adresse FROM clients WHERE raison_sociale=?",
            (MARKER,),
        ).fetchone()
        admin = connection.execute(
            """
            SELECT id, username, role, must_change_password, password_hash
            FROM users WHERE username='admin'
            """
        ).fetchone()
        session_count = connection.execute(
            "SELECT COUNT(*) FROM user_sessions WHERE user_id=?", (admin["id"],)
        ).fetchone()[0]
        permission_count = connection.execute(
            "SELECT COUNT(*) FROM role_permissions WHERE role='super_admin'"
        ).fetchone()[0]
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        migration = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        invoice_columns = {row[1] for row in connection.execute("PRAGMA table_info(invoices)")}
        invoice_select = """
            SELECT id, invoice_number, purchase_order_id, site_id,
                   total_ht, retenue_garantie, montant_ht_apres_rg, tva, total_ttc
        """
        if {"rg_rate", "tva_rate"} <= invoice_columns:
            invoice_select += ", rg_rate, tva_rate"
        invoice_select += " FROM invoices WHERE invoice_number=?"
        historical_invoice = connection.execute(invoice_select, (INVOICE_MARKER,)).fetchone()
        historical_line = connection.execute(
            """
            SELECT article_number, pu_ht_snapshot, quantite, montant_ht
            FROM invoice_lines WHERE invoice_id=?
            """,
            (historical_invoice["id"],),
        ).fetchone() if historical_invoice else None

    result = {
        "action": args.action,
        "marker": list(marker) if marker else None,
        "admin_role": admin["role"],
        "admin_must_change_password": admin["must_change_password"],
        "upgrade_password_preserved": verify_password(UPGRADE_PASSWORD, admin["password_hash"]),
        "admin_session_count": session_count,
        "super_admin_permission_count": permission_count,
        "integrity_check": integrity,
        "foreign_key_violations": len(foreign_keys),
        "max_migration": migration,
        "invoice_columns": sorted(invoice_columns),
        "historical_invoice": dict(historical_invoice) if historical_invoice else None,
        "historical_line": dict(historical_line) if historical_line else None,
    }
    assert marker and admin["role"] == "super_admin"
    assert historical_invoice and historical_line
    assert tuple(historical_invoice[key] for key in (
        "total_ht", "retenue_garantie", "montant_ht_apres_rg", "tva", "total_ttc"
    )) == HISTORICAL_FINANCIALS
    assert tuple(historical_line) == (9902, 100, 10, 1000)
    assert result["upgrade_password_preserved"]
    assert integrity == "ok" and not foreign_keys and permission_count > 0
    if args.action == "verify":
        assert migration == 19
        assert {"rg_rate", "tva_rate"} <= invoice_columns
        assert tuple(historical_invoice[key] for key in ("rg_rate", "tva_rate")) == (5, 19)
        assert admin["must_change_password"] == 1
        assert session_count == 0
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
