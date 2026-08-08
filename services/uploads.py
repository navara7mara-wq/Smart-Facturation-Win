from pathlib import Path
from uuid import uuid4


def save_upload(upload, folder, allowed_suffixes, root_dir, upload_dir):
    if not upload or not upload.get("filename"):
        return ""
    suffix = Path(upload["filename"]).suffix.lower()
    if suffix not in allowed_suffixes:
        raise ValueError(f"Extension non autorisee: {suffix}")
    target_dir = upload_dir / folder
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{uuid4().hex}{suffix}"
    target.write_bytes(upload["content"])
    return str(target.relative_to(root_dir)).replace("\\", "/")
