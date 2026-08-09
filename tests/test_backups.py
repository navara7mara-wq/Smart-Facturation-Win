from services import backups


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

    backups.restore_backup_content(b"new")

    assert db_path.read_bytes() == b"new"
    assert list(backup_dir.glob("*.sqlite3"))


class _NullContext:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False
