from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import socket

from db import db
from services.auth import user_has_permission


BROUILLON = "BROUILLON"
READY_DTC = "READY_DTC"
DEPOSITED_DTC = "DEPOSITED_DTC"
AWAITING_PAYMENT = "AWAITING_PAYMENT"
PAID = "PAID"
CANCELLED = "CANCELLED"

STATUS_LABELS = {
    BROUILLON: "Brouillon de facture",
    READY_DTC: "Prête pour dépôt DTC",
    DEPOSITED_DTC: "Déposée DTC",
    AWAITING_PAYMENT: "En attente paiement",
    PAID: "Payée",
    CANCELLED: "Annulée",
}

TRACKING_PERMISSIONS = {
    "date_depot_dtc": "invoice.deposit_dtc",
    "date_depot_mobilis": "invoice.deposit_mobilis",
    "date_ov": "invoice.mark_paid",
}

PROTECTED_INVOICE_FIELDS = frozenset({
    "invoice_number",
    "invoice_type",
    "purchase_order_id",
    "site_id",
    "invoice_date",
    "total_ht",
    "retenue_garantie",
    "montant_ht_apres_rg",
    "tva",
    "total_ttc",
    "montant_en_lettres",
    "rg_rate",
    "tva_rate",
})

UNSET = object()


class InvoiceLifecycleError(ValueError):
    pass


class InvoiceNotFoundError(InvoiceLifecycleError):
    pass


class InvoiceLockedError(InvoiceLifecycleError):
    pass


class InvoicePermissionError(PermissionError):
    pass


