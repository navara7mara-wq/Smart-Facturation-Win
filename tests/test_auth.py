import sqlite3

from services import auth
from services.auth import hash_password, user_can_write, user_is_admin, verify_password


def test_password_hash_verification():
    stored = hash_password("secret")

    assert verify_password("secret", stored)
    assert not verify_password("wrong", stored)


def test_role_helpers():
    admin = {"role": "admin"}
    editor = {"role": "editor"}
    viewer = {"role": "viewer"}

    assert user_can_write(admin)
    assert user_can_write(editor)
    assert not user_can_write(viewer)
    assert user_is_admin(admin)
    assert not user_is_admin(editor)


def test_create_user_and_update_password(monkeypatch):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    monkeypatch.setattr(auth, "db", lambda: _ConnectionContext(connection))

    auth.create_user("editor", "secret", "editor")
    user = connection.execute("SELECT * FROM users WHERE username='editor'").fetchone()
    assert user["role"] == "editor"
    assert auth.verify_password("secret", user["password_hash"])

    auth.update_user_password(user["id"], "new-secret")
    updated = connection.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
    assert auth.verify_password("new-secret", updated["password_hash"])


class _ConnectionContext:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.connection.commit()
        return False
