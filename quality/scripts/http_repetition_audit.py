"""Run varied authenticated HTTP repetitions against the isolated RC-1 server."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_EVEN
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener


CENT = Decimal("0.01")
PREFIX = "QA_RC1_AUDIT_20260825"


def q2(value):
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_EVEN)


def expected_totals(quantity, unit_price):
    total_ht = q2(Decimal(str(quantity)) * Decimal(str(unit_price)))
    retention = q2(total_ht * Decimal("0.05"))
    after = q2(total_ht - retention)
    tax = q2(after * Decimal("0.19"))
    return {
        "total_ht": total_ht,
        "retenue_garantie": retention,
        "montant_ht_apres_rg": after,
        "tva": tax,
        "total_ttc": q2(after + tax),
    }


def request(opener, base_url, path, data=None):
    body = urlencode(data).encode("utf-8") if data is not None else None
    req = Request(base_url + path, data=body)
    if body is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    response = opener.open(req, timeout=60)
    content = response.read()
    return response, content


def csrf(html):
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    if not match:
        raise AssertionError("CSRF token not found")
    return match.group(1)


def text(content):
    return content.decode("utf-8", errors="replace")


def scalar(connection, statement, parameters=()):
    row = connection.execute(statement, parameters).fetchone()
    return row[0] if row else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8766")
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()
    database = args.database.resolve()
    if "tmp\\rc1-audit" not in str(database):
        raise RuntimeError(f"Refusing non-QA database: {database}")

    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    _, login_page = request(opener, args.base_url, "/login")
    _, dashboard = request(opener, args.base_url, "/login", {
        "csrf_token": csrf(text(login_page)),
        "username": "admin",
        "password": "QA_RC1_Pass123",
    })
    assert "Tableau de bord" in text(dashboard)

    connection = sqlite3.connect(database, timeout=30)
    connection.row_factory = sqlite3.Row
    results = {"prefix": PREFIX, "iterations": [], "searches": [], "double_submit": {}, "csrf_get_delete": {}}
    try:
        if scalar(connection, "SELECT COUNT(*) FROM clients WHERE sigle LIKE ?", (PREFIX + "%",)):
            raise RuntimeError("QA prefix already exists; restore the isolated baseline before rerun")
        branch_id = scalar(connection, "SELECT id FROM company_branches WHERE is_active=1 ORDER BY id LIMIT 1")
        design_office_id = scalar(connection, "SELECT id FROM design_offices WHERE is_active=1 ORDER BY id LIMIT 1")
        unit_price = Decimal(str(scalar(connection, "SELECT pu_ht FROM bpu_items WHERE article_number=1")))
        assert branch_id and design_office_id and unit_price >= 0

        created = []
        quantities = ["1", "1.25", "2", "2.5", "3.75", "5", "7.125", "10", "12.5", "25.75"]
        names = [
            "Alger", "Oran", "Tizi Ouzou", "Béjaïa", "قسنطينة",
            "Sétif", "Ghardaïa", "Aïn Defla", "El Oued", "Ouargla",
        ]
        start_date = date(2026, 1, 5)
        session_token = csrf(text(request(opener, args.base_url, "/clients?new=1")[1]))

        for index in range(10):
            suffix = f"{index + 1:02d}"
            sigle = f"{PREFIX}_{suffix}"
            client_name = f"{sigle} Société {names[index]}"
            _, client_response = request(opener, args.base_url, "/clients/save", {
                "csrf_token": session_token,
                "raison_sociale": client_name,
                "sigle": sigle,
                "rgc": f"QA-RGC-{suffix}",
                "nif": f"QA-NIF-{suffix}",
                "adresse": f"QA adresse {names[index]}",
            })
            assert client_name in text(client_response)
            client_id = scalar(connection, "SELECT id FROM clients WHERE sigle=?", (sigle,))
            assert client_id

            direction_sigle = f"QA DR {suffix}"
            _, direction_response = request(opener, args.base_url, "/clients/directions/save", {
                "csrf_token": session_token,
                "client_id": str(client_id),
                "company_branch_id": str(branch_id),
                "sigle": direction_sigle,
                "name": f"{PREFIX} Direction {names[index]}",
                "address": f"QA direction {names[index]}",
            })
            assert direction_sigle in text(direction_response)
            direction_id = scalar(connection, "SELECT id FROM client_directions WHERE client_id=? AND sigle=?", (client_id, direction_sigle))
            assert direction_id

            po_number = f"{PREFIX}_BC_{suffix}"
            invoice_date = start_date + timedelta(days=index * 7)
            _, po_response = request(opener, args.base_url, "/purchase-orders", {
                "csrf_token": session_token,
                "numero_bc": po_number,
                "date_bc": invoice_date.isoformat(),
                "client_direction_id": str(direction_id),
                "type_bc": "ACQUISITION",
                "objet": f"{PREFIX} Objet {names[index]}",
                "montant_ttc": str(100000 + index * 1234.56),
                "code_sites": "",
            })
            assert po_number in text(po_response)
            po_id = scalar(connection, "SELECT id FROM purchase_orders WHERE numero_bc=?", (po_number,))
            assert po_id

            site_code = f"{PREFIX}_SITE_{suffix}"
            _, site_response = request(opener, args.base_url, "/sites", {
                "csrf_token": session_token,
                "purchase_order_id": str(po_id),
                "code_site": site_code,
                "nom_site": f"{PREFIX} Site {names[index]}",
                "typologie_site": "A9",
                "typologie_libelle": "A9",
                "subcontractor_id": "",
                "design_office_id": str(design_office_id),
            })
            assert site_code in text(site_response)
            site_id = scalar(connection, "SELECT id FROM sites WHERE code_site=?", (site_code,))
            assert site_id

            invoice_number = f"{PREFIX}_FAC_{suffix}"
            lines = json.dumps([{"article_number": 1, "quantity": quantities[index], "source_reference_type": "GENERAL", "source_st_number": None}])
            _, invoice_response = request(opener, args.base_url, "/invoices", {
                "csrf_token": session_token,
                "invoice_number": invoice_number,
                "invoice_date": invoice_date.isoformat(),
                "purchase_order_id": str(po_id),
                "site_id": str(site_id),
                "lines": lines,
            })
            assert invoice_number in text(invoice_response)
            invoice = connection.execute("SELECT * FROM invoices WHERE invoice_number=?", (invoice_number,)).fetchone()
            assert invoice
            expected = expected_totals(quantities[index], unit_price)
            for key, value in expected.items():
                assert q2(invoice[key]) == value, (invoice_number, key, invoice[key], value)

            reopen_html = text(request(opener, args.base_url, f"/invoices?edit_id={invoice['id']}")[1])
            assert invoice_number in reopen_html and po_number in reopen_html and site_code in reopen_html

            edited_quantity = str(Decimal(quantities[index]) + Decimal("0.125"))
            edited_lines = json.dumps([{"article_number": 1, "quantity": edited_quantity, "source_reference_type": "GENERAL", "source_st_number": None}])
            edited_date = (invoice_date + timedelta(days=1)).isoformat()
            _, edit_response = request(opener, args.base_url, "/invoices", {
                "csrf_token": session_token,
                "id": str(invoice["id"]),
                "invoice_number": invoice_number,
                "invoice_date": edited_date,
                "purchase_order_id": str(po_id),
                "site_id": str(site_id),
                "lines": edited_lines,
            })
            assert invoice_number in text(edit_response)
            edited = connection.execute("SELECT * FROM invoices WHERE id=?", (invoice["id"],)).fetchone()
            assert edited["invoice_date"] == edited_date
            expected_edited = expected_totals(edited_quantity, unit_price)
            for key, value in expected_edited.items():
                assert q2(edited[key]) == value
            created.append((invoice_number, invoice["id"], po_id, site_id, edited_lines))
            results["iterations"].append({
                "index": index + 1,
                "client": sigle,
                "purchase_order": po_number,
                "site": site_code,
                "invoice": invoice_number,
                "quantity_created": quantities[index],
                "quantity_edited": edited_quantity,
                "expected_edited": {key: str(value) for key, value in expected_edited.items()},
                "status": "PASS",
            })

        for invoice_number, invoice_id, *_ in created:
            page = text(request(opener, args.base_url, "/invoices?" + urlencode({"q": invoice_number}))[1])
            passed = invoice_number in page
            results["searches"].append({"query": invoice_number, "pass": passed})
            assert passed

        first_number, _, first_po, first_site, first_lines = created[0]
        duplicate_payload = {
            "csrf_token": session_token,
            "invoice_number": first_number,
            "invoice_date": "2026-01-06",
            "purchase_order_id": str(first_po),
            "site_id": str(first_site),
            "lines": first_lines,
        }
        request(opener, args.base_url, "/invoices", duplicate_payload)
        request(opener, args.base_url, "/invoices", duplicate_payload)
        duplicate_count = scalar(connection, "SELECT COUNT(*) FROM invoices WHERE invoice_number=?", (first_number,))
        results["double_submit"] = {"invoice": first_number, "database_count": duplicate_count, "pass": duplicate_count == 1}
        assert duplicate_count == 1

        target_number, target_id, *_ = created[-1]
        before_deleted = scalar(connection, "SELECT deleted_at FROM invoices WHERE id=?", (target_id,))
        response, _ = request(opener, args.base_url, f"/invoices/delete?id={target_id}")
        after_deleted = scalar(connection, "SELECT deleted_at FROM invoices WHERE id=?", (target_id,))
        results["csrf_get_delete"] = {
            "invoice": target_number,
            "http_status": response.status,
            "before_deleted_at": before_deleted,
            "after_deleted_at": after_deleted,
            "state_changed_without_csrf": bool(after_deleted and not before_deleted),
        }
        assert after_deleted and not before_deleted

        results["database"] = {
            "integrity_check": scalar(connection, "PRAGMA integrity_check"),
            "foreign_key_violations": len(connection.execute("PRAGMA foreign_key_check").fetchall()),
            "qa_clients": scalar(connection, "SELECT COUNT(*) FROM clients WHERE sigle LIKE ?", (PREFIX + "%",)),
            "qa_invoices": scalar(connection, "SELECT COUNT(*) FROM invoices WHERE invoice_number LIKE ?", (PREFIX + "%",)),
            "qa_active_invoices": scalar(connection, "SELECT COUNT(*) FROM invoices WHERE invoice_number LIKE ? AND deleted_at IS NULL", (PREFIX + "%",)),
        }
        assert results["database"]["integrity_check"] == "ok"
        assert results["database"]["foreign_key_violations"] == 0
    finally:
        connection.close()

    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
