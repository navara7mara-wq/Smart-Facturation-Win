"""Prove whether configurable financial rates are labelled accurately in exports."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from decimal import Decimal, ROUND_HALF_EVEN
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

import fitz
import openpyxl


PREFIX = "QA_RC1_RATE_LABEL_20260825"


def call(opener, url: str, data=None) -> bytes:
    payload = urlencode(data).encode() if data else None
    return opener.open(Request(url, data=payload), timeout=120).read()


def csrf(content: bytes) -> str:
    return re.search(rb'name="csrf_token" value="([^"]+)"', content).group(1).decode()


def q2(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8766")
    parser.add_argument("--database", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if "tmp\\rc1-audit" not in args.database:
        raise RuntimeError("Non-QA database refused")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    login = call(opener, args.base_url + "/login")
    call(opener, args.base_url + "/login", {
        "csrf_token": csrf(login), "username": "admin", "password": "QA_RC1_Pass123"
    })
    token = csrf(call(opener, args.base_url + "/invoices"))

    connection = sqlite3.connect(args.database)
    connection.row_factory = sqlite3.Row
    original = {
        key: connection.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()[0]
        for key in ("retention_rate", "tax_rate")
    }
    try:
        connection.execute("UPDATE app_settings SET value='0.10' WHERE key='retention_rate'")
        connection.execute("UPDATE app_settings SET value='0.20' WHERE key='tax_rate'")
        connection.commit()
        direction_id = connection.execute(
            "SELECT id FROM client_directions WHERE deleted_at IS NULL ORDER BY id LIMIT 1"
        ).fetchone()[0]
        design_office_id = connection.execute(
            "SELECT id FROM design_offices WHERE is_active=1 ORDER BY id LIMIT 1"
        ).fetchone()[0]
        po_number = PREFIX + "_BC"
        call(opener, args.base_url + "/purchase-orders", {
            "csrf_token": token, "numero_bc": po_number, "date_bc": "2026-07-01",
            "client_direction_id": str(direction_id), "type_bc": "ACQUISITION",
            "objet": "QA configurable rate labels", "montant_ttc": "91800", "code_sites": "",
        })
        po_id = connection.execute("SELECT id FROM purchase_orders WHERE numero_bc=?", (po_number,)).fetchone()[0]
        site_code = PREFIX + "_SITE"
        call(opener, args.base_url + "/sites", {
            "csrf_token": token, "purchase_order_id": str(po_id), "code_site": site_code,
            "nom_site": "QA rate label site", "typologie_site": "A9", "typologie_libelle": "A9",
            "subcontractor_id": "", "design_office_id": str(design_office_id),
        })
        site_id = connection.execute("SELECT id FROM sites WHERE code_site=?", (site_code,)).fetchone()[0]
        invoice_number = PREFIX + "_FAC"
        call(opener, args.base_url + "/invoices", {
            "csrf_token": token, "invoice_number": invoice_number, "invoice_date": "2026-07-02",
            "purchase_order_id": str(po_id), "site_id": str(site_id),
            "lines": json.dumps([{
                "article_number": 1, "quantity": "1", "source_reference_type": "GENERAL",
                "source_st_number": None,
            }]),
        })
        invoice = connection.execute("SELECT * FROM invoices WHERE invoice_number=?", (invoice_number,)).fetchone()
        assert invoice
        expected = {
            "total_ht": q2(Decimal("85000")),
            "retenue_garantie": q2(Decimal("85000") * Decimal("0.10")),
            "montant_ht_apres_rg": q2(Decimal("85000") * Decimal("0.90")),
            "tva": q2(Decimal("76500") * Decimal("0.20")),
            "total_ttc": q2(Decimal("76500") + Decimal("15300")),
        }
        assert all(q2(Decimal(str(invoice[key]))) == value for key, value in expected.items())

        xlsx_content = call(opener, args.base_url + f"/invoices/export?id={invoice['id']}")
        pdf_content = call(opener, args.base_url + f"/invoices/pdf/facture/{invoice['id']}/facture.pdf")
        xlsx_path = args.output_dir / "rate_10_20.xlsx"
        pdf_path = args.output_dir / "rate_10_20.pdf"
        xlsx_path.write_bytes(xlsx_content)
        pdf_path.write_bytes(pdf_content)
        workbook = openpyxl.load_workbook(xlsx_path, data_only=True, read_only=False, keep_links=False)
        sheet = workbook["Facture"]
        xlsx_labels = {"retention": sheet["E28"].value, "tax": sheet["E30"].value}
        workbook.close()
        document = fitz.open(pdf_path)
        pdf_text = "\n".join(page.get_text("text") for page in document)
        pixmap = document[0].get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
        rendered = args.output_dir / "rate_10_20.png"
        pixmap.save(rendered)
        document.close()
        output = {
            "configured_rates": {"retention": "10%", "tax": "20%"},
            "database_totals": {key: str(q2(Decimal(str(invoice[key])))) for key in expected},
            "xlsx_labels": xlsx_labels,
            "pdf_has_5_percent_label": "RETENUE DE GARANTIE 5%" in pdf_text,
            "pdf_has_19_percent_label": "T V A 19%" in pdf_text,
            "pdf_has_10_percent_label": "RETENUE DE GARANTIE 10%" in pdf_text,
            "pdf_has_20_percent_label": "T V A 20%" in pdf_text,
            "paths": {"xlsx": str(xlsx_path), "pdf": str(pdf_path), "rendered": str(rendered)},
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    finally:
        for key, value in original.items():
            connection.execute("UPDATE app_settings SET value=? WHERE key=?", (value, key))
        connection.commit()
        connection.close()


if __name__ == "__main__":
    main()
