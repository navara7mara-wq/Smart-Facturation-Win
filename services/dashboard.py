from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from services.invoice_lifecycle import (
    AWAITING_PAYMENT,
    BROUILLON,
    CANCELLED,
    DEPOSITED_DTC,
    PAID,
    READY_DTC,
    STATUS_LABELS,
)
from services.invoice_types import PURCHASE_ORDER_TYPE_LABELS


LIFECYCLE_STATUS_ORDER = (
    BROUILLON,
    READY_DTC,
    DEPOSITED_DTC,
    AWAITING_PAYMENT,
    PAID,
)


@dataclass(frozen=True)
class DashboardFilters:
    date_from: str = ""
    date_to: str = ""
    direction: str = ""
    type_bc: str = ""
    status: str = ""
    company_branch: str = ""
    client: str = ""

    def as_query(self):
        return {
            "date_from": self.date_from,
            "date_to": self.date_to,
            "direction": self.direction,
            "type_bc": self.type_bc,
            "status": self.status,
            "company_branch": self.company_branch,
            "client": self.client,
        }


def _iso_date(value):
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return ""


def normalize_dashboard_filters(values):
    notices = []
    raw_from = str(values.get("date_from", "") or "").strip()
    raw_to = str(values.get("date_to", "") or "").strip()
    date_from = _iso_date(raw_from)
    date_to = _iso_date(raw_to)
    if raw_from and not date_from:
        notices.append("La date de début invalide a été ignorée.")
    if raw_to and not date_to:
        notices.append("La date de fin invalide a été ignorée.")
    if date_from and date_to and date_from > date_to:
        date_from, date_to = date_to, date_from
        notices.append("Les dates de la période ont été réordonnées.")

    direction = str(values.get("direction", "") or "").strip()
    type_bc = str(values.get("type_bc", "") or "").strip()
    if type_bc not in PURCHASE_ORDER_TYPE_LABELS:
        type_bc = ""
    status = str(values.get("status", "") or "").strip()
    if status not in STATUS_LABELS:
        status = ""

    return DashboardFilters(
        date_from=date_from,
        date_to=date_to,
        direction=direction,
        type_bc=type_bc,
        status=status,
        company_branch=str(values.get("company_branch", "") or "").strip(),
        client=str(values.get("client", "") or "").strip(),
    ), tuple(notices)


def _amount(row):
    return Decimal(str(row.get("total_ttc") or 0))


def _summary(rows):
    return {
        "count": len(rows),
        "amount": sum((_amount(row) for row in rows), Decimal("0")),
    }


def _month_key(value):
    try:
        parsed = date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return ""
    return f"{parsed.year:04d}-{parsed.month:02d}"


def _previous_months(anchor, count=12):
    keys = []
    year = anchor.year
    month = anchor.month
    for offset in range(count - 1, -1, -1):
        absolute_month = year * 12 + month - 1 - offset
        item_year, zero_month = divmod(absolute_month, 12)
        keys.append(f"{item_year:04d}-{zero_month + 1:02d}")
    return keys


