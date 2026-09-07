from datetime import datetime
import os
from pathlib import Path
import sqlite3
import tempfile

from db import DB_PATH, db, invalidate_schema_cache


BACKUP_DIR = Path(
    os.environ.get("PHOENIX_BACKUP_DIR", Path(__file__).resolve().parents[1] / "backups")
)


def create_backup():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    target = BACKUP_DIR / f"phoenix_backup_{timestamp}.sqlite3"
    if not DB_PATH.exists():
        with db():
            pass
    temporary_target = target.with_suffix(".tmp")
    source_connection = sqlite3.connect(DB_PATH, timeout=10)
    backup_connection = sqlite3.connect(temporary_target)
    try:
        source_connection.backup(backup_connection)
        result = backup_connection.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise ValueError(f"Base invalide apres sauvegarde: {result}")
    except Exception:
        backup_connection.close()
        source_connection.close()
        temporary_target.unlink(missing_ok=True)
        raise
    else:
        backup_connection.close()
        source_connection.close()
    os.replace(temporary_target, target)
    return target


def list_backups():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(BACKUP_DIR.glob("*.sqlite3"), key=lambda path: path.stat().st_mtime, reverse=True)


def restore_backup_content(content):
    if not content:
        raise ValueError("Fichier de sauvegarde vide.")
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        suffix=".sqlite3",
        delete=False,
        dir=DB_PATH.parent,
    ) as tmp:
        tmp.write(content)
        tmp.flush()
        os.fsync(tmp.fileno())
        temp_path = Path(tmp.name)
    try:
        con = sqlite3.connect(temp_path)
        try:
            result = con.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                raise ValueError(f"Base invalide: {result}")
            required = {"company_settings", "invoices", "invoice_lines"}
            existing = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            missing = required - existing
            if missing:
                raise ValueError("Tables manquantes: " + ", ".join(sorted(missing)))
        finally:
            con.close()
        create_backup()
        source_connection = sqlite3.connect(temp_path, timeout=10)
        destination_connection = sqlite3.connect(DB_PATH, timeout=10)
        try:
            source_connection.backup(destination_connection)
            restored_check = destination_connection.execute(
                "PRAGMA integrity_check"
            ).fetchone()[0]
            if restored_check != "ok":
                raise ValueError(f"Base invalide apres restauration: {restored_check}")
        finally:
            destination_connection.close()
            source_connection.close()
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    else:
        temp_path.unlink(missing_ok=True)
    invalidate_schema_cache(DB_PATH)
    with db():
        pass
