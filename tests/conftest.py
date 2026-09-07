import pytest


@pytest.fixture(autouse=True)
def isolate_backup_artifacts(tmp_path, monkeypatch):
    """Keep pytest databases/backups outside application and user data paths."""
    import db as db_module
    from services import backups

    qa_database = tmp_path / "qa-data" / "pytest.sqlite3"
    monkeypatch.setenv("PHOENIX_DB_PATH", str(qa_database))
    monkeypatch.setattr(db_module, "DB_PATH", qa_database)
    monkeypatch.setattr(backups, "DB_PATH", qa_database)
    monkeypatch.setattr(backups, "BACKUP_DIR", tmp_path / "qa-backups")
