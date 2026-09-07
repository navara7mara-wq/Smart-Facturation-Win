import sqlite3

import pytest

from services.typologies import (
    resolve_invoice_typology,
    typology_export_label,
    validate_typology_values,
)


pytestmark = pytest.mark.unit


def typology_connection():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE typologies(
            id INTEGER PRIMARY KEY,
            sigle TEXT NOT NULL COLLATE NOCASE UNIQUE,
            libelle_complet TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    connection.executemany(
        "INSERT INTO typologies(sigle, libelle_complet, is_active) VALUES(?,?,?)",
        [
            ("A12", "A12 ( MAT 12M + BTS OUTDOOR )", 1),
            ("MGC", "Maintenance génie civil", 1),
            ("OLD", "Ancienne typologie", 0),
        ],
    )
    return connection


def test_typology_uses_sigle_in_app_and_full_label_in_documents():
    connection = typology_connection()
    try:
        typology = resolve_invoice_typology(connection, "CONSTRUCTION", "a12")
        assert typology["sigle"] == "A12"
        assert typology["libelle_complet"] == "A12 ( MAT 12M + BTS OUTDOOR )"
        assert resolve_invoice_typology(connection, "MGC", "A12")["sigle"] == "MGC"
        assert resolve_invoice_typology(connection, "CONSTRUCTION", "OLD") is None
    finally:
        connection.close()


def test_invoice_export_prefers_historical_full_label_snapshot():
    invoice = {
        "typologie_label_snapshot": "A12 (Ancien libellé)",
        "typologie_snapshot": "A12",
        "typologie_site": "A12",
    }
    assert typology_export_label(invoice) == "A12 (Ancien libellé)"


def test_typology_validation_normalizes_sigle_and_rejects_missing_label():
    assert validate_typology_values(" mat a12 ", "  MAT A12 complet  ") == (
        "MAT A12",
        "MAT A12 complet",
    )
    with pytest.raises(ValueError, match="libellé complet"):
        validate_typology_values("A12", "")
