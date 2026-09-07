from pathlib import Path

import pytest

from services.uploads import save_upload


def test_save_upload_writes_allowed_file_under_upload_dir(tmp_path):
    upload_dir = tmp_path / "uploads"
    result = save_upload(
        {"filename": "logo.PNG", "content": b"\x89PNG\r\n\x1a\nimage-bytes"},
        "logos",
        {".png"},
        tmp_path,
        upload_dir,
    )

    saved_path = tmp_path / Path(result)
    assert result.startswith("uploads/logos/")
    assert saved_path.read_bytes() == b"\x89PNG\r\n\x1a\nimage-bytes"


def test_save_upload_rejects_disallowed_extension(tmp_path):
    with pytest.raises(ValueError, match="Extension non autorisee"):
        save_upload(
            {"filename": "payload.exe", "content": b"nope"},
            "logos",
            {".png"},
            tmp_path,
            tmp_path / "uploads",
        )


def test_save_upload_rejects_spoofed_content_and_oversized_file(tmp_path):
    with pytest.raises(ValueError, match="Contenu de fichier invalide"):
        save_upload(
            {"filename": "spoofed.png", "content": b"not-a-png"},
            "logos",
            {".png"},
            tmp_path,
            tmp_path / "uploads",
        )
    with pytest.raises(ValueError, match="Fichier trop volumineux"):
        save_upload(
            {"filename": "large.png", "content": b"\x89PNG\r\n\x1a\n" + b"x" * (5 * 1024 * 1024)},
            "logos",
            {".png"},
            tmp_path,
            tmp_path / "uploads",
        )


@pytest.mark.parametrize(
    ("suffix", "content"),
    [
        (".png", b"\x89PNG\r\n\x1a\nvalid"),
        (".jpg", b"\xff\xd8\xffvalid"),
        (".webp", b"RIFF\x04\x00\x00\x00WEBPvalid"),
        (".pdf", b"%PDF-1.7\nvalid"),
        (".xlsx", b"PK\x03\x04valid"),
        (".xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1valid"),
    ],
)
def test_save_upload_accepts_supported_file_signatures(tmp_path, suffix, content):
    result = save_upload(
        {"filename": "valid" + suffix, "content": content},
        "validated",
        {suffix},
        tmp_path,
        tmp_path / "uploads",
    )

    assert (tmp_path / Path(result)).read_bytes() == content
