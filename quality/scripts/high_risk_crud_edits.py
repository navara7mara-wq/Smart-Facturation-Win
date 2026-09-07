"""Ten varied edit/reopen iterations for high-risk referential entities."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from http.cookiejar import CookieJar
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener


PREFIX = "QA_RC1_AUDIT_20260825"


def call(opener, base: str, path: str, data=None) -> bytes:
    payload = urlencode(data).encode() if data else None
    return opener.open(Request(base + path, data=payload), timeout=60).read()


def csrf(content: bytes) -> str:
    return re.search(rb'name="csrf_token" value="([^"]+)"', content).group(1).decode()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8766")
    parser.add_argument("--database", required=True)
    args = parser.parse_args()
    if "tmp\\rc1-audit" not in args.database:
        raise RuntimeError("Non-QA database refused")
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    login = call(opener, args.base_url, "/login")
    call(opener, args.base_url, "/login", {
        "csrf_token": csrf(login), "username": "admin", "password": "QA_RC1_Pass123"
    })
    token = csrf(call(opener, args.base_url, "/clients?new=1"))
    connection = sqlite3.connect(args.database)
    connection.row_factory = sqlite3.Row
    clients = connection.execute(
        "SELECT * FROM clients WHERE sigle LIKE ? ORDER BY id LIMIT 10", (PREFIX + "%",)
    ).fetchall()
    directions = connection.execute(
        """
        SELECT cd.* FROM client_directions cd JOIN clients c ON c.id=cd.client_id
        WHERE c.sigle LIKE ? ORDER BY cd.id LIMIT 10
        """, (PREFIX + "%",)
    ).fetchall()
    purchase_orders = connection.execute(
        "SELECT * FROM purchase_orders WHERE numero_bc LIKE ? ORDER BY id LIMIT 10", (PREFIX + "%",)
    ).fetchall()
    sites = connection.execute(
        "SELECT * FROM sites WHERE code_site LIKE ? ORDER BY id LIMIT 10", (PREFIX + "%",)
    ).fetchall()
    assert all(len(items) == 10 for items in (clients, directions, purchase_orders, sites))
    results = {"clients": [], "directions": [], "purchase_orders": [], "sites": []}

    for index, client in enumerate(clients, start=1):
        address = f"{client['adresse']} | édité {index:02d}"
        content = call(opener, args.base_url, "/clients/save", {
            "csrf_token": token, "id": str(client["id"]), "raison_sociale": client["raison_sociale"],
            "sigle": client["sigle"], "rgc": client["rgc"], "nif": client["nif"], "adresse": address,
        })
        stored = connection.execute("SELECT adresse FROM clients WHERE id=?", (client["id"],)).fetchone()[0]
        results["clients"].append({"id": client["id"], "pass": stored == address and client["sigle"].encode() in content})

    for index, direction in enumerate(directions, start=1):
        name = f"{direction['name']} | édition {index:02d}"
        content = call(opener, args.base_url, "/clients/directions/save", {
            "csrf_token": token, "id": str(direction["id"]), "client_id": str(direction["client_id"]),
            "company_branch_id": str(direction["company_branch_id"]), "sigle": direction["sigle"],
            "name": name, "address": direction["address"],
        })
        stored = connection.execute("SELECT name FROM client_directions WHERE id=?", (direction["id"],)).fetchone()[0]
        results["directions"].append({"id": direction["id"], "pass": stored == name and direction["sigle"].encode() in content})

    for index, po in enumerate(purchase_orders, start=1):
        obj = f"{po['objet']} | édition {index:02d}"
        content = call(opener, args.base_url, "/purchase-orders", {
            "csrf_token": token, "id": str(po["id"]), "numero_bc": po["numero_bc"],
            "date_bc": po["date_bc"], "client_direction_id": str(po["client_direction_id"]),
            "type_bc": po["type_bc"], "objet": obj, "montant_ttc": str(po["montant_ttc"]), "code_sites": "",
        })
        stored = connection.execute("SELECT objet FROM purchase_orders WHERE id=?", (po["id"],)).fetchone()[0]
        reopened = call(opener, args.base_url, f"/purchase-orders?view_id={po['id']}")
        results["purchase_orders"].append({
            "id": po["id"], "pass": stored == obj and po["numero_bc"].encode() in content and po["numero_bc"].encode() in reopened
        })

    for index, site in enumerate(sites, start=1):
        name = f"{site['nom_site']} | édition {index:02d}"
        typology_label = connection.execute(
            "SELECT libelle_complet FROM typologies WHERE id=?", (site["typology_id"],)
        ).fetchone()[0]
        content = call(opener, args.base_url, "/sites", {
            "csrf_token": token, "id": str(site["id"]), "purchase_order_id": str(site["purchase_order_id"]),
            "code_site": site["code_site"], "nom_site": name, "typologie_site": site["typologie_site"],
            "typologie_libelle": typology_label, "subcontractor_id": str(site["subcontractor_id"] or ""),
            "design_office_id": str(site["design_office_id"] or ""),
        })
        stored = connection.execute("SELECT nom_site FROM sites WHERE id=?", (site["id"],)).fetchone()[0]
        results["sites"].append({"id": site["id"], "pass": stored == name and site["code_site"].encode() in content})

    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    connection.close()
    output = {
        "iterations_per_entity": 10, "results": results,
        "passed": sum(1 for group in results.values() for item in group if item["pass"]),
        "failed": sum(1 for group in results.values() for item in group if not item["pass"]),
        "integrity_check": integrity, "foreign_key_violations": len(foreign_keys),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    assert integrity == "ok" and not foreign_keys


if __name__ == "__main__":
    main()
