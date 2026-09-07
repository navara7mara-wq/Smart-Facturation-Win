import os
from pathlib import Path
from uuid import uuid4


MAX_UPLOAD_BYTES = {
    ".png": 5 * 1024 * 1024,
    ".jpg": 5 * 1024 * 1024,
    ".jpeg": 5 * 1024 * 1024,
    ".webp": 5 * 1024 * 1024,
    ".gif": 5 * 1024 * 1024,
    ".pdf": 20 * 1024 * 1024,
    ".xlsx": 20 * 1024 * 1024,
    ".xls": 20 * 1024 * 1024,
}


def _content_matches_suffix(content, suffix):
    if suffix == ".png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if suffix in {".jpg", ".jpeg"}:
        return content.startswith(b"\xff\xd8\xff")
    if suffix == ".webp":
        return len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WEBP"
    if suffix == ".gif":
        return content.startswith((b"GIF87a", b"GIF89a"))
    if suffix == ".pdf":
        return content.lstrip().startswith(b"%PDF-")
    if suffix == ".xlsx":
        return content.startswith(b"PK\x03\x04")
    if suffix == ".xls":
        return content.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    return False


def save_upload(upload, folder, allowed_suffixes, root_dir, upload_dir):
    if not upload or not upload.get("filename"):
        return ""
    suffix = Path(upload["filename"]).suffix.lower()
    if suffix not in allowed_suffixes:
        raise ValueError(f"Extension non autorisee: {suffix}")
    content = upload.get("content")
    if not isinstance(content, bytes) or not _content_matches_suffix(content, suffix):
        raise ValueError("Contenu de fichier invalide pour l'extension indiquee.")
    maximum = MAX_UPLOAD_BYTES.get(suffix, 10 * 1024 * 1024)
    if len(content) > maximum:
        raise ValueError(f"Fichier trop volumineux (maximum {maximum // (1024 * 1024)} Mo).")
    upload_root = Path(upload_dir).resolve()
    target_dir = (upload_root / folder).resolve()
    try:
        target_dir.relative_to(upload_root)
    except ValueError as exc:
        raise ValueError("Dossier de televersement invalide.") from exc
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{uuid4().hex}{suffix}"
    temporary = target.with_suffix(target.suffix + ".tmp")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise
    try:
        stored_path = target.relative_to(root_dir)
    except ValueError:
        stored_path = Path("uploads") / target.relative_to(upload_dir)
    return str(stored_path).replace("\\", "/")
