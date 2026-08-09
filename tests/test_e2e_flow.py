import re
import threading
from http.cookiejar import CookieJar
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

import db as db_module
import services.backups as backups
from app import App
from http.server import ThreadingHTTPServer


def _csrf(html):
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, html[:500]
    return match.group(1)


def _request(opener, url, data=None, headers=None):
    payload = None
    if isinstance(data, dict):
        payload = urlencode(data).encode("utf-8")
    elif data is not None:
        payload = data
    request = Request(url, data=payload, headers=headers or {})
    try:
        return opener.open(request, timeout=10)
    except HTTPError as exc:
        return exc


def _multipart(fields, files):
    boundary = "----phoenix-e2e-boundary"
    chunks = []
    for name, value in fields.items():
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            str(value).encode(),
            b"\r\n",
        ])
    for name, file_info in files.items():
        filename, content = file_info
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode(),
            b"Content-Type: application/octet-stream\r\n\r\n",
            content,
            b"\r\n",
        ])
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def test_first_run_csrf_backup_restore_e2e(tmp_path, monkeypatch):
    database_path = tmp_path / "data" / "e2e.sqlite3"
    backup_dir = tmp_path / "backups"
    monkeypatch.setenv("PHOENIX_DB_PATH", str(database_path))
    monkeypatch.setattr(db_module, "DB_PATH", database_path)
    monkeypatch.setattr(backups, "DB_PATH", database_path)
    monkeypatch.setattr(backups, "BACKUP_DIR", backup_dir)

    server = ThreadingHTTPServer(("127.0.0.1", 0), App)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    opener = build_opener(HTTPCookieProcessor(CookieJar()))

    try:
        setup_html = _request(opener, base_url + "/setup").read().decode("utf-8")
        setup_token = _csrf(setup_html)
        response = _request(opener, base_url + "/setup", {
            "csrf_token": setup_token,
            "current_password": "admin123",
            "new_password": "Market123",
            "confirm_password": "Market123",
        })
        assert response.status == 200
        assert "Tableau" in response.read().decode("utf-8")

        users_page = _request(opener, base_url + "/users")
        assert users_page.status == 200
        users_html = users_page.read().decode("utf-8")
        assert "Utilisateurs" in users_html
        user_token = _csrf(users_html)
        create_user = _request(opener, base_url + "/users/create", {
            "csrf_token": user_token,
            "username": "viewer_e2e",
            "password": "Viewer123",
            "role": "viewer",
        })
        assert create_user.status == 200
        assert "viewer_e2e" in create_user.read().decode("utf-8")

        settings_page = _request(opener, base_url + "/settings")
        assert settings_page.status == 200
        settings_html = settings_page.read().decode("utf-8")
        assert "Parametres" in settings_html
        assert "/users" in settings_html
        assert 'data-path="/users"' not in settings_html
        assert 'data-path="/settings"' in settings_html

        about_page = _request(opener, base_url + "/about")
        assert about_page.status == 200
        assert "1.5.0" in about_page.read().decode("utf-8")

        license_page = _request(opener, base_url + "/license")
        assert license_page.status == 200
        assert "demo" in license_page.read().decode("utf-8").lower()

        guide_pdf = _request(opener, base_url + "/docs/Guide_utilisateur_PhoEniX_BPU.pdf")
        assert guide_pdf.status == 200
        assert guide_pdf.read().startswith(b"%PDF")

        with db_module.db() as con:
            con.execute(
                "UPDATE company_settings SET nom='SAPTA', rgc='RGC', nif='NIF', art='ART', adresse='Adresse', numero_compte='RIB' WHERE id=1"
            )
            con.execute("UPDATE contract_settings SET reference_contrat='CTR-001' WHERE id=1")
            direction_id = con.execute(
                "INSERT INTO mobilis_directions(doit_nom, direction_regionale, adresse, rgc, nif) VALUES('Mobilis', 'DR Alger', 'Alger', 'RGC-M', 'NIF-M')"
            ).lastrowid
            con.execute(
                "INSERT INTO bpu_items(article_number, designation, unite, pu_ht, categorie) VALUES(1, 'Cable fibre', 'm', 100, 'fourniture')"
            )
            po_id = con.execute(
                "INSERT INTO purchase_orders(numero_bc, date_bc, mobilis_direction_id, type_bc, objet, montant_ttc) VALUES('BC-E2E', '2026-08-09', ?, 'CONSTRUCTION', 'Projet E2E', 1190)",
                (direction_id,),
            ).lastrowid
            site_id = con.execute(
                "INSERT INTO sites(purchase_order_id, code_site, nom_site, region, typologie_site) VALUES(?, 'SITE-E2E', 'Site E2E', 'Alger', 'A')",
                (po_id,),
            ).lastrowid
            invoice_id = con.execute(
                """
                INSERT INTO invoices(
                    invoice_number, invoice_type, purchase_order_id, site_id, invoice_date,
                    total_ht, retenue_garantie, montant_ht_apres_rg, tva, total_ttc, montant_en_lettres
                ) VALUES('FAC-E2E', 'CONSTRUCTION', ?, ?, '2026-08-09', 1000, 50, 950, 180.5, 1130.5, 'MILLE CENT TRENTE')
                """,
                (po_id, site_id),
            ).lastrowid
            con.execute(
                """
                INSERT INTO invoice_lines(
                    invoice_id, article_number, designation_snapshot, unite_snapshot,
                    pu_ht_snapshot, categorie_snapshot, quantite, montant_ht
                ) VALUES(?, 1, 'Cable fibre', 'm', 100, 'fourniture', 10, 1000)
                """,
                (invoice_id,),
            )

        excel = _request(opener, base_url + f"/invoices/export?id={invoice_id}")
        assert excel.status == 200
        assert excel.headers["Content-Type"].startswith(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        assert excel.read().startswith(b"PK")

        rejected = _request(opener, base_url + "/backup/create", {"csrf_token": "bad"})
        assert rejected.status == 200
        assert "CSRF rejected" not in rejected.read().decode("utf-8")

        backup_html = _request(opener, base_url + "/backup").read().decode("utf-8")
        backup_token = _csrf(backup_html)
        created = _request(opener, base_url + "/backup/create", {"csrf_token": backup_token})
        assert created.status == 200
        created_html = created.read().decode("utf-8")
        backup_name = re.search(r"/backups/([^\"']+\.sqlite3)", created_html).group(1)

        backup_content = _request(opener, base_url + "/backups/" + backup_name).read()
        assert backup_content.startswith(b"SQLite format 3")

        restore_html = _request(opener, base_url + "/backup").read().decode("utf-8")
        restore_token = _csrf(restore_html)
        payload, content_type = _multipart(
            {"csrf_token": restore_token},
            {"backup_file": (backup_name, backup_content)},
        )
        restored = _request(opener, base_url + "/backup/restore", payload, {"Content-Type": content_type})
        assert restored.status == 200
        assert list(backup_dir.glob("*.sqlite3"))

        status_html = _request(opener, base_url + "/status").read().decode("utf-8")
        assert "Database" in status_html
        assert "OK" in status_html
    finally:
        server.shutdown()
        server.server_close()
