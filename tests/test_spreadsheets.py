import pytest
from zipfile import ZipFile

from services.bpu import import_bpu, read_xlsx_sheet
from services.xlsx import exact_number, make_xlsx, styled


pytestmark = pytest.mark.unit


def test_make_xlsx_creates_readable_workbook(tmp_path):
    workbook = tmp_path / "workbook.xlsx"
    workbook.write_bytes(make_xlsx([("Sheet 1", [["Name", "Qty"], ["Article", 3]])]))

    assert read_xlsx_sheet(workbook, "Sheet 1") == [["Name", "Qty"], ["Article", 3]]


def test_import_bpu_extracts_articles_by_category(tmp_path):
    workbook = tmp_path / "bpu.xlsx"
    workbook.write_bytes(make_xlsx([
        ("BPU", [
            ["Item", "Designation", "Unite", "Prix"],
            ["", "Acquisition", "", ""],
            [1, "Router", "U", 1500],
            ["", "Prestation", "", ""],
            [6, "Installation", "Forfait", 2500],
        ])
    ]))

    assert import_bpu(workbook) == [
        (1, "Router", "U", 1500.0, "acquisition"),
        (6, "Installation", "Forfait", 2500.0, "prestation"),
    ]


def test_import_bpu_assigns_leading_articles_to_first_declared_category(tmp_path):
    workbook = tmp_path / "bpu-leading-rows.xlsx"
    workbook.write_bytes(make_xlsx([
        ("BPU_Entreprise", [
            ["code", "D é s i g n a t i o n", "Unité", "prix Unitaire"],
            [1, "Étude initiale", "Forfait", 1000],
            [2, "Visite technique", "Forfait", 2000],
            ["", "Prestation", "", ""],
            [3, "Installation", "U", 3000],
        ])
    ]))

    assert import_bpu(workbook) == [
        (1, "Étude initiale", "Forfait", 1000.0, "prestation"),
        (2, "Visite technique", "Forfait", 2000.0, "prestation"),
        (3, "Installation", "U", 3000.0, "prestation"),
    ]


def test_styled_preserves_value_and_style():
    assert styled("TOTAL", 6) == ("TOTAL", 6)


def test_exact_number_is_serialized_without_binary_float_noise(tmp_path):
    workbook = tmp_path / "exact.xlsx"
    workbook.write_bytes(make_xlsx([("Sheet 1", [[exact_number("78.40")]])]))

    with ZipFile(workbook) as archive:
        sheet_xml = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")

    assert "<v>78.40</v>" in sheet_xml
    assert "78.40000000000001" not in sheet_xml
