import sqlite3

import pytest

import db as db_module
from services.auth import set_user_permission, user_has_permission
from services.invoice_lifecycle import (
    AWAITING_PAYMENT,
    BROUILLON,
    CANCELLED,
    DEPOSITED_DTC,
    PAID,
    READY_DTC,
    InvoiceLifecycleError,
    InvoiceLockedError,
    InvoicePermissionError,
    calculate_status,
    cancel_invoice,
    get_invoice_lifecycle,
    record_invoice_export,
    restore_cancelled_invoice,
    update_payment_reference,
    update_invoice_fields,
    update_tracking_dates,
)


pytestmark = pytest.mark.integration


def seeded_database(tmp_path, monkeypatch):
    database_path = tmp_path / "lifecycle.sqlite3"
    monkeypatch.setattr(db_module, "DB_PATH", database_path)
    db_module.invalidate_schema_cache(database_path)
    with db_module.db() as con:
        users = {}
        for role in ("admin", "editor", "viewer"):
            user_id = con.execute(
                """
                INSERT INTO users(username, password_hash, role, company_branch_id)
                VALUES(?, 'hash', ?, 1)
                """,
                (role, role),
            ).lastrowid
            users[role] = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        direction_id = con.execute(
            """
            INSERT INTO mobilis_directions(doit_nom, direction_regionale)
            VALUES('Mobilis', 'Alger')
            """
        ).lastrowid
        po_id = con.execute(
            """
            INSERT INTO purchase_orders(numero_bc, mobilis_direction_id, type_bc)
            VALUES('BC-LIFE', ?, 'CONSTRUCTION')
            """,
            (direction_id,),
        ).lastrowid
        site_id = con.execute(
            """
            INSERT INTO sites(purchase_order_id, code_site, nom_site)
            VALUES(?, 'SITE-LIFE', 'Lifecycle site')
            """,
            (po_id,),
        ).lastrowid
        invoice_id = con.execute(
            """
            INSERT INTO invoices(
                invoice_number, invoice_type, purchase_order_id, site_id,
                invoice_date, total_ht, retenue_garantie,
                montant_ht_apres_rg, tva, total_ttc, created_by, updated_by
            )
            VALUES(
                'FAC-LIFE', 'CONSTRUCTION', ?, ?, '2026-08-01',
                1000, 50, 950, 180.5, 1130.5, 'editor', 'editor'
            )
            """,
            (po_id, site_id),
        ).lastrowid
        line_id = con.execute(
            """
            INSERT INTO invoice_lines(
                invoice_id, article_number, designation_snapshot,
                unite_snapshot, pu_ht_snapshot, categorie_snapshot,
                quantite, montant_ht
            )
            VALUES(?, 1, 'Article', 'U', 1000, 'fourniture', 1, 1000)
            """,
            (invoice_id,),
        ).lastrowid
    return database_path, users, invoice_id, line_id


def test_calculate_status_covers_the_full_lifecycle():
    invoice = {
        "purchase_order_id": 1,
        "invoice_type": "CONSTRUCTION",
        "invoice_number": "FAC-1",
        "invoice_date": "2026-08-01",
        "total_ttc": 100,
        "site_id": 1,
        "cancelled_at": None,
    }
    tracking = {
        "date_depot_dtc": None,
        "date_depot_mobilis": None,
        "date_ov": None,
    }
    assert calculate_status({**invoice, "total_ttc": 0}, tracking) == BROUILLON
    assert calculate_status(invoice, tracking) == READY_DTC
    tracking["date_depot_dtc"] = "2026-08-02"
    assert calculate_status(invoice, tracking) == DEPOSITED_DTC
    tracking["date_depot_mobilis"] = "2026-08-03"
    assert calculate_status(invoice, tracking) == AWAITING_PAYMENT
    tracking["date_ov"] = "2026-08-04"
    assert calculate_status(invoice, tracking) == PAID
    assert calculate_status({**invoice, "cancelled_at": "2026-08-05"}, tracking) == CANCELLED


