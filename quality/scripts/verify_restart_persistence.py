"""Verify RC-1 QA records and calculations after an application restart."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from decimal import Decimal, ROUND_HALF_EVEN
from http.cookiejar import CookieJar
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener


def call(opener, url: str, data=None) -> bytes:
    payload = urlencode(data).encode() if data else None
    response = opener.open(Request(url, data=payload), timeout=60)
    return response.read()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8766")
    parser.add_argument("--database", required=True)
    args = parser.parse_args()
    if "tmp\\rc1-audit" not in args.database:
        raise RuntimeError("Non-QA database refused")
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    login = call(opener, args.base_url + "/login")
    token = re.search(rb'name="csrf_token" value="([^"]+)"', login).group(1).decode()
    dashboard = call(opener, args.base_url + "/login", {
        "csrf_token": token, "username": "admin", "password": "QA_RC1_Pass123"
    })
    assert b"Tableau de bord" in dashboard

    connection = sqlite3.connect(args.database)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """
        SELECT i.id, i.invoice_number, i.deleted_at, i.total_ht, i.retenue_garantie,
               i.montant_ht_apres_rg, i.tva, i.total_ttc,
               SUM(il.montant_ht) AS line_sum
        FROM invoices i JOIN invoice_lines il ON il.invoice_id=i.id
        WHERE i.invoice_number LIKE 'QA_RC1_AUDIT_20260825%'
        GROUP BY i.id ORDER BY i.id
        """
    ).fetchall()
    checks = []
    cent = Decimal("0.01")
    for row in rows:
        html = call(opener, args.base_url + f"/invoices?edit_id={row['id']}")
        active = row["deleted_at"] is None
        reopen_matches = (row["invoice_number"].encode() in html) if active else (row["invoice_number"].encode() not in html)
        total_ht = Decimal(str(row["total_ht"])).quantize(cent, rounding=ROUND_HALF_EVEN)
        after_rg = Decimal(str(row["montant_ht_apres_rg"])).quantize(cent, rounding=ROUND_HALF_EVEN)
        expected_ttc = (after_rg + Decimal(str(row["tva"]))).quantize(cent, rounding=ROUND_HALF_EVEN)
        checks.append({
            "invoice": row["invoice_number"],
            "active": active,
            "reopen_behavior_matches_state": reopen_matches,
            "line_sum_matches_ht": Decimal(str(row["line_sum"])).quantize(cent) == total_ht,
            "ttc_reconciles": Decimal(str(row["total_ttc"])).quantize(cent) == expected_ttc,
        })
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    connection.close()
    output = {
        "records": len(checks),
        "active_records": sum(1 for item in checks if item["active"]),
        "soft_deleted_records": sum(1 for item in checks if not item["active"]),
        "checks": checks,
        "integrity_check": integrity,
        "foreign_key_violations": len(foreign_keys),
    }
    assert len(checks) == 10 and all(
        item["reopen_behavior_matches_state"] and item["line_sum_matches_ht"] and item["ttc_reconciles"]
        for item in checks
    )
    assert integrity == "ok" and not foreign_keys
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
