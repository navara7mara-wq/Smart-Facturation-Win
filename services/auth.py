import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

from db import db


SESSION_COOKIE = "phoenix_session"
SESSION_DAYS = 7
DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin123"
MAX_FAILED_ATTEMPTS = 5
LOCK_MINUTES = 15


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


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


def validate_password_strength(password):
    password = password or ""
    if len(password) < 8:
        raise ValueError("Le mot de passe doit contenir au moins 8 caracteres.")
    if password in {DEFAULT_ADMIN_PASSWORD, "password", "12345678", "admin1234"}:
        raise ValueError("Mot de passe trop faible.")
    if not any(char.isalpha() for char in password) or not any(char.isdigit() for char in password):
        raise ValueError("Le mot de passe doit contenir lettres et chiffres.")


def ensure_default_admin():
    with db() as con:
        count = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if count:
            return
        con.execute(
            "INSERT INTO users(username, password_hash, role, must_change_password) VALUES(?, ?, 'admin', 1)",
            (DEFAULT_ADMIN_USERNAME, hash_password(DEFAULT_ADMIN_PASSWORD)),
        )


def first_run_required():
    ensure_default_admin()
    with db() as con:
        row = con.execute(
            "SELECT id, username, must_change_password, last_login_at FROM users WHERE username=?",
            (DEFAULT_ADMIN_USERNAME,),
        ).fetchone()
        count = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    return bool(row and count == 1 and row["must_change_password"] and not row["last_login_at"])


def complete_first_run_setup(current_password, new_password):
    user = authenticate(DEFAULT_ADMIN_USERNAME, current_password)
    if not user:
        raise ValueError("Mot de passe initial invalide.")
    update_user_password(user["id"], new_password)
    return authenticate(DEFAULT_ADMIN_USERNAME, new_password)


def authenticate(username, password):
    ensure_default_admin()
    now = utc_now()
    with db() as con:
        user = con.execute(
            "SELECT * FROM users WHERE username=? AND is_active=1",
            (username,),
        ).fetchone()
        if user and user["locked_until"]:
            try:
                if datetime.fromisoformat(user["locked_until"]) > now:
                    return None
            except ValueError:
                pass
        if not user or not verify_password(password, user["password_hash"]):
            if user:
                attempts = int(user["failed_attempts"] or 0) + 1
                locked_until = None
                if attempts >= MAX_FAILED_ATTEMPTS:
                    locked_until = (now + timedelta(minutes=LOCK_MINUTES)).isoformat(timespec="seconds")
                con.execute(
                    "UPDATE users SET failed_attempts=?, locked_until=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (attempts, locked_until, user["id"]),
                )
            return None
        con.execute(
            "UPDATE users SET failed_attempts=0, locked_until=NULL, last_login_at=CURRENT_TIMESTAMP WHERE id=?",
            (user["id"],),
        )
        user = con.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
    if not user:
        return None
    return user


def create_session(user_id):
    token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    expires_at = (utc_now() + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds")
    with db() as con:
        con.execute(
            "INSERT INTO user_sessions(token, user_id, expires_at, csrf_token) VALUES(?, ?, ?, ?)",
            (token, user_id, expires_at, csrf_token),
        )
    return token


def session_csrf_token(token):
    if not token:
        return ""
    now = utc_now().isoformat(timespec="seconds")
    with db() as con:
        row = con.execute(
            "SELECT csrf_token FROM user_sessions WHERE token=? AND expires_at>?",
            (token, now),
        ).fetchone()
    return row["csrf_token"] if row else ""


def get_session_user(token):
    if not token:
        return None
    now = utc_now().isoformat(timespec="seconds")
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
            "SELECT id, username, role, is_active, created_at, last_login_at, locked_until FROM users ORDER BY username"
        ).fetchall()


def create_user(username, password, role):
    username = (username or "").strip()
    if not username:
        raise ValueError("Nom utilisateur obligatoire.")
    if role not in {"admin", "editor", "viewer"}:
        raise ValueError("Role invalide.")
    validate_password_strength(password)
    with db() as con:
        con.execute(
            "INSERT INTO users(username, password_hash, role) VALUES(?, ?, ?)",
            (username, hash_password(password), role),
        )


def update_user_password(user_id, password):
    validate_password_strength(password)
    with db() as con:
        con.execute(
            "UPDATE users SET password_hash=?, must_change_password=0, failed_attempts=0, locked_until=NULL, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (hash_password(password), user_id),
        )


def set_user_role(user_id, role):
    if role not in {"admin", "editor", "viewer"}:
        raise ValueError("Role invalide.")
    with db() as con:
        con.execute("UPDATE users SET role=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (role, user_id))


def set_user_active(user_id, is_active):
    with db() as con:
        con.execute("UPDATE users SET is_active=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (1 if is_active else 0, user_id))


def user_can_write(user):
    return bool(user and user["role"] in {"admin", "editor"})


def user_is_admin(user):
    return bool(user and user["role"] == "admin")
