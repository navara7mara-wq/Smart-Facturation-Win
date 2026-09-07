from contextlib import closing
import sqlite3

import pytest

from services import auth
from services.auth import hash_password, user_can_write, user_is_admin, user_is_super_admin, verify_password


def test_password_hash_verification():
    stored = hash_password("secret")

    assert verify_password("secret", stored)
    assert not verify_password("wrong", stored)


def test_role_helpers():
    admin = {"role": "admin"}
    editor = {"role": "editor"}
    viewer = {"role": "viewer"}
    super_admin = {"role": "super_admin"}

    assert user_can_write(admin)
    assert user_can_write(editor)
    assert not user_can_write(viewer)
    assert user_is_admin(admin)
    assert user_is_admin(super_admin)
    assert user_is_super_admin(super_admin)
    assert not user_is_admin(editor)


def test_create_user_and_update_password(monkeypatch):
    with closing(memory_auth_db()) as connection:
        monkeypatch.setattr(auth, "db", lambda: _ConnectionContext(connection))

        auth.create_user("editor", "Secret123", "editor", 1)
        user = connection.execute("SELECT * FROM users WHERE username='editor'").fetchone()
        assert user["role"] == "editor"
        assert auth.verify_password("Secret123", user["password_hash"])

        auth.update_user_password(user["id"], "Newsecret123")
        updated = connection.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
        assert auth.verify_password("Newsecret123", updated["password_hash"])


@pytest.mark.parametrize("iteration", range(10))
def test_secure_first_run_setup_has_no_reusable_default(iteration, monkeypatch):
    with closing(memory_auth_db()) as connection:
        monkeypatch.setattr(auth, "db", lambda: _ConnectionContext(connection))

        auth.ensure_default_admin()
        pending = connection.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        assert pending["role"] == "super_admin"
        assert pending["must_change_password"] == 1
        assert not auth.verify_password("admin123", pending["password_hash"])
        assert auth.first_run_required() is True

        password = f"SecureRC2Pass{iteration}"
        configured = auth.complete_first_run_setup(password)
        assert configured["role"] == "super_admin"
        assert configured["must_change_password"] == 0
        assert auth.verify_password(password, configured["password_hash"])
        assert not auth.verify_password("admin123", configured["password_hash"])


def test_mandatory_password_change_preserves_role_and_invalidates_sessions(monkeypatch):
    with closing(memory_auth_db()) as connection:
        monkeypatch.setattr(auth, "db", lambda: _ConnectionContext(connection))
        user_id = connection.execute(
            """
            INSERT INTO users(username, password_hash, role, must_change_password)
            VALUES('admin', ?, 'super_admin', 1)
            """,
            (auth.hash_password("Existing123"),),
        ).lastrowid
        connection.execute(
            "INSERT INTO user_sessions(token, user_id, expires_at, csrf_token) VALUES('existing', ?, '2999-01-01', 'csrf')",
            (user_id,),
        )

        with pytest.raises(ValueError, match="actuel invalide"):
            auth.change_own_password(user_id, "Wrong123", "Changed123")
        auth.change_own_password(user_id, "Existing123", "Changed123")

        updated = connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        assert updated["role"] == "super_admin"
        assert updated["must_change_password"] == 0
        assert auth.verify_password("Changed123", updated["password_hash"])
        assert connection.execute("SELECT COUNT(*) FROM user_sessions WHERE user_id=?", (user_id,)).fetchone()[0] == 0


def test_last_active_admin_is_protected(monkeypatch):
    with closing(memory_auth_db()) as connection:
        monkeypatch.setattr(auth, "db", lambda: _ConnectionContext(connection))
        admin_id = connection.execute(
            "INSERT INTO users(username, password_hash, role) VALUES('admin', 'hash', 'super_admin')"
        ).lastrowid

        for operation in (
            lambda: auth.set_user_active(admin_id, False),
            lambda: auth.set_user_role(admin_id, "viewer", 1),
            lambda: auth.delete_user(admin_id),
        ):
            try:
                operation()
                assert False, "operation should be rejected"
            except ValueError as exc:
                assert "dernier administrateur" in str(exc)


def test_delete_test_users_removes_only_test_accounts(monkeypatch):
    with closing(memory_auth_db()) as connection:
        monkeypatch.setattr(auth, "db", lambda: _ConnectionContext(connection))
        keep_id = connection.execute(
            "INSERT INTO users(username, password_hash, role) VALUES('admin', 'hash', 'admin')"
        ).lastrowid
        test_id = connection.execute(
            "INSERT INTO users(username, password_hash, role) VALUES('test123', 'hash', 'viewer')"
        ).lastrowid
        connection.execute("INSERT INTO user_sessions(token, user_id, expires_at, csrf_token) VALUES('t', ?, '2999-01-01', 'c')", (test_id,))

        auth.delete_test_users()

        assert connection.execute("SELECT id FROM users WHERE id=?", (keep_id,)).fetchone()
        assert not connection.execute("SELECT id FROM users WHERE id=?", (test_id,)).fetchone()
        assert not connection.execute("SELECT token FROM user_sessions WHERE token='t'").fetchone()


def memory_auth_db():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            company_branch_id INTEGER,
            is_active INTEGER NOT NULL DEFAULT 1,
            must_change_password INTEGER NOT NULL DEFAULT 0,
            failed_attempts INTEGER NOT NULL DEFAULT 0,
            locked_until TEXT,
            last_login_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    connection.execute("""
        CREATE TABLE user_sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            expires_at TEXT NOT NULL,
            csrf_token TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    return connection


class _ConnectionContext:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.connection.commit()
        return False
