import base64
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from services.machine_identity import LICENSE_SCHEMA, PRODUCT_ID, canonical_json, verify_activation_request


def load_private_key(content, password=None):
    password_bytes = password.encode("utf-8") if password else None
    key = serialization.load_pem_private_key(content, password=password_bytes)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("La clé doit être une clé privée Ed25519.")
    return key


def save_encrypted_private_key(private_key, path, password):
    if len(password) < 12:
        raise ValueError("Le mot de passe maître doit contenir au moins 12 caractères.")
    content = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.BestAvailableEncryption(password.encode("utf-8")),
    )
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return target


def public_key_pem(private_key):
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def public_key_fingerprint(private_key):
    digest = hashlib.sha256(public_key_pem(private_key)).hexdigest().upper()
    return ":".join(digest[index:index + 4] for index in range(0, 32, 4))


def create_private_key():
    return Ed25519PrivateKey.generate()


def parse_request_bytes(content):
    try:
        document = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Le fichier de demande d’activation est invalide.") from exc
    payload = verify_activation_request(document)
    if not payload:
        raise ValueError("La demande d’activation est altérée ou incompatible.")
    return payload


def issue_license(
    private_key,
    request_payload,
    customer,
    edition,
    expires_at,
    max_users,
    max_invoices,
    reference="",
):
    customer = str(customer or "").strip()
    edition = str(edition or "").strip().lower()
    machine = str(request_payload.get("machine_id") or "").strip()
    if not customer:
        raise ValueError("Le nom du client est obligatoire.")
    if edition not in {"standard", "professional", "enterprise"}:
        raise ValueError("L’édition sélectionnée est invalide.")
    perpetual = expires_at in {None, "", "perpetual"}
    if perpetual:
        expiry = None
    else:
        try:
            expiry = date.fromisoformat(str(expires_at))
        except ValueError as exc:
            raise ValueError("La date d’expiration doit respecter le format AAAA-MM-JJ.") from exc
        if expiry < date.today():
            raise ValueError("La date d’expiration ne peut pas être antérieure à aujourd’hui.")
    unlimited_invoices = max_invoices in {None, "", "unlimited"}
    if int(max_users) < 1:
        raise ValueError("La limite utilisateurs doit être positive.")
    if not unlimited_invoices and int(max_invoices) < 1:
        raise ValueError("La limite factures doit être positive.")
    if not machine.startswith("PHX-"):
        raise ValueError("La demande ne contient pas une empreinte appareil valide.")

    payload = {
        "schema": LICENSE_SCHEMA,
        "product": PRODUCT_ID,
        "license_id": str(uuid4()),
        "customer": customer,
        "edition": edition,
        "license_term": "perpetual" if perpetual else "subscription",
        "expires_at": expiry.isoformat() if expiry else None,
        "max_users": int(max_users),
        "max_invoices": None if unlimited_invoices else int(max_invoices),
        "machine_id": machine,
        "device_name": str(request_payload.get("device_name") or "Windows PC"),
        "issued_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "issuer_reference": str(reference or "").strip(),
    }
    signature = base64.b64encode(private_key.sign(canonical_json(payload))).decode("ascii")
    return {"payload": payload, "signature": signature}


def verify_issued_document(document, private_key):
    if not isinstance(document, dict):
        return False
    payload = document.get("payload")
    signature = document.get("signature")
    if not isinstance(payload, dict) or not isinstance(signature, str):
        return False
    try:
        private_key.public_key().verify(base64.b64decode(signature), canonical_json(payload))
    except (InvalidSignature, ValueError, TypeError):
        return False
    return True
