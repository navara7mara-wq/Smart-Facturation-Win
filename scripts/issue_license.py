import argparse
import base64
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_KEY = ROOT / "config" / "license_private_key.pem"
PUBLIC_KEY = ROOT / "config" / "license_public_key.pem"


def canonical(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def ensure_keys():
    PRIVATE_KEY.parent.mkdir(parents=True, exist_ok=True)
    if PRIVATE_KEY.exists():
        private_key = serialization.load_pem_private_key(PRIVATE_KEY.read_bytes(), password=None)
    else:
        private_key = Ed25519PrivateKey.generate()
        PRIVATE_KEY.write_bytes(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ))
    public_key = private_key.public_key()
    PUBLIC_KEY.write_bytes(public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ))
    return private_key


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--customer", required=True)
    parser.add_argument("--edition", default="standard")
    parser.add_argument("--expires-at", required=True)
    parser.add_argument("--max-users", type=int, default=5)
    parser.add_argument("--max-invoices", type=int, default=1000000)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    payload = {
        "customer": args.customer,
        "edition": args.edition,
        "expires_at": args.expires_at,
        "max_users": args.max_users,
        "max_invoices": args.max_invoices,
    }
    private_key = ensure_keys()
    signature = base64.b64encode(private_key.sign(canonical(payload))).decode("ascii")
    document = {"payload": payload, "signature": signature}
    Path(args.output).write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
