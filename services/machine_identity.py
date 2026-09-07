import hashlib
import hmac
import json
import os
import platform
import socket
import uuid
from datetime import datetime, timezone


PRODUCT_ID = "com.sapta.phoenix-bpu"
REQUEST_SCHEMA = 1
LICENSE_SCHEMA = 2


def _windows_machine_guid():
    if os.name != "nt":
        return ""
    try:
        import winreg

        access = winreg.KEY_READ
        if hasattr(winreg, "KEY_WOW64_64KEY"):
            access |= winreg.KEY_WOW64_64KEY
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
            0,
            access,
        ) as key:
            return str(winreg.QueryValueEx(key, "MachineGuid")[0]).strip()
    except (OSError, ValueError):
        return ""


def _machine_seed():
    stable_id = _windows_machine_guid()
    if not stable_id:
        stable_id = f"{platform.system()}|{platform.machine()}|{uuid.getnode():012x}"
    return f"{PRODUCT_ID}|{stable_id}".encode("utf-8")


def machine_id():
    digest = hashlib.sha256(_machine_seed()).hexdigest().upper()
    return f"PHX-{digest[:8]}-{digest[8:16]}-{digest[16:24]}-{digest[24:32]}"


def device_name():
    return socket.gethostname().strip() or "Windows PC"


def canonical_json(payload):
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def activation_request(product_version):
    payload = {
        "schema": REQUEST_SCHEMA,
        "product": PRODUCT_ID,
        "product_version": str(product_version),
        "machine_id": machine_id(),
        "device_name": device_name(),
        "platform": "Windows" if os.name == "nt" else platform.system(),
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    return {
        "request": payload,
        "checksum": hashlib.sha256(canonical_json(payload)).hexdigest(),
    }


def verify_activation_request(document):
    if not isinstance(document, dict):
        return None
    payload = document.get("request")
    checksum = str(document.get("checksum") or "")
    if not isinstance(payload, dict):
        return None
    if payload.get("schema") != REQUEST_SCHEMA or payload.get("product") != PRODUCT_ID:
        return None
    expected = hashlib.sha256(canonical_json(payload)).hexdigest()
    if not hmac.compare_digest(checksum, expected):
        return None
    requested_machine = str(payload.get("machine_id") or "")
    if not requested_machine.startswith("PHX-") or len(requested_machine) != 39:
        return None
    return payload
