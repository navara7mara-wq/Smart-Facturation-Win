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
MAX_FAILED_ATTEMPTS = 5
LOCK_MINUTES = 15
ALL_PERMISSION_CODES = frozenset({
    "invoice.read",
    "invoice.create",
    "invoice.edit_draft",
    "invoice.deposit_dtc",
    "invoice.deposit_mobilis",
    "invoice.mark_paid",
    "invoice.unlock",
    "invoice.cancel",
    "invoice.export",
    "audit.read",
    "client.manage",
    "company_branch.manage",
    "purchase_order.edit",
    "bpu_st.manage",
})
ROLE_PERMISSION_DEFAULTS = {
    "super_admin": ALL_PERMISSION_CODES,
    "admin": frozenset({
        "invoice.read", "invoice.create", "invoice.edit_draft",
        "invoice.deposit_dtc", "invoice.deposit_mobilis", "invoice.mark_paid",
        "invoice.cancel", "invoice.export", "purchase_order.edit",
    }),
    "editor": frozenset({
        "invoice.read",
        "invoice.create",
        "invoice.edit_draft",
        "invoice.deposit_dtc",
        "invoice.deposit_mobilis",
        "invoice.mark_paid",
        "invoice.export",
    }),
    "viewer": frozenset({"invoice.read"}),
}


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
    if password.casefold() in {"admin123", "password", "12345678", "admin1234"}:
        raise ValueError("Mot de passe trop faible.")
    if not any(char.isalpha() for char in password) or not any(char.isdigit() for char in password):
        raise ValueError("Le mot de passe doit contenir lettres et chiffres.")


def ensure_default_admin():
    with db() as con:
        count = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if count:
            return
        one_time_secret = secrets.token_urlsafe(48)
        con.execute(
            "INSERT INTO users(username, password_hash, role, company_branch_id, must_change_password) VALUES(?, ?, 'super_admin', NULL, ?)",
            (DEFAULT_ADMIN_USERNAME, hash_password(one_time_secret), 1),
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


def complete_first_run_setup(new_password):
    validate_password_strength(new_password)
    with db() as con:
        count = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        user = con.execute(
            """
            SELECT * FROM users
            WHERE username=? AND role='super_admin' AND is_active=1
              AND must_change_password=1 AND last_login_at IS NULL
            """,
            (DEFAULT_ADMIN_USERNAME,),
        ).fetchone()
        if count != 1 or not user:
            raise ValueError("Configuration initiale indisponible.")
        con.execute(
            """
            UPDATE users
            SET password_hash=?, must_change_password=0, failed_attempts=0,
                locked_until=NULL, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (hash_password(new_password), user["id"]),
        )
    return authenticate(DEFAULT_ADMIN_USERNAME, new_password)


def change_own_password(user_id, current_password, new_password):
    validate_password_strength(new_password)
    with db() as con:
        user = con.execute(
            "SELECT * FROM users WHERE id=? AND is_active=1",
            (user_id,),
        ).fetchone()
        if not user or not verify_password(current_password, user["password_hash"]):
            raise ValueError("Mot de passe actuel invalide.")
        con.execute(
            """
            UPDATE users
            SET password_hash=?, must_change_password=0, failed_attempts=0,
                locked_until=NULL, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (hash_password(new_password), user_id),
        )
        con.execute("DELETE FROM user_sessions WHERE user_id=?", (user_id,))


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
            "SELECT id, username, role, company_branch_id, is_active, created_at, last_login_at, locked_until FROM users ORDER BY username"
        ).fetchall()


def create_user(username, password, role, company_branch_id=None):
    username = (username or "").strip()
    if not username:
        raise ValueError("Nom utilisateur obligatoire.")
    if role not in ROLE_PERMISSION_DEFAULTS:
        raise ValueError("Role invalide.")
    if role != "super_admin" and not company_branch_id:
        raise ValueError("Direction de l'entreprise obligatoire.")
    validate_password_strength(password)
    with db() as con:
        con.execute(
            "INSERT INTO users(username, password_hash, role, company_branch_id) VALUES(?, ?, ?, ?)",
            (username, hash_password(password), role, None if role == "super_admin" else company_branch_id),
        )


def update_user_password(user_id, password):
    validate_password_strength(password)
    with db() as con:
        con.execute(
            "UPDATE users SET password_hash=?, must_change_password=0, failed_attempts=0, locked_until=NULL, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (hash_password(password), user_id),
        )


def active_admin_count(connection):
    return connection.execute(
        "SELECT COUNT(*) FROM users WHERE role='super_admin' AND is_active=1"
    ).fetchone()[0]


def user_by_id(connection, user_id):
    return connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()


def set_user_role(user_id, role, company_branch_id=None):
    if role not in ROLE_PERMISSION_DEFAULTS:
        raise ValueError("Role invalide.")
    if role != "super_admin" and not company_branch_id:
        raise ValueError("Direction de l'entreprise obligatoire.")
    with db() as con:
        user = user_by_id(con, user_id)
        if not user:
            raise ValueError("Utilisateur introuvable.")
        if user["role"] == "super_admin" and role != "super_admin" and user["is_active"] and active_admin_count(con) <= 1:
            raise ValueError("Impossible de retirer le dernier administrateur actif.")
        con.execute(
            "UPDATE users SET role=?, company_branch_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (role, None if role == "super_admin" else company_branch_id, user_id),
        )


