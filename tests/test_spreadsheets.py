from services.bpu import import_bpu, read_xlsx_sheet
from services.xlsx import make_xlsx, styled


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


def test_styled_preserves_value_and_style():
    assert styled("TOTAL", 6) == ("TOTAL", 6)
