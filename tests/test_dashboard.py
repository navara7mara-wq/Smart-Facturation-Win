from datetime import date
from decimal import Decimal

import db as db_module
from services.dashboard import (
    DashboardFilters,
    load_dashboard,
    normalize_dashboard_filters,
)
from services.invoice_lifecycle import (
    AWAITING_PAYMENT,
    BROUILLON,
    DEPOSITED_DTC,
    PAID,
    READY_DTC,
)


import pytest


pytestmark = pytest.mark.integration


def _seed_dashboard(tmp_path, monkeypatch):
    database_path = tmp_path / "dashboard.sqlite3"
    monkeypatch.setattr(db_module, "DB_PATH", database_path)
    db_module.invalidate_schema_cache(database_path)
    with db_module.db() as con:
        alger = con.execute(
            """
            INSERT INTO client_directions(client_id, company_branch_id, name, sigle)
            VALUES(1, 1, 'Alger', 'DR ALGER')
            """
        ).lastrowid
        oran = con.execute(
            """
            INSERT INTO client_directions(client_id, company_branch_id, name, sigle)
            VALUES(1, 1, 'Oran', 'DR ORAN')
            """
        ).lastrowid
        po_acq = con.execute(
            """
            INSERT INTO purchase_orders(numero_bc, client_direction_id, company_branch_id, type_bc)
            VALUES('BC-ACQ', ?, 1, 'ACQUISITION')
            """,
            (alger,),
        ).lastrowid
        po_mgc = con.execute(
            """
            INSERT INTO purchase_orders(numero_bc, client_direction_id, company_branch_id, type_bc)
            VALUES('BC-MGC', ?, 1, 'MGC')
            """,
            (alger,),
        ).lastrowid
        po_ndc = con.execute(
            """
            INSERT INTO purchase_orders(numero_bc, client_direction_id, company_branch_id, type_bc)
            VALUES('BC-NDC-WAIT', ?, 1, 'NDC')
            """,
            (oran,),
        ).lastrowid
        po_ndc_paid = con.execute(
            """
            INSERT INTO purchase_orders(numero_bc, client_direction_id, company_branch_id, type_bc)
            VALUES('BC-NDC-PAID', ?, 1, 'NDC')
            """,
            (oran,),
        ).lastrowid

        site_ids = []
        for index, po_id in enumerate((po_acq, po_acq, po_mgc, po_mgc), start=1):
            site_ids.append(
                con.execute(
                    """
                    INSERT INTO sites(purchase_order_id, code_site, nom_site)
                    VALUES(?, ?, ?)
                    """,
                    (po_id, f"SITE-{index}", f"Site {index}"),
                ).lastrowid
            )
        ndc_site_ids = [
            con.execute(
                """
                INSERT INTO sites(purchase_order_id, code_site, nom_site)
                VALUES(?, 'SITE-5', 'Site 5')
                """,
                (po_ndc,),
            ).lastrowid,
            con.execute(
                """
                INSERT INTO sites(purchase_order_id, code_site, nom_site)
                VALUES(?, 'SITE-6', 'Site 6')
                """,
                (po_ndc_paid,),
            ).lastrowid,
        ]

        def invoice(number, invoice_type, po_id, site_id, invoice_date, total_ttc):
            return con.execute(
                """
                INSERT INTO invoices(
                    invoice_number, invoice_type, purchase_order_id, site_id,
                    invoice_date, total_ttc, created_by, updated_by
                ) VALUES(?, ?, ?, ?, ?, ?, 'tester', 'tester')
                """,
                (number, invoice_type, po_id, site_id, invoice_date, total_ttc),
            ).lastrowid

        invoice("DRAFT-1", "ACQUISITION", po_acq, site_ids[0], None, 0)
        invoice("READY-1", "ACQUISITION", po_acq, site_ids[1], "2026-01-05", 100)
        deposited = invoice(
            "DTC-1", "CONSTRUCTION", po_mgc, site_ids[2], "2026-01-10", 200
        )
        overdue = invoice(
            "WAIT-OLD", "CONSTRUCTION", po_mgc, site_ids[3], "2026-01-12", 300
        )
        waiting = invoice("WAIT-NEW", "NDC", po_ndc, None, "2026-03-01", 400)
        paid = invoice("PAID-1", "NDC", po_ndc_paid, None, "2026-02-01", 500)
        con.executemany(
            "INSERT INTO invoice_sites(invoice_id, site_id) VALUES(?, ?)",
            ((waiting, ndc_site_ids[0]), (paid, ndc_site_ids[1])),
        )
        con.execute(
            "UPDATE invoice_tracking SET date_depot_dtc='2026-01-11' WHERE invoice_id=?",
            (deposited,),
        )
        con.execute(
            """
            UPDATE invoice_tracking
            SET date_depot_dtc='2026-01-13', date_depot_mobilis='2026-01-14'
            WHERE invoice_id=?
            """,
            (overdue,),
        )
        con.execute(
            """
            UPDATE invoice_tracking
            SET date_depot_dtc='2026-03-02', date_depot_mobilis='2026-03-15'
            WHERE invoice_id=?
            """,
            (waiting,),
        )
        con.execute(
            """
            UPDATE invoice_tracking
            SET date_depot_dtc='2026-02-02', date_depot_mobilis='2026-02-03',
                date_ov='2026-02-10'
            WHERE invoice_id=?
            """,
            (paid,),
        )
    return database_path, oran


