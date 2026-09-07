"""Add 15 varied invoice E2E scenarios so the critical workflow reaches 25."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_EVEN
from http.cookiejar import CookieJar
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener


CENT = Decimal("0.01")
PREFIX = "QA_RC1_CRITICAL3_20260825"


def q2(value) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_EVEN)


def totals(lines: list[tuple[int, Decimal, Decimal]]) -> dict[str, Decimal]:
    total_ht = sum((q2(quantity * price) for _, quantity, price in lines), Decimal("0.00"))
    retention = q2(total_ht * Decimal("0.05"))
    after = q2(total_ht - retention)
    tax = q2(after * Decimal("0.19"))
    return {
        "total_ht": q2(total_ht),
        "retenue_garantie": retention,
        "montant_ht_apres_rg": after,
        "tva": tax,
        "total_ttc": q2(after + tax),
    }


def call(opener, base: str, path: str, data=None) -> bytes:
    payload = urlencode(data).encode() if data else None
    response = opener.open(Request(base + path, data=payload), timeout=90)
    return response.read()


def csrf(content: bytes) -> str:
    return re.search(rb'name="csrf_token" value="([^"]+)"', content).group(1).decode()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8766")
    parser.add_argument("--database", required=True)
    args = parser.parse_args()
    if "tmp\\rc1-audit" not in args.database:
        raise RuntimeError("Non-QA database refused")
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    login = call(opener, args.base_url, "/login")
    dashboard = call(opener, args.base_url, "/login", {
        "csrf_token": csrf(login), "username": "admin", "password": "QA_RC1_Pass123"
    })
    assert b"Tableau de bord" in dashboard
    token = csrf(call(opener, args.base_url, "/invoices"))

    connection = sqlite3.connect(args.database)
    connection.row_factory = sqlite3.Row
    # Safe rerun cleanup: the database path was already restricted to the QA root,
    # and every target is additionally constrained by the dedicated QA prefix.
    critical_invoice_ids = [
        row[0] for row in connection.execute(
            "SELECT id FROM invoices WHERE invoice_number LIKE ?", (PREFIX + "%",)
        )
    ]
    for invoice_id in critical_invoice_ids:
        connection.execute("DELETE FROM invoice_tracking_events WHERE invoice_id=?", (invoice_id,))
        connection.execute("DELETE FROM invoice_sites WHERE invoice_id=?", (invoice_id,))
        connection.execute("DELETE FROM invoice_lines WHERE invoice_id=?", (invoice_id,))
    connection.execute("DELETE FROM invoices WHERE invoice_number LIKE ?", (PREFIX + "%",))
    connection.execute("DELETE FROM sites WHERE code_site LIKE ?", (PREFIX + "%",))
    connection.execute("DELETE FROM purchase_orders WHERE numero_bc LIKE ?", (PREFIX + "%",))
    connection.commit()
    assert connection.execute(
        "SELECT COUNT(*) FROM invoices WHERE invoice_number LIKE ?", (PREFIX + "%",)
    ).fetchone()[0] == 0
    sites = connection.execute(
        """
        SELECT s.id AS site_id, s.purchase_order_id
        FROM sites s JOIN purchase_orders p ON p.id=s.purchase_order_id
        WHERE p.numero_bc LIKE 'QA_RC1_AUDIT_20260825%' ORDER BY s.id
        """
    ).fetchall()
    prices = {
        int(row["article_number"]): Decimal(str(row["pu_ht"]))
        for row in connection.execute(
            "SELECT article_number, pu_ht FROM bpu_items WHERE article_number BETWEEN 1 AND 5"
        )
    }
    assert len(sites) == 10 and len(prices) == 5
    directions = connection.execute(
        """
        SELECT cd.id FROM client_directions cd
        JOIN clients c ON c.id=cd.client_id
        WHERE c.sigle LIKE 'QA_RC1_AUDIT_20260825%' ORDER BY cd.id
        """
    ).fetchall()
    assert len(directions) == 10
    design_office_id = connection.execute(
        "SELECT id FROM design_offices WHERE is_active=1 ORDER BY id LIMIT 1"
    ).fetchone()[0]
    for index in range(15):
        po_number = f"{PREFIX}_BC_{index + 11:02d}"
        po_date = (date(2026, 3, 1) + timedelta(days=index * 3)).isoformat()
        po_content = call(opener, args.base_url, "/purchase-orders", {
            "csrf_token": token,
            "numero_bc": po_number,
            "date_bc": po_date,
            "client_direction_id": str(directions[index % len(directions)]["id"]),
            "type_bc": "ACQUISITION",
            "objet": f"QA critical varied PO {index + 11:02d}",
            "montant_ttc": str(250000 + index * 731.25),
            "code_sites": "",
        })
        assert po_number.encode() in po_content
        purchase_order_id = connection.execute(
            "SELECT id FROM purchase_orders WHERE numero_bc=?", (po_number,)
        ).fetchone()[0]
        code = f"{PREFIX}_SITE_{index + 11:02d}"
        content = call(opener, args.base_url, "/sites", {
            "csrf_token": token,
            "purchase_order_id": str(purchase_order_id),
            "code_site": code,
            "nom_site": f"QA critical varied site {index + 11:02d}",
            "typologie_site": "A9",
            "typologie_libelle": "A9",
            "subcontractor_id": "",
            "design_office_id": str(design_office_id),
        })
        if code.encode() not in content:
            decoded = content.decode("utf-8", errors="replace")
            messages = re.findall(r"(?:Erreur|Limite|invalide|introuvable)[^<]{0,300}", decoded, flags=re.IGNORECASE)
            raise AssertionError(messages[:10] or decoded[-2000:])
    critical_sites = connection.execute(
        """
        SELECT s.id AS site_id, s.purchase_order_id
        FROM sites s WHERE s.code_site LIKE ? ORDER BY s.id
        """,
        (PREFIX + "%",),
    ).fetchall()
    assert len(critical_sites) == 15
    quantities = [
        Decimal("0.50"), Decimal("1.10"), Decimal("2.25"), Decimal("3.333"), Decimal("7.75"),
        Decimal("10"), Decimal("12.125"), Decimal("25.50"), Decimal("99.99"), Decimal("0.125"),
        Decimal("4.40"), Decimal("8.875"), Decimal("15"), Decimal("31.625"), Decimal("100.01"),
    ]
    results = []
    for index in range(15):
        site = critical_sites[index]
        line_count = (index % 5) + 1
        scenario_lines = []
        payload_lines = []
        for offset in range(line_count):
            article = offset + 1
            quantity = quantities[(index + offset) % len(quantities)]
            scenario_lines.append((article, quantity, prices[article]))
            payload_lines.append({
                "article_number": article,
                "quantity": str(quantity),
                "source_reference_type": "GENERAL",
                "source_st_number": None,
            })
        invoice_number = f"{PREFIX}_FAC_{index + 11:02d}"
        invoice_date = (date(2026, 4, 1) + timedelta(days=index * 5)).isoformat()
        data = {
            "csrf_token": token,
            "invoice_number": invoice_number,
            "invoice_date": invoice_date,
            "purchase_order_id": str(site["purchase_order_id"]),
            "site_id": str(site["site_id"]),
            "lines": json.dumps(payload_lines),
        }
        content = call(opener, args.base_url, "/invoices", data)
        if invoice_number.encode() not in content:
            decoded = content.decode("utf-8", errors="replace")
            messages = re.findall(r"(?:Erreur|Limite|invalide|introuvable)[^<]{0,300}", decoded, flags=re.IGNORECASE)
            raise AssertionError(messages[:10] or decoded[-2000:])
        invoice = connection.execute("SELECT * FROM invoices WHERE invoice_number=?", (invoice_number,)).fetchone()
        assert invoice
        expected_created = totals(scenario_lines)
        created_comparison = {key: {"actual": str(q2(invoice[key])), "expected": str(value)} for key, value in expected_created.items()}
        created_pass = all(item["actual"] == item["expected"] for item in created_comparison.values())
        assert invoice_number.encode() in call(opener, args.base_url, f"/invoices?edit_id={invoice['id']}")

        edited_payload = []
        edited_scenario = []
        for article, quantity, price in scenario_lines:
            edited_quantity = quantity + Decimal("0.01")
            edited_scenario.append((article, edited_quantity, price))
            edited_payload.append({
                "article_number": article,
                "quantity": str(edited_quantity),
                "source_reference_type": "GENERAL",
                "source_st_number": None,
            })
        data.update({"id": str(invoice["id"]), "lines": json.dumps(edited_payload)})
        call(opener, args.base_url, "/invoices", data)
        edited = connection.execute("SELECT * FROM invoices WHERE id=?", (invoice["id"],)).fetchone()
        expected_edited = totals(edited_scenario)
        edited_comparison = {key: {"actual": str(q2(edited[key])), "expected": str(value)} for key, value in expected_edited.items()}
        edited_pass = all(item["actual"] == item["expected"] for item in edited_comparison.values())
        results.append({
            "scenario": index + 11,
            "invoice": invoice_number,
            "line_count": line_count,
            "date": invoice_date,
            "expected_after_edit": {key: str(value) for key, value in expected_edited.items()},
            "created_comparison": created_comparison,
            "edited_comparison": edited_comparison,
            "status": "PASS" if created_pass and edited_pass else "FAIL",
        })
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    connection.close()
    assert integrity == "ok" and not foreign_keys
    print(json.dumps({
        "iterations": results,
        "count": len(results),
        "total_critical_invoice_scenarios_including_prior": 25,
        "passed": sum(1 for item in results if item["status"] == "PASS"),
        "failed": sum(1 for item in results if item["status"] == "FAIL"),
        "integrity_check": integrity,
        "foreign_key_violations": len(foreign_keys),
    }, indent=2))


if __name__ == "__main__":
    main()
