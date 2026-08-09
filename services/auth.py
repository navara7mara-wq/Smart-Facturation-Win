import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta

from db import db


SESSION_COOKIE = "phoenix_session"
SESSION_DAYS = 7
DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin123"


def hash_password(password, salt=None):
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return base64.b64encode(salt).decode("ascii") + "$" + base64.b64encode(digest).decode("ascii")


def verify_password(password, stored_hash):
    try:
        salt_text, digest_text = stored_hash.split("$", 1)
        salt = base64.b64decode(salt_text.encode("ascii"))
    except (ValueError, TypeError):
        return False
    candidate = hash_password(password, salt).split("$", 1)[1]
    return hmac.compare_digest(candidate, digest_text)


def ensure_default_admin():
    with db() as con:
        count = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if count:
            return
        con.execute(
            "INSERT INTO users(username, password_hash, role) VALUES(?, ?, 'admin')",
            (DEFAULT_ADMIN_USERNAME, hash_password(DEFAULT_ADMIN_PASSWORD)),
        )


def authenticate(username, password):
    ensure_default_admin()
    with db() as con:
        user = con.execute(
            "SELECT * FROM users WHERE username=? AND is_active=1",
            (username,),
        ).fetchone()
    if not user or not verify_password(password, user["password_hash"]):
        return None
    return user


def create_session(user_id):
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.utcnow() + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds")
    with db() as con:
        con.execute(
            "INSERT INTO user_sessions(token, user_id, expires_at) VALUES(?, ?, ?)",
            (token, user_id, expires_at),
        )
    return token


def get_session_user(token):
    if not token:
        return None
    now = datetime.utcnow().isoformat(timespec="seconds")
    with db() as con:
        user = con.execute(
            """
            SELECT u.*
            FROM user_sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token=? AND s.expires_at>? AND u.is_active=1
            """,
            (token, now),
        ).fetchone()
    return user


def delete_session(token):
    if not token:
        return
    with db() as con:
        con.execute("DELETE FROM user_sessions WHERE token=?", (token,))


def list_users():
    ensure_default_admin()
    with db() as con:
        return con.execute(
            "SELECT id, username, role, is_active, created_at FROM users ORDER BY username"
        ).fetchall()


def create_user(username, password, role):
    username = (username or "").strip()
    if not username:
        raise ValueError("Nom utilisateur obligatoire.")
    if role not in {"admin", "editor", "viewer"}:
        raise ValueError("Role invalide.")
    if not password:
        raise ValueError("Mot de passe obligatoire.")
    with db() as con:
        con.execute(
            "INSERT INTO users(username, password_hash, role) VALUES(?, ?, ?)",
            (username, hash_password(password), role),
        )


def update_user_password(user_id, password):
    if not password:
        raise ValueError("Mot de passe obligatoire.")
    with db() as con:
        con.execute(
            "UPDATE users SET password_hash=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (hash_password(password), user_id),
        )


def user_can_write(user):
    return bool(user and user["role"] in {"admin", "editor"})


def user_is_admin(user):
    return bool(user and user["role"] == "admin")
