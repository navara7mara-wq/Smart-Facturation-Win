"""Exercise backup/download/restore against the isolated running RC-1 server."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from http.cookiejar import CookieJar
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener


def call(opener, url: str, data=None, headers=None) -> tuple[int, bytes]:
    payload = urlencode(data).encode() if isinstance(data, dict) else data
    response = opener.open(Request(url, data=payload, headers=headers or {}), timeout=120)
    return response.status, response.read()


def csrf(content: bytes) -> str:
    match = re.search(rb'name="csrf_token" value="([^"]+)"', content)
    assert match
    return match.group(1).decode()


def multipart(token: str, filename: str, content: bytes) -> tuple[bytes, str]:
    boundary = "----phoenix-rc1-audit-boundary"
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"csrf_token\"\r\n\r\n{token}\r\n".encode()
        + f"--{boundary}\r\nContent-Disposition: form-data; name=\"backup_file\"; filename=\"{filename}\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode()
        + content
        + f"\r\n--{boundary}--\r\n".encode()
    )
    return body, f"multipart/form-data; boundary={boundary}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8766")
    parser.add_argument("--database", required=True)
    args = parser.parse_args()
    if "tmp\\rc1-audit" not in args.database:
        raise RuntimeError("Non-QA database refused")

    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    _, login = call(opener, args.base_url + "/login")
    _, dashboard = call(opener, args.base_url + "/login", {
        "csrf_token": csrf(login), "username": "admin", "password": "QA_RC1_Pass123"
    })
    assert b"Tableau de bord" in dashboard

    _, backup_page = call(opener, args.base_url + "/backup")
    _, created_page = call(opener, args.base_url + "/backup/create", {"csrf_token": csrf(backup_page)})
    match = re.search(rb'/backups/([^"\']+\.sqlite3)', created_page)
    assert match, created_page[:500]
    backup_name = match.group(1).decode()
    _, backup_content = call(opener, args.base_url + "/backups/" + backup_name)
    assert backup_content.startswith(b"SQLite format 3")

    with sqlite3.connect(args.database) as connection:
        row = connection.execute(
            "SELECT id, adresse FROM clients WHERE raison_sociale LIKE 'QA_RC1_AUDIT_20260825%' ORDER BY id LIMIT 1"
        ).fetchone()
        assert row
        client_id, original_address = row
        connection.execute("UPDATE clients SET adresse='QA_RC1_RESTORE_MUTATION' WHERE id=?", (client_id,))
        connection.commit()
        assert connection.execute("SELECT adresse FROM clients WHERE id=?", (client_id,)).fetchone()[0] == "QA_RC1_RESTORE_MUTATION"

    _, restore_page = call(opener, args.base_url + "/backup")
    payload, content_type = multipart(csrf(restore_page), backup_name, backup_content)
    status, restored_page = call(
        opener,
        args.base_url + "/backup/restore",
        payload,
        {"Content-Type": content_type},
    )
    with sqlite3.connect(args.database) as connection:
        restored_address = connection.execute("SELECT adresse FROM clients WHERE id=?", (client_id,)).fetchone()[0]
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    output = {
        "status": status,
        "backup_name": backup_name,
        "backup_bytes": len(backup_content),
        "restore_message_present": b"Base restauree" in restored_page,
        "original_address": original_address,
        "restored_address": restored_address,
        "restored_value_matches": restored_address == original_address,
        "integrity_check": integrity,
        "foreign_key_violations": len(foreign_keys),
    }
    assert output["restored_value_matches"] and integrity == "ok" and not foreign_keys
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
