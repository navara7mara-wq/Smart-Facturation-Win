import json

import pytest

from services import licensing
from services.license_issuer import (
    create_private_key,
    issue_license,
    load_private_key,
    parse_request_bytes,
    save_encrypted_private_key,
    verify_issued_document,
)
from services.machine_identity import activation_request, verify_activation_request


def test_encrypted_vault_and_machine_bound_license(tmp_path):
    private_key = create_private_key()
    vault = tmp_path / "publisher-key.pem"
    password = "Correct-Horse-Battery-Staple"
    save_encrypted_private_key(private_key, vault, password)

    assert b"ENCRYPTED PRIVATE KEY" in vault.read_bytes()
    assert b"PRIVATE KEY-----" not in vault.read_bytes().replace(b"ENCRYPTED PRIVATE KEY-----", b"")
    unlocked = load_private_key(vault.read_bytes(), password)

    request_document = activation_request("2.2.0")
    request_payload = parse_request_bytes(json.dumps(request_document).encode("utf-8"))
    document = issue_license(
        unlocked,
        request_payload,
        "ATM Mobilis",
        "professional",
        "2999-12-31",
        25,
        500000,
        "CMD-2026-001",
    )

    assert verify_issued_document(document, unlocked)
    assert document["payload"]["machine_id"] == request_payload["machine_id"]
    assert document["payload"]["issuer_reference"] == "CMD-2026-001"


def test_tampering_invalidates_request_and_license():
    request_document = activation_request("2.2.0")
    request_document["request"]["machine_id"] = "PHX-00000000-00000000-00000000-00000000"
    assert verify_activation_request(request_document) is None

    private_key = create_private_key()
    request_payload = verify_activation_request(activation_request("2.2.0"))
    document = issue_license(private_key, request_payload, "Client", "standard", "2999-12-31", 5, 100)
    document["payload"]["max_users"] = 999
    assert not verify_issued_document(document, private_key)


def test_application_rejects_license_for_another_machine(tmp_path, monkeypatch):
    private_key = create_private_key()
    public_path = tmp_path / "license_public_key.pem"
    from cryptography.hazmat.primitives import serialization

    public_path.write_bytes(private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ))
    monkeypatch.setattr(licensing, "PUBLIC_KEY_PATH", public_path)

    request_payload = verify_activation_request(activation_request("2.2.0"))
    document = issue_license(private_key, request_payload, "Client", "enterprise", "2999-12-31", 50, 1000)
    monkeypatch.setattr(licensing, "machine_id", lambda: "PHX-FFFFFFFF-FFFFFFFF-FFFFFFFF-FFFFFFFF")
    assert licensing.verify_license_document(document) is None


def test_wrong_vault_password_is_rejected(tmp_path):
    vault = tmp_path / "publisher-key.pem"
    save_encrypted_private_key(create_private_key(), vault, "A-valid-master-password")
    with pytest.raises((TypeError, ValueError)):
        load_private_key(vault.read_bytes(), "wrong-password")


def test_perpetual_unlimited_invoice_license(monkeypatch, tmp_path):
    private_key = create_private_key()
    request_payload = verify_activation_request(activation_request("2.3.1"))
    document = issue_license(
        private_key,
        request_payload,
        "Lifetime Client",
        "enterprise",
        None,
        25,
        None,
    )

    assert document["payload"]["license_term"] == "perpetual"
    assert document["payload"]["expires_at"] is None
    assert document["payload"]["max_invoices"] is None
    assert verify_issued_document(document, private_key)

    from cryptography.hazmat.primitives import serialization

    public_path = tmp_path / "license_public_key.pem"
    public_path.write_bytes(private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ))
    monkeypatch.setattr(licensing, "PUBLIC_KEY_PATH", public_path)
    monkeypatch.setattr(licensing, "machine_id", lambda: request_payload["machine_id"])
    assert licensing.verify_license_document(document)["license_term"] == "perpetual"


def test_unlimited_invoice_license_never_blocks_creation(monkeypatch):
    monkeypatch.setattr(licensing, "license_status", lambda: {
        "is_valid": True,
        "invoice_count": 99_999_999,
        "max_invoices": None,
        "user_count": 1,
        "max_users": 5,
    })
    assert licensing.require_feature("create_invoice")["max_invoices"] is None
