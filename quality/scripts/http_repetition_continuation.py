"""Finish RC-1 repetition checks after the invoice-list defect is observed."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener


PREFIX = "QA_RC1_AUDIT_20260825"


def call(opener, base, path, data=None):
    body = urlencode(data).encode() if data is not None else None
    request = Request(base + path, data=body)
    if body is not None:
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
    response = opener.open(request, timeout=60)
    return response, response.read().decode("utf-8", errors="replace")


def token(html):
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    if not match:
        raise AssertionError("CSRF token missing")
    return match.group(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8766")
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()
    database = args.database.resolve()
    if "tmp\\rc1-audit" not in str(database):
        raise RuntimeError("Non-QA database refused")

    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    _, login = call(opener, args.base_url, "/login")
    _, dashboard = call(opener, args.base_url, "/login", {
        "csrf_token": token(login), "username": "admin", "password": "QA_RC1_Pass123",
    })
    assert "Tableau de bord" in dashboard
    _, invoice_page = call(opener, args.base_url, "/invoices")
    csrf = token(invoice_page)

    connection = sqlite3.connect(database, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT i.id, i.invoice_number, i.invoice_date, i.purchase_order_id,
                   i.site_id, il.article_number, il.quantite
            FROM invoices i JOIN invoice_lines il ON il.invoice_id=i.id
            WHERE i.invoice_number LIKE ? ORDER BY i.id
            """,
            (PREFIX + "%",),
        ).fetchall()
        assert len(rows) == 10
        output = {"reopen": [], "search": [], "double_submit": {}, "csrf_get_delete": {}}
        for row in rows:
            _, reopen = call(opener, args.base_url, f"/invoices?edit_id={row['id']}")
            output["reopen"].append({"invoice": row["invoice_number"], "pass": row["invoice_number"] in reopen})
            _, search = call(opener, args.base_url, "/invoices?" + urlencode({"q": row["invoice_number"]}))
            output["search"].append({"invoice": row["invoice_number"], "pass": row["invoice_number"] in search})
        assert all(item["pass"] for item in output["reopen"])

        first = rows[0]
        payload = {
            "csrf_token": csrf,
            "invoice_number": first["invoice_number"],
            "invoice_date": first["invoice_date"],
            "purchase_order_id": str(first["purchase_order_id"]),
            "site_id": str(first["site_id"]),
            "lines": json.dumps([{
                "article_number": first["article_number"],
                "quantity": first["quantite"],
                "source_reference_type": "GENERAL",
                "source_st_number": None,
            }]),
        }
        call(opener, args.base_url, "/invoices", payload)
        call(opener, args.base_url, "/invoices", payload)
        duplicate_count = connection.execute(
            "SELECT COUNT(*) FROM invoices WHERE invoice_number=?", (first["invoice_number"],)
        ).fetchone()[0]
        output["double_submit"] = {"database_count": duplicate_count, "pass": duplicate_count == 1}
        assert duplicate_count == 1

        last = rows[-1]
        before = connection.execute("SELECT deleted_at FROM invoices WHERE id=?", (last["id"],)).fetchone()[0]
        response, _ = call(opener, args.base_url, f"/invoices/delete?id={last['id']}")
        after = connection.execute("SELECT deleted_at FROM invoices WHERE id=?", (last["id"],)).fetchone()[0]
        output["csrf_get_delete"] = {
            "invoice": last["invoice_number"], "http_status": response.status,
            "before": before, "after": after,
            "state_changed_without_csrf": bool(after and not before),
        }
        output["database"] = {
            "integrity_check": connection.execute("PRAGMA integrity_check").fetchone()[0],
            "foreign_key_violations": len(connection.execute("PRAGMA foreign_key_check").fetchall()),
            "qa_clients": connection.execute("SELECT COUNT(*) FROM clients WHERE sigle LIKE ?", (PREFIX + "%",)).fetchone()[0],
            "qa_purchase_orders": connection.execute("SELECT COUNT(*) FROM purchase_orders WHERE numero_bc LIKE ?", (PREFIX + "%",)).fetchone()[0],
            "qa_sites": connection.execute("SELECT COUNT(*) FROM sites WHERE code_site LIKE ?", (PREFIX + "%",)).fetchone()[0],
            "qa_invoices": connection.execute("SELECT COUNT(*) FROM invoices WHERE invoice_number LIKE ?", (PREFIX + "%",)).fetchone()[0],
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
