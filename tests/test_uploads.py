from pathlib import Path

import pytest

from services.uploads import save_upload


def test_save_upload_writes_allowed_file_under_upload_dir(tmp_path):
    upload_dir = tmp_path / "uploads"
    result = save_upload(
        {"filename": "logo.PNG", "content": b"image-bytes"},
        "logos",
        {".png"},
        tmp_path,
        upload_dir,
    )

    saved_path = tmp_path / Path(result)
    assert result.startswith("uploads/logos/")
    assert saved_path.read_bytes() == b"image-bytes"


def test_save_upload_rejects_disallowed_extension(tmp_path):
    with pytest.raises(ValueError, match="Extension non autorisee"):
        save_upload(
            {"filename": "payload.exe", "content": b"nope"},
            "logos",
            {".png"},
            tmp_path,
            tmp_path / "uploads",
        )