def test_dashboard_metrics_follow_invoice_lifecycle(tmp_path, monkeypatch):
    _seed_dashboard(tmp_path, monkeypatch)
    with db_module.db() as con:
        result = load_dashboard(con, as_of=date(2026, 4, 10))

    assert result["kpis"]["issued"] == {"count": 4, "amount": Decimal("1400")}
    assert result["kpis"]["total_issued"]["amount"] == Decimal("1400")
    assert result["kpis"]["paid"] == {"count": 1, "amount": Decimal("500")}
    assert result["kpis"]["outstanding"] == {
        "count": 3,
        "amount": Decimal("900"),
    }
    assert result["kpis"]["drafts"] == {"count": 1, "amount": Decimal("0")}
    assert result["status_cards"][BROUILLON]["count"] == 1
    assert result["status_cards"][READY_DTC]["count"] == 1
    assert result["status_cards"][DEPOSITED_DTC]["count"] == 1
    assert result["status_cards"][AWAITING_PAYMENT]["count"] == 2
    assert result["status_cards"][PAID]["count"] == 1


def test_dashboard_overdue_and_operational_queues_are_ordered(tmp_path, monkeypatch):
    _seed_dashboard(tmp_path, monkeypatch)
    with db_module.db() as con:
        result = load_dashboard(con, as_of=date(2026, 4, 10))

    assert result["overdue"] == {"count": 1, "amount": Decimal("300")}
    assert result["overdue_rows"][0]["invoice_number"] == "WAIT-OLD"
    assert result["overdue_rows"][0]["days_overdue"] == 86
    assert result["overdue_by_direction"] == [
        {"direction": "Alger", "count": 1, "amount": Decimal("300")}
    ]
    assert [row["invoice_number"] for row in result["ready_dtc"]] == ["READY-1"]
    assert [row["invoice_number"] for row in result["ready_mobilis"]] == ["DTC-1"]
    assert [row["invoice_number"] for row in result["awaiting_payment"]] == [
        "WAIT-OLD",
        "WAIT-NEW",
    ]


def test_dashboard_filters_use_effective_bc_type_and_invoice_date(tmp_path, monkeypatch):
    _, oran = _seed_dashboard(tmp_path, monkeypatch)
    with db_module.db() as con:
        mgc = load_dashboard(
            con,
            DashboardFilters(type_bc="MGC"),
            as_of=date(2026, 4, 10),
        )
        ndc = load_dashboard(
            con,
            DashboardFilters(type_bc="NDC"),
            as_of=date(2026, 4, 10),
        )
        march_oran = load_dashboard(
            con,
            DashboardFilters(
                date_from="2026-03-01",
                date_to="2026-03-31",
                direction=str(oran),
            ),
            as_of=date(2026, 4, 10),
        )

    assert mgc["status_cards"][DEPOSITED_DTC]["amount"] == Decimal("200")
    assert mgc["status_cards"][AWAITING_PAYMENT]["amount"] == Decimal("300")
    assert ndc["status_cards"][AWAITING_PAYMENT]["amount"] == Decimal("400")
    assert ndc["status_cards"][PAID]["amount"] == Decimal("500")
    assert march_oran["visible_count"] == 1
    assert march_oran["kpis"]["outstanding"]["amount"] == Decimal("400")


def test_status_filter_changes_kpis_but_preserves_stage_distribution(tmp_path, monkeypatch):
    _seed_dashboard(tmp_path, monkeypatch)
    with db_module.db() as con:
        result = load_dashboard(
            con,
            DashboardFilters(status=AWAITING_PAYMENT),
            as_of=date(2026, 4, 10),
        )

    assert result["visible_count"] == 2
    assert result["kpis"]["issued"]["amount"] == Decimal("700")
    assert result["kpis"]["outstanding"]["amount"] == Decimal("700")
    assert result["status_cards"][PAID]["count"] == 1
    assert result["ready_dtc"] == []


def test_dashboard_filter_normalization_is_safe_and_deterministic():
    filters, notices = normalize_dashboard_filters(
        {
            "date_from": "2026-04-30",
            "date_to": "2026-04-01",
            "type_bc": "UNKNOWN",
            "status": "UNKNOWN",
        }
    )
    assert filters.date_from == "2026-04-01"
    assert filters.date_to == "2026-04-30"
    assert filters.type_bc == ""
    assert filters.status == ""
    assert notices == ("Les dates de la période ont été réordonnées.",)
