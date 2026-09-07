"""Verify ten invoice searches on the canonical billing-table surface."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from http.cookiejar import CookieJar
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener


def fetch(opener, url: str, data=None) -> bytes:
    payload = urlencode(data).encode() if data else None
    return opener.open(Request(url, data=payload), timeout=60).read()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8766")
    parser.add_argument("--database", required=True)
    args = parser.parse_args()
    if "tmp\\rc1-audit" not in args.database:
        raise RuntimeError("Non-QA database refused")
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    login = fetch(opener, args.base_url + "/login")
    token = re.search(rb'name="csrf_token" value="([^"]+)"', login).group(1).decode()
    fetch(opener, args.base_url + "/login", {
        "csrf_token": token, "username": "admin", "password": "QA_RC1_Pass123"
    })
    with sqlite3.connect(args.database) as connection:
        numbers = [row[0] for row in connection.execute(
            """
            SELECT invoice_number FROM invoices
            WHERE invoice_number LIKE 'QA_RC1_AUDIT_20260825%' ORDER BY id LIMIT 10
            """
        )]
    results = []
    for number in numbers:
        content = fetch(opener, args.base_url + "/table-facturation-new?" + urlencode({"q": number}))
        results.append({"query": number, "found": number.encode() in content})
    assert len(results) == 10 and all(item["found"] for item in results)
    print(json.dumps({"surface": "/table-facturation-new", "results": results}, indent=2))


if __name__ == "__main__":
    main()
