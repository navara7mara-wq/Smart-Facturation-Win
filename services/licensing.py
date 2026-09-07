import base64
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from db import app_settings, current_actor, db
from services.machine_identity import LICENSE_SCHEMA, PRODUCT_ID, machine_id


ROOT_DIR = Path(__file__).resolve().parents[1]
LICENSE_PATH = Path(os.environ.get("PHOENIX_LICENSE_PATH", ROOT_DIR / "data" / "license.json"))
PUBLIC_KEY_PATH = ROOT_DIR / "config" / "license_public_key.pem"
DEMO_DAYS = 30
DEMO_MAX_INVOICES = 20
DEMO_MAX_USERS = 2


def _utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _canonical(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _load_public_key():
    if not PUBLIC_KEY_PATH.exists():
        return None
    return serialization.load_pem_public_key(PUBLIC_KEY_PATH.read_bytes())


def verify_license_document(document):
    payload = document.get("payload") or {}
    signature = document.get("signature") or ""
    public_key = _load_public_key()
    if not isinstance(public_key, Ed25519PublicKey):
        return None
    try:
        public_key.verify(base64.b64decode(signature), _canonical(payload))
    except (InvalidSignature, ValueError, TypeError):
        return None
    if payload.get("schema") != LICENSE_SCHEMA or payload.get("product") != PRODUCT_ID:
        return None
    license_term = str(payload.get("license_term") or "subscription")
    expires_at = payload.get("expires_at")
    if license_term == "perpetual":
        if expires_at not in {None, ""}:
            return None
    else:
        expires_at = str(expires_at or "")
        if not expires_at or datetime.fromisoformat(expires_at[:10]) < _utc_now():
            return None
    licensed_machine = str(payload.get("machine_id") or "").strip()
    if not licensed_machine or licensed_machine != machine_id():
        return None
    try:
        if int(payload.get("max_users", 0)) < 1:
            return None
        max_invoices = payload.get("max_invoices")
        if max_invoices is not None and int(max_invoices) < 1:
            return None
    except (TypeError, ValueError):
        return None
    return payload


def installed_license():
    if not LICENSE_PATH.exists():
        return None
    try:
        document = json.loads(LICENSE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return verify_license_document(document)


def _ensure_demo_started():
    settings = app_settings()
    started = settings.get("license_demo_started_at")
    if started:
        return started
    started = _utc_now().date().isoformat()
    with db() as con:
        con.execute(
            "INSERT OR REPLACE INTO app_settings(key, value, updated_by) VALUES('license_demo_started_at', ?, ?)",
            (started, current_actor()),
        )
    return started


def license_status():
    payload = installed_license()
    with db() as con:
        invoice_count = con.execute("SELECT COUNT(*) FROM invoices WHERE deleted_at IS NULL").fetchone()[0]
        user_count = con.execute("SELECT COUNT(*) FROM users WHERE is_active=1").fetchone()[0]
    if payload:
        return {
            "edition": payload.get("edition", "standard"),
            "customer": payload.get("customer", ""),
            "expires_at": payload.get("expires_at"),
            "is_perpetual": payload.get("license_term") == "perpetual",
            "max_users": int(payload.get("max_users", 5)),
            "max_invoices": None if payload.get("max_invoices") is None else int(payload.get("max_invoices")),
            "unlimited_invoices": payload.get("max_invoices") is None,
            "is_demo": False,
            "is_valid": True,
            "invoice_count": invoice_count,
            "user_count": user_count,
            "machine_id": machine_id(),
            "license_id": payload.get("license_id", ""),
        }
    started = _ensure_demo_started()
    demo_end = datetime.fromisoformat(started) + timedelta(days=DEMO_DAYS)
    days_left = max(0, (demo_end.date() - _utc_now().date()).days)
    return {
        "edition": "demo",
        "customer": "DEMO",
        "expires_at": demo_end.date().isoformat(),
        "is_perpetual": False,
        "max_users": DEMO_MAX_USERS,
        "max_invoices": DEMO_MAX_INVOICES,
        "unlimited_invoices": False,
        "is_demo": True,
        "is_valid": days_left > 0,
        "days_left": days_left,
        "invoice_count": invoice_count,
        "user_count": user_count,
        "machine_id": machine_id(),
        "license_id": "",
    }


def install_license(content):
    document = json.loads(content.decode("utf-8"))
    payload = verify_license_document(document)
    if not payload:
        raise ValueError("Licence invalide.")
    LICENSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    LICENSE_PATH.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def require_feature(feature):
    status = license_status()
    if not status["is_valid"]:
        raise ValueError("Licence expiree. Activez le produit.")
    if feature == "create_invoice" and status["max_invoices"] is not None and status["invoice_count"] >= status["max_invoices"]:
        raise ValueError("Limite de factures atteinte pour cette licence.")
    if feature == "create_user" and status["user_count"] >= status["max_users"]:
        raise ValueError("Limite utilisateurs atteinte pour cette licence.")
    return status