def test_tracking_transitions_are_authorized_validated_and_audited(tmp_path, monkeypatch):
    _, users, invoice_id, _ = seeded_database(tmp_path, monkeypatch)
    editor = users["editor"]

    invoice = update_tracking_dates(
        invoice_id,
        editor,
        date_depot_dtc="2026-08-02",
        actor="editor",
        device_name="TEST-PC",
    )
    assert invoice["lifecycle_status"] == DEPOSITED_DTC
    invoice = update_tracking_dates(
        invoice_id,
        editor,
        date_depot_mobilis="2026-08-03",
        actor="editor",
        device_name="TEST-PC",
    )
    assert invoice["lifecycle_status"] == AWAITING_PAYMENT
    invoice = update_tracking_dates(
        invoice_id,
        editor,
        date_ov="2026-08-04",
        actor="editor",
        device_name="TEST-PC",
    )
    assert invoice["lifecycle_status"] == PAID

    with db_module.db() as con:
        assert con.execute("SELECT depos FROM invoices WHERE id=?", (invoice_id,)).fetchone()[0] == 1
        assert con.execute(
            "SELECT COUNT(*) FROM invoice_tracking_events WHERE invoice_id=? AND actor='editor'",
            (invoice_id,),
        ).fetchone()[0] == 3
        assert con.execute(
            "SELECT COUNT(*) FROM audit_log WHERE entity_type='invoice' AND entity_id=?",
            (str(invoice_id),),
        ).fetchone()[0] == 3


def test_payment_transfer_reference_is_persisted_authorized_and_audited(tmp_path, monkeypatch):
    _, users, invoice_id, _ = seeded_database(tmp_path, monkeypatch)
    editor = users["editor"]
    viewer = users["viewer"]

    with pytest.raises(InvoiceLifecycleError, match="Date depot Mobilis"):
        update_payment_reference(invoice_id, editor, "OV-2026-001")
    update_tracking_dates(invoice_id, editor, date_depot_dtc="2026-08-02")
    update_tracking_dates(invoice_id, editor, date_depot_mobilis="2026-08-03")
    with pytest.raises(InvoicePermissionError, match="invoice.mark_paid"):
        update_payment_reference(invoice_id, viewer, "OV-2026-001")

    updated = update_payment_reference(
        invoice_id,
        editor,
        "OV-2026-001",
        actor="editor",
        device_name="TEST-PC",
    )
    assert updated["numero_ordre_virement"] == "OV-2026-001"
    with pytest.raises(InvoiceLifecycleError, match="Motif obligatoire"):
        update_payment_reference(invoice_id, editor, "OV-2026-002")
    corrected = update_payment_reference(
        invoice_id,
        editor,
        "OV-2026-002",
        reason="Correction du numéro bancaire",
        actor="editor",
        device_name="TEST-PC",
    )
    assert corrected["numero_ordre_virement"] == "OV-2026-002"
    with db_module.db() as con:
        event = con.execute(
            """
            SELECT old_value, new_value, reason
            FROM invoice_tracking_events
            WHERE invoice_id=? AND event_type='PAYMENT_REFERENCE_UPDATED'
            ORDER BY id DESC LIMIT 1
            """,
            (invoice_id,),
        ).fetchone()
        assert tuple(event) == (
            "OV-2026-001",
            "OV-2026-002",
            "Correction du numéro bancaire",
        )


def test_tracking_rejects_invalid_order_and_incomplete_invoice(tmp_path, monkeypatch):
    _, users, invoice_id, _ = seeded_database(tmp_path, monkeypatch)
    editor = users["editor"]
    with pytest.raises(InvoiceLifecycleError, match="Date depot DTC"):
        update_tracking_dates(
            invoice_id,
            editor,
            date_depot_mobilis="2026-08-03",
        )
    with pytest.raises(InvoiceLifecycleError, match="Date DTC"):
        update_tracking_dates(
            invoice_id,
            editor,
            date_depot_dtc="2026-07-31",
        )

    with db_module.db() as con:
        draft_id = con.execute(
            "INSERT INTO invoices(invoice_number, created_by) VALUES('DRAFT', 'editor')"
        ).lastrowid
    with pytest.raises(InvoiceLifecycleError, match="Facture incomplete"):
        update_tracking_dates(
            draft_id,
            editor,
            date_depot_dtc="2026-08-02",
        )