def _dashboard_rows(connection, filters):
    where = ["lifecycle.deleted_at IS NULL"]
    params = []
    if filters.date_from:
        where.append("DATE(lifecycle.invoice_date) >= DATE(?)")
        params.append(filters.date_from)
    if filters.date_to:
        where.append("DATE(lifecycle.invoice_date) <= DATE(?)")
        params.append(filters.date_to)
    if filters.direction:
        where.append("CAST(po.client_direction_id AS TEXT) = ?")
        params.append(filters.direction)
    if filters.client:
        where.append("CAST(md.client_id AS TEXT) = ?")
        params.append(filters.client)
    if filters.type_bc:
        where.append(
            "CASE WHEN lifecycle.invoice_type='NDC' THEN 'NDC' ELSE po.type_bc END = ?"
        )
        params.append(filters.type_bc)
    if filters.company_branch:
        where.append("CAST(po.company_branch_id AS TEXT) = ?")
        params.append(filters.company_branch)

    rows = connection.execute(
        f"""
        SELECT
            lifecycle.id,
            lifecycle.invoice_number,
            lifecycle.invoice_date,
            lifecycle.invoice_type,
            lifecycle.total_ttc,
            lifecycle.lifecycle_status,
            lifecycle.is_issued,
            lifecycle.date_depot_dtc,
            lifecycle.date_depot_mobilis,
            lifecycle.date_ov,
            po.numero_bc,
            po.client_direction_id AS mobilis_direction_id,
            CASE
                WHEN lifecycle.invoice_type='NDC' THEN 'NDC'
                ELSE po.type_bc
            END AS effective_type_bc,
            COALESCE(md.name, 'Sans direction') AS direction_name,
            COALESCE(md.sigle, '') AS direction_sigle,
            CASE
                WHEN lifecycle.invoice_type='NDC' THEN
                    CAST((SELECT COUNT(*) FROM invoice_sites invoice_site
                          WHERE invoice_site.invoice_id=lifecycle.id) AS TEXT) || ' sites'
                ELSE COALESCE(site.code_site, '—')
            END AS site_label
        FROM invoice_lifecycle lifecycle
        LEFT JOIN purchase_orders po ON po.id=lifecycle.purchase_order_id
        LEFT JOIN client_directions md ON md.id=po.client_direction_id
        LEFT JOIN sites site ON site.id=lifecycle.site_id
        WHERE {' AND '.join(where)}
        ORDER BY lifecycle.id DESC
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def load_dashboard(connection, filters=None, as_of=None, list_limit=6):
    filters = filters or DashboardFilters()
    as_of = as_of or date.today()
    base_rows = _dashboard_rows(connection, filters)
    visible_rows = (
        [row for row in base_rows if row["lifecycle_status"] == filters.status]
        if filters.status
        else list(base_rows)
    )
    active_rows = [
        row for row in visible_rows if row["lifecycle_status"] != CANCELLED
    ]

    status_cards = {
        status: _summary(
            [row for row in base_rows if row["lifecycle_status"] == status]
        )
        for status in LIFECYCLE_STATUS_ORDER
    }
    issued_rows = [row for row in active_rows if int(row["is_issued"] or 0)]
    paid_rows = [row for row in active_rows if row["lifecycle_status"] == PAID]
    outstanding_rows = [
        row
        for row in active_rows
        if row["lifecycle_status"] in {DEPOSITED_DTC, AWAITING_PAYMENT}
    ]
    draft_rows = [
        row for row in active_rows if row["lifecycle_status"] == BROUILLON
    ]

    overdue_rows = []
    for row in active_rows:
        if row["lifecycle_status"] != AWAITING_PAYMENT:
            continue
        try:
            mobilis_date = date.fromisoformat(str(row["date_depot_mobilis"])[:10])
        except (TypeError, ValueError):
            continue
        days_overdue = (as_of - mobilis_date).days
        if days_overdue > 60:
            overdue_rows.append({**row, "days_overdue": days_overdue})
    overdue_rows.sort(
        key=lambda row: (row["date_depot_mobilis"] or "9999-12-31", row["id"])
    )

    overdue_groups = {}
    for row in overdue_rows:
        direction = row["direction_name"] or "Sans direction"
        group = overdue_groups.setdefault(
            direction, {"direction": direction, "count": 0, "amount": Decimal("0")}
        )
        group["count"] += 1
        group["amount"] += _amount(row)
    overdue_by_direction = sorted(
        overdue_groups.values(), key=lambda item: (-item["amount"], item["direction"])
    )

    ready_dtc = sorted(
        [row for row in visible_rows if row["lifecycle_status"] == READY_DTC],
        key=lambda row: (row["invoice_date"] or "9999-12-31", row["id"]),
    )[:list_limit]
    ready_mobilis = sorted(
        [row for row in visible_rows if row["lifecycle_status"] == DEPOSITED_DTC],
        key=lambda row: (row["date_depot_dtc"] or "9999-12-31", row["id"]),
    )[:list_limit]
    awaiting_payment = sorted(
        [row for row in visible_rows if row["lifecycle_status"] == AWAITING_PAYMENT],
        key=lambda row: (row["date_depot_mobilis"] or "9999-12-31", row["id"]),
    )[:list_limit]

    issued_dates = [
        date.fromisoformat(str(row["invoice_date"])[:10])
        for row in issued_rows
        if _iso_date(row["invoice_date"])
    ]
    if filters.date_to:
        anchor = date.fromisoformat(filters.date_to)
    elif issued_dates:
        anchor = max(issued_dates)
    else:
        anchor = as_of
    month_keys = _previous_months(anchor)
    monthly_totals = {key: Decimal("0") for key in month_keys}
    for row in issued_rows:
        key = _month_key(row["invoice_date"])
        if key in monthly_totals:
            monthly_totals[key] += _amount(row)
    monthly_ttc = [
        {"month": key, "amount": monthly_totals[key]} for key in month_keys
    ]

    direction_where = ["deleted_at IS NULL"]
    direction_params = []
    if filters.client:
        direction_where.append("CAST(client_id AS TEXT)=?")
        direction_params.append(filters.client)
    directions = connection.execute(
        """
        SELECT id, client_id, name AS direction_regionale, sigle
        FROM client_directions
        WHERE %s
        ORDER BY sigle, name
        """ % " AND ".join(direction_where),
        direction_params,
    ).fetchall()
    branches = connection.execute(
        "SELECT id, name, sigle FROM company_branches WHERE is_active=1 ORDER BY sigle, name"
    ).fetchall()
    clients = connection.execute(
        "SELECT id, raison_sociale, sigle FROM clients WHERE deleted_at IS NULL AND is_active=1 ORDER BY sigle, raison_sociale"
    ).fetchall()
    company = connection.execute("SELECT nom FROM company_settings WHERE id=1").fetchone()
    date_bounds = connection.execute(
        """
        SELECT MIN(DATE(invoice_date)), MAX(DATE(invoice_date))
        FROM invoices
        WHERE deleted_at IS NULL AND invoice_date IS NOT NULL
        """
    ).fetchone()

    return {
        "filters": filters,
        "kpis": {
            "issued": _summary(issued_rows),
            "total_issued": _summary(issued_rows),
            "paid": _summary(paid_rows),
            "outstanding": _summary(outstanding_rows),
            "drafts": _summary(draft_rows),
        },
        "status_cards": status_cards,
        "status_distribution": [
            {
                "code": status,
                "label": STATUS_LABELS[status],
                **status_cards[status],
            }
            for status in LIFECYCLE_STATUS_ORDER
        ],
        "monthly_ttc": monthly_ttc,
        "ready_dtc": ready_dtc,
        "ready_mobilis": ready_mobilis,
        "awaiting_payment": awaiting_payment,
        "overdue": _summary(overdue_rows),
        "overdue_rows": overdue_rows[:list_limit],
        "overdue_by_direction": overdue_by_direction,
        "directions": [dict(row) for row in directions],
        "branches": [dict(row) for row in branches],
        "clients": [dict(row) for row in clients],
        "company_name": (company[0] if company else "entreprise") or "entreprise",
        "date_bounds": tuple(date_bounds) if date_bounds else (None, None),
        "visible_count": len(visible_rows),
    }
