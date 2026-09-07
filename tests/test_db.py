import db as db_module


def test_schema_is_initialized_once_until_invalidated(tmp_path, monkeypatch):
    database_path = tmp_path / "data" / "cached-schema.sqlite3"
    monkeypatch.setattr(db_module, "DB_PATH", database_path)
    db_module.invalidate_schema_cache(database_path)
    original_ensure_schema = db_module.ensure_schema
    calls = []

    def counted_ensure_schema(connection):
        calls.append(database_path)
        return original_ensure_schema(connection)

    monkeypatch.setattr(db_module, "ensure_schema", counted_ensure_schema)
    try:
        with db_module.db():
            pass
        with db_module.db():
            pass
        assert calls == [database_path]

        db_module.invalidate_schema_cache(database_path)
        with db_module.db():
            pass
        assert calls == [database_path, database_path]
    finally:
        db_module.invalidate_schema_cache(database_path)