def test_viewer_cannot_change_tracking_or_export(tmp_path, monkeypatch):
    _, users, invoice_id, _ = seeded_database(tmp_path, monkeypatch)
    viewer = users["viewer"]

    with pytest.raises(InvoicePermissionError, match="invoice.deposit_dtc"):
        update_tracking_dates(
            invoice_id,
            viewer,
            date_depot_dtc="2026-08-02",
        )
    with pytest.raises(InvoicePermissionError, match="invoice.export"):
        record_invoice_export(invoice_id, "xlsx", viewer)


def test_issued_invoice_is_locked_at_database_and_service_layers(tmp_path, monkeypatch):
    _, users, invoice_id, line_id = seeded_database(tmp_path, monkeypatch)
    editor = users["editor"]
    admin = users["admin"]
    update_tracking_dates(invoice_id, editor, date_depot_dtc="2026-08-02")

    with db_module.db() as con:
        with pytest.raises(sqlite3.IntegrityError, match="Issued invoice is locked"):
            con.execute("UPDATE invoices SET total_ttc=2000 WHERE id=?", (invoice_id,))
        with pytest.raises(sqlite3.IntegrityError, match="lines are locked"):
            con.execute("DELETE FROM invoice_lines WHERE id=?", (line_id,))

    with pytest.raises(InvoicePermissionError, match="invoice.unlock"):
        update_invoice_fields(invoice_id, {"total_ttc": 1200}, editor, reason="Correction")
    with pytest.raises(InvoiceLifecycleError, match="Motif obligatoire"):
        update_invoice_fields(invoice_id, {"total_ttc": 1200}, admin)

    updated = update_invoice_fields(
        invoice_id,
        {"total_ttc": 1200},
        admin,
        reason="Correction comptable autorisee",
        actor="admin",
        device_name="TEST-PC",
    )
    assert updated["total_ttc"] == 1200
    with db_module.db() as con:
        assert con.execute("SELECT COUNT(*) FROM invoice_unlock_authorizations").fetchone()[0] == 0
        event = con.execute(
            """
            SELECT field_name, reason FROM invoice_tracking_events
            WHERE invoice_id=? AND event_type='INVOICE_FIELD_UPDATED'
            """,
            (invoice_id,),
        ).fetchone()
        assert tuple(event) == ("total_ttc", "Correction comptable autorisee")


def test_cancelled_invoice_is_locked_and_can_be_restored(tmp_path, monkeypatch):
    _, users, invoice_id, _ = seeded_database(tmp_path, monkeypatch)
    editor = users["editor"]
    admin = users["admin"]
    update_tracking_dates(invoice_id, editor, date_depot_dtc="2026-08-02")

    cancelled = cancel_invoice(
        invoice_id,
        admin,
        "Facture remplacee par un avoir",
        actor="admin",
        device_name="TEST-PC",
    )
    assert cancelled["lifecycle_status"] == CANCELLED
    with pytest.raises(InvoiceLockedError, match="annulee"):
        update_tracking_dates(
            invoice_id,
            editor,
            date_depot_mobilis="2026-08-03",
        )
    restored = restore_cancelled_invoice(
        invoice_id,
        admin,
        "Annulation saisie par erreur",
        actor="admin",
        device_name="TEST-PC",
    )
    assert restored["lifecycle_status"] == DEPOSITED_DTC


def test_user_permission_override_is_effective(tmp_path, monkeypatch):
    _, users, _, _ = seeded_database(tmp_path, monkeypatch)
    editor = users["editor"]
    assert user_has_permission(editor, "invoice.export")

    set_user_permission(editor["id"], "invoice.export", False, actor="admin")
    with db_module.db() as con:
        refreshed = con.execute("SELECT * FROM users WHERE id=?", (editor["id"],)).fetchone()
    assert not user_has_permission(refreshed, "invoice.export")
