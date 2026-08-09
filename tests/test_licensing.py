import base64
import json
import sqlite3

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from services import licensing


def canonical(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def test_verify_signed_license_document(tmp_path, monkeypatch):
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    public_path = tmp_path / "license_public_key.pem"
    public_path.write_bytes(public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ))
    monkeypatch.setattr(licensing, "PUBLIC_KEY_PATH", public_path)

    payload = {
        "customer": "Client",
        "edition": "standard",
        "expires_at": "2999-12-31",
        "max_users": 5,
        "max_invoices": 100,
    }
    document = {
        "payload": payload,
        "signature": base64.b64encode(private_key.sign(canonical(payload))).decode("ascii"),
    }

    assert licensing.verify_license_document(document)["customer"] == "Client"


def test_demo_license_blocks_invoice_limit(monkeypatch):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("CREATE TABLE app_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_by TEXT NOT NULL DEFAULT '')")
    connection.execute("CREATE TABLE invoices(id INTEGER PRIMARY KEY, deleted_at TEXT)")
    connection.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, is_active INTEGER NOT NULL DEFAULT 1)")
    for index in range(licensing.DEMO_MAX_INVOICES):
        connection.execute("INSERT INTO invoices(id) VALUES(?)", (index + 1,))
    connection.execute("INSERT INTO users(id) VALUES(1)")
    monkeypatch.setattr(licensing, "LICENSE_PATH", licensing.Path("missing-license.json"))
    monkeypatch.setattr(licensing, "app_settings", lambda: {})
    monkeypatch.setattr(licensing, "db", lambda: _ConnectionContext(connection))

    try:
        licensing.require_feature("create_invoice")
        assert False, "demo invoice limit should block creation"
    except ValueError as exc:
        assert "Limite de factures" in str(exc)


class _ConnectionContext:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.connection.commit()
        return False