def set_user_active(user_id, is_active):
    with db() as con:
        user = user_by_id(con, user_id)
        if not user:
            raise ValueError("Utilisateur introuvable.")
        if user["role"] == "super_admin" and user["is_active"] and not is_active and active_admin_count(con) <= 1:
            raise ValueError("Impossible de desactiver le dernier administrateur actif.")
        con.execute("UPDATE users SET is_active=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (1 if is_active else 0, user_id))


def unlock_user(user_id):
    with db() as con:
        if not user_by_id(con, user_id):
            raise ValueError("Utilisateur introuvable.")
        con.execute(
            "UPDATE users SET failed_attempts=0, locked_until=NULL, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (user_id,),
        )


def delete_user(user_id):
    with db() as con:
        user = user_by_id(con, user_id)
        if not user:
            raise ValueError("Utilisateur introuvable.")
        if user["role"] == "super_admin" and user["is_active"] and active_admin_count(con) <= 1:
            raise ValueError("Impossible de supprimer le dernier administrateur actif.")
        con.execute("DELETE FROM user_sessions WHERE user_id=?", (user_id,))
        con.execute("DELETE FROM users WHERE id=?", (user_id,))


def delete_test_users():
    with db() as con:
        con.execute("DELETE FROM user_sessions WHERE user_id IN (SELECT id FROM users WHERE username LIKE 'test%' OR username LIKE 'fix%' OR username LIKE 'viewer_e2e%')")
        con.execute("DELETE FROM users WHERE username LIKE 'test%' OR username LIKE 'fix%' OR username LIKE 'viewer_e2e%'")


def _user_value(user, key, default=None):
    if user is None:
        return default
    try:
        value = user[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


def permissions_for_user(user, connection=None):
    if not user or not bool(_user_value(user, "is_active", 1)):
        return frozenset()
    role = str(_user_value(user, "role", "viewer"))
    user_id = _user_value(user, "id")
    if connection is None and user_id is None:
        return ROLE_PERMISSION_DEFAULTS.get(role, frozenset())

    close_connection = connection is None
    con = connection or db()
    try:
        permissions = {
            row[0]
            for row in con.execute(
                "SELECT permission_code FROM role_permissions WHERE role=?",
                (role,),
            )
        }
        if user_id is not None:
            overrides = con.execute(
                """
                SELECT permission_code, allowed
                FROM user_permissions
                WHERE user_id=?
                """,
                (user_id,),
            ).fetchall()
            for permission_code, allowed in overrides:
                if allowed:
                    permissions.add(permission_code)
                else:
                    permissions.discard(permission_code)
        return frozenset(permissions)
    finally:
        if close_connection:
            con.close()


def user_has_permission(user, permission_code, connection=None):
    if permission_code not in ALL_PERMISSION_CODES:
        return False
    return permission_code in permissions_for_user(user, connection)


def set_role_permissions(role, permission_codes, actor=""):
    if role not in ROLE_PERMISSION_DEFAULTS:
        raise ValueError("Role invalide.")
    requested = set(permission_codes)
    unknown = requested - ALL_PERMISSION_CODES
    if unknown:
        raise ValueError("Permission invalide: " + ", ".join(sorted(unknown)))
    with db() as con:
        con.execute("DELETE FROM role_permissions WHERE role=?", (role,))
        con.executemany(
            "INSERT INTO role_permissions(role, permission_code) VALUES(?, ?)",
            [(role, permission_code) for permission_code in sorted(requested)],
        )
        con.execute(
            """
            INSERT INTO audit_log(actor, action, entity_type, entity_id, details)
            VALUES(?, 'permissions.role.update', 'role', ?, ?)
            """,
            (actor, role, ",".join(sorted(requested))),
        )


def set_user_permission(user_id, permission_code, allowed, actor=""):
    if permission_code not in ALL_PERMISSION_CODES:
        raise ValueError("Permission invalide.")
    with db() as con:
        if not user_by_id(con, user_id):
            raise ValueError("Utilisateur introuvable.")
        con.execute(
            """
            INSERT INTO user_permissions(
                user_id, permission_code, allowed, updated_by, updated_at
            )
            VALUES(?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, permission_code) DO UPDATE SET
                allowed=excluded.allowed,
                updated_by=excluded.updated_by,
                updated_at=CURRENT_TIMESTAMP
            """,
            (user_id, permission_code, 1 if allowed else 0, actor),
        )
        con.execute(
            """
            INSERT INTO audit_log(actor, action, entity_type, entity_id, details)
            VALUES(?, 'permissions.user.update', 'user', ?, ?)
            """,
            (actor, str(user_id), f"{permission_code}={1 if allowed else 0}"),
        )


def user_can_write(user):
    return bool(user and user["role"] in {"super_admin", "admin", "editor"})


def user_is_admin(user):
    return bool(user and user["role"] in {"super_admin", "admin"})


def user_is_super_admin(user):
    return bool(user and user["role"] == "super_admin")
