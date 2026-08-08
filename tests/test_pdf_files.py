import os
import time
from pathlib import Path

from services.pdf_files import archive_metadata, cleanup_old_exports


def test_archive_metadata_extracts_invoice_number():
    assert archive_metadata(Path("Facture_136-2026_4866700d.pdf")) == ("FACT", "136-2026")
    assert archive_metadata(Path("Devis_Quantitatif_TEST-FAC-231657_3f416f82.pdf")) == ("DQ", "TEST-FAC-231657")
    assert archive_metadata(Path("unknown.pdf")) == ("PDF", "")


def test_cleanup_old_exports_keeps_newest_files(tmp_path):
    files = []
    for index in range(5):
        path = tmp_path / f"Facture_1_{index}.pdf"
        path.write_bytes(b"pdf")
        timestamp = time.time() + index
        os.utime(path, (timestamp, timestamp))
        files.append(path)

    cleanup_old_exports(tmp_path, "Facture_1", ".pdf", keep=2)

    assert sorted(path.name for path in tmp_path.glob("*.pdf")) == [
        files[3].name,
        files[4].name,
    ]
