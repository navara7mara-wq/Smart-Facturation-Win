from datetime import date
from decimal import Decimal

import pytest

import db as db_module
from services.dashboard import DashboardFilters, load_dashboard
from services.invoice_lifecycle import READY_DTC


pytestmark = pytest.mark.integration


def test_dashboard_aggregates_two_thousand_invoices(tmp_path, monkeypatch):
    database_path = tmp_path / "large.sqlite3"
    monkeypatch.setattr(db_module, "DB_PATH", database_path)
    db_module.invalidate_schema_cache(database_path)

    count = 2_000
    with db_module.db() as connection:
        connection.executemany(
            """
            INSERT INTO purchase_orders(
                id, numero_bc, client_direction_id, company_branch_id, type_bc
            ) VALUES(?, ?, 1, 1, 'ACQUISITION')
            """,
            ((index, f"BC-LARGE-{index:04d}") for index in range(1, count + 1)),
        )
        connection.executemany(
            """
            INSERT INTO sites(id, purchase_order_id, code_site, nom_site, typologie_site)
            VALUES(?, ?, ?, ?, 'A12')
            """,
            (
                (index, index, f"SITE-{index:04d}", f"Site {index:04d}")
                for index in range(1, count + 1)
            ),
        )
        connection.executemany(
            """
            INSERT INTO invoices(
                id, invoice_number, invoice_type, purchase_order_id, site_id,
                invoice_date, total_ttc, typologie_snapshot, created_by, updated_by
            ) VALUES(?, ?, 'ACQUISITION', ?, ?, '2026-06-15', 1000, 'A12', 'load-test', 'load-test')
            """,
            (
                (index, f"FAC-LARGE-{index:04d}", index, index)
                for index in range(1, count + 1)
            ),
        )

    with db_module.db() as connection:
        result = load_dashboard(
            connection,
            DashboardFilters(date_from="2026-06-01", date_to="2026-06-30"),
            as_of=date(2026, 8, 24),
        )
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

    assert result["visible_count"] == count
    assert result["status_cards"][READY_DTC] == {
        "count": count,
        "amount": Decimal("2000000"),
    }
    assert len(result["ready_dtc"]) == 6
    assert result["ready_dtc"][0]["invoice_number"] == "FAC-LARGE-0001"
