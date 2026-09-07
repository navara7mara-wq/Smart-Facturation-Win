from datetime import date
from decimal import Decimal
import sqlite3

import pytest

import db as db_module
from services.dashboard import DashboardFilters, load_dashboard
from services.invoice_lifecycle import (
    AWAITING_PAYMENT,
    DEPOSITED_DTC,
    PAID,
    READY_DTC,
    update_tracking_dates,
)


pytestmark = pytest.mark.integration


def _seed_workflow(tmp_path, monkeypatch):
    database_path = tmp_path / "workflow.sqlite3"
    monkeypatch.setattr(db_module, "DB_PATH", database_path)
    db_module.invalidate_schema_cache(database_path)
    with db_module.db() as con:
        user_id = con.execute(
            "INSERT INTO users(username, password_hash, role, company_branch_id) VALUES('operator', 'hash', 'editor', 1)"
        ).lastrowid
        operator = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        direction_id = con.execute(
            """
            INSERT INTO mobilis_directions(doit_nom, direction_regionale)
            VALUES('Mobilis', 'Direction integration')
            """
        ).lastrowid
        mgc_po_id = con.execute(
            """
            INSERT INTO purchase_orders(
                numero_bc, date_bc, mobilis_direction_id, type_bc, objet, montant_ttc
            ) VALUES('BC-MGC-WORKFLOW', '2026-01-01', ?, 'MGC', 'Maintenance', 2261)
            """,
            (direction_id,),
        ).lastrowid
        ndc_po_id = con.execute(
            """
            INSERT INTO purchase_orders(
                numero_bc, date_bc, mobilis_direction_id, type_bc, objet, montant_ttc
            ) VALUES('BC-NDC-WORKFLOW', '2026-01-01', ?, 'NDC', 'Multi-sites', 2380)
            """,
            (direction_id,),
        ).lastrowid
        mgc_site_id = con.execute(
            """
            INSERT INTO sites(purchase_order_id, code_site, nom_site)
            VALUES(?, 'MGC-SITE', 'Site MGC')
            """,
            (mgc_po_id,),
        ).lastrowid
        ndc_site_ids = [
            con.execute(
                """
                INSERT INTO sites(purchase_order_id, code_site, nom_site)
                VALUES(?, ?, ?)
                """,
                (ndc_po_id, f"NDC-SITE-{index}", f"Site NDC {index}"),
            ).lastrowid
            for index in (1, 2)
        ]
        mgc_invoice_id = con.execute(
            """
            INSERT INTO invoices(
                invoice_number, invoice_type, purchase_order_id, site_id,
                invoice_date, total_ht, retenue_garantie,
                montant_ht_apres_rg, tva, total_ttc, created_by, updated_by
            ) VALUES(
                'FAC-MGC-WORKFLOW', 'CONSTRUCTION', ?, ?, '2026-01-05',
                2000, 100, 1900, 361, 2261, 'operator', 'operator'
            )
            """,
            (mgc_po_id, mgc_site_id),
        ).lastrowid
        con.execute(
            """
            INSERT INTO invoice_lines(
                invoice_id, article_number, designation_snapshot, unite_snapshot,
                pu_ht_snapshot, categorie_snapshot, quantite, montant_ht
            ) VALUES(?, 1, 'Maintenance', 'U', 2000, 'prestation', 1, 2000)
            """,
            (mgc_invoice_id,),
        )
        ndc_invoice_id = con.execute(
            """
            INSERT INTO invoices(
                invoice_number, invoice_type, purchase_order_id, invoice_date,
                total_ht, retenue_garantie, montant_ht_apres_rg, tva, total_ttc,
                created_by, updated_by
            ) VALUES(
                'FAC-NDC-WORKFLOW', 'NDC', ?, '2026-01-06',
                2000, 100, 1900, 361, 2261, 'operator', 'operator'
            )
            """,
            (ndc_po_id,),
        ).lastrowid
        con.executemany(
            "INSERT INTO invoice_sites(invoice_id, site_id) VALUES(?, ?)",
            ((ndc_invoice_id, site_id) for site_id in ndc_site_ids),
        )
        con.execute(
            """
            INSERT INTO invoice_lines(
                invoice_id, article_number, designation_snapshot, unite_snapshot,
                pu_ht_snapshot, categorie_snapshot, quantite, montant_ht
            ) VALUES(?, 6, 'Forfait NDC', 'Site', 1000, 'ndc', 2, 2000)
            """,
            (ndc_invoice_id,),
        )
    return operator, mgc_invoice_id, ndc_invoice_id


def test_invoice_workflow_updates_status_dashboard_lock_and_audit(tmp_path, monkeypatch):
    operator, invoice_id, ndc_invoice_id = _seed_workflow(tmp_path, monkeypatch)

    with db_module.db() as con:
        assert con.execute(
            "SELECT lifecycle_status FROM invoice_lifecycle WHERE id=?", (invoice_id,)
        ).fetchone()[0] == READY_DTC
        assert con.execute(
            "SELECT lifecycle_status FROM invoice_lifecycle WHERE id=?", (ndc_invoice_id,)
        ).fetchone()[0] == READY_DTC
        initial = load_dashboard(con, as_of=date(2026, 4, 10))
    assert initial["kpis"]["issued"] == {"count": 0, "amount": Decimal("0")}
    assert initial["status_cards"][READY_DTC] == {
        "count": 2,
        "amount": Decimal("4522"),
    }

    deposited = update_tracking_dates(
        invoice_id,
        operator,
        date_depot_dtc="2026-01-07",
        actor="operator",
        device_name="INTEGRATION-PC",
    )
    assert deposited["lifecycle_status"] == DEPOSITED_DTC
    waiting = update_tracking_dates(
        invoice_id,
        operator,
        date_depot_mobilis="2026-01-08",
        actor="operator",
        device_name="INTEGRATION-PC",
    )
    assert waiting["lifecycle_status"] == AWAITING_PAYMENT

    with db_module.db() as con:
        dashboard = load_dashboard(
            con,
            DashboardFilters(type_bc="MGC"),
            as_of=date(2026, 4, 10),
        )
        assert dashboard["kpis"]["issued"] == {
            "count": 1,
            "amount": Decimal("2261"),
        }
        assert dashboard["overdue"] == {
            "count": 1,
            "amount": Decimal("2261"),
        }
        with pytest.raises(sqlite3.IntegrityError, match="Issued invoice is locked"):
            con.execute("UPDATE invoices SET total_ttc=1 WHERE id=?", (invoice_id,))

    paid = update_tracking_dates(
        invoice_id,
        operator,
        date_ov="2026-01-09",
        actor="operator",
        device_name="INTEGRATION-PC",
    )
    assert paid["lifecycle_status"] == PAID
    with db_module.db() as con:
        assert con.execute(
            """
            SELECT COUNT(*) FROM invoice_tracking_events
            WHERE invoice_id=? AND event_type='TRACKING_UPDATED'
              AND actor='operator' AND device_name='INTEGRATION-PC'
            """,
            (invoice_id,),
        ).fetchone()[0] == 3
        assert con.execute(
            """
            SELECT COUNT(*) FROM audit_log
            WHERE entity_type='invoice' AND entity_id=?
              AND action='invoice.tracking.update'
            """,
            (str(invoice_id),),
        ).fetchone()[0] == 3
        final = load_dashboard(con, as_of=date(2026, 4, 10))
    assert final["kpis"]["paid"] == {"count": 1, "amount": Decimal("2261")}
    assert final["kpis"]["outstanding"] == {"count": 0, "amount": Decimal("0")}
