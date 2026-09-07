"""Download real RC-1 XLSX/PDF outputs from the isolated authenticated server."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener


def fetch(opener, url, data=None, timeout=120):
    body = urlencode(data).encode() if data is not None else None
    request = Request(url, data=body)
    if body is not None:
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
    response = opener.open(request, timeout=timeout)
    return response, response.read()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8766")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    database = args.database.resolve()
    if "tmp\\rc1-audit" not in str(database):
        raise RuntimeError("Non-QA database refused")
    args.output.mkdir(parents=True, exist_ok=True)

    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    _, login = fetch(opener, args.base_url + "/login")
    match = re.search(rb'name="csrf_token" value="([^"]+)"', login)
    assert match
    _, dashboard = fetch(opener, args.base_url + "/login", {
        "csrf_token": match.group(1).decode(), "username": "admin", "password": "QA_RC1_Pass123",
    })
    assert b"Tableau de bord" in dashboard

    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        normal = connection.execute(
            "SELECT * FROM invoices WHERE invoice_number='QA_RC1_AUDIT_20260825_FAC_01'"
        ).fetchone()
        ndc = connection.execute(
            "SELECT * FROM invoices WHERE invoice_type='NDC' AND deleted_at IS NULL ORDER BY id LIMIT 1"
        ).fetchone()
        assert normal and ndc
        invoice_rows = [("normal", normal), ("ndc", ndc)]
        records = []
        for label, invoice in invoice_rows:
            targets = [
                ("xlsx", f"/invoices/export?id={invoice['id']}", ".xlsx"),
                ("facture", f"/invoices/pdf/facture/{invoice['id']}/facture.pdf", ".pdf"),
                ("devis_quantitatif", f"/invoices/pdf/devis-quantitatif/{invoice['id']}/devis-quantitatif.pdf", ".pdf"),
                ("devis_estimatif", f"/invoices/pdf/devis-estimatif/{invoice['id']}/devis-estimatif.pdf", ".pdf"),
            ]
            for kind, route, suffix in targets:
                response, content = fetch(opener, args.base_url + route)
                expected_prefix = b"PK" if suffix == ".xlsx" else b"%PDF"
                assert content.startswith(expected_prefix), (route, response.status, response.headers.get("Content-Type"))
                path = args.output / f"{label}_{kind}{suffix}"
                path.write_bytes(content)
                records.append({
                    "label": label,
                    "invoice_id": invoice["id"],
                    "invoice_number": invoice["invoice_number"],
                    "kind": kind,
                    "path": str(path.resolve()),
                    "bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "content_type": response.headers.get("Content-Type"),
                    "database_totals": {
                        key: invoice[key]
                        for key in ("total_ht", "retenue_garantie", "montant_ht_apres_rg", "tva", "total_ttc")
                    },
                })
    finally:
        connection.close()
    print(json.dumps(records, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
