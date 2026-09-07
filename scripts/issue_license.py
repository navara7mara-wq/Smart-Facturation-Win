import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.license_issuer import issue_license, load_private_key, parse_request_bytes


DEFAULT_KEY = ROOT / "config" / "license_private_key.pem"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--key", default=str(DEFAULT_KEY))
    parser.add_argument("--customer", required=True)
    parser.add_argument("--edition", default="standard")
    term = parser.add_mutually_exclusive_group(required=True)
    term.add_argument("--expires-at")
    term.add_argument("--perpetual", action="store_true")
    parser.add_argument("--max-users", type=int, default=5)
    parser.add_argument("--max-invoices", type=int, default=1000000)
    parser.add_argument("--unlimited-invoices", action="store_true")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    password = os.environ.get("PHOENIX_LICENSE_KEY_PASSWORD")
    private_key = load_private_key(Path(args.key).read_bytes(), password)
    request_payload = parse_request_bytes(Path(args.request).read_bytes())
    document = issue_license(
        private_key,
        request_payload,
        args.customer,
        args.edition,
        None if args.perpetual else args.expires_at,
        args.max_users,
        None if args.unlimited_invoices else args.max_invoices,
    )
    Path(args.output).write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
