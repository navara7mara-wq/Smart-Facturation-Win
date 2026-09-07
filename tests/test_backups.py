from contextlib import closing

from services import backups
import pytest
import sqlite3


def test_create_backup_copies_database(tmp_path, monkeypatch):
    db_path = tmp_path / "data" / "pos_ai.sqlite3"
    db_path.parent.mkdir()
    with closing(sqlite3.connect(db_path)) as con:
        con.execute("CREATE TABLE sample(value TEXT)")
        con.execute("INSERT INTO sample(value) VALUES('sqlite-data')")
        con.commit()
    backup_dir = tmp_path / "backups"

    monkeypatch.setattr(backups, "DB_PATH", db_path)
    monkeypatch.setattr(backups, "BACKUP_DIR", backup_dir)

    backup_path = backups.create_backup()

    assert backup_path.exists()
    with closing(sqlite3.connect(backup_path)) as con:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("SELECT value FROM sample").fetchone()[0] == "sqlite-data"


def test_restore_backup_content_preserves_previous_database(tmp_path, monkeypatch):
    db_path = tmp_path / "data" / "pos_ai.sqlite3"
    db_path.parent.mkdir()
    with closing(sqlite3.connect(db_path)) as con:
        con.execute("CREATE TABLE old_data(value TEXT)")
        con.execute("INSERT INTO old_data(value) VALUES('preserved')")
        con.commit()
    backup_dir = tmp_path / "backups"

    monkeypatch.setattr(backups, "DB_PATH", db_path)
    monkeypatch.setattr(backups, "BACKUP_DIR", backup_dir)
    monkeypatch.setattr(backups, "db", lambda: _NullContext())

    valid_backup = tmp_path / "valid.sqlite3"
    with closing(sqlite3.connect(valid_backup)) as con:
        con.execute("CREATE TABLE company_settings(id INTEGER)")
        con.execute("CREATE TABLE invoices(id INTEGER)")
        con.execute("CREATE TABLE invoice_lines(id INTEGER)")
        con.commit()

    backups.restore_backup_content(valid_backup.read_bytes())

    with closing(sqlite3.connect(db_path)) as con:
        restored_tables = {
            row[0]
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"company_settings", "invoices", "invoice_lines"} <= restored_tables
        assert "old_data" not in restored_tables
    previous_backups = list(backup_dir.glob("*.sqlite3"))
    assert previous_backups
    with closing(sqlite3.connect(previous_backups[0])) as con:
        assert con.execute("SELECT value FROM old_data").fetchone()[0] == "preserved"


def test_restore_backup_content_rejects_invalid_sqlite(tmp_path, monkeypatch):
    monkeypatch.setattr(backups, "DB_PATH", tmp_path / "data" / "pos_ai.sqlite3")
    monkeypatch.setattr(backups, "BACKUP_DIR", tmp_path / "backups")

    with pytest.raises(Exception):
        backups.restore_backup_content(b"not sqlite")


class _NullContext:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False
