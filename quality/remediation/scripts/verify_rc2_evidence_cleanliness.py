"""Read-only verifier for authoritative RC-2 DEF-RC1-003 evidence."""

from __future__ import annotations

from contextlib import closing
from decimal import Decimal, ROUND_DOWN, localcontext
from http.cookiejar import CookieJar
from http.server import ThreadingHTTPServer
import argparse
import html
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import threading
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener
from zipfile import ZipFile

from pypdf import PdfReader


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

CENT = Decimal("0.01")


def truncate(value: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 50
        return value.quantize(CENT, rounding=ROUND_DOWN)


def oracle(quantity: str, price: str, rg_rate: str, tva_rate: str):
    total_ht = truncate(Decimal(quantity) * Decimal(price))
    rg = truncate(total_ht * Decimal(rg_rate) / Decimal("100"))
    after_rg = truncate(total_ht - rg)
    tva = truncate(after_rg * Decimal(tva_rate) / Decimal("100"))
    return total_ht, rg, after_rg, tva, truncate(after_rg + tva)


def money_fr(value: Decimal) -> str:
    whole, cents = f"{value:.2f}".split(".")
    groups = []
    while whole:
        groups.append(whole[-3:])
        whole = whole[:-3]
    return f"{' '.join(reversed(groups))},{cents}"


def rate_text(value) -> str:
    decimal = Decimal(str(value))
    return str(int(decimal)) if decimal == decimal.to_integral_value() else format(decimal, "f")


def csrf(page: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', page)
    if not match:
        match = re.search(r'name="csrf-token" content="([^"]+)"', page)
    if not match:
        raise AssertionError("CSRF token missing")
    return match.group(1)


def request(opener, url: str, data=None):
    payload = urlencode(data).encode("utf-8") if isinstance(data, dict) else data
    with opener.open(Request(url, data=payload), timeout=60) as response:
        return response.read()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("evidence_root", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--qa-username", required=True)
    parser.add_argument("--qa-password", required=True)
    args = parser.parse_args()

    source_database = args.database.resolve()
    evidence_root = args.evidence_root.resolve()
    output = args.output.resolve()
    if "tmp\\pytest-rc2-complete-final-rerun" not in str(source_database):
        raise RuntimeError("Only the isolated final RC-2 pytest database is accepted")
    if not source_database.exists():
        raise FileNotFoundError(source_database)

    ui_database = (PROJECT_ROOT / "tmp" / "evidence-cleanliness" / "ui-copy.sqlite3").resolve()
    ui_database.parent.mkdir(parents=True, exist_ok=True)
    if ui_database.exists():
        ui_database.unlink()
    shutil.copy2(source_database, ui_database)

    with closing(sqlite3.connect(f"file:{source_database.as_posix()}?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT invoice_number, rg_rate, tva_rate, total_ht, retenue_garantie,
                   montant_ht_apres_rg, tva, total_ttc
            FROM invoices WHERE invoice_number LIKE 'QA-RC2-FAC-%'
            ORDER BY invoice_number
            """
        ).fetchall()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = len(connection.execute("PRAGMA foreign_key_check").fetchall())
    assert len(rows) == 10 and integrity == "ok" and foreign_keys == 0

    os.environ["PHOENIX_DB_PATH"] = str(ui_database)
    import db as db_module
    db_module.DB_PATH = ui_database
    db_module.invalidate_schema_cache(ui_database)
    from app import App

    server = ThreadingHTTPServer(("127.0.0.1", 0), App)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    cases = []
    try:
        login = request(opener, base_url + "/login").decode("utf-8")
        request(opener, base_url + "/login", {
            "csrf_token": csrf(login),
            "username": args.qa_username,
            "password": args.qa_password,
        })
        for index, row in enumerate(rows, start=1):
            rg_rate = rate_text(row["rg_rate"])
            tva_rate = rate_text(row["tva_rate"])
            expected = oracle(f"{index}.349", "12.349", rg_rate, tva_rate)
            database_values = tuple(Decimal(str(row[key])) for key in (
                "total_ht", "retenue_garantie", "montant_ht_apres_rg", "tva", "total_ttc"
            ))
            assert database_values == expected

            invoice_id = index
            with closing(sqlite3.connect(f"file:{source_database.as_posix()}?mode=ro", uri=True)) as lookup:
                invoice_id = lookup.execute(
                    "SELECT id FROM invoices WHERE invoice_number=?", (row["invoice_number"],)
                ).fetchone()[0]
            edit_page = html.unescape(request(
                opener, base_url + f"/invoices?edit_id={invoice_id}"
            ).decode("utf-8"))
            preview_page = html.unescape(request(
                opener, base_url + f"/invoices/preview/facture?id={invoice_id}"
            ).decode("utf-8"))
            ui_checks = {
                "edit_rg": f"RG {rg_rate},00%" in edit_page,
                "edit_tva": f"TVA {tva_rate},00%" in edit_page,
                "preview_rg": f"RETENUE DE GARANTIE {rg_rate}%" in preview_page,
                "preview_tva": f"T V A {tva_rate}%" in preview_page,
                "preview_values": all(money_fr(value) in preview_page for value in expected),
            }

            pdf_path = evidence_root / "pdf" / f"invoice-{index:02d}.pdf"
            pdf_text = "\n".join(page.extract_text() or "" for page in PdfReader(pdf_path).pages)
            pdf_text = re.sub(r"\s+", " ", pdf_text)
            pdf_checks = {
                "rg_label": f"RETENUE DE GARANTIE {rg_rate}%" in pdf_text,
                "tva_label": f"T V A {tva_rate}%" in pdf_text,
                "values": all(money_fr(value) in pdf_text for value in expected),
            }

            xlsx_path = evidence_root / "xlsx" / f"invoice-{index:02d}.xlsx"
            with ZipFile(xlsx_path) as archive:
                worksheet_xml = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
            xlsx_checks = {
                "rg_label": f"RETENUE DE GARANTIE {rg_rate}%" in worksheet_xml,
                "tva_label": f"TVA {tva_rate} %" in worksheet_xml,
                "values": all(f"<v>{value:.2f}</v>" in worksheet_xml for value in expected),
                "no_float_noise": "00000000000001" not in worksheet_xml,
            }
            checks = {
                "db": database_values == expected,
                "ui": all(ui_checks.values()),
                "pdf": all(pdf_checks.values()),
                "xlsx": all(xlsx_checks.values()),
            }
            cases.append({
                "invoice": row["invoice_number"],
                "rates": {"rg": rg_rate, "tva": tva_rate},
                "expected": [str(value) for value in expected],
                "ui_checks": ui_checks,
                "pdf_checks": pdf_checks,
                "xlsx_checks": xlsx_checks,
                "checks": checks,
                "result": "PASS" if all(checks.values()) else "FAIL",
            })
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
        db_module.invalidate_schema_cache(ui_database)

    result = {
        "purpose": "Authoritative post-fix DEF-RC1-003 evidence reconciliation",
        "authoritative_evidence_root": str(evidence_root),
        "database_source": str(source_database),
        "database_source_opened_read_only": True,
        "integrity_check": integrity,
        "foreign_key_violations": foreign_keys,
        "cases_passed": sum(case["result"] == "PASS" for case in cases),
        "cases_total": len(cases),
        "layers": ["UI", "DB", "PDF", "XLSX"],
        "cases": cases,
        "result": "PASS" if all(case["result"] == "PASS" for case in cases) else "FAIL",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "cases_passed": result["cases_passed"],
        "cases_total": result["cases_total"],
        "layers": result["layers"],
        "result": result["result"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
