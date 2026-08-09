from datetime import datetime
from pathlib import Path
import shutil
import sqlite3
import tempfile

from db import DB_PATH, db


BACKUP_DIR = Path(__file__).resolve().parents[1] / "backups"


def create_backup():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = BACKUP_DIR / f"phoenix_backup_{timestamp}.sqlite3"
    if DB_PATH.exists():
        shutil.copy2(DB_PATH, target)
    else:
        with db():
            pass
        shutil.copy2(DB_PATH, target)
    return target


def list_backups():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(BACKUP_DIR.glob("*.sqlite3"), key=lambda path: path.stat().st_mtime, reverse=True)


def restore_backup_content(content):
    if not content:
        raise ValueError("Fichier de sauvegarde vide.")
    with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as tmp:
        tmp.write(content)
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
    finally:
        temp_path.unlink(missing_ok=True)
    create_backup()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    DB_PATH.write_bytes(content)
    with db():
        pass
