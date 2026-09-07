"""Read-only SQLite integrity and business-consistency audit for RC-1."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    path = args.database.resolve()
    output = {
        "path": str(path),
        "size": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        connection.row_factory = sqlite3.Row
        output["integrity_check"] = connection.execute("PRAGMA integrity_check").fetchone()[0]
        output["foreign_key_check"] = [dict(row) for row in connection.execute("PRAGMA foreign_key_check")]
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        output["counts"] = {
            table: connection.execute(f"SELECT COUNT(*) FROM {quote_identifier(table)}").fetchone()[0]
            for table in tables
        }
        checks = {
            "orphan_invoice_lines": "SELECT COUNT(*) FROM invoice_lines il LEFT JOIN invoices i ON i.id=il.invoice_id WHERE i.id IS NULL",
            "orphan_invoices_purchase_order": "SELECT COUNT(*) FROM invoices i LEFT JOIN purchase_orders po ON po.id=i.purchase_order_id WHERE po.id IS NULL",
            "orphan_invoices_site": "SELECT COUNT(*) FROM invoices i LEFT JOIN sites s ON s.id=i.site_id WHERE i.site_id IS NOT NULL AND s.id IS NULL",
            "orphan_sites_purchase_order": "SELECT COUNT(*) FROM sites s LEFT JOIN purchase_orders po ON po.id=s.purchase_order_id WHERE po.id IS NULL",
            "duplicate_invoice_number": "SELECT COUNT(*) FROM (SELECT invoice_number FROM invoices GROUP BY invoice_number HAVING COUNT(*)>1)",
            "duplicate_purchase_order_number": "SELECT COUNT(*) FROM (SELECT numero_bc FROM purchase_orders GROUP BY numero_bc HAVING COUNT(*)>1)",
            "line_total_mismatch": "SELECT COUNT(*) FROM invoices i WHERE ABS(COALESCE(i.total_ht,0)-COALESCE((SELECT SUM(montant_ht) FROM invoice_lines il WHERE il.invoice_id=i.id),0))>0.011",
            "ttc_formula_mismatch": "SELECT COUNT(*) FROM invoices WHERE ABS(COALESCE(total_ttc,0)-(COALESCE(montant_ht_apres_rg,0)+COALESCE(tva,0)))>0.011",
        }
        output["checks"] = {
            name: connection.execute(statement).fetchone()[0]
            for name, statement in checks.items()
        }
        if "schema_migrations" in tables:
            output["schema_migrations"] = [
                list(row) for row in connection.execute("SELECT * FROM schema_migrations ORDER BY version")
            ]
    finally:
        connection.close()
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

