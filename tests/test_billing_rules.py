from contextlib import closing
from decimal import Decimal
import sqlite3
from pathlib import Path

import pytest

from services.billing import amount_to_french, parse_invoice_lines, totals_from_lines
from services.money import line_total, truncate_money


pytestmark = pytest.mark.unit


ROOT_DIR = Path(__file__).resolve().parents[1]
SCHEMA_SQL = (ROOT_DIR / "database" / "schema.sql").read_text(encoding="utf-8")


def memory_db():
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys = ON;")
    connection.executescript(SCHEMA_SQL)
    return connection


def seed_invoice_context(connection):
    direction_id = connection.execute(
        """
        INSERT INTO mobilis_directions(doit_nom, direction_regionale)
        VALUES('Mobilis', 'Alger')
        """
    ).lastrowid
    ndc_po_id = connection.execute(
        """
        INSERT INTO purchase_orders(numero_bc, mobilis_direction_id, type_bc)
        VALUES('BC-NDC-1', ?, 'NDC')
        """,
        (direction_id,),
    ).lastrowid
    regular_po_id = connection.execute(
        """
        INSERT INTO purchase_orders(numero_bc, mobilis_direction_id, type_bc)
        VALUES('BC-ACQ-1', ?, 'ACQUISITION')
        """,
        (direction_id,),
    ).lastrowid
    ndc_site_id = connection.execute(
        """
        INSERT INTO sites(purchase_order_id, code_site, nom_site)
        VALUES(?, 'NDC-SITE-1', 'NDC Site 1')
        """,
        (ndc_po_id,),
    ).lastrowid
    regular_site_id = connection.execute(
        """
        INSERT INTO sites(purchase_order_id, code_site, nom_site)
        VALUES(?, 'ACQ-SITE-1', 'Acquisition Site 1')
        """,
        (regular_po_id,),
    ).lastrowid
    return ndc_po_id, regular_po_id, ndc_site_id, regular_site_id


def test_parse_invoice_lines_accepts_commas_tabs_semicolons():
    assert parse_invoice_lines("1, 2\n2\t3; 6,1.5") == [
        (1, 2.0),
        (2, 3.0),
        (6, 1.5),
    ]


def test_parse_invoice_lines_rejects_invalid_rows():
    with pytest.raises(ValueError, match="Chaque ligne article"):
        parse_invoice_lines("1, 2, 3")


def test_totals_from_lines_apply_retenue_and_tva():
    lines = [{"montant_ht": 1000.0}, {"montant_ht": 250.0}]

    assert totals_from_lines(lines) == tuple(map(Decimal, ("1250.00", "62.50", "1187.50", "225.62", "1413.12")))


def test_totals_from_lines_accept_custom_financial_rates():
    lines = [{"montant_ht": 1000.0}]

    assert totals_from_lines(lines, retention_rate=10, tax_rate=20) == tuple(map(Decimal, ("1000.00", "100.00", "900.00", "180.00", "1080.00")))


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("12.349", "12.34"),
        ("12.345", "12.34"),
        ("12.999", "12.99"),
        ("100.005", "100.00"),
        ("-12.999", "-12.99"),
    ],
)
def test_authoritative_money_is_truncated_toward_zero(source, expected):
    assert truncate_money(source) == Decimal(expected)


@pytest.mark.parametrize(
    ("case_id", "quantity", "unit_price", "expected"),
    [
        ("FIN-G-06", "1", "0.005", "0.00"),
        ("FIN-G-07", "1", "0.015", "0.01"),
        ("FIN-G-19", "1", "0.025", "0.02"),
    ],
)
def test_known_rc1_boundary_failures_follow_approved_truncation(case_id, quantity, unit_price, expected):
    assert line_total(unit_price, quantity) == Decimal(expected), case_id


def test_amount_to_french_handles_zero_without_mojibake():
    assert amount_to_french(0) == "zero dinars"


def test_ndc_invoice_requires_no_direct_site():
    with closing(memory_db()) as connection:
        ndc_po_id, _, ndc_site_id, _ = seed_invoice_context(connection)

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO invoices(invoice_number, invoice_type, purchase_order_id, site_id)
                VALUES('NDC-INVALID', 'NDC', ?, ?)
                """,
                (ndc_po_id, ndc_site_id),
            )


def test_invoice_sites_accepts_only_ndc_invoices():
    with closing(memory_db()) as connection:
        _, regular_po_id, _, regular_site_id = seed_invoice_context(connection)
        invoice_id = connection.execute(
            """
            INSERT INTO invoices(invoice_number, invoice_type, purchase_order_id, site_id)
            VALUES('ACQ-1', 'ACQUISITION', ?, ?)
            """,
            (regular_po_id, regular_site_id),
        ).lastrowid

        with pytest.raises(sqlite3.IntegrityError, match="invoice_sites accepts only NDC invoices"):
            connection.execute(
                "INSERT INTO invoice_sites(invoice_id, site_id) VALUES(?, ?)",
                (invoice_id, regular_site_id),
            )


def test_ndc_invoice_lines_accept_only_article_6():
    with closing(memory_db()) as connection:
        ndc_po_id, _, _, _ = seed_invoice_context(connection)
        invoice_id = connection.execute(
            """
            INSERT INTO invoices(invoice_number, invoice_type, purchase_order_id)
            VALUES('NDC-1', 'NDC', ?)
            """,
            (ndc_po_id,),
        ).lastrowid

        with pytest.raises(sqlite3.IntegrityError, match="NDC invoices accept only article 6"):
            connection.execute(
                """
                INSERT INTO invoice_lines(
                    invoice_id, article_number, designation_snapshot, unite_snapshot,
                    pu_ht_snapshot, categorie_snapshot, quantite, montant_ht
                )
                VALUES(?, 5, 'Invalid article', 'U', 100, 'ndc', 1, 100)
                """,
                (invoice_id,),
            )