def _value(record, key, default=None):
    if record is None:
        return default
    try:
        value = record[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


def _actor_name(user, actor=None):
    actor = (actor or str(_value(user, "username", ""))).strip()
    return actor or "system"


def _device_name(device_name=None):
    return (device_name or socket.gethostname() or "unknown").strip()


def _utc_timestamp():
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


@contextmanager
def _connection_scope(connection=None):
    owns_connection = connection is None
    con = connection or db()
    try:
        yield con
        if owns_connection:
            con.commit()
    except Exception:
        if owns_connection:
            con.rollback()
        raise
    finally:
        if owns_connection:
            con.close()


def _require_permission(connection, user, permission_code):
    if not user_has_permission(user, permission_code, connection):
        raise InvoicePermissionError(f"Permission requise: {permission_code}")


def _normalize_date(value, field_name):
    if value is None or str(value).strip() == "":
        return None
    normalized = str(value).strip()
    try:
        return date.fromisoformat(normalized).isoformat()
    except ValueError as exc:
        raise InvoiceLifecycleError(
            f"Format de date invalide pour {field_name}: YYYY-MM-DD requis."
        ) from exc


def _positive_amount(value):
    try:
        return Decimal(str(value or 0)) > 0
    except (InvalidOperation, ValueError):
        return False


def missing_required_fields(invoice, ndc_site_count=0):
    missing = []
    if not _value(invoice, "purchase_order_id"):
        missing.append("purchase_order_id")
    invoice_type = str(_value(invoice, "invoice_type", "") or "")
    if not invoice_type:
        missing.append("invoice_type")
    if not str(_value(invoice, "invoice_number", "") or "").strip():
        missing.append("invoice_number")
    if not _value(invoice, "invoice_date"):
        missing.append("invoice_date")
    if not _positive_amount(_value(invoice, "total_ttc", 0)):
        missing.append("total_ttc")
    if invoice_type == "NDC":
        if int(ndc_site_count or 0) < 1:
            missing.append("site_id")
    elif not _value(invoice, "site_id"):
        missing.append("site_id")
    return tuple(missing)


def calculate_status(invoice, tracking, ndc_site_count=0):
    if _value(invoice, "cancelled_at"):
        return CANCELLED
    if missing_required_fields(invoice, ndc_site_count):
        return BROUILLON
    if not _value(tracking, "date_depot_dtc"):
        return READY_DTC
    if not _value(tracking, "date_depot_mobilis"):
        return DEPOSITED_DTC
    if not _value(tracking, "date_ov"):
        return AWAITING_PAYMENT
    return PAID


def _fetch_lifecycle(connection, invoice_id):
    row = connection.execute(
        """
        SELECT lifecycle.*,
               (SELECT COUNT(*) FROM invoice_sites invoice_site
                WHERE invoice_site.invoice_id = lifecycle.id) AS ndc_site_count
        FROM invoice_lifecycle lifecycle
        WHERE lifecycle.id=? AND lifecycle.deleted_at IS NULL
        """,
        (invoice_id,),
    ).fetchone()
    if not row:
        raise InvoiceNotFoundError("Facture introuvable.")
    return row


def get_invoice_lifecycle(invoice_id, connection=None):
    with _connection_scope(connection) as con:
        return _fetch_lifecycle(con, invoice_id)


def _record_event(
    connection,
    invoice_id,
    event_type,
    field_name,
    old_value,
    new_value,
    actor,
    device_name,
    reason,
):
    connection.execute(
        """
        INSERT INTO invoice_tracking_events(
            invoice_id, event_type, field_name, old_value, new_value,
            actor, device_name, reason
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            invoice_id,
            event_type,
            field_name,
            None if old_value is None else str(old_value),
            None if new_value is None else str(new_value),
            actor,
            device_name,
            reason,
        ),
    )


def _record_audit(connection, actor, action, invoice_id, details):
    connection.execute(
        """
        INSERT INTO audit_log(actor, action, entity_type, entity_id, details)
        VALUES(?, ?, 'invoice', ?, ?)
        """,
        (actor, action, str(invoice_id), json.dumps(details, sort_keys=True)),
    )


def _validate_tracking_dates(invoice, final_dates, ndc_site_count):
    dtc = final_dates["date_depot_dtc"]
    mobilis = final_dates["date_depot_mobilis"]
    ov = final_dates["date_ov"]
    if mobilis and not dtc:
        raise InvoiceLifecycleError("Date depot DTC requise avant Date depot Mobilis.")
    if ov and not mobilis:
        raise InvoiceLifecycleError("Date depot Mobilis requise avant Date OV.")
    if dtc and mobilis and mobilis < dtc:
        raise InvoiceLifecycleError("Date Mobilis doit etre posterieure ou egale a Date DTC.")
    if mobilis and ov and ov < mobilis:
        raise InvoiceLifecycleError("Date OV doit etre posterieure ou egale a Date Mobilis.")
    invoice_date = _value(invoice, "invoice_date")
    if dtc and invoice_date and dtc < invoice_date:
        raise InvoiceLifecycleError("Date DTC doit etre posterieure ou egale a Date facture.")
    if dtc:
        missing = missing_required_fields(invoice, ndc_site_count)
        if missing:
            raise InvoiceLifecycleError(
                "Facture incomplete avant depot DTC: " + ", ".join(missing)
            )


def update_tracking_dates(
    invoice_id,
    user,
    *,
    date_depot_dtc=UNSET,
    date_depot_mobilis=UNSET,
    date_ov=UNSET,
    reason="",
    actor=None,
    device_name=None,
    connection=None,
):
    requested = {
        "date_depot_dtc": date_depot_dtc,
        "date_depot_mobilis": date_depot_mobilis,
        "date_ov": date_ov,
    }
    requested = {
        field: _normalize_date(value, field)
        for field, value in requested.items()
        if value is not UNSET
    }
    with _connection_scope(connection) as con:
        invoice = _fetch_lifecycle(con, invoice_id)
        if _value(invoice, "cancelled_at"):
            raise InvoiceLockedError("La facture annulee est verrouillee.")
        if not requested:
            return invoice

        actor_name = _actor_name(user, actor)
        device = _device_name(device_name)
        reason = (reason or "").strip()
        final_dates = {
            field: _value(invoice, field)
            for field in TRACKING_PERMISSIONS
        }
        changes = {}
        for field, new_value in requested.items():
            old_value = final_dates[field]
            if old_value == new_value:
                continue
            permission = TRACKING_PERMISSIONS[field]
            _require_permission(con, user, permission)
            if field == "date_depot_dtc" and old_value and new_value is None:
                _require_permission(con, user, "invoice.unlock")
            if old_value is not None and not reason:
                raise InvoiceLifecycleError(
                    f"Motif obligatoire pour corriger ou supprimer {field}."
                )
            final_dates[field] = new_value
            changes[field] = (old_value, new_value)

        if not changes:
            return invoice
        _validate_tracking_dates(invoice, final_dates, invoice["ndc_site_count"])
        status_before = invoice["lifecycle_status"]
        con.execute(
            """
            UPDATE invoice_tracking
            SET date_depot_dtc=?, date_depot_mobilis=?, date_ov=?,
                migration_review_required=CASE
                    WHEN ? IS NOT NULL THEN 0 ELSE migration_review_required
                END,
                updated_by=?, updated_at=CURRENT_TIMESTAMP
            WHERE invoice_id=?
            """,
            (
                final_dates["date_depot_dtc"],
                final_dates["date_depot_mobilis"],
                final_dates["date_ov"],
                final_dates["date_depot_dtc"],
                actor_name,
                invoice_id,
            ),
        )
        updated = _fetch_lifecycle(con, invoice_id)
        for field, (old_value, new_value) in changes.items():
            _record_event(
                con,
                invoice_id,
                "TRACKING_UPDATED",
                field,
                old_value,
                new_value,
                actor_name,
                device,
                reason,
            )
        _record_audit(
            con,
            actor_name,
            "invoice.tracking.update",
            invoice_id,
            {
                "changes": {
                    field: {"old": old, "new": new}
                    for field, (old, new) in changes.items()
                },
                "device": device,
                "reason": reason,
                "status_before": status_before,
                "status_after": updated["lifecycle_status"],
            },
        )
        return updated


def update_payment_reference(
    invoice_id,
    user,
    numero_ordre_virement,
    *,
    reason="",
    actor=None,
    device_name=None,
    connection=None,
):
    reference = str(numero_ordre_virement or "").strip()
    if len(reference) > 120:
        raise InvoiceLifecycleError(
            "Le numéro d’ordre de virement ne doit pas dépasser 120 caractères."
        )
    with _connection_scope(connection) as con:
        invoice = _fetch_lifecycle(con, invoice_id)
        if _value(invoice, "cancelled_at"):
            raise InvoiceLockedError("La facture annulee est verrouillee.")
        _require_permission(con, user, "invoice.mark_paid")
        if reference and not _value(invoice, "date_depot_mobilis"):
            raise InvoiceLifecycleError(
                "Date depot Mobilis requise avant le numéro d’ordre de virement."
            )
        old_reference = str(_value(invoice, "numero_ordre_virement", "") or "")
        if old_reference == reference:
            return invoice
        reason = (reason or "").strip()
        if old_reference and not reason:
            raise InvoiceLifecycleError(
                "Motif obligatoire pour corriger ou supprimer le numéro d’ordre de virement."
            )
        actor_name = _actor_name(user, actor)
        device = _device_name(device_name)
        con.execute(
            """
            UPDATE invoice_tracking
            SET numero_ordre_virement=?, updated_by=?, updated_at=CURRENT_TIMESTAMP
            WHERE invoice_id=?
            """,
            (reference, actor_name, invoice_id),
        )
        updated = _fetch_lifecycle(con, invoice_id)
        _record_event(
            con,
            invoice_id,
            "PAYMENT_REFERENCE_UPDATED",
            "numero_ordre_virement",
            old_reference,
            reference,
            actor_name,
            device,
            reason,
        )
        _record_audit(
            con,
            actor_name,
            "invoice.payment_reference.update",
            invoice_id,
            {
                "old": old_reference,
                "new": reference,
                "device": device,
                "reason": reason,
            },
        )
        return updated


def update_invoice_fields(
    invoice_id,
    changes,
    user,
    *,
    reason="",
    actor=None,
    device_name=None,
    connection=None,
):
    unknown = set(changes) - PROTECTED_INVOICE_FIELDS
    if unknown:
        raise InvoiceLifecycleError(
            "Champs facture non autorises: " + ", ".join(sorted(unknown))
        )
    with _connection_scope(connection) as con:
        invoice = _fetch_lifecycle(con, invoice_id)
        if _value(invoice, "cancelled_at"):
            raise InvoiceLockedError("La facture annulee est verrouillee.")
        actor_name = _actor_name(user, actor)
        device = _device_name(device_name)
        reason = (reason or "").strip()
        issued = bool(_value(invoice, "date_depot_dtc"))
        if issued:
            _require_permission(con, user, "invoice.unlock")
            if not reason:
                raise InvoiceLifecycleError(
                    "Motif obligatoire pour modifier une facture emise."
                )
        else:
            _require_permission(con, user, "invoice.edit_draft")

        actual_changes = {
            field: (_value(invoice, field), value)
            for field, value in changes.items()
            if _value(invoice, field) != value
        }
        if not actual_changes:
            return invoice

        if issued:
            con.execute(
                """
                INSERT INTO invoice_unlock_authorizations(invoice_id, actor, reason)
                VALUES(?, ?, ?)
                """,
                (invoice_id, actor_name, reason),
            )
        try:
            assignments = ", ".join(f"{field}=?" for field in actual_changes)
            parameters = [new_value for _, new_value in actual_changes.values()]
            con.execute(
                f"UPDATE invoices SET {assignments}, updated_by=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                [*parameters, actor_name, invoice_id],
            )
        finally:
            if issued:
                con.execute(
                    "DELETE FROM invoice_unlock_authorizations WHERE invoice_id=?",
                    (invoice_id,),
                )

        for field, (old_value, new_value) in actual_changes.items():
            _record_event(
                con,
                invoice_id,
                "INVOICE_FIELD_UPDATED",
                field,
                old_value,
                new_value,
                actor_name,
                device,
                reason,
            )
        _record_audit(
            con,
            actor_name,
            "invoice.fields.update",
            invoice_id,
            {
                "fields": sorted(actual_changes),
                "device": device,
                "reason": reason,
                "unlocked": issued,
            },
        )
        return _fetch_lifecycle(con, invoice_id)


def cancel_invoice(
    invoice_id,
    user,
    reason,
    *,
    actor=None,
    device_name=None,
    connection=None,
):
    reason = (reason or "").strip()
    if not reason:
        raise InvoiceLifecycleError("Motif d'annulation obligatoire.")
    with _connection_scope(connection) as con:
        invoice = _fetch_lifecycle(con, invoice_id)
        _require_permission(con, user, "invoice.cancel")
        if _value(invoice, "cancelled_at"):
            raise InvoiceLifecycleError("Facture deja annulee.")
        actor_name = _actor_name(user, actor)
        device = _device_name(device_name)
        status_before = invoice["lifecycle_status"]
        cancelled_at = _utc_timestamp()
        con.execute(
            """
            UPDATE invoices
            SET cancelled_at=?, cancelled_by=?, cancellation_reason=?,
                updated_by=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (cancelled_at, actor_name, reason, actor_name, invoice_id),
        )
        _record_event(
            con,
            invoice_id,
            "INVOICE_CANCELLED",
            "lifecycle_status",
            status_before,
            CANCELLED,
            actor_name,
            device,
            reason,
        )
        _record_audit(
            con,
            actor_name,
            "invoice.cancel",
            invoice_id,
            {"device": device, "reason": reason, "status_before": status_before},
        )
        return _fetch_lifecycle(con, invoice_id)


def restore_cancelled_invoice(
    invoice_id,
    user,
    reason,
    *,
    actor=None,
    device_name=None,
    connection=None,
):
    reason = (reason or "").strip()
    if not reason:
        raise InvoiceLifecycleError("Motif de restauration obligatoire.")
    with _connection_scope(connection) as con:
        invoice = _fetch_lifecycle(con, invoice_id)
        _require_permission(con, user, "invoice.cancel")
        if not _value(invoice, "cancelled_at"):
            raise InvoiceLifecycleError("La facture n'est pas annulee.")
        actor_name = _actor_name(user, actor)
        device = _device_name(device_name)
        con.execute(
            """
            UPDATE invoices
            SET cancelled_at=NULL, cancelled_by='', cancellation_reason='',
                updated_by=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (actor_name, invoice_id),
        )
        restored = _fetch_lifecycle(con, invoice_id)
        _record_event(
            con,
            invoice_id,
            "INVOICE_RESTORED",
            "lifecycle_status",
            CANCELLED,
            restored["lifecycle_status"],
            actor_name,
            device,
            reason,
        )
        _record_audit(
            con,
            actor_name,
            "invoice.restore",
            invoice_id,
            {
                "device": device,
                "reason": reason,
                "status_after": restored["lifecycle_status"],
            },
        )
        return restored


def record_invoice_export(
    invoice_id,
    export_type,
    user,
    *,
    actor=None,
    device_name=None,
    connection=None,
):
    with _connection_scope(connection) as con:
        _fetch_lifecycle(con, invoice_id)
        _require_permission(con, user, "invoice.export")
        actor_name = _actor_name(user, actor)
        device = _device_name(device_name)
        export_type = (export_type or "unknown").strip().lower()
        _record_event(
            con,
            invoice_id,
            "INVOICE_EXPORTED",
            "export_type",
            None,
            export_type,
            actor_name,
            device,
            "",
        )
        _record_audit(
            con,
            actor_name,
            "invoice.export",
            invoice_id,
            {"device": device, "export_type": export_type},
        )
