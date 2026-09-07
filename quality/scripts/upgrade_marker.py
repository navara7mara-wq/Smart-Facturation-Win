"""Seed or verify an isolated business marker for installer upgrade testing."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


MARKER = "QA_RC1_UPGRADE_250_TO_251"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("seed", "verify"))
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    database = args.database.resolve()
    if "build\\upgrade-smoke" not in str(database):
        raise RuntimeError("Non-QA upgrade database refused")
    with sqlite3.connect(database) as connection:
        if args.action == "seed":
            connection.execute(
                """
                INSERT INTO clients(
                    raison_sociale, sigle, rgc, nif, adresse, logo_path,
                    is_active, created_by, updated_by
                ) VALUES(?,?,?,?,?,'',1,'qa-audit','qa-audit')
                """,
                (MARKER, "QA-UPG", "QA-RGC-UPG", "QA-NIF-UPG", "QA upgrade address"),
            )
            connection.commit()
        row = connection.execute(
            "SELECT raison_sociale, sigle, adresse FROM clients WHERE raison_sociale=?", (MARKER,)
        ).fetchone()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        migrations = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    result = {
        "action": args.action,
        "marker": list(row) if row else None,
        "integrity_check": integrity,
        "foreign_key_violations": len(foreign_keys),
        "max_migration": migrations,
    }
    assert row and integrity == "ok" and not foreign_keys
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
