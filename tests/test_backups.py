from services import backups
import pytest
import sqlite3


def test_create_backup_copies_database(tmp_path, monkeypatch):
    db_path = tmp_path / "data" / "pos_ai.sqlite3"
    db_path.parent.mkdir()
    db_path.write_bytes(b"sqlite-data")
    backup_dir = tmp_path / "backups"

    monkeypatch.setattr(backups, "DB_PATH", db_path)
    monkeypatch.setattr(backups, "BACKUP_DIR", backup_dir)

    backup_path = backups.create_backup()

    assert backup_path.exists()
    assert backup_path.read_bytes() == b"sqlite-data"


def test_restore_backup_content_preserves_previous_database(tmp_path, monkeypatch):
    db_path = tmp_path / "data" / "pos_ai.sqlite3"
    db_path.parent.mkdir()
    db_path.write_bytes(b"old")
    backup_dir = tmp_path / "backups"

    monkeypatch.setattr(backups, "DB_PATH", db_path)
    monkeypatch.setattr(backups, "BACKUP_DIR", backup_dir)
    monkeypatch.setattr(backups, "db", lambda: _NullContext())

    valid_backup = tmp_path / "valid.sqlite3"
    with sqlite3.connect(valid_backup) as con:
        con.execute("CREATE TABLE company_settings(id INTEGER)")
        con.execute("CREATE TABLE invoices(id INTEGER)")
        con.execute("CREATE TABLE invoice_lines(id INTEGER)")

    backups.restore_backup_content(valid_backup.read_bytes())

    assert db_path.read_bytes() == valid_backup.read_bytes()
    assert list(backup_dir.glob("*.sqlite3"))


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
