from html import escape
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode
from uuid import uuid4
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import sqlite3
import re
import zipfile
import xml.etree.ElementTree as ET
from io import BytesIO
import subprocess
import sys
import tempfile
import json
import os
import threading
try:
    import winreg
except ImportError:
    winreg = None
from datetime import datetime, timedelta

from services.billing import (
    amount_to_french,
    amount_words_placeholder,
    parse_invoice_lines,
    totals_from_lines,
)


ROOT_DIR = Path(__file__).resolve().parent
DB_PATH = ROOT_DIR / "data" / "pos_ai.sqlite3"
SCHEMA_PATH = ROOT_DIR / "database" / "schema.sql"
UPLOAD_DIR = ROOT_DIR / "uploads"
EXPORT_DIR = ROOT_DIR / "exports"
DOWNLOAD_DIR = Path.home() / "Downloads"
BUNDLED_PYTHON = Path(r"C:\Users\Administrateur\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe")
BUNDLED_NODE = Path(r"C:\Users\Administrateur\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe")
NODE_MODULES = Path(r"C:\Users\Administrateur\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules")
MOBILIS_DOIT = "Algérie Télécom Mobile / Mobilis"
PDF_VIEWER_CANDIDATES = [
    Path(r"C:\Program Files\Adobe\Acrobat DC\Acrobat\Acrobat.exe"),
    Path(r"C:\Program Files (x86)\Adobe\Acrobat DC\Acrobat\Acrobat.exe"),
    Path(r"C:\Program Files\Adobe\Acrobat Reader DC\Reader\AcroRd32.exe"),
    Path(r"C:\Program Files (x86)\Adobe\Acrobat Reader DC\Reader\AcroRd32.exe"),
]


def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON;")
    if not connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='company_settings'").fetchone():
        connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    ensure_schema(connection)
    return connection


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute("""
        CREATE TABLE IF NOT EXISTS template_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_by TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS mobilis_client_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            doit TEXT NOT NULL DEFAULT '',
            nif TEXT NOT NULL DEFAULT '',
            nis TEXT NOT NULL DEFAULT '',
            is_configured INTEGER NOT NULL DEFAULT 0 CHECK (is_configured IN (0, 1)),
            created_by TEXT NOT NULL DEFAULT '',
            updated_by TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    add_audit_columns(connection)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(purchase_orders)")}
    additions = {
        "objet": "ALTER TABLE purchase_orders ADD COLUMN objet TEXT NOT NULL DEFAULT ''",
        "montant_ttc": "ALTER TABLE purchase_orders ADD COLUMN montant_ttc NUMERIC NOT NULL DEFAULT 0 CHECK (montant_ttc >= 0)",
        "attachment_path": "ALTER TABLE purchase_orders ADD COLUMN attachment_path TEXT",
    }
    for column, statement in additions.items():
        if column not in columns:
            connection.execute(statement)
    site_columns = {row[1] for row in connection.execute("PRAGMA table_info(sites)")}
    if "bet" not in site_columns:
        connection.execute("ALTER TABLE sites ADD COLUMN bet TEXT NOT NULL DEFAULT ''")
    invoice_columns = {row[1] for row in connection.execute("PRAGMA table_info(invoices)")}
    invoice_additions = {
        "remarque": "ALTER TABLE invoices ADD COLUMN remarque TEXT NOT NULL DEFAULT ''",
        "depos": "ALTER TABLE invoices ADD COLUMN depos INTEGER NOT NULL DEFAULT 0 CHECK (depos IN (0, 1))",
    }
    for column, statement in invoice_additions.items():
        if column not in invoice_columns:
            connection.execute(statement)
    connection.execute("DROP INDEX IF EXISTS idx_invoices_site_type_regular")
    connection.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_invoices_site_type_regular
        ON invoices (site_id, invoice_type)
        WHERE invoice_type IN ('ACQUISITION', 'CONSTRUCTION', 'CONST_ACQUIS')
        AND deleted_at IS NULL
    """)
    ensure_template_defaults(connection)
    connection.commit()


def mobilis_client_settings(connection=None):
    close_connection = connection is None
    con = connection or db()
    row = con.execute("SELECT * FROM mobilis_client_settings WHERE id=1").fetchone()
    if not row:
        con.execute(
            "INSERT INTO mobilis_client_settings(id, created_by, updated_by) VALUES (1, ?, ?)",
            (current_actor(), current_actor()),
        )
        con.commit()
        row = con.execute("SELECT * FROM mobilis_client_settings WHERE id=1").fetchone()
    if close_connection:
        con.close()
    return row


DEFAULT_TEMPLATE_SETTINGS = {
    "logo_width_px": "340",
    "logo_height_px": "170",
    "company_logo_anchor": "E1",
    "company_logo_offset_px": "118",
    "client_logo_anchor": "",
    "client_logo_offset_px": "0",
    "line_height_normal": "30",
    "line_height_wrapped": "50",
    "designation_wrap_chars": "52",
    "amount_words_font_size": "24",
    "col_a_width": "9",
    "col_b_width": "103",
    "col_c_width": "13.5",
    "col_d_width": "19",
    "col_e_width": "31",
    "col_f_width": "33",
    "hide_empty_sections": "1",
    "visual_blocks_json": "",
    "visual_blocks_facture_json": "",
    "visual_blocks_devis_quantitatif_json": "",
    "visual_blocks_devis_estimatif_json": "",
}


def ensure_template_defaults(connection):
    for key, value in DEFAULT_TEMPLATE_SETTINGS.items():
        connection.execute(
            "INSERT OR IGNORE INTO template_settings(key, value, updated_by) VALUES (?, ?, ?)",
            (key, value, current_actor()),
        )


def template_settings(connection=None):
    close_connection = connection is None
    con = connection or db()
    ensure_template_defaults(con)
    rows = con.execute("SELECT key, value FROM template_settings").fetchall()
    settings = dict(DEFAULT_TEMPLATE_SETTINGS)
    settings.update({row["key"]: row["value"] for row in rows})
    if close_connection:
        con.commit()
        con.close()
    return settings


def default_visual_blocks(document_type="facture"):
    common_logos = [
        {"id": "client_logo", "title": "Logo Mobilis", "x": 24, "y": 20, "w": 150, "h": 70, "font": 12, "content": "{{Logo.Client}}"},
        {"id": "company_logo", "title": "Logo Entreprise", "x": 610, "y": 20, "w": 150, "h": 70, "font": 12, "content": "{{Logo.Entreprise}}"},
    ]
    if document_type == "devis_quantitatif":
        return common_logos + [
            {"id": "devis_title", "title": "Titre devis", "x": 205, "y": 20, "w": 380, "h": 72, "font": 16, "content": "DEVIS QUANTITATIF {{FACTURE.TYPE}}\nATTACHEMENT"},
            {"id": "site_info", "title": "Site", "x": 28, "y": 110, "w": 500, "h": 105, "font": 13, "content": "Code de site: {{Code_Site}}\nNom de site: {{Nom_Site}}\nRegion: {{Region}}\nTypologie de site: {{Typologie}}"},
            {"id": "articles_table", "title": "Table quantitatif", "x": 28, "y": 250, "w": 740, "h": 230, "font": 11, "content": "N? | Designation | Unite | Quantites"},
        ]
    if document_type == "devis_estimatif":
        return common_logos + [
            {"id": "devis_title", "title": "Titre devis", "x": 205, "y": 20, "w": 380, "h": 72, "font": 16, "content": "DEVIS ESTIMATIF {{FACTURE.TYPE}}"},
            {"id": "site_info", "title": "Site", "x": 28, "y": 110, "w": 500, "h": 105, "font": 13, "content": "Code de site: {{Code_Site}}\nNom de site: {{Nom_Site}}\nRegion: {{Region}}\nTypologie de site: {{Typologie}}"},
            {"id": "articles_table", "title": "Table estimatif", "x": 28, "y": 250, "w": 740, "h": 260, "font": 11, "content": "N? | Designation | Unite | Quantites | PU/HT | Montant/HT"},
            {"id": "totals_table", "title": "Total general", "x": 470, "y": 540, "w": 300, "h": 45, "font": 12, "content": "TOTAL GENERAL"},
            {"id": "amount_words", "title": "Montant en lettres", "x": 28, "y": 630, "w": 680, "h": 65, "font": 24, "content": "{{Montant_En_Lettres}}"},
            {"id": "signature", "title": "Signature", "x": 610, "y": 745, "w": 160, "h": 40, "font": 11, "content": "L'ENTREPRISE/{{Entreprise.Nom}}"},
        ]
    return common_logos + [
        {"id": "enterprise_info", "title": "Entreprise", "x": 28, "y": 120, "w": 350, "h": 90, "font": 13, "content": "RGC: {{Entreprise.RGC}}\nNIF: {{Entreprise.NIF}}\nART: {{Entreprise.ART}}\nADRESSE: {{Entreprise.Adresse}}\nN? COMPTE: {{Entreprise.RIB}}"},
        {"id": "client_info", "title": "Client", "x": 470, "y": 120, "w": 300, "h": 90, "font": 13, "content": "DOIT : {{Client.Nom}}\n{{Client.Direction}}\n{{Client.Adresse}}\nRGC N?: {{Client.RGC}}\nNIF N?: {{Client.NIF}}"},
        {"id": "invoice_title", "title": "Titre facture", "x": 28, "y": 235, "w": 740, "h": 35, "font": 14, "content": "FACTURE N? : {{N_Facture}}"},
        {"id": "site_info", "title": "Site / BC", "x": 28, "y": 292, "w": 500, "h": 105, "font": 13, "content": "Reference Contrat: {{Ref_Contrat}}\nCode de site: {{Code_Site}}\nNom de site: {{Nom_Site}}\nRegion: {{Region}}\nTypologie de site: {{Typologie}}\nBon de commande: {{N_BC}}"},
        {"id": "articles_table", "title": "Table articles", "x": 28, "y": 430, "w": 740, "h": 170, "font": 11, "content": "N? | Designation | Unite | Quantites | PU/HT | Montant/HT"},
        {"id": "totals_table", "title": "Table montants", "x": 470, "y": 620, "w": 300, "h": 115, "font": 12, "content": "TOTAL EN HT\nRETENUE DE GARANTIE 5%\nMONTANT HT APRES RG\nTVA 19%\nTOTAL EN TTC"},
        {"id": "amount_words", "title": "Montant en lettres", "x": 28, "y": 765, "w": 680, "h": 65, "font": 24, "content": "{{Montant_En_Lettres}}"},
        {"id": "signature", "title": "Signature", "x": 610, "y": 875, "w": 160, "h": 40, "font": 11, "content": "L'ENTREPRISE/{{Entreprise.Nom}}"},
    ]

def add_audit_columns(connection):
    audit_tables = ["mobilis_directions", "purchase_orders", "sites", "invoices"]
    audit_columns = {
        "created_by": "TEXT NOT NULL DEFAULT ''",
        "updated_by": "TEXT NOT NULL DEFAULT ''",
        "deleted_at": "TEXT",
        "deleted_by": "TEXT",
    }
    for table in audit_tables:
        existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        for column, definition in audit_columns.items():
            if column not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def current_actor():
    return os.environ.get("USERNAME") or os.environ.get("USER") or "User"


def open_pdf_with_system_viewer(pdf_path):
    adobe = next((path for path in PDF_VIEWER_CANDIDATES if path.exists()), None)
    if not adobe and winreg is not None:
        for key_path in (
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\Acrobat.exe",
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\AcroRd32.exe",
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\Acrobat.exe",
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\AcroRd32.exe",
        ):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
                    candidate = Path(winreg.QueryValue(key, ""))
                    if candidate.exists():
                        adobe = candidate
                        break
            except OSError:
                pass
    if adobe:
        subprocess.Popen(
            [str(adobe), str(pdf_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
        )
        return
    subprocess.Popen(
        ["cmd", "/c", "start", "", str(pdf_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def h(value) -> str:
    return escape("" if value is None else str(value), quote=True)


def money(value) -> str:
    formatted = f"{float(value or 0):,.2f}"
    return formatted.replace(",", "\u00a0").replace(".", ",").replace("\u00a0", " ")


def display_type(value) -> str:
    return "CONST/ACQUIS" if value == "CONST_ACQUIS" else str(value or "")


def date_fr(value) -> str:
    if not value:
        return "—"
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return str(value)


def timestamp_from_db(value) -> float:
    if not value:
        return 0.0
    text = str(value).replace("T", " ")
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return 0.0


def pdf_source_timestamp(document, invoice_id):
    with db() as con:
        row = con.execute("""
            SELECT
                i.updated_at AS invoice_updated,
                po.updated_at AS po_updated,
                md.updated_at AS direction_updated,
                COALESCE(s.updated_at, '') AS site_updated,
                c.updated_at AS company_updated,
                cs.updated_at AS contract_updated,
                COALESCE(mcs.updated_at, '') AS mobilis_client_updated,
                COALESCE(MAX(il.created_at), '') AS lines_updated,
                (SELECT COALESCE(MAX(updated_at), '') FROM template_settings) AS template_updated
            FROM invoices i
            JOIN purchase_orders po ON po.id = i.purchase_order_id
            JOIN mobilis_directions md ON md.id = po.mobilis_direction_id
            LEFT JOIN sites s ON s.id = i.site_id
            CROSS JOIN company_settings c
            CROSS JOIN contract_settings cs
            LEFT JOIN mobilis_client_settings mcs ON mcs.id = 1
            LEFT JOIN invoice_lines il ON il.invoice_id = i.id
            WHERE i.id=?
            GROUP BY i.id
        """, (invoice_id,)).fetchone()
    if not row:
        return 0.0
    times = [timestamp_from_db(row[key]) for key in row.keys()]
    template_path = ROOT_DIR / "templates" / "Facture_Construction.xlsx"
    if template_path.exists():
        times.append(template_path.stat().st_mtime)
    return max(times or [0.0])


def cleanup_old_exports(stem, suffix, keep=3):
    files = sorted(
        EXPORT_DIR.glob(f"{stem}_*{suffix}"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in files[keep:]:
        try:
            path.unlink()
        except OSError:
            pass


def parse_amount(value):
    raw = str(value or "0").strip().replace("\u00a0", "").replace(" ", "")
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".")
    else:
        raw = raw.replace(",", ".")
    return float(raw or 0)


def field(name, label, value="", field_type="text", required=False):
    req = " required" if required else ""
    return f'<label><span>{h(label)}</span><input type="{field_type}" name="{h(name)}" value="{h(value)}"{req}></label>'


def decimal_text_field(name, label, value="", required=False):
    req = " required" if required else ""
    return f'<label><span>{h(label)}</span><input type="text" inputmode="decimal" name="{h(name)}" value="{h(value)}"{req}></label>'


def file_field(name, label, accept):
    return f'<label><span>{h(label)}</span><input type="file" name="{h(name)}" accept="{h(accept)}"></label>'


def textarea_field(name, label, hint=""):
    return f'<label class="wide"><span>{h(label)}</span><textarea name="{h(name)}" rows="5"></textarea><small>{h(hint)}</small></label>'


def select_field(name, label, options, selected=""):
    opts = []
    for value, text in options:
        attr = " selected" if str(value) == str(selected) else ""
        opts.append(f'<option value="{h(value)}"{attr}>{h(text)}</option>')
    return f'<label><span>{h(label)}</span><select name="{h(name)}">{"".join(opts)}</select></label>'


def table(headers, rows):
    head = "".join(f"<th>{h(x)}</th>" for x in headers)
    body = ["<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows]
    if not body:
        body.append(f'<tr><td colspan="{len(headers)}" class="empty">Aucune donnee</td></tr>')
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def action_links(base_path, row_id):
    return (
        f'<span class="action-icons">'
        f'<a class="button-link icon-link" href="{base_path}?edit_id={row_id}" title="Modifier" aria-label="Modifier">✎</a>'
        f'<a class="button-link icon-link danger-link" href="{base_path}/delete?id={row_id}" title="Supprimer" aria-label="Supprimer" onclick="return confirm(\'Supprimer cet element ?\')">×</a>'
        f'</span>'
    )


def layout(title, content, subtitle=""):
    nav = [
        ("/", "Tableau de bord", "i-dashboard"),
        ("/table-facturation-new", "Table Facturation", "i-table"),
        ("/invoices", "Factures", "i-file"),
        ("/purchase-orders", "Bons de commande", "i-clipboard"),
        ("/mobilis", "Mobilis", "i-radio"),
        ("/bpu", "BPU", "i-calculator"),
        ("/company", "Entreprise", "i-building"),
        ("/templates", "Templates", "i-template"),
    ]
    links = "".join(
        f'<a href="{url}" data-path="{url}"><svg aria-hidden="true"><use href="#{icon}"/></svg><span>{label}</span></a>'
        for url, label, icon in nav
    )
    display_title = "Tableau de bord" if title == "Tableau" else title
    page_class = "page-" + re.sub(r"[^a-z0-9]+", "-", display_title.lower()).strip("-")
    mobilis_page = title == "Directions Mobilis"
    drawer_active = mobilis_page and "drawer-open" in content
    body_extra = " drawer-active" if drawer_active else ""
    mobilis_setup_required = mobilis_page and "mobilis-client-setup-required" in content
    title_action = '<a class="mobilis-title-action button-link" href="/mobilis?new=1">＋ Ajouter une direction</a>' if mobilis_page and not mobilis_setup_required else ''
    subtitle_html = f'<p class="sapta-page-subtitle">{h(subtitle)}</p>' if subtitle else ''
    title_markup = (
        f'<div class="sapta-title-copy"><h1>{h(display_title)}</h1>{subtitle_html}</div>'
        if subtitle else f'<h1>{h(display_title)}</h1>'
    )
    user_name = current_actor()
    user_initials = initials(user_name)
    return f"""<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{h(title)} - SAPTA Facturation</title>
  <link rel="stylesheet" href="/static/sapta-new.css">
  <link rel="stylesheet" href="/static/style.css">
</head>
<body class="sapta-app legacy-page {h(page_class)}{body_extra}">
  <svg class="svg-sprite" aria-hidden="true">
    <symbol id="i-dashboard" viewBox="0 0 24 24"><path d="M4 13a8 8 0 1 1 16 0"/><path d="M12 13l3-4"/><path d="M5 18h14"/><path d="M8 18v-2m8 2v-2"/></symbol>
    <symbol id="i-table" viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18"/></symbol>
    <symbol id="i-file" viewBox="0 0 24 24"><path d="M6 2h8l4 4v16H6z"/><path d="M14 2v5h5M9 12h6M9 16h6"/></symbol>
    <symbol id="i-clipboard" viewBox="0 0 24 24"><rect x="5" y="4" width="14" height="18" rx="2"/><path d="M9 4V2h6v2M9 9h6M9 13h6M9 17h4"/></symbol>
    <symbol id="i-radio" viewBox="0 0 24 24"><path d="M12 12v9M8 21h8M9 12l3-9 3 9"/><path d="M6.5 5.5a8 8 0 0 0 0 11M17.5 5.5a8 8 0 0 1 0 11M4 3a11.5 11.5 0 0 0 0 18M20 3a11.5 11.5 0 0 1 0 18"/></symbol>
    <symbol id="i-calculator" viewBox="0 0 24 24"><rect x="4" y="2" width="16" height="20" rx="2"/><path d="M8 6h8v4H8zM8 14h.01M12 14h.01M16 14h.01M8 18h.01M12 18h.01M16 18h.01"/></symbol>
    <symbol id="i-building" viewBox="0 0 24 24"><path d="M4 22V5l8-3v20M12 8h8v14M8 7h.01M8 11h.01M8 15h.01M8 19h.01M16 12h.01M16 16h.01M16 20h.01M2 22h20"/></symbol>
    <symbol id="i-template" viewBox="0 0 24 24"><path d="M4 3h16v18H4zM4 8h16M9 8v13"/><path d="M12 12h5M12 16h5"/></symbol>
    <symbol id="i-chevron-down" viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></symbol>
  </svg>

  <aside class="sapta-sidebar" id="saptaSidebar">
    <a class="sapta-brand" href="/" aria-label="SAPTA Facturation">
      <svg class="sapta-logo" viewBox="0 0 42 44" aria-hidden="true">
        <path d="M21 1 39 11 21 21 3 11z"/><path d="M3 18 10 14l11 6 11-6 7 4-18 10z"/><path d="M3 27 10 23l11 6 11-6 7 4-18 10z"/><path d="M3 36 10 32l11 6 11-6 7 4-18 10z"/>
      </svg>
      <span><strong>SAPTA</strong><small>Facturation</small></span>
    </a>
    <nav class="sapta-nav" aria-label="Navigation principale">{links}</nav>
    <div class="sapta-user">
      <span class="sapta-avatar">{h(user_initials)}</span>
      <span class="sapta-user-copy"><strong>{h(user_name)}</strong><small>Administrateur</small></span>
      <svg class="sapta-user-chevron"><use href="#i-chevron-down"/></svg>
    </div>
  </aside>

  <main class="sapta-main legacy-main">
    <div class="sapta-page legacy-page-content">
      <div class="sapta-title-row">{title_markup}{title_action}</div>
      <div class="legacy-content">{content}</div>
    </div>
  </main>

  <script src="https://unpkg.com/lucide@0.468.0/dist/umd/lucide.min.js"></script>
  <script>
    if (window.lucide) lucide.createIcons();
    const currentPath = window.location.pathname;
    document.querySelectorAll('.sapta-nav a').forEach(link => {{
      const path = link.dataset.path;
      if ((path === '/' && currentPath === '/') || (path !== '/' && currentPath.startsWith(path))) link.classList.add('active');
    }});
    document.addEventListener('click', async (event) => {{
      const link = event.target.closest('a.pdf-download');
      if (!link) return;
      event.preventDefault();
      const originalText = link.textContent;
      link.textContent = '...';
      try {{
        const response = await fetch(link.href, {{ cache: 'no-store' }});
        if (!response.ok) throw new Error(`HTTP ${{response.status}}`);
        const payload = await response.json();
        if (!payload.url) throw new Error('Lien PDF manquant');
        window.location.href = payload.url;
      }} catch (error) {{
        alert('Erreur PDF: ' + error.message);
      }} finally {{
        link.textContent = originalText;
      }}
    }});
  </script>
</body>
</html>"""


def render_html_template(name, **context):
    template_path = ROOT_DIR / "templates" / name
    html = template_path.read_text(encoding="utf-8")
    for key, value in context.items():
        html = html.replace("{{" + key + "}}", str(value))
    return html


def initials(value):
    parts = [part for part in re.split(r"[\s._-]+", str(value or "")) if part]
    if not parts:
        return "U"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def option_html(value, label, selected):
    selected_attr = " selected" if str(value) == str(selected) else ""
    return f'<option value="{h(value)}"{selected_attr}>{h(label)}</option>'


def save_upload(upload, folder, allowed_suffixes):
    if not upload or not upload.get("filename"):
        return ""
    suffix = Path(upload["filename"]).suffix.lower()
    if suffix not in allowed_suffixes:
        raise ValueError(f"Extension non autorisee: {suffix}")
    target_dir = UPLOAD_DIR / folder
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{uuid4().hex}{suffix}"
    target.write_bytes(upload["content"])
    return str(target.relative_to(ROOT_DIR)).replace("\\", "/")


def parse_code_sites(raw):
    codes = []
    seen = set()
    for part in raw.replace(",", "\n").replace(";", "\n").splitlines():
        code = part.strip()
        if code and code not in seen:
            seen.add(code)
            codes.append(code)
    return codes


def import_bpu(path):
    worksheet = read_xlsx_sheet(path, "BPU")
    current_category = None
    rows = []
    columns = {"number": 0, "designation": 1, "unite": 2, "price": 3}
    for row in worksheet:
        normalized = [str(value or "").strip().lower() for value in row]
        if any("désignation" in value or "d é s i g n a t i o n" in value for value in normalized):
            for index, value in enumerate(normalized):
                if "item" in value or value.startswith("n°"):
                    columns["number"] = index
                elif "désignation" in value or "d é s i g n a t i o n" in value:
                    columns["designation"] = index
                elif "unité" in value or "unite" in value:
                    columns["unite"] = index
                elif "prix" in value:
                    columns["price"] = index
            continue

        padded = row + [None] * 10
        number = padded[columns["number"]]
        designation = padded[columns["designation"]]
        unite = padded[columns["unite"]]
        pu_ht = padded[columns["price"]]
        label = str(designation or "").strip().lower()
        if label in {"acquisition", "fourniture", "fournitures", "prestation", "prestations"}:
            current_category = "fourniture" if label.startswith("fourniture") else "prestation" if label.startswith("prestation") else "acquisition"
            continue
        try:
            article_number = int(float(number))
            price = float(pu_ht)
        except (TypeError, ValueError):
            continue
        if designation and unite and current_category:
            rows.append((article_number, str(designation).strip(), str(unite).strip(), price, current_category))
    if not rows:
        raise ValueError("Aucun article BPU trouve dans le fichier Excel.")
    return rows


def read_xlsx_sheet(path, sheet_name):
    ns = {
        "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        "pkg": "http://schemas.openxmlformats.org/package/2006/relationships",
    }
    with zipfile.ZipFile(path) as archive:
        shared_strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall("main:si", ns):
                text = "".join(node.text or "" for node in item.findall(".//main:t", ns))
                shared_strings.append(text)

        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        rel_id = None
        for sheet in workbook.findall("main:sheets/main:sheet", ns):
            if sheet.attrib.get("name") == sheet_name:
                rel_id = sheet.attrib.get(f"{{{ns['rel']}}}id")
                break
        if rel_id is None:
            rel_id = workbook.find("main:sheets/main:sheet", ns).attrib.get(f"{{{ns['rel']}}}id")

        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        target = None
        for rel in rels.findall("pkg:Relationship", ns):
            if rel.attrib.get("Id") == rel_id:
                target = rel.attrib["Target"].lstrip("/")
                break
        sheet_path = "xl/" + target if not target.startswith("xl/") else target
        root = ET.fromstring(archive.read(sheet_path))

        rows = []
        for row in root.findall(".//main:sheetData/main:row", ns):
            values = []
            for cell in row.findall("main:c", ns):
                ref = cell.attrib.get("r", "")
                column_index = column_number(ref) - 1
                while len(values) < column_index:
                    values.append(None)
                values.append(cell_value(cell, shared_strings, ns))
            rows.append(values)
        return rows


def column_number(ref):
    letters = re.match(r"[A-Z]+", ref or "A")
    value = 0
    for char in letters.group(0):
        value = value * 26 + ord(char) - ord("A") + 1
    return value


def cell_value(cell, shared_strings, ns):
    cell_type = cell.attrib.get("t")
    value = cell.find("main:v", ns)
    inline = cell.find("main:is/main:t", ns)
    if cell_type == "inlineStr":
        return inline.text if inline is not None else ""
    if value is None:
        return None
    raw = value.text or ""
    if cell_type == "s":
        return shared_strings[int(raw)]
    try:
        number = float(raw)
        return int(number) if number.is_integer() else number
    except ValueError:
        return raw


def xlsx_cell_ref(row_index, col_index):
    letters = ""
    col = col_index
    while col:
        col, remainder = divmod(col - 1, 26)
        letters = chr(65 + remainder) + letters
    return f"{letters}{row_index}"


def xlsx_escape(value):
    return escape("" if value is None else str(value), quote=False)


def worksheet_xml(rows):
    xml_rows = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for col_index, value in enumerate(row, start=1):
            ref = xlsx_cell_ref(row_index, col_index)
            style = None
            if isinstance(value, tuple):
                value, style = value
            style_attr = f' s="{style}"' if style is not None else ""
            if isinstance(value, (int, float)):
                cells.append(f'<c r="{ref}"{style_attr}><v>{value}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="inlineStr"{style_attr}><is><t>{xlsx_escape(value)}</t></is></c>')
        xml_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <cols>
    <col min="1" max="1" width="10" customWidth="1"/>
    <col min="2" max="2" width="42" customWidth="1"/>
    <col min="3" max="3" width="14" customWidth="1"/>
    <col min="4" max="4" width="12" customWidth="1"/>
    <col min="5" max="5" width="14" customWidth="1"/>
    <col min="6" max="6" width="16" customWidth="1"/>
  </cols>
  <sheetData>{"".join(xml_rows)}</sheetData>
</worksheet>'''


def make_xlsx(sheets):
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
''' + "".join(f'  <Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>\n' for i in range(1, len(sheets) + 1)) + "</Types>")
        archive.writestr("_rels/.rels", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>''')
        archive.writestr("xl/workbook.xml", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>''' + "".join(f'<sheet name="{xlsx_escape(name)}" sheetId="{i}" r:id="rId{i}"/>' for i, (name, _) in enumerate(sheets, start=1)) + '''</sheets>
</workbook>''')
        archive.writestr("xl/_rels/workbook.xml.rels", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
''' + "".join(f'  <Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>\n' for i in range(1, len(sheets) + 1)) + f'  <Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>\n</Relationships>')
        archive.writestr("xl/styles.xml", styles_xml())
        for index, (_, rows) in enumerate(sheets, start=1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", worksheet_xml(rows))
    return output.getvalue()


def styles_xml():
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="3">
    <font><sz val="10"/><name val="Arial"/></font>
    <font><b/><sz val="10"/><name val="Arial"/></font>
    <font><b/><sz val="14"/><name val="Arial"/></font>
  </fonts>
  <fills count="5">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="gray125"/></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFD9D9D9"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FF9AD75D"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFB7DEE8"/><bgColor indexed="64"/></patternFill></fill>
  </fills>
  <borders count="2">
    <border><left/><right/><top/><bottom/><diagonal/></border>
    <border><left style="thin"/><right style="thin"/><top style="thin"/><bottom style="thin"/><diagonal/></border>
  </borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="8">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="2" fillId="2" borderId="0" xfId="0" applyAlignment="1"><alignment horizontal="center"/></xf>
    <xf numFmtId="0" fontId="1" fillId="4" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center"/></xf>
    <xf numFmtId="0" fontId="1" fillId="3" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center"/></xf>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="0" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="right"/></xf>
    <xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment horizontal="right"/></xf>
  </cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>'''


def styled(value, style):
    return (value, style)


def invoice_export_data(invoice_id):
    with db() as con:
        invoice = con.execute("""
            SELECT i.*, po.numero_bc, po.objet,
                   COALESCE(NULLIF(mcs.doit, ''), md.doit_nom) AS doit_nom,
                   md.direction_regionale, md.adresse AS client_adresse,
                   md.rgc AS client_rgc,
                   COALESCE(NULLIF(mcs.nif, ''), md.nif) AS client_nif,
                   md.logo_path AS client_logo_path,
                   s.code_site, s.nom_site, s.region, s.typologie_site
            FROM invoices i
            JOIN purchase_orders po ON po.id = i.purchase_order_id
            JOIN mobilis_directions md ON md.id = po.mobilis_direction_id
            LEFT JOIN mobilis_client_settings mcs ON mcs.id = 1
            LEFT JOIN sites s ON s.id = i.site_id
            WHERE i.id=?
        """, (invoice_id,)).fetchone()
        if not invoice:
            raise ValueError("Facture introuvable.")
        company = con.execute("SELECT * FROM company_settings WHERE id=1").fetchone()
        contract = con.execute("SELECT * FROM contract_settings WHERE id=1").fetchone()
        lines = con.execute("SELECT * FROM invoice_lines WHERE invoice_id=? ORDER BY id", (invoice_id,)).fetchall()
        ndc_sites = con.execute("""
            SELECT s.code_site, s.nom_site
            FROM invoice_sites invs
            JOIN sites s ON s.id = invs.site_id
            WHERE invs.invoice_id=?
            ORDER BY s.code_site
        """, (invoice_id,)).fetchall()
    return invoice, company, contract, lines, ndc_sites


def invoice_xlsx(invoice_id):
    invoice, company, contract, lines, ndc_sites = invoice_export_data(invoice_id)
    site_label = invoice["code_site"] or ", ".join(site["code_site"] for site in ndc_sites)
    site_name = invoice["nom_site"] or f"{len(ndc_sites)} sites NDC"
    grouped = {
        "ACQUISITION": [line for line in lines if line["categorie_snapshot"] == "acquisition"],
        "FOURNITURES": [line for line in lines if line["categorie_snapshot"] == "fourniture"],
        "PRESTATION": [line for line in lines if line["categorie_snapshot"] == "prestation"],
        "NOTE DE CALCUL": [line for line in lines if line["categorie_snapshot"] == "ndc"],
    }

    header = [
        [styled(company["nom"] or "ENTREPRISE", 2), "", "", "", styled("mobilis", 2), ""],
        [],
        [styled("RGC:", 1), company["rgc"], "", styled("DOIT:", 1), invoice["doit_nom"]],
        [styled("NIF:", 1), company["nif"], "", styled("Direction:", 1), invoice["direction_regionale"]],
        [styled("ART:", 1), company["art"], "", styled("Adresse:", 1), invoice["client_adresse"]],
        [styled("ADRESSE:", 1), company["adresse"], "", styled("RGC N°:", 1), invoice["client_rgc"]],
        [styled("N° COMPTE:", 1), company["numero_compte"], "", styled("NIF N°:", 1), invoice["client_nif"]],
        [],
        ["", styled(f"FACTURE N° : {invoice['invoice_number']}", 2), "", "", "", ""],
        [styled("Référence Contrat:", 1), contract["reference_contrat"]],
        [styled("Code de site:", 1), site_label],
        [styled("Nom de site:", 1), site_name],
        [styled("Région:", 1), invoice["region"] or ""],
        [styled("Typologie de site:", 1), invoice["typologie_site"] or ""],
        [styled("Bon de commande:", 1), invoice["numero_bc"]],
        [styled("Objet:", 1), invoice["objet"]],
        [],
        [styled("N°", 3), styled("Désignation", 3), styled("Unité", 3), styled("Quantités", 3), styled("PU/HT", 3), styled("Montant/HT", 3)],
    ]
    line_rows = section_rows(grouped, with_prices=True)
    totals = [
        [],
        ["", "", "", "", styled("TOTAL EN HT", 6), styled(invoice["total_ht"], 5)],
        ["", "", "", "", styled("RETENUE DE GARANTIE 5%", 6), styled(invoice["retenue_garantie"], 5)],
        ["", "", "", "", styled("MONTANT HT APRES RETENUE", 6), styled(invoice["montant_ht_apres_rg"], 5)],
        ["", "", "", "", styled("TVA 19 %", 6), styled(invoice["tva"], 5)],
        ["", "", "", "", styled("TOTAL EN TTC", 6), styled(invoice["total_ttc"], 5)],
        [],
        [styled("Arrêté la présente Facture à la somme de:", 1), invoice["montant_en_lettres"]],
        [],
        ["", "", "", "", styled(f"L'ENTREPRISE/{company['nom']}", 7), ""],
    ]

    quantitative = [
        [styled("DEVIS QUANTITATIF", 2)],
        [],
        [styled("Code de site:", 1), site_label],
        [styled("Nom de site:", 1), site_name],
        [styled("Région:", 1), invoice["region"] or ""],
        [styled("Typologie de site:", 1), invoice["typologie_site"] or ""],
        [],
        [styled("N°", 3), styled("Désignation", 3), styled("Unité", 3), styled("Quantités", 3)],
    ] + section_rows(grouped, with_prices=False) + [[], [styled("VALIDATION MOBILIS", 1)]]

    estimative = [
        [styled("DEVIS ESTIMATIF", 2)],
        [],
        [styled("Code de site:", 1), site_label],
        [styled("Nom de site:", 1), site_name],
        [styled("Région:", 1), invoice["region"] or ""],
        [styled("Typologie de site:", 1), invoice["typologie_site"] or ""],
        [],
        [styled("N°", 3), styled("Désignation", 3), styled("Unité", 3), styled("Quantités", 3), styled("PU/HT", 3), styled("Montant/HT", 3)],
    ] + section_rows(grouped, with_prices=True) + [
        [],
        ["", "", "", "", styled("TOTAL GENERAL", 6), styled(invoice["total_ht"], 5)],
        [styled("Arrêté la présente Facture à la somme de:", 1), invoice["montant_en_lettres"]],
        [],
        ["", "", "", "", styled(f"L'ENTREPRISE/{company['nom']}", 7), ""],
    ]
    return make_xlsx([
        ("Facture", header + line_rows + totals),
        ("Devis Quantitatif", quantitative),
        ("Devis Estimatif", estimative),
    ]), invoice["invoice_number"]


def section_rows(grouped, with_prices):
    rows = []
    for title, items in grouped.items():
        if not items:
            continue
        rows.append([styled(title, 4), styled("", 4), styled("", 4), styled("", 4), styled("", 4), styled("", 4)] if with_prices else [styled(title, 4), styled("", 4), styled("", 4), styled("", 4)])
        for line in items:
            if with_prices:
                rows.append([
                    styled(line["article_number"], 5),
                    styled(line["designation_snapshot"], 5),
                    styled(line["unite_snapshot"], 5),
                    styled(line["quantite"], 5),
                    styled(line["pu_ht_snapshot"], 5),
                    styled(line["montant_ht"], 5),
                ])
            else:
                rows.append([
                    styled(line["article_number"], 5),
                    styled(line["designation_snapshot"], 5),
                    styled(line["unite_snapshot"], 5),
                    styled(line["quantite"], 5),
                ])
    return rows


class App(BaseHTTPRequestHandler):
    def do_HEAD(self):
        path = self.path.split("?")[0]
        pdf_request = self.parse_pdf_request(path)
        if pdf_request:
            return self.pdf_head(*pdf_request)
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if path.startswith("/uploads/"):
            return self.uploaded_file(path)
        if path.startswith("/exports/"):
            return self.exported_file(path)
        if path.startswith("/static/"):
            return self.static_file(path)
        if path == "/table-facturation-new/export":
            return self.export_table_facturation_new()
        if path == "/invoices/export":
            return self.export_invoice()
        if path == "/invoices/preview/devis-quantitatif":
            return self.preview_devis_quantitatif()
        if path == "/invoices/preview/devis-estimatif":
            return self.preview_devis_estimatif()
        if path == "/invoices/preview/facture":
            return self.preview_facture()
        if path == "/invoices/pdf/devis-quantitatif":
            return self.export_preview_pdf("devis-quantitatif")
        if path == "/invoices/pdf/devis-estimatif":
            return self.export_preview_pdf("devis-estimatif")
        pdf_request = self.parse_pdf_request(path)
        if pdf_request:
            return self.export_preview_pdf(*pdf_request)
        pdf_ready_request = self.parse_pdf_ready_request(path)
        if pdf_ready_request:
            return self.open_preview_pdf(*pdf_ready_request)
        if path == "/bpu/item":
            return self.bpu_item()
        if path == "/mobilis/delete":
            return self.soft_delete("mobilis_directions", "/mobilis")
        if path == "/purchase-orders/delete":
            return self.soft_delete("purchase_orders", "/purchase-orders")
        if path == "/purchase-orders/document/delete":
            return self.delete_purchase_order_document()
        if path == "/purchase-orders/site/delete":
            query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            po_id = query.get("po_id", [""])[0]
            return self.soft_delete("sites", f"/purchase-orders?view_id={quote(po_id)}")
        if path == "/sites/delete":
            return self.soft_delete("sites", "/sites")
        if path == "/invoices/delete":
            return self.soft_delete("invoices", "/invoices")
        if path == "/archives/delete":
            return self.delete_archive()
        if path == "/archives/open":
            return self.open_archive()
        routes = {
            "/": self.dashboard,
            "/table-facturation": self.table_facturation,
            "/table-facturation-new": self.table_facturation_new,
            "/company": self.company,
            "/mobilis": self.mobilis,
            "/bpu": self.bpu,
            "/purchase-orders": self.purchase_orders,
            "/invoices": self.invoices,
            "/templates": self.templates,
            "/static/style.css": self.style,
        }
        if path == "/contract":
            return self.redirect("/company")
        if path == "/sites":
            return self.redirect("/purchase-orders")
        if path == "/archives":
            return self.redirect("/invoices")
        handler = routes.get(path)
        if handler:
            return handler()
        self.respond("Not found", status=404, content_type="text/plain")

    def do_POST(self):
        path = self.path.split("?")[0]
        routes = {
            "/table-facturation": self.save_table_facturation,
            "/table-facturation-new/update": self.update_table_facturation_new,
            "/company": self.save_company,
            "/mobilis": self.save_mobilis,
            "/mobilis/client-settings": self.save_mobilis_client_settings,
            "/contract": self.save_contract,
            "/bpu": self.save_bpu,
            "/purchase-orders": self.save_purchase_order,
            "/purchase-orders/document": self.save_purchase_order_document,
            "/sites": self.save_site,
            "/invoices": self.save_invoice,
            "/templates": self.save_templates,
        }
        handler = routes.get(path)
        if handler:
            return handler()
        self.respond("Not found", status=404, content_type="text/plain")

    def form(self):
        length = int(self.headers.get("Content-Length", "0"))
        data = self.rfile.read(length).decode("utf-8")
        return {key: values[0].strip() for key, values in parse_qs(data).items()}

    def multipart_form(self):
        values, files = {}, {}
        content_type = self.headers.get("Content-Type", "")
        boundary_token = "boundary="
        if boundary_token not in content_type:
            return self.form(), files
        boundary = ("--" + content_type.split(boundary_token, 1)[1].split(";", 1)[0].strip().strip('"')).encode()
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        for part in body.split(boundary):
            part = part.strip(b"\r\n")
            if not part or part == b"--" or b"\r\n\r\n" not in part:
                continue
            raw_headers, content = part.split(b"\r\n\r\n", 1)
            content = content.removesuffix(b"\r\n")
            header_text = raw_headers.decode("utf-8", errors="ignore")
            disposition = ""
            for line in header_text.splitlines():
                if line.lower().startswith("content-disposition:"):
                    disposition = line
                    break
            attrs = {}
            for section in disposition.split(";"):
                if "=" in section:
                    key, value = section.strip().split("=", 1)
                    attrs[key] = value.strip().strip('"')
            name = attrs.get("name")
            if not name:
                continue
            if "filename" in attrs and attrs["filename"]:
                files[name] = {"filename": attrs["filename"], "content": content}
            else:
                values[name] = content.decode("utf-8", errors="ignore").strip()
        return values, files

    def redirect(self, path):
        self.send_response(303)
        self.send_header("Location", path)
        self.end_headers()

    def soft_delete(self, table, redirect_to):
        query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        row_id = query.get("id", [""])[0]
        if row_id:
            with db() as con:
                con.execute(
                    f"UPDATE {table} SET deleted_at=CURRENT_TIMESTAMP, deleted_by=?, updated_by=? WHERE id=?",
                    (current_actor(), current_actor(), row_id),
                )
        self.redirect(f"{redirect_to}?message=Element supprime")

    def respond(self, body, status=200, content_type="text/html; charset=utf-8"):
        encoded = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def static_file(self, path):
        static_root = (ROOT_DIR / "static").resolve()
        target = (ROOT_DIR / path.lstrip("/")).resolve()
        if not str(target).startswith(str(static_root)) or not target.exists() or not target.is_file():
            return self.respond("Not found", status=404, content_type="text/plain")
        content_types = {
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }
        self.respond(target.read_bytes(), content_type=content_types.get(target.suffix.lower(), "application/octet-stream"))

    def uploaded_file(self, path):
        target = (ROOT_DIR / path.lstrip("/")).resolve()
        if not str(target).startswith(str(UPLOAD_DIR.resolve())) or not target.exists():
            return self.respond("Not found", status=404, content_type="text/plain")
        content_type = "application/pdf" if target.suffix.lower() == ".pdf" else "image/jpeg"
        self.respond(target.read_bytes(), content_type=content_type)

    def exported_file(self, path):
        target = (ROOT_DIR / path.lstrip("/")).resolve()
        if not str(target).startswith(str(EXPORT_DIR.resolve())) or not target.exists() or target.suffix.lower() != ".pdf":
            return self.respond("Not found", status=404, content_type="text/plain")
        content = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Disposition", f'inline; filename="{target.name}"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def archive_metadata(self, path):
        name = path.name
        document_type = "PDF"
        invoice_number = ""
        for prefix, label in (
            ("Devis_Quantitatif_", "DQ"),
            ("Devis_Estimatif_", "DE"),
            ("Facture_", "FACT"),
        ):
            if name.startswith(prefix):
                document_type = label
                rest = name[len(prefix):].removesuffix(".pdf")
                invoice_number = rest.rsplit("_", 1)[0] if "_" in rest else rest
                break
        return document_type, invoice_number

    def archives(self):
        query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        message = query.get("message", [""])[0]
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        files = sorted(EXPORT_DIR.glob("*.pdf"), key=lambda path: path.stat().st_mtime, reverse=True)
        grouped = {}
        for path in files:
            stat = path.stat()
            document_type, invoice_number = self.archive_metadata(path)
            if not invoice_number:
                continue
            entry = grouped.setdefault(invoice_number, {"files": {}, "latest": 0.0, "size": 0})
            entry["latest"] = max(entry["latest"], stat.st_mtime)
            entry["size"] += stat.st_size
            current = entry["files"].get(document_type)
            if current is None or stat.st_mtime > current.stat().st_mtime:
                entry["files"][document_type] = path

        rows = []
        for invoice_number, entry in sorted(grouped.items(), key=lambda item: item[1]["latest"], reverse=True):
            buttons = []
            for document_type in ("FACT", "DQ", "DE"):
                path = entry["files"].get(document_type)
                if path:
                    buttons.append(
                        f'<a class="button-link icon-link output-link" href="/archives/open?file={quote(path.name)}" title="Ouvrir {document_type}">{document_type}</a>'
                    )
                else:
                    buttons.append(f'<span class="button-link icon-link output-link disabled-link" title="{document_type} non genere">{document_type}</span>')
            rows.append([
                h(invoice_number),
                h(datetime.fromtimestamp(entry["latest"]).strftime("%Y-%m-%d %H:%M:%S")),
                f"{entry['size'] / 1024:.1f} Ko",
                f'<span class="action-icons">{"".join(buttons)}</span>',
            ])
        alert = f'<section class="alert">{h(message)}</section>' if message else ""
        listing = table(["N° Facture", "Derniere generation", "Taille totale", "Sorties"], rows)
        self.respond(layout("Archives", alert + listing))

    def open_archive(self):
        query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        filename = Path(query.get("file", [""])[0]).name
        if not filename.lower().endswith(".pdf"):
            return self.redirect("/archives?message=Fichier invalide")
        target = (EXPORT_DIR / filename).resolve()
        if not str(target).startswith(str(EXPORT_DIR.resolve())) or not target.exists():
            return self.redirect("/archives?message=Fichier introuvable")
        threading.Timer(0.1, open_pdf_with_system_viewer, args=[target]).start()
        return self.redirect(f"/archives?message=PDF ouvert: {quote(str(target))}")

    def delete_archive(self):
        query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        filename = Path(query.get("file", [""])[0]).name
        if not filename.lower().endswith(".pdf"):
            return self.redirect("/archives?message=Fichier invalide")
        target = (EXPORT_DIR / filename).resolve()
        if not str(target).startswith(str(EXPORT_DIR.resolve())) or not target.exists():
            return self.redirect("/archives?message=Fichier introuvable")
        try:
            target.unlink()
        except OSError:
            return self.redirect("/archives?message=Impossible de supprimer: fichier ouvert")
        return self.redirect("/archives?message=Fichier supprime")

    def respond_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def bpu_item(self):
        query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        article_number = query.get("article_number", [""])[0].strip()
        if not article_number:
            return self.respond_json({"error": "missing article_number"}, status=400)
        with db() as con:
            row = con.execute("""
                SELECT article_number, designation, unite, pu_ht, categorie
                FROM bpu_items
                WHERE article_number=?
            """, (article_number,)).fetchone()
        if not row:
            return self.respond_json({"error": "Article introuvable"}, status=404)
        return self.respond_json({
            "article_number": row["article_number"],
            "designation": row["designation"],
            "unite": row["unite"],
            "pu_ht": row["pu_ht"],
            "categorie": row["categorie"],
        })

    def templates(self):
        with db() as con:
            settings = template_settings(con)
            company_logo_row = con.execute("SELECT logo_path FROM company_settings WHERE id=1").fetchone()
            client_logo_row = con.execute("SELECT logo_path FROM mobilis_directions WHERE deleted_at IS NULL AND logo_path IS NOT NULL AND logo_path<>'' ORDER BY id LIMIT 1").fetchone()
            sample_invoice = con.execute("""
                SELECT i.invoice_number, i.invoice_type, s.code_site, s.nom_site, s.region, s.typologie_site
                FROM invoices i LEFT JOIN sites s ON s.id=i.site_id
                WHERE i.deleted_at IS NULL AND i.invoice_type<>'NDC' ORDER BY i.id DESC LIMIT 1
            """).fetchone()
            sample_lines = con.execute("""
                SELECT il.article_number, il.designation_snapshot, il.unite_snapshot, il.quantite, il.pu_ht_snapshot, il.montant_ht
                FROM invoice_lines il JOIN invoices i ON i.id=il.invoice_id
                WHERE i.deleted_at IS NULL ORDER BY i.id DESC, il.id LIMIT 7
            """).fetchall()
            audit = con.execute("""
                SELECT updated_by, updated_at
                FROM template_settings
                ORDER BY updated_at DESC
                LIMIT 1
            """).fetchone()
        query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        message = query.get("message", [""])[0]
        document_type = query.get("doc", ["facture"])[0]
        if document_type not in {"facture", "devis_quantitatif", "devis_estimatif"}:
            document_type = "facture"
        blocks_key = f"visual_blocks_{document_type}_json"
        alert = f'<div class="alert">{h(message)}</div>' if message else ""

        def setting_field(key, label, step="1"):
            return (
                f'<label><span>{h(label)}</span>'
                f'<input type="number" step="{h(step)}" name="{h(key)}" value="{h(settings[key])}"></label>'
            )

        def text_setting_field(key, label):
            return field(key, label, settings.get(key, ""))

        checked = " checked" if settings.get("hide_empty_sections") == "1" else ""
        updated = ""
        if audit:
            updated = f'<p class="file-note">Derniere modification: {h(audit["updated_by"])} - {h(audit["updated_at"])}</p>'
        try:
            saved_blocks = settings.get(blocks_key) or (settings.get("visual_blocks_json") if document_type == "facture" else "")
            blocks = json.loads(saved_blocks or "[]") or default_visual_blocks(document_type)
        except json.JSONDecodeError:
            blocks = default_visual_blocks(document_type)
        existing_block_ids = {str(block.get("id")) for block in blocks}
        for default_block in reversed(default_visual_blocks(document_type)):
            if str(default_block.get("id")) not in existing_block_ids:
                blocks.insert(0, default_block)
        block_html = []
        for block in blocks:
            style = (
                f'left:{float(block.get("x", 0))}px;top:{float(block.get("y", 0))}px;'
                f'width:{float(block.get("w", 120))}px;height:{float(block.get("h", 50))}px;'
                f'font-size:{float(block.get("font", 12))}px;'
                f'color:{h(block.get("color", "#111111"))};'
                f'background:{h(block.get("background", "")) if block.get("background") else ""};'
                f'text-align:{h(block.get("align", "left"))};'
                f'z-index:{int(block.get("z", 1) or 1)};'
                f'{"display:none;" if block.get("hidden") else ""}'
            )
            extra_class = " devis-title-block" if block.get("id") == "devis_title" else ""
            if block.get("locked"):
                extra_class += " locked"
            image_html = ""
            preview_html = ""
            if block.get("id") == "company_logo" and company_logo_row and company_logo_row["logo_path"]:
                extra_class += " logo-template-block"
                image_html = f'<img src="/{h(company_logo_row["logo_path"])}" alt="Logo entreprise">'
            if block.get("id") == "client_logo" and client_logo_row and client_logo_row["logo_path"]:
                extra_class += " logo-template-block"
                image_html = f'<img src="/{h(client_logo_row["logo_path"])}" alt="Logo Mobilis">'
            if block.get("id") == "site_info":
                extra_class += " sample-content-block"
                preview_html = f'<div class="sample-site"><b>Projet</b><span>:</span><span>Extension du réseau</span><b>Site</b><span>:</span><span>{h(sample_invoice["nom_site"] if sample_invoice and sample_invoice["nom_site"] else "Cité 500 Logements")}</span><b>Wilaya</b><span>:</span><span>{h(sample_invoice["region"] if sample_invoice and sample_invoice["region"] else "ORAN")}</span><b>Typologie</b><span>:</span><span>{h(sample_invoice["typologie_site"] if sample_invoice and sample_invoice["typologie_site"] else "A12")}</span></div>'
            if block.get("id") == "articles_table":
                extra_class += " sample-table-block"
                columns = '<th>N°</th><th>Désignation</th><th>Unité</th><th>Quantités</th>' + ('' if document_type == 'devis_quantitatif' else '<th>PU/HT</th><th>Montant/HT</th>')
                line_rows_parts = []
                for line in sample_lines[:5]:
                    prices = '' if document_type == 'devis_quantitatif' else f'<td>{money(line["pu_ht_snapshot"])}</td><td>{money(line["montant_ht"])}</td>'
                    line_rows_parts.append(f'<tr><td>{h(line["article_number"])}</td><td>{h(line["designation_snapshot"])}</td><td>{h(line["unite_snapshot"])}</td><td>{h(line["quantite"])}</td>{prices}</tr>')
                line_rows = ''.join(line_rows_parts)
                preview_html = f'<table><thead><tr>{columns}</tr></thead><tbody><tr class="sample-category"><td>A</td><td colspan="5">FOURNITURES</td></tr>{line_rows or "<tr><td>470</td><td>Support d antenne mural galvanisé</td><td>U</td><td>3</td></tr>"}</tbody></table>'
            if block.get("id") in {"totals_table", "signature", "amount_words"}:
                extra_class += " sample-content-block"
            block_html.append(
                f'<div class="template-block{extra_class}" data-id="{h(block.get("id"))}" style="{style}">'
                f'{image_html}{preview_html}<strong>{h(block.get("title"))}</strong><pre>{h(block.get("content"))}</pre></div>'
            )
        layer_html = "".join(
            f'<button type="button" data-select-id="{h(block.get("id"))}"><span>◉</span>{h(block.get("title"))}<small>{"Verrouillé" if block.get("locked") else ""}</small></button>'
            for block in blocks
        )
        blocks_json = h(json.dumps(blocks, ensure_ascii=False))
        doc_tabs = "".join(
            f'<a class="template-tab{" active" if document_type == value else ""}" href="/templates?doc={value}">{label}</a>'
            for value, label in [
                ("facture", "Facture"),
                ("devis_quantitatif", "Devis Quantitatif"),
                ("devis_estimatif", "Devis Estimatif"),
            ]
        )

        form_html = f"""
        <form method="post" action="/templates" class="panel template-editor">
          <input type="hidden" name="document_type" value="{h(document_type)}">
          <input type="hidden" id="visualBlocksJson" name="visual_blocks_json" value="{blocks_json}">
          <section class="template-primary-editor">
            <div class="template-tabs">{doc_tabs}</div>
            <div class="template-toolbar"><span>↶</span><span>↷</span><i></i><button type="button" class="active">➤</button><span>✥</span><i></i><span>▤</span><span>☷</span><span>≡</span><span>▦</span><i></i><label>− &nbsp; 90 % &nbsp; ＋</label><span class="toolbar-spacer"></span><button type="button" class="outline-button">◉ Aperçu</button><button type="submit">▣ Enregistrer</button></div>
            <div class="template-workbench">
              <aside class="layers-panel"><h3>Calques</h3>{layer_html}</aside>
              <div class="paper-preview" id="paperPreview">
                {''.join(block_html)}
              </div>
              <section class="block-inspector">
                <h3>Propriétés du bloc</h3>
                <label><span>Bloc</span><input id="blockTitle" type="text"></label>
                <div class="tool-row">
                  <button type="button" id="duplicateBlock" title="Dupliquer">⧉</button>
                  <button type="button" id="resetBlock" title="Reset">↺</button>
                  <button type="button" id="frontBlock" title="Devant">⬆</button>
                  <button type="button" id="backBlock" title="Derriere">⬇</button>
                </div>
                <div class="tool-row">
                  <button type="button" data-align="left" title="Aligner gauche">⇤</button>
                  <button type="button" data-align="center" title="Centrer">↔</button>
                  <button type="button" data-align="right" title="Aligner droite">⇥</button>
                </div>
                <div class="grid mini-grid">
                  <label><span>X</span><input id="blockX" type="number" step="1"></label>
                  <label><span>Y</span><input id="blockY" type="number" step="1"></label>
                  <label><span>Largeur</span><input id="blockW" type="number" step="1"></label>
                  <label><span>Hauteur</span><input id="blockH" type="number" step="1"></label>
                  <label><span>Police</span><input id="blockFont" type="number" step="1"></label>
                </div>
                <div class="grid mini-grid">
                  <label><span>Couleur texte</span><input id="blockColor" type="color"></label>
                  <label><span>Fond</span><input id="blockBackground" type="color"></label>
                  <label><span>Alignement</span><select id="blockAlign"><option value="left">Gauche</option><option value="center">Centre</option><option value="right">Droite</option></select></label>
                  <label><span>Z</span><input id="blockZ" type="number" step="1"></label>
                </div>
                <div class="check-list compact-checks">
                  <label class="check-row"><input id="blockBold" type="checkbox"> Bold</label>
                  <label class="check-row"><input id="blockBorder" type="checkbox"> Bordure</label>
                  <label class="check-row"><input id="blockHidden" type="checkbox"> Masquer</label>
                  <label class="check-row"><input id="blockLocked" type="checkbox"> Verrouiller</label>
                  <label class="check-row"><input id="snapGrid" type="checkbox" checked> Snap 5px</label>
                </div>
                <label><span>Contenu</span><textarea id="blockContent" rows="8"></textarea></label>
                <div class="placeholder-palette">
                  <button type="button" data-ph="{{FACTURE.TYPE}}">{{FACTURE.TYPE}}</button>
                  <button type="button" data-ph="{{N_Facture}}">{{N_Facture}}</button>
                  <button type="button" data-ph="{{Code_Site}}">{{Code_Site}}</button>
                  <button type="button" data-ph="{{Nom_Site}}">{{Nom_Site}}</button>
                  <button type="button" data-ph="{{Region}}">{{Region}}</button>
                  <button type="button" data-ph="{{Montant_En_Lettres}}">{{Montant_En_Lettres}}</button>
                </div>
                <p class="file-note">Cliquez sur un bloc puis deplacez-le avec la souris. Le contenu accepte les placeholders entre accolades.</p>
              </section>
            </div>
            <footer class="template-status"><span>A4</span><span>Portrait</span><span>Marges : Haut 15 mm</span><span>Bas 15 mm</span><span>Gauche 15 mm</span><span>Droite 15 mm</span><strong>Page 1 / 1</strong></footer>
          </section>
          <details class="advanced-template-settings"><summary>Réglages avancés Excel et impression</summary><section>
            <h3>Reglages precis Excel</h3>
            <div class="grid">
              {setting_field("logo_width_px", "Largeur logo px")}
              {setting_field("logo_height_px", "Hauteur logo px")}
              {text_setting_field("company_logo_anchor", "Cellule logo entreprise")}
              {setting_field("company_logo_offset_px", "Decalage logo entreprise px")}
              {text_setting_field("client_logo_anchor", "Cellule logo client")}
              {setting_field("client_logo_offset_px", "Decalage logo client px")}
            </div>
          </section>
          <section>
            <h3>Tableaux</h3>
            <div class="grid">
              {setting_field("col_a_width", "A / N°", "0.1")}
              {setting_field("col_b_width", "B / Designation", "0.1")}
              {setting_field("col_c_width", "C / Unite", "0.1")}
              {setting_field("col_d_width", "D / Quantites", "0.1")}
              {setting_field("col_e_width", "E / PU/HT", "0.1")}
              {setting_field("col_f_width", "F / Montant/HT", "0.1")}
              {setting_field("line_height_normal", "Hauteur normale")}
              {setting_field("line_height_wrapped", "Hauteur avec retour ligne")}
              {setting_field("designation_wrap_chars", "Seuil retour ligne designation")}
            </div>
          </section>
          <section>
            <h3>Montant en lettres</h3>
            <div class="grid">
              {setting_field("amount_words_font_size", "Taille police montant en lettres")}
              <label class="check-row"><input type="checkbox" name="hide_empty_sections" value="1"{checked}> Masquer les sections sans articles</label>
            </div>
          </section>
          </details>
          {updated}
          <script>
          (function() {{
            const blocks = JSON.parse(document.getElementById('visualBlocksJson').value || '[]');
            const defaultBlocks = JSON.parse(document.getElementById('visualBlocksJson').value || '[]');
            const previewType = {json.dumps(display_type(sample_invoice['invoice_type']) if sample_invoice else 'CONSTRUCTION', ensure_ascii=False)};
            const hidden = document.getElementById('visualBlocksJson');
            const paper = document.getElementById('paperPreview');
            const fields = {{
              title: document.getElementById('blockTitle'),
              x: document.getElementById('blockX'),
              y: document.getElementById('blockY'),
              w: document.getElementById('blockW'),
              h: document.getElementById('blockH'),
              font: document.getElementById('blockFont'),
              color: document.getElementById('blockColor'),
              background: document.getElementById('blockBackground'),
              align: document.getElementById('blockAlign'),
              z: document.getElementById('blockZ'),
              bold: document.getElementById('blockBold'),
              border: document.getElementById('blockBorder'),
              hidden: document.getElementById('blockHidden'),
              locked: document.getElementById('blockLocked'),
              content: document.getElementById('blockContent')
            }};
            const snapGrid = document.getElementById('snapGrid');
            let selected = blocks[0];
            let drag = null;
            function persist() {{ hidden.value = JSON.stringify(blocks); }}
            function blockEl(id) {{ return paper.querySelector('[data-id="' + id + '"]'); }}
            function snap(value) {{ return snapGrid && snapGrid.checked ? Math.round(value / 5) * 5 : value; }}
            function draw(block) {{
              const el = blockEl(block.id);
              if (!el) return;
              el.style.left = block.x + 'px';
              el.style.top = block.y + 'px';
              el.style.width = block.w + 'px';
              el.style.height = block.h + 'px';
              el.style.fontSize = block.font + 'px';
              el.style.color = block.color || '#111111';
              el.style.background = block.background || (block.id === 'devis_title' ? '#4472c4' : 'rgba(240, 248, 255, 0.72)');
              el.style.textAlign = block.align || 'left';
              el.style.fontWeight = block.bold ? '700' : '';
              el.style.borderStyle = block.border === false ? 'none' : 'dashed';
              el.style.zIndex = block.z || 1;
              el.style.display = block.hidden ? 'none' : '';
              el.classList.toggle('locked', !!block.locked);
              el.querySelector('strong').textContent = block.title;
              el.querySelector('pre').textContent = block.id === 'devis_title' ? String(block.content || '').replace('{{{{FACTURE.TYPE}}}}', previewType) : block.content;
            }}
            function select(block) {{
              selected = block;
              paper.querySelectorAll('.template-block').forEach(el => el.classList.toggle('selected', el.dataset.id === block.id));
              document.querySelectorAll('[data-select-id]').forEach(el => el.classList.toggle('active', el.dataset.selectId === block.id));
              fields.title.value = block.title || '';
              fields.x.value = block.x || 0;
              fields.y.value = block.y || 0;
              fields.w.value = block.w || 0;
              fields.h.value = block.h || 0;
              fields.font.value = block.font || 12;
              fields.color.value = block.color || '#111111';
              fields.background.value = block.background || (block.id === 'devis_title' ? '#4472c4' : '#f0f8ff');
              fields.align.value = block.align || 'left';
              fields.z.value = block.z || 1;
              fields.bold.checked = !!block.bold;
              fields.border.checked = block.border !== false;
              fields.hidden.checked = !!block.hidden;
              fields.locked.checked = !!block.locked;
              fields.content.value = block.content || '';
            }}
            function updateSelected() {{
              if (!selected) return;
              selected.title = fields.title.value;
              selected.x = Number(fields.x.value || 0);
              selected.y = Number(fields.y.value || 0);
              selected.w = Number(fields.w.value || 0);
              selected.h = Number(fields.h.value || 0);
              selected.font = Number(fields.font.value || 12);
              selected.color = fields.color.value;
              selected.background = fields.background.value;
              selected.align = fields.align.value;
              selected.z = Number(fields.z.value || 1);
              selected.bold = fields.bold.checked;
              selected.border = fields.border.checked;
              selected.hidden = fields.hidden.checked;
              selected.locked = fields.locked.checked;
              selected.content = fields.content.value;
              draw(selected);
              persist();
            }}
            Object.values(fields).forEach(input => input.addEventListener('input', updateSelected));
            paper.querySelectorAll('.template-block').forEach(el => {{
              el.addEventListener('mousedown', ev => {{
                const block = blocks.find(item => item.id === el.dataset.id);
                select(block);
                if (block.locked) return;
                drag = {{ block, startX: ev.clientX, startY: ev.clientY, x: block.x, y: block.y }};
                ev.preventDefault();
              }});
            }});
            window.addEventListener('mousemove', ev => {{
              if (!drag) return;
              drag.block.x = Math.max(0, snap(drag.x + ev.clientX - drag.startX));
              drag.block.y = Math.max(0, snap(drag.y + ev.clientY - drag.startY));
              draw(drag.block);
              select(drag.block);
              persist();
            }});
            document.getElementById('duplicateBlock').addEventListener('click', () => {{
              if (!selected) return;
              const clone = {{...selected, id: selected.id + '_copy_' + Date.now(), title: selected.title + ' copie', x: Number(selected.x || 0) + 20, y: Number(selected.y || 0) + 20}};
              blocks.push(clone);
              const el = document.createElement('div');
              el.className = 'template-block' + (clone.id === 'devis_title' ? ' devis-title-block' : '');
              el.dataset.id = clone.id;
              el.innerHTML = '<strong></strong><pre></pre>';
              paper.appendChild(el);
              el.addEventListener('mousedown', ev => {{
                select(clone);
                if (clone.locked) return;
                drag = {{ block: clone, startX: ev.clientX, startY: ev.clientY, x: clone.x, y: clone.y }};
                ev.preventDefault();
              }});
              draw(clone); select(clone); persist();
            }});
            document.getElementById('resetBlock').addEventListener('click', () => {{
              if (!selected) return;
              const original = defaultBlocks.find(item => item.id === selected.id);
              if (!original) return;
              Object.assign(selected, JSON.parse(JSON.stringify(original)));
              draw(selected); select(selected); persist();
            }});
            document.getElementById('frontBlock').addEventListener('click', () => {{ if(selected) {{ selected.z = Number(selected.z || 1) + 1; draw(selected); select(selected); persist(); }} }});
            document.getElementById('backBlock').addEventListener('click', () => {{ if(selected) {{ selected.z = Math.max(0, Number(selected.z || 1) - 1); draw(selected); select(selected); persist(); }} }});
            document.querySelectorAll('[data-align]').forEach(btn => btn.addEventListener('click', () => {{ if(selected) {{ selected.align = btn.dataset.align; draw(selected); select(selected); persist(); }} }}));
            document.querySelectorAll('[data-select-id]').forEach(btn => btn.addEventListener('click', () => {{
              const block = blocks.find(item => item.id === btn.dataset.selectId);
              if (block) select(block);
            }}));
            document.querySelectorAll('[data-ph]').forEach(btn => btn.addEventListener('click', () => {{
              const textarea = fields.content;
              const start = textarea.selectionStart || 0;
              const end = textarea.selectionEnd || 0;
              textarea.value = textarea.value.slice(0, start) + btn.dataset.ph + textarea.value.slice(end);
              textarea.dispatchEvent(new Event('input'));
              textarea.focus();
            }}));
            window.addEventListener('mouseup', () => drag = null);
            blocks.forEach(draw);
            if (selected) select(selected);
            persist();
          }})();
          </script>
        </form>
        <section class="panel">
          <h3>Note</h3>
          <p class="file-note">Les modifications du template sont globales: elles s'appliquent au prochain export de toutes les factures, y compris les factures deja creees. Chaque document garde son design visuel separe: Facture, Devis Quantitatif et Devis Estimatif.</p>
        </section>
        """
        self.respond(layout("Templates", alert + form_html))

    def save_templates(self):
        values = self.form()
        document_type = values.get("document_type", "facture")
        if document_type not in {"facture", "devis_quantitatif", "devis_estimatif"}:
            document_type = "facture"
        visual_blocks_key = f"visual_blocks_{document_type}_json"
        with db() as con:
            for key, default in DEFAULT_TEMPLATE_SETTINGS.items():
                if key.startswith("visual_blocks_") and key != visual_blocks_key:
                    continue
                value = values.get(key)
                if key == visual_blocks_key:
                    value = values.get("visual_blocks_json", "")
                if key == "hide_empty_sections":
                    value = "1" if values.get(key) == "1" else "0"
                if value is None or value == "":
                    value = default
                con.execute("""
                    INSERT INTO template_settings(key, value, updated_by, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(key) DO UPDATE SET
                        value=excluded.value,
                        updated_by=excluded.updated_by,
                        updated_at=CURRENT_TIMESTAMP
                """, (key, value, current_actor()))
        self.redirect(f"/templates?doc={document_type}&message=Template enregistre")

    def table_facturation_new_state(self):
        query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        state = {
            "q": query.get("q", [""])[0].strip(),
            "type": query.get("type", [""])[0].strip(),
            "region": query.get("region", [""])[0].strip(),
            "depos": query.get("depos", [""])[0].strip(),
            "direction": query.get("direction", [""])[0].strip(),
            "date_start": query.get("date_start", [""])[0].strip(),
            "date_end": query.get("date_end", [""])[0].strip(),
            "message": query.get("message", [""])[0].strip(),
            "sort": query.get("sort", ["date"])[0].strip().lower(),
            "order": query.get("order", ["desc"])[0].strip().lower(),
        }
        allowed_sorts = {"site", "bet", "typologie", "type", "bc", "invoice", "region", "date", "ttc", "depos", "maj"}
        if state["sort"] not in allowed_sorts:
            state["sort"] = "date"
        if state["order"] not in {"asc", "desc"}:
            state["order"] = "desc" if state["sort"] == "date" else "asc"
        try:
            state["page"] = max(1, int(query.get("page", ["1"])[0]))
        except ValueError:
            state["page"] = 1
        try:
            requested_per_page = int(query.get("per_page", ["20"])[0])
        except ValueError:
            requested_per_page = 20
        state["per_page"] = requested_per_page if requested_per_page in {10, 20, 50, 100} else 20
        return state

    def table_facturation_new_where(self, state):
        where = ["i.deleted_at IS NULL"]
        params = []
        if state["q"]:
            where.append("""(
                i.invoice_number LIKE ? OR po.numero_bc LIKE ? OR s.code_site LIKE ? OR
                EXISTS (
                    SELECT 1 FROM invoice_sites xis
                    JOIN sites xs ON xs.id=xis.site_id
                    WHERE xis.invoice_id=i.id AND xs.code_site LIKE ?
                )
            )""")
            token = f"%{state['q']}%"
            params.extend([token, token, token, token])
        if state["type"]:
            where.append("i.invoice_type=?")
            params.append(state["type"])
        if state["region"]:
            where.append("""(
                s.region=? OR EXISTS (
                    SELECT 1 FROM invoice_sites xis
                    JOIN sites xs ON xs.id=xis.site_id
                    WHERE xis.invoice_id=i.id AND xs.region=?
                )
            )""")
            params.extend([state["region"], state["region"]])
        if state["depos"] in {"0", "1"}:
            where.append("i.depos=?")
            params.append(int(state["depos"]))
        if state["direction"]:
            where.append("po.mobilis_direction_id=?")
            params.append(state["direction"])
        if state["date_start"]:
            where.append("DATE(i.invoice_date)>=DATE(?)")
            params.append(state["date_start"])
        if state["date_end"]:
            where.append("DATE(i.invoice_date)<=DATE(?)")
            params.append(state["date_end"])
        return where, params

    def table_facturation_new_rows(self, state, paginate=True):
        """Return filtered rows sorted by the values actually shown in the table.

        Sorting is deliberately applied to the complete filtered result set *before*
        pagination.  This avoids SQLite/text-affinity corner cases and guarantees
        that clicking a header visibly reorders the rows in both directions.
        """
        where, params = self.table_facturation_new_where(state)
        from_sql = """
            FROM invoices i
            JOIN purchase_orders po ON po.id=i.purchase_order_id
            JOIN mobilis_directions md ON md.id=po.mobilis_direction_id
            LEFT JOIN sites s ON s.id=i.site_id
        """
        select_sql = """
            SELECT i.*, po.numero_bc, po.date_bc, po.type_bc, md.direction_regionale,
                   s.code_site, s.region, s.typologie_site, s.bet,
                   (SELECT COUNT(*) FROM invoice_sites xis WHERE xis.invoice_id=i.id) AS ndc_site_count,
                   (SELECT GROUP_CONCAT(xs.code_site, ', ') FROM invoice_sites xis
                    JOIN sites xs ON xs.id=xis.site_id WHERE xis.invoice_id=i.id) AS ndc_codes,
                   (SELECT GROUP_CONCAT(DISTINCT xs.region) FROM invoice_sites xis
                    JOIN sites xs ON xs.id=xis.site_id WHERE xis.invoice_id=i.id) AS ndc_regions,
                   (SELECT COUNT(*) FROM invoice_lines il WHERE il.invoice_id=i.id) AS line_count
        """

        def natural_text(value):
            # Natural, case-insensitive ordering: 2 < 10 and A12 < A100.
            value = str(value or '').strip().casefold()
            return tuple(int(part) if part.isdigit() else part for part in re.split(r'(\d+)', value))

        def row_sort_key(row):
            is_ndc = row['invoice_type'] == 'NDC'
            key = state['sort']
            if key == 'site':
                # Match exactly what the user sees in C. SITE.
                value = f"{int(row['ndc_site_count'] or 0)} sites" if is_ndc else (row['code_site'] or '')
                return natural_text(value)
            if key == 'bet':
                value = row['bet'] if row['invoice_type'] in {'ACQUISITION', 'CONST_ACQUIS'} else ''
                return natural_text(value)
            if key == 'typologie':
                return natural_text('NDC' if is_ndc else (row['typologie_site'] or ''))
            if key == 'type':
                return natural_text(row['invoice_type'])
            if key == 'bc':
                return natural_text(row['numero_bc'])
            if key == 'invoice':
                return natural_text(row['invoice_number'])
            if key == 'region':
                value = row['ndc_regions'] if is_ndc else row['region']
                return natural_text(value)
            if key == 'date':
                return str(row['invoice_date'] or row['date_bc'] or '')
            if key == 'ttc':
                try:
                    return float(row['total_ttc'] or 0)
                except (TypeError, ValueError):
                    return 0.0
            if key == 'depos':
                return int(row['depos'] or 0)
            if key == 'maj':
                return str(row['updated_at'] or row['created_at'] or '')
            return natural_text(row['invoice_number'])

        with db() as con:
            totals = con.execute(
                f"SELECT COUNT(*) AS qty, COALESCE(SUM(i.total_ttc),0) AS total {from_sql} WHERE {' AND '.join(where)}",
                params,
            ).fetchone()
            total_count = int(totals['qty'] or 0)
            total_ttc = float(totals['total'] or 0)
            total_pages = max(1, (total_count + state['per_page'] - 1) // state['per_page'])
            state['page'] = min(state['page'], total_pages)

            # Fetch the full filtered set, then sort, then paginate.
            all_rows = con.execute(
                f"{select_sql} {from_sql} WHERE {' AND '.join(where)}",
                params,
            ).fetchall()
            # Deterministic tie-break: newest record first when two displayed values are equal.
            all_rows = sorted(all_rows, key=lambda row: int(row['id']), reverse=True)
            all_rows = sorted(all_rows, key=row_sort_key, reverse=(state['order'] == 'desc'))

            if paginate:
                start = (state['page'] - 1) * state['per_page']
                rows = all_rows[start:start + state['per_page']]
            else:
                rows = all_rows

            regions = [row[0] for row in con.execute(
                "SELECT DISTINCT region FROM sites WHERE deleted_at IS NULL AND region<>'' ORDER BY region"
            ).fetchall()]
            directions = con.execute(
                "SELECT id, direction_regionale FROM mobilis_directions WHERE deleted_at IS NULL ORDER BY direction_regionale"
            ).fetchall()
            date_limits = con.execute(
                "SELECT MIN(DATE(invoice_date)), MAX(DATE(invoice_date)) FROM invoices WHERE deleted_at IS NULL AND invoice_date IS NOT NULL"
            ).fetchone()
        return rows, total_count, total_ttc, total_pages, regions, directions, date_limits

    def table_facturation_new_url(self, state, **changes):
        values = {
            "q": state["q"], "type": state["type"], "region": state["region"],
            "depos": state["depos"], "direction": state["direction"],
            "date_start": state["date_start"], "date_end": state["date_end"],
            "sort": state["sort"], "order": state["order"],
            "page": state["page"], "per_page": state["per_page"],
        }
        values.update(changes)
        values = {key: value for key, value in values.items() if str(value) != ""}
        return "/table-facturation-new?" + urlencode(values)

    def table_facturation_new(self):
        state = self.table_facturation_new_state()
        rows, total_count, total_ttc, total_pages, regions, directions, date_limits = self.table_facturation_new_rows(state)
        type_labels = {
            "ACQUISITION": "BC Fournitures",
            "CONSTRUCTION": "BC Travaux",
            "CONST_ACQUIS": "BC Mixte",
            "NDC": "Avenant",
        }
        row_html = []
        for row in rows:
            is_ndc = row["invoice_type"] == "NDC"
            site_label = f"{row['ndc_site_count']} sites" if is_ndc else (row["code_site"] or "—")
            site_title = row["ndc_codes"] if is_ndc else row["code_site"]
            region = (row["ndc_regions"] or "").replace(",", ", ") if is_ndc else row["region"]
            typologie = "NDC" if is_ndc else (row["typologie_site"] or "—")
            bet = row["bet"] if row["invoice_type"] in {"ACQUISITION", "CONST_ACQUIS"} else "—"
            remark = row["remarque"] or ""
            deposited = bool(row["depos"])
            deposit_date = date_fr(row["updated_at"]) if deposited else "—"
            updated_actor = row['updated_by'] or row['created_by'] or '—'
            updated_raw = row['updated_at'] or row['created_at']
            try:
                updated_dt = datetime.fromisoformat(str(updated_raw).replace('T', ' ')) if updated_raw else None
                updated_label = updated_dt.strftime('%d/%m/%Y %H:%M') if updated_dt else '—'
            except ValueError:
                updated_label = str(updated_raw or '—')
            row_html.append(f'''<tr data-invoice-id="{row['id']}">
              <td class="nowrap" title="{h(site_title)}">{h(site_label)}</td>
              <td class="nowrap">{h(bet or '—')}</td>
              <td class="nowrap">{h(typologie)}</td>
              <td class="nowrap">{h(type_labels.get(row['invoice_type'], display_type(row['invoice_type'])))}</td>
              <td class="nowrap">{h(row['numero_bc'])}</td>
              <td class="nowrap">{h(row['invoice_number'])}</td>
              <td class="nowrap">{h(region or '—')}</td>
              <td class="nowrap">{h(date_fr(row['invoice_date']))}</td>
              <td class="money">{money(row['total_ttc'])} DA</td>
              <td><div class="remark-editor"><span class="remark-text" data-remark-text>{h(remark or '—')}</span><button class="remark-edit-button" type="button" data-edit-remark title="Modifier la remarque"><svg><use href="#i-edit"/></svg></button><textarea class="remark-input" data-remark-input rows="2" hidden>{h(remark)}</textarea></div></td>
              <td><div class="deposit-editor"><label class="deposit-label"><input type="checkbox" data-deposit-toggle{' checked' if deposited else ''}><span class="deposit-check"></span><span data-deposit-label>{'Oui' if deposited else 'Non'}</span></label><span class="deposit-date" data-deposit-date>{h(deposit_date)}</span></div></td>
              <td><div class="update-meta"><strong>{h(updated_actor)}</strong><span>{h(updated_label)}</span></div></td>
              <td class="actions-cell"><div class="action-buttons">
                <div class="action-menu-wrap">
                  <button class="action-icon document-menu-trigger" type="button" data-document-menu-trigger aria-expanded="false" aria-controls="document-menu-{row['id']}" data-tooltip="Ouvrir" title="Ouvrir"><svg><use href="#i-eye"/></svg></button>
                  <div class="document-menu" id="document-menu-{row['id']}" role="menu" hidden>
                    <div class="document-menu-title">Ouvrir un document</div>
                    <a class="document-menu-item" role="menuitem" href="/invoices/pdf-ready/facture/{row['id']}"><span>Facture</span><small>FACT</small></a>
                    {f'<a class="document-menu-item" role="menuitem" href="/invoices/pdf-ready/devis-quantitatif/{row['id']}"><span>Devis quantitatif</span><small>DQ</small></a>' if int(row['line_count'] or 0) > 0 else '<span class="document-menu-item disabled" role="menuitem" aria-disabled="true"><span>Devis quantitatif</span><small>DQ</small></span>'}
                    {f'<a class="document-menu-item" role="menuitem" href="/invoices/pdf-ready/devis-estimatif/{row['id']}"><span>Devis estimatif</span><small>DE</small></a>' if int(row['line_count'] or 0) > 0 else '<span class="document-menu-item disabled" role="menuitem" aria-disabled="true"><span>Devis estimatif</span><small>DE</small></span>'}
                  </div>
                </div>
                <a class="action-icon" href="/invoices?edit_id={row['id']}" data-tooltip="Modifier" title="Modifier"><svg><use href="#i-edit"/></svg></a>
                <a class="action-icon danger" href="/invoices/delete?id={row['id']}" data-tooltip="Supprimer" title="Supprimer" onclick="return confirm('Supprimer cette facture ?')"><svg><use href="#i-trash"/></svg></a>
              </div></td>
            </tr>''')
        if not row_html:
            row_html.append('<tr class="empty-row"><td colspan="13">Aucune facture ne correspond aux filtres.</td></tr>')

        page_items = {1, total_pages}
        page_items.update(range(max(1, state["page"] - 2), min(total_pages, state["page"] + 2) + 1))
        page_items = sorted(page_items)
        buttons = []
        prev_page = max(1, state["page"] - 1)
        next_page = min(total_pages, state["page"] + 1)
        first_class = " disabled" if state["page"] == 1 else ""
        last_class = " disabled" if state["page"] == total_pages else ""
        buttons.append(f'<a class="page-button{first_class}" href="{h(self.table_facturation_new_url(state, page=1))}" aria-label="Première page">«</a>')
        buttons.append(f'<a class="page-button{first_class}" href="{h(self.table_facturation_new_url(state, page=prev_page))}" aria-label="Page précédente"><svg><use href="#i-chevron-left"/></svg></a>')
        last_number = 0
        for number in page_items:
            if number - last_number > 1:
                buttons.append('<span class="page-button ellipsis">…</span>')
            current = " current" if number == state["page"] else ""
            buttons.append(f'<a class="page-button{current}" href="{h(self.table_facturation_new_url(state, page=number))}">{number}</a>')
            last_number = number
        buttons.append(f'<a class="page-button{last_class}" href="{h(self.table_facturation_new_url(state, page=next_page))}" aria-label="Page suivante"><svg><use href="#i-chevron-right"/></svg></a>')
        buttons.append(f'<a class="page-button{last_class}" href="{h(self.table_facturation_new_url(state, page=total_pages))}" aria-label="Dernière page">»</a>')
        pagination = '<div class="page-buttons">' + ''.join(buttons) + '</div>'

        direction_options = ''.join([option_html('', 'Toutes', state['direction'])] + [option_html(r['id'], r['direction_regionale'], state['direction']) for r in directions])
        type_options = ''.join([
            option_html('', 'Tous', state['type']), option_html('ACQUISITION', 'Acquisition', state['type']),
            option_html('CONSTRUCTION', 'Construction', state['type']), option_html('CONST_ACQUIS', 'Const/Acquis', state['type']),
            option_html('NDC', 'NDC', state['type']),
        ])
        region_options = ''.join([option_html('', 'Toutes', state['region'])] + [option_html(region, region, state['region']) for region in regions])
        depos_options = ''.join([option_html('', 'Tous', state['depos']), option_html('1', 'Déposé', state['depos']), option_html('0', 'Non déposé', state['depos'])])
        per_page_options = ''.join(option_html(value, value, state['per_page']) for value in (10, 20, 50, 100))
        date_start = state['date_start'] or (date_limits[0] or '')
        date_end = state['date_end'] or (date_limits[1] or '')
        actor = current_actor()
        alert = f'<div class="sapta-alert">{h(state["message"])}</div>' if state["message"] else ''
        summary = f"{total_count} facture{'s' if total_count != 1 else ''} — Total TTC {money(total_ttc)} DA"
        html = render_html_template(
            "table_facturation_new.html",
            USER_INITIALS=h(initials(actor)), USER_NAME=h(actor), ALERT=alert,
            SEARCH=h(state['q']), DIRECTION_OPTIONS=direction_options, TYPE_OPTIONS=type_options,
            REGION_OPTIONS=region_options, DEPOS_OPTIONS=depos_options,
            DATE_START=h(date_start), DATE_END=h(date_end), PER_PAGE=state['per_page'],
            ROWS=''.join(row_html), SUMMARY=h(summary), PAGINATION=pagination,
            PER_PAGE_OPTIONS=per_page_options,
        )
        self.respond(html)

    def update_table_facturation_new(self):
        try:
            values = self.form()
            invoice_id = int(values.get("invoice_id", "0"))
            with db() as con:
                row = con.execute("SELECT remarque, depos FROM invoices WHERE id=? AND deleted_at IS NULL", (invoice_id,)).fetchone()
                if not row:
                    raise ValueError("Facture introuvable")
                remark = values.get("remarque", row["remarque"] or "")
                depos = int(values.get("depos", row["depos"] or 0))
                if depos not in {0, 1}:
                    raise ValueError("État de dépôt invalide")
                con.execute("""
                    UPDATE invoices SET remarque=?, depos=?, updated_by=?, updated_at=CURRENT_TIMESTAMP
                    WHERE id=? AND deleted_at IS NULL
                """, (remark, depos, current_actor(), invoice_id))
            self.respond(json.dumps({"ok": True, "date": datetime.now().strftime("%d/%m/%Y")}), content_type="application/json; charset=utf-8")
        except Exception as exc:
            self.respond(json.dumps({"ok": False, "error": str(exc)}), status=400, content_type="application/json; charset=utf-8")

    def export_table_facturation_new(self):
        state = self.table_facturation_new_state()
        rows, _, _, _, _, _, _ = self.table_facturation_new_rows(state, paginate=False)
        type_labels = {"ACQUISITION": "BC Fournitures", "CONSTRUCTION": "BC Travaux", "CONST_ACQUIS": "BC Mixte", "NDC": "Avenant"}
        sheet_rows = [[
            styled("C. SITE", 3), styled("BET", 3), styled("TYPOLOGIE", 3), styled("TYPE BC", 3),
            styled("N° BON DE COMMANDE", 3), styled("N° FACTURE", 3), styled("RÉGION", 3),
            styled("DATE FACTURE", 3), styled("TTC", 3), styled("REMARQUE", 3), styled("DÉPOSÉ", 3),
        ]]
        for row in rows:
            is_ndc = row["invoice_type"] == "NDC"
            sheet_rows.append([
                f"{row['ndc_site_count']} sites" if is_ndc else row["code_site"],
                row["bet"] if row["invoice_type"] in {"ACQUISITION", "CONST_ACQUIS"} else "—",
                "NDC" if is_ndc else row["typologie_site"],
                type_labels.get(row["invoice_type"], display_type(row["invoice_type"])),
                row["numero_bc"], row["invoice_number"],
                (row["ndc_regions"] or "").replace(",", ", ") if is_ndc else row["region"],
                date_fr(row["invoice_date"]), float(row["total_ttc"] or 0), row["remarque"],
                "Oui" if row["depos"] else "Non",
            ])
        content = make_xlsx([("Table Facturation", sheet_rows)])
        filename = f"Table_Facturation_{datetime.now().strftime('%Y%m%d')}.xlsx"
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def table_facturation(self):
        query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        search = query.get("q", [""])[0].strip()
        type_filter = query.get("type", [""])[0].strip()
        region_filter = query.get("region", [""])[0].strip()
        depos_filter = query.get("depos", [""])[0].strip()
        direction_filter = query.get("direction", [""])[0].strip()
        message = query.get("message", [""])[0]
        where = ["i.deleted_at IS NULL"]
        params = []
        if search:
            where.append("""(
                i.invoice_number LIKE ? OR po.numero_bc LIKE ? OR s.code_site LIKE ? OR
                EXISTS (
                    SELECT 1 FROM invoice_sites xis
                    JOIN sites xs ON xs.id=xis.site_id
                    WHERE xis.invoice_id=i.id AND xs.code_site LIKE ?
                )
            )""")
            token = f"%{search}%"
            params.extend([token, token, token, token])
        if type_filter:
            where.append("i.invoice_type=?")
            params.append(type_filter)
        if region_filter:
            where.append("""(
                s.region=? OR EXISTS (
                    SELECT 1 FROM invoice_sites xis
                    JOIN sites xs ON xs.id=xis.site_id
                    WHERE xis.invoice_id=i.id AND xs.region=?
                )
            )""")
            params.extend([region_filter, region_filter])
        if depos_filter in {"0", "1"}:
            where.append("i.depos=?")
            params.append(int(depos_filter))
        if direction_filter:
            where.append("po.mobilis_direction_id=?")
            params.append(direction_filter)
        with db() as con:
            regions = [row[0] for row in con.execute(
                "SELECT DISTINCT region FROM sites WHERE deleted_at IS NULL AND region<>'' ORDER BY region"
            ).fetchall()]
            directions = con.execute("SELECT id, direction_regionale FROM mobilis_directions WHERE deleted_at IS NULL ORDER BY direction_regionale").fetchall()
            rows = con.execute(f"""
                SELECT i.*, po.numero_bc, po.date_bc, po.type_bc, md.direction_regionale,
                       s.code_site, s.region, s.typologie_site, s.bet,
                       (SELECT COUNT(*) FROM invoice_sites xis WHERE xis.invoice_id=i.id) AS ndc_site_count,
                       (SELECT GROUP_CONCAT(xs.code_site, ', ') FROM invoice_sites xis
                        JOIN sites xs ON xs.id=xis.site_id WHERE xis.invoice_id=i.id) AS ndc_codes,
                       (SELECT GROUP_CONCAT(DISTINCT xs.region) FROM invoice_sites xis
                        JOIN sites xs ON xs.id=xis.site_id WHERE xis.invoice_id=i.id) AS ndc_regions
                FROM invoices i
                JOIN purchase_orders po ON po.id=i.purchase_order_id
                JOIN mobilis_directions md ON md.id=po.mobilis_direction_id
                LEFT JOIN sites s ON s.id=i.site_id
                WHERE {' AND '.join(where)}
                ORDER BY COALESCE(po.date_bc, i.invoice_date) DESC, i.id DESC
            """, params).fetchall()

        filter_form = f"""
        <form method="get" class="panel tracking-toolbar">
          <label class="search-field"><span class="sr-only">Recherche</span><input name="q" value="{h(search)}" placeholder="Rechercher site, BC ou facture"></label>
          {select_field('direction', 'Direction', [('', 'Toutes')] + [(r['id'], r['direction_regionale']) for r in directions], direction_filter)}
          {select_field('type', 'Type BC', [('', 'Tous'), ('ACQUISITION','ACQUISITION'), ('CONSTRUCTION','CONSTRUCTION'), ('CONST_ACQUIS','CONST/ACQUIS'), ('NDC','NDC')], type_filter)}
          {select_field('region', 'Région', [('', 'Toutes')] + [(x, x) for x in regions], region_filter)}
          {select_field('depos', 'État dépôt', [('', 'Tous'), ('1','Déposé'), ('0','Non déposé')], depos_filter)}
          <label><span>Période</span><input type="text" value="01/01/2026  →  31/12/2026" readonly></label>
          <button type="submit" class="outline-button" title="Appliquer les filtres">⌁</button>
          <a class="button-link export-button" href="#">▣ Exporter Excel</a>
        </form>"""
        forms = []
        body = []
        total_ttc = 0.0
        for row in rows:
            total_ttc += float(row["total_ttc"] or 0)
            form_id = f'tracking-{row["id"]}'
            forms.append(f'<form id="{form_id}" method="post" action="/table-facturation"><input type="hidden" name="invoice_id" value="{row["id"]}"></form>')
            is_ndc = row["invoice_type"] == "NDC"
            site_label = f'{row["ndc_site_count"]} sites' if is_ndc else row["code_site"]
            site_title = row["ndc_codes"] if is_ndc else row["code_site"]
            region = (row["ndc_regions"] or "").replace(",", ", ") if is_ndc else row["region"]
            bet = row["bet"] if row["invoice_type"] in {"ACQUISITION", "CONST_ACQUIS"} else "—"
            status_class = "depos-ok" if row["depos"] else ("depos-issue" if row["remarque"].strip() else "depos-pending")
            body.append(f"""
              <tr class="{status_class}">
                <td title="{h(site_title)}"><strong>{h(site_label)}</strong></td>
                <td>{h(bet or '—')}</td>
                <td>{h('Multi-sites' if is_ndc else row['typologie_site'])}</td>
                <td>{h(display_type(row['type_bc']))}</td>
                <td>{h(row['numero_bc'])}</td>
                <td><strong>{h(row['invoice_number'])}</strong></td>
                <td>{h(region)}</td>
                <td>{h(date_fr(row['date_bc']))}</td>
                <td class="money-cell">{money(row['total_ttc'])}</td>
                <td><textarea class="inline-remark" name="remarque" form="{form_id}" rows="2" placeholder="Ajouter une remarque">{h(row['remarque'])}</textarea></td>
                <td><select class="inline-status" name="depos" form="{form_id}"><option value="0"{' selected' if not row['depos'] else ''}>Non</option><option value="1"{' selected' if row['depos'] else ''}>Oui</option></select></td>
                <td><span class="action-icons"><button class="icon-button" type="submit" form="{form_id}" title="Enregistrer">✓</button><a class="button-link icon-link" href="/invoices/pdf-ready/facture/{row['id']}" title="Ouvrir la facture">◉</a><a class="button-link icon-link" href="/invoices?edit_id={row['id']}" title="Modifier">✎</a><a class="button-link icon-link danger-link" href="/invoices/delete?id={row['id']}" title="Supprimer" onclick="return confirm('Supprimer cette facture ?')">⌫</a></span></td>
              </tr>
            """)
        if not body:
            body.append('<tr><td colspan="13" class="empty">Aucune facture ne correspond aux filtres.</td></tr>')
        alert = f'<section class="alert">{h(message)}</section>' if message else ""
        listing = f"""
        {''.join(forms)}
        <section class="panel tracking-panel">
          <div class="table-scroll"><table class="tracking-table">
            <thead><tr><th>C.SITE</th><th>BET</th><th>TYPOLOGIE</th><th>TYPE BC</th><th>N° BON DE COMMANDE</th><th>N° FACT</th><th>RÉGION</th><th>DATE BC</th><th>TTC</th><th>REMARQUE</th><th>DEPOS</th><th>ACTIONS</th></tr></thead>
            <tbody>{''.join(body)}</tbody>
          </table></div>
          <footer class="table-summary"><strong>{len(rows)} facture{'s' if len(rows) != 1 else ''} — Total TTC {money(total_ttc)} DA</strong><span class="pager-pages"><select><option>20</option></select><span>«</span><span>‹</span><span class="current">1</span><span>›</span><span>»</span></span></footer>
        </section>"""
        self.respond(layout("Table Facturation", alert + filter_form + listing))

    def save_table_facturation(self):
        values = self.form()
        invoice_id = values.get("invoice_id", "")
        if not invoice_id:
            return self.redirect("/table-facturation?message=Facture introuvable")
        with db() as con:
            con.execute("""
                UPDATE invoices SET remarque=?, depos=?, updated_by=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND deleted_at IS NULL
            """, (values.get("remarque", ""), 1 if values.get("depos") == "1" else 0, current_actor(), invoice_id))
        self.redirect("/table-facturation?message=Suivi mis a jour")

    def dashboard(self):
        query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        year_filter = query.get("year", [""])[0].strip()
        direction_filter = query.get("direction", [""])[0].strip()
        selected_year = year_filter or str(datetime.now().year)
        where = ["i.deleted_at IS NULL"]
        params = []
        if selected_year:
            where.append("strftime('%Y', COALESCE(NULLIF(i.invoice_date,''), i.created_at))=?")
            params.append(selected_year)
        if direction_filter:
            where.append("po.mobilis_direction_id=?")
            params.append(direction_filter)
        where_sql = " AND ".join(where)

        with db() as con:
            directions = con.execute("SELECT id, direction_regionale FROM mobilis_directions WHERE deleted_at IS NULL ORDER BY direction_regionale").fetchall()
            years = [str(row[0]) for row in con.execute("""
                SELECT DISTINCT strftime('%Y', COALESCE(NULLIF(invoice_date,''), created_at)) AS y
                FROM invoices WHERE deleted_at IS NULL AND y IS NOT NULL ORDER BY y DESC
            """).fetchall()]
            if selected_year not in years:
                years.insert(0, selected_year)
            invoice_count = con.execute(f"SELECT COUNT(*) FROM invoices i JOIN purchase_orders po ON po.id=i.purchase_order_id WHERE {where_sql}", params).fetchone()[0]
            total_ttc = con.execute(f"SELECT COALESCE(SUM(i.total_ttc),0) FROM invoices i JOIN purchase_orders po ON po.id=i.purchase_order_id WHERE {where_sql}", params).fetchone()[0]
            invoiced_sites = con.execute(f"""
                WITH filtered AS (
                  SELECT i.id, i.site_id FROM invoices i JOIN purchase_orders po ON po.id=i.purchase_order_id WHERE {where_sql}
                ), linked AS (
                  SELECT site_id FROM filtered WHERE site_id IS NOT NULL
                  UNION SELECT xis.site_id FROM invoice_sites xis JOIN filtered f ON f.id=xis.invoice_id
                ) SELECT COUNT(*) FROM linked
            """, params).fetchone()[0]
            pending_count = con.execute(f"SELECT COUNT(*) FROM invoices i JOIN purchase_orders po ON po.id=i.purchase_order_id WHERE {where_sql} AND i.depos=0", params).fetchone()[0]
            uninvoiced_sites = con.execute("""
                SELECT COUNT(*) FROM sites s WHERE s.deleted_at IS NULL
                AND NOT EXISTS (SELECT 1 FROM invoices i WHERE i.site_id=s.id AND i.deleted_at IS NULL)
                AND NOT EXISTS (SELECT 1 FROM invoice_sites xis JOIN invoices i ON i.id=xis.invoice_id WHERE xis.site_id=s.id AND i.deleted_at IS NULL)
            """).fetchone()[0]
            type_rows = con.execute(f"""
                SELECT i.invoice_type, COUNT(*) AS qty, COALESCE(SUM(i.total_ttc),0) AS total
                FROM invoices i JOIN purchase_orders po ON po.id=i.purchase_order_id
                WHERE {where_sql} GROUP BY i.invoice_type
            """, params).fetchall()
            monthly_rows = con.execute(f"""
                SELECT CAST(strftime('%m', COALESCE(NULLIF(i.invoice_date,''), i.created_at)) AS INTEGER) AS month_no,
                       COALESCE(SUM(i.total_ttc),0) AS total
                FROM invoices i JOIN purchase_orders po ON po.id=i.purchase_order_id
                WHERE {where_sql} GROUP BY month_no
            """, params).fetchall()
            pending = con.execute(f"""
                SELECT i.invoice_number, i.invoice_date, i.total_ttc,
                       COALESCE(s.code_site, (SELECT COUNT(*) || ' sites' FROM invoice_sites xis WHERE xis.invoice_id=i.id)) AS site_label
                FROM invoices i JOIN purchase_orders po ON po.id=i.purchase_order_id
                LEFT JOIN sites s ON s.id=i.site_id
                WHERE {where_sql} AND i.depos=0
                ORDER BY CASE WHEN i.invoice_date IS NULL OR i.invoice_date='' THEN 1 ELSE 0 END,
                         i.invoice_date DESC, i.id DESC LIMIT 5
            """, params).fetchall()
            without_invoice = con.execute("""
                SELECT s.code_site, s.nom_site, COALESCE(md.direction_regionale, s.region) AS direction, po.date_bc
                FROM sites s JOIN purchase_orders po ON po.id=s.purchase_order_id
                LEFT JOIN mobilis_directions md ON md.id=po.mobilis_direction_id
                WHERE s.deleted_at IS NULL
                AND NOT EXISTS (SELECT 1 FROM invoices i WHERE i.deleted_at IS NULL AND i.site_id=s.id)
                AND NOT EXISTS (SELECT 1 FROM invoice_sites xis JOIN invoices i ON i.id=xis.invoice_id WHERE i.deleted_at IS NULL AND xis.site_id=s.id)
                ORDER BY s.id DESC LIMIT 5
            """).fetchall()
            latest = con.execute(f"""
                SELECT i.invoice_number, i.invoice_date, i.total_ttc,
                       COALESCE(s.code_site, (SELECT COUNT(*) || ' sites' FROM invoice_sites xis WHERE xis.invoice_id=i.id)) AS site_label
                FROM invoices i JOIN purchase_orders po ON po.id=i.purchase_order_id
                LEFT JOIN sites s ON s.id=i.site_id WHERE {where_sql}
                ORDER BY CASE WHEN i.invoice_date IS NULL OR i.invoice_date='' THEN 1 ELSE 0 END,
                         i.invoice_date DESC, i.id DESC LIMIT 5
            """, params).fetchall()

        # Dashboard financial display standard: KDA (1 KDA = 1,000 DZD).
        # Database values remain stored in DZD; conversion is display-only.
        def money_kda(value):
            return money(float(value or 0) / 1000)

        metrics = [
            ("Factures émises", invoice_count, "file-text"),
            ("Total TTC", f"{money_kda(total_ttc)} KDA", "coins"),
            ("Sites facturés", invoiced_sites, "building-2"),
            ("À déposer", pending_count, "upload"),
        ]
        cards = "".join(
            f'<section class="metric"><span class="metric-icon"><i data-lucide="{icon}"></i></span><div><span>{h(label)}</span><strong>{h(value)}</strong></div></section>'
            for label, value, icon in metrics
        )

        month_names = ['Janv.','Févr.','Mars','Avr.','Mai','Juin','Juil.','Août','Sept.','Oct.','Nov.','Déc.']
        monthly = {int(r['month_no'] or 0): float(r['total'] or 0) for r in monthly_rows}
        max_value = max(list(monthly.values()) or [0])
        axis_max = max(1_000_000, int((max_value + 999_999) // 1_000_000) * 1_000_000)
        axis_step = axis_max / 5
        axis_labels = ''.join(f'<span>{money_kda(axis_max - (idx * axis_step)).replace(",00", "")}</span>' for idx in range(6))
        bars = ''.join(
            f'<div class="month-column"><span>{money_kda(monthly.get(i,0)) if monthly.get(i,0) else ""}</span>'
            f'<i style="height:{(monthly.get(i,0)/axis_max*158 if monthly.get(i,0) else 0):.1f}px"></i><b>{name}</b></div>'
            for i,name in enumerate(month_names,1)
        )

        color_map = {"CONSTRUCTION":"#369b49", "ACQUISITION":"#347fbd", "CONST_ACQUIS":"#f58b1f", "NDC":"#8a8d90"}
        order = ["NDC", "CONST_ACQUIS", "ACQUISITION", "CONSTRUCTION"]
        row_map = {str(r['invoice_type']): r for r in type_rows}
        ordered_rows = [row_map[k] for k in order if k in row_map] + [r for r in type_rows if str(r['invoice_type']) not in order]
        total_types = sum(int(r['qty']) for r in ordered_rows) or 1
        gradient_parts, donut_labels, legend_parts = [], [], []
        cursor = 0.0
        for r in ordered_rows:
            key = str(r['invoice_type'])
            qty = int(r['qty'])
            pct = qty / total_types * 100
            color = color_map.get(key, '#6b7280')
            gradient_parts.append(f'{color} {cursor:.3f}% {cursor+pct:.3f}%')
            middle_deg = (cursor + pct/2) * 3.6 - 90
            donut_labels.append(f'<span class="donut-percent" style="--angle:{middle_deg:.3f}deg">{pct:.1f}%</span>')
            legend_parts.append(f'<div><i style="background:{color}"></i><span>{h(display_type(key))}</span></div>')
            cursor += pct
        donut_style = ','.join(gradient_parts) if gradient_parts else '#d8dee5 0 100%'
        type_legend = ''.join(legend_parts)

        def due_date(value):
            if not value:
                return '—'
            try:
                return (datetime.strptime(str(value)[:10], '%Y-%m-%d') + timedelta(days=9)).strftime('%d/%m/%Y')
            except ValueError:
                return '—'

        pending_rows = [[
            f'<span class="invoice-link">{h(r["invoice_number"])}</span>',
            h(date_fr(r['invoice_date'])), h(r['site_label']), money_kda(r['total_ttc']),
            f'<span class="danger-text">{h(due_date(r["invoice_date"]))}</span>'
        ] for r in pending]
        no_invoice_rows = [[h(r['code_site']), h(r['nom_site']), h(r['direction']), h(date_fr(r['date_bc']))] for r in without_invoice]
        latest_rows = [[f'<span class="invoice-link">{h(r["invoice_number"])}</span>', h(date_fr(r['invoice_date'])), h(r['site_label']), money_kda(r['total_ttc'])] for r in latest]

        year_options = ''.join(f'<option value="{h(y)}"{" selected" if y==selected_year else ""}>Année {h(y)}</option>' for y in years)
        direction_options = '<option value="">Toutes les directions</option>' + ''.join(
            f'<option value="{r["id"]}"{" selected" if str(r["id"])==direction_filter else ""}>{h(r["direction_regionale"])}</option>' for r in directions
        )
        empty_sites = '<div class="dashboard-empty">Aucun site sans facture</div>' if not no_invoice_rows else table(['Code site','Site','Direction','Dernier BC'], no_invoice_rows)
        content = f"""
          <form class="dashboard-toolbar" method="get"><span></span>
            <label class="dashboard-select year-select"><i data-lucide="calendar-days"></i><select name="year" onchange="this.form.submit()">{year_options}</select></label>
            <label class="dashboard-select"><select name="direction" onchange="this.form.submit()">{direction_options}</select></label>
          </form>
          <div class="metrics dashboard-metrics">{cards}</div>
          <div class="dashboard-main-grid">
            <section class="panel dashboard-chart"><header><h3>Montant TTC par mois</h3></header><div class="chart-body"><div class="chart-currency">KDA</div><div class="chart-axis">{axis_labels}</div><div class="monthly-chart">{bars}</div></div></section>
            <section class="panel donut-panel"><header><h3>Répartition par type</h3></header><div class="donut-wrap"><div class="donut-ring" style="background:conic-gradient({donut_style})"><div class="donut-hole"></div>{''.join(donut_labels)}</div><div class="donut-legend">{type_legend or '<p class="empty">Aucune donnée</p>'}</div></div></section>
          </div>
          <div class="dashboard-lists">
            <section class="panel dashboard-list"><h3>Factures non déposées</h3>{table(['N° Facture','Date facture','Site','Montant TTC (KDA)','Échéance dépôt'], pending_rows)}<a href="/table-facturation-new?depos=0">Voir toutes ({pending_count})</a></section>
            <section class="panel dashboard-list"><h3>Sites sans facture</h3>{empty_sites}<a href="/purchase-orders">Voir tous ({uninvoiced_sites})</a></section>
            <section class="panel dashboard-list"><h3>Dernières factures</h3>{table(['N° Facture','Date facture','Site','Montant TTC (KDA)'], latest_rows)}<a href="/invoices">Voir toutes ({invoice_count})</a></section>
          </div>
          <footer class="dashboard-footer"><span>© 2026 SAPTA Facturation – Tous droits réservés.</span><span>Version 1.0.0</span></footer>
        """
        self.respond(layout("Tableau", content))

    def company(self):
        with db() as con:
            row = con.execute("SELECT * FROM company_settings WHERE id=1").fetchone()
            contract = con.execute("SELECT * FROM contract_settings WHERE id=1").fetchone()

        logo = (
            f'<img class="logo-preview" src="/{h(row["logo_path"])}" alt="Logo de l\'entreprise">'
            if row["logo_path"]
            else '<div class="file-note company-logo-empty">Aucun logo chargé</div>'
        )

        updated_value = row["updated_at"] or ""
        try:
            updated_dt = datetime.fromisoformat(str(updated_value).replace("Z", "+00:00"))
            updated_display = updated_dt.strftime("%d/%m/%Y à %H:%M")
        except (TypeError, ValueError):
            updated_display = str(updated_value or "—")

        content = f"""
        <form method="post" enctype="multipart/form-data" class="company-settings">
          <section class="settings-section company-info-section">
            <h3>Informations de l'entreprise</h3>
            <div class="company-fields">
              {field('nom', 'Nom', row['nom'], required=True)}
              {field('rgc', 'RGC', row['rgc'])}
              {field('nif', 'NIF', row['nif'])}
              {field('art', 'ART', row['art'])}
              <label class="company-address-field"><span>Adresse</span><textarea name="adresse" rows="3">{h(row['adresse'])}</textarea></label>
              {field('numero_compte', 'N° Compte', row['numero_compte'])}
            </div>

            <div class="logo-upload">
              <span class="logo-upload-title">Logo de l'entreprise</span>
              <section class="upload-preview">{logo}<small>Formats acceptés : PNG, JPG (max. 5 Mo)</small></section>
              <div class="logo-actions">
                <label class="outline-button logo-replace-button"><i data-lucide="upload" aria-hidden="true"></i><span>Remplacer</span><input class="hidden-file" type="file" name="logo" accept="image/png,image/jpeg,image/webp"></label>
                <button type="submit" class="outline-button icon-only logo-delete-button" name="delete_logo" value="1" title="Supprimer le logo" aria-label="Supprimer le logo" onclick="return confirm('Supprimer le logo de l\'entreprise ?')"><i data-lucide="trash-2" aria-hidden="true"></i></button>
              </div>
            </div>
          </section>

          <section class="settings-section company-billing-section">
            <h3>Contrat Mobilis</h3>
            {field('reference_contrat', 'Référence Contrat', contract['reference_contrat'], required=True)}
            <div class="contract-status-row"><span>Statut</span><span class="status-dot success"><i data-lucide="circle-check" aria-hidden="true"></i>Contrat actif</span></div>
            <p class="contract-help">Un seul contrat utilisé pour toutes les factures.</p>

            <h3>Paramètres de facturation</h3>
            <div class="setting-row"><span>Retenue de garantie</span><label class="suffix-input"><input value="5,00" readonly><b>%</b></label></div>
            <div class="setting-row"><span>TVA</span><label class="suffix-input"><input value="19,00" readonly><b>%</b></label></div>
            <div class="setting-row"><span>Source pour le montant en lettres</span><select><option>Total TTC</option></select></div>
            <div class="setting-row"><span>Devise</span><select><option>DA - Dinar</option></select></div>
            <div class="setting-row"><span>Montants avec séparateurs de milliers</span><span class="toggle-switch on"></span></div>
            <div class="setting-row"><span>Deux décimales obligatoires</span><span class="toggle-switch on"></span></div>
          </section>

          <footer class="form-actions"><a class="outline-button" href="/company">Annuler</a><button type="submit">Enregistrer les modifications</button></footer>
          <p class="company-audit"><i data-lucide="clock-3" aria-hidden="true"></i>Dernière modification par {h(current_actor())} — {h(updated_display)}</p>
        </form>"""
        self.respond(layout("Entreprise", content, subtitle="Configuration générale"))

    def save_company(self):
        values, files = self.multipart_form()
        with db() as con:
            current = con.execute("SELECT logo_path FROM company_settings WHERE id=1").fetchone()
            logo_path = current["logo_path"] or ""
            if values.get("delete_logo") == "1":
                logo_path = ""
            elif "logo" in files:
                logo_path = save_upload(files["logo"], "logos", {".png", ".jpg", ".jpeg", ".webp"})
            con.execute("""
                UPDATE company_settings
                SET nom=?, logo_path=?, rgc=?, nif=?, art=?, adresse=?, numero_compte=?
                WHERE id=1
            """, (values.get("nom", ""), logo_path, values.get("rgc", ""), values.get("nif", ""), values.get("art", ""), values.get("adresse", ""), values.get("numero_compte", "")))
            con.execute("UPDATE contract_settings SET reference_contrat=? WHERE id=1", (values.get("reference_contrat", ""),))
        self.redirect("/company")

    def mobilis(self):
        query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        edit_id = query.get("edit_id", [""])[0]
        view_id = query.get("view_id", [""])[0]
        add_mode = query.get("new", [""])[0] == "1"
        client_settings_mode = query.get("client_settings", [""])[0] == "1"
        error = query.get("error", [""])[0]
        search_q = (query.get("q", [""])[0] or "").strip().lower()
        region_filter = (query.get("region", [""])[0] or "").strip()
        with db() as con:
            client = mobilis_client_settings(con)
            if not int(client["is_configured"] or 0):
                legacy = con.execute(
                    "SELECT doit_nom, nif FROM mobilis_directions WHERE deleted_at IS NULL "
                    "ORDER BY CASE WHEN COALESCE(nif, '') <> '' THEN 0 ELSE 1 END, id LIMIT 1"
                ).fetchone()
                preset_doit = legacy["doit_nom"] if legacy and legacy["doit_nom"] else MOBILIS_DOIT
                preset_nif = legacy["nif"] if legacy and legacy["nif"] else ""
                alert = '<div class="mobilis-setup-error">Les trois champs sont obligatoires.</div>' if error else ''
                setup = f'''
                <div class="mobilis-client-setup-required">
                  <section class="mobilis-client-setup-card">
                    <header>
                      <small>Configuration initiale obligatoire</small>
                      <h2>Informations client Mobilis</h2>
                      <p>Renseignez une seule fois les informations générales du client avant de gérer les directions.</p>
                    </header>
                    {alert}
                    <form method="post" action="/mobilis/client-settings" class="mobilis-client-settings-form">
                      <input type="hidden" name="initial_setup" value="1">
                      <label><span>DOIT *</span><input name="doit" value="{h(preset_doit)}" required></label>
                      <label><span>NIF *</span><input name="nif" value="{h(preset_nif)}" required></label>
                      <label><span>NIS *</span><input name="nis" value="" required></label>
                      <footer><button type="submit">Enregistrer et continuer</button></footer>
                    </form>
                  </section>
                </div>'''
                self.respond(layout("Directions Mobilis", setup))
                return
            edit = con.execute("SELECT * FROM mobilis_directions WHERE id=? AND deleted_at IS NULL", (edit_id,)).fetchone() if edit_id else None
            view = con.execute("SELECT * FROM mobilis_directions WHERE id=? AND deleted_at IS NULL", (view_id,)).fetchone() if view_id else None
            all_rows = con.execute('''
                SELECT md.*,
                       (SELECT COUNT(*) FROM purchase_orders po WHERE po.mobilis_direction_id=md.id AND po.deleted_at IS NULL) AS bc_count,
                       (SELECT COUNT(*) FROM invoices i JOIN purchase_orders po ON po.id=i.purchase_order_id WHERE po.mobilis_direction_id=md.id AND i.deleted_at IS NULL) AS invoice_count
                FROM mobilis_directions md
                WHERE md.deleted_at IS NULL
                ORDER BY md.direction_regionale
            ''').fetchall()
        rows = []
        for r in all_rows:
            haystack = " ".join([
                str(r["direction_regionale"] or ""),
                str(r["adresse"] or ""),
                str(r["rgc"] or ""),
                str(r["nif"] or ""),
            ]).lower()
            if search_q and search_q not in haystack:
                continue
            if region_filter and region_filter not in (str(r["direction_regionale"] or ""), str(r["id"])):
                continue
            rows.append(r)

        region_options = [("", "Toutes les régions")]
        seen = set()
        for r in all_rows:
            value = str(r["direction_regionale"] or "").strip()
            if value and value not in seen:
                seen.add(value)
                region_options.append((value, value))

        def list_logo(path):
            if path:
                return f'<img class="table-logo" src="/{h(path)}" alt="Logo Mobilis">'
            return '<div class="fixture-mobilis-logo"><small>موبيليس</small><strong>mobilis</strong></div>'

        def panel_logo(path):
            if path:
                return f'<img src="/{h(path)}" alt="Logo Mobilis">'
            return '<div class="fixture-mobilis-drawer-logo"><small>موبيليس</small><strong>mobilis</strong></div>'

        active_row = edit or view
        selected_id = str(active_row["id"]) if active_row else ""
        body_rows = []
        for r in rows:
            selected = ' class="selected-row"' if selected_id and str(r["id"]) == selected_id else ''
            body_rows.append(
                f'<tr{selected}>'
                f'<td>{list_logo(r["logo_path"])}</td>'
                f'<td>{h(r["direction_regionale"] or "")}</td>'
                f'<td>{h(r["adresse"] or "")}</td>'
                f'<td><a class="linked-count" href="/purchase-orders">{r["bc_count"]}</a></td>'
                f'<td><a class="linked-count" href="/invoices">{r["invoice_count"]}</a></td>'
                f'<td><span class="action-icons">'
                f'<a class="button-link icon-link" href="/mobilis?view_id={r["id"]}" title="Ouvrir" aria-label="Ouvrir"><i data-lucide="external-link"></i></a>'
                f'<a class="button-link icon-link" href="/mobilis?edit_id={r["id"]}" title="Modifier" aria-label="Modifier"><i data-lucide="pencil"></i></a>'
                f'<a class="button-link icon-link danger-link" href="/mobilis/delete?id={r["id"]}" title="Supprimer" aria-label="Supprimer" onclick="return confirm(\'Supprimer cette direction ?\')"><i data-lucide="trash-2"></i></a>'
                f'</span></td>'
                f'</tr>'
            )
        if not body_rows:
            body_rows.append('<tr><td colspan="6" class="empty">Aucune direction trouvée</td></tr>')

        listing = f'''
        <table>
          <thead>
            <tr>
              <th>Logo</th>
              <th>Direction régionale</th>
              <th>Adresse</th>
              <th>BC</th>
              <th>Factures</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>{''.join(body_rows)}</tbody>
        </table>'''

        region_select = ''.join(option_html(val, label, region_filter) for val, label in region_options)
        toolbar = f'''
          <div class="page-action-row">
            <section class="page-subtitle">Client : <strong>{h(client['doit'])}</strong><span>{len(all_rows)} directions</span><a class="mobilis-client-settings-link" href="/mobilis?client_settings=1">Modifier les informations Mobilis</a></section>
          </div>
          <form class="entity-toolbar" method="get">
            <label class="search-field"><span class="sr-only">Recherche</span><i data-lucide="search"></i><input name="q" value="{h(search_q)}" placeholder="Rechercher une direction régionale..."></label>
            <label><span class="sr-only">Région</span><select name="region">{region_select}</select></label>
          </form>
        '''

        if client_settings_mode:
            panel = f'''
            <aside class="entity-drawer mobilis-side-panel mobilis-client-settings-panel" aria-label="Modifier les informations Mobilis">
              <header class="drawer-header">
                <div>
                  <small>Informations client Mobilis</small>
                  <h3>Modifier les informations</h3>
                </div>
                <a class="drawer-close" href="/mobilis" aria-label="Fermer">×</a>
              </header>
              <form method="post" action="/mobilis/client-settings" class="drawer-form mobilis-client-settings-form">
                <label><span>DOIT *</span><input name="doit" value="{h(client['doit'])}" required></label>
                <label><span>NIF *</span><input name="nif" value="{h(client['nif'])}" required></label>
                <label><span>NIS *</span><input name="nis" value="{h(client['nis'])}" required></label>
                <footer class="drawer-actions"><a class="outline-button" href="/mobilis">Annuler</a><button type="submit">Enregistrer</button></footer>
              </form>
            </aside>'''
        elif edit or add_mode:
            panel = f'''
            <aside class="entity-drawer mobilis-side-panel" aria-label="{'Modifier la direction' if edit else 'Ajouter une direction'}">
              <header class="drawer-header">
                <div>
                  <small>{'Modifier la direction régionale' if edit else 'Nouvelle direction régionale'}</small>
                  <h3>{h(edit['direction_regionale']) if edit else 'Ajouter une direction'}</h3>
                </div>
                <a class="drawer-close" href="/mobilis" aria-label="Fermer">×</a>
              </header>
              <form method="post" enctype="multipart/form-data" class="drawer-form">
                <input type="hidden" name="id" value="{h(edit['id'] if edit else '')}">
                {field('direction_regionale', 'Direction régionale *', edit['direction_regionale'] if edit else '', required=True)}
                <label class="wide"><span>Adresse *</span><textarea name="adresse" rows="4">{h(edit['adresse']) if edit else ''}</textarea></label>
                <label class="wide"><span>Logo *</span>
                  <div class="drawer-upload">
                    {panel_logo(edit['logo_path']) if edit else panel_logo('')}
                    <div class="drawer-upload-side">
                      <label class="fixture-mobilis-browse" for="mobilisLogoInput">Parcourir...</label>
                      <input id="mobilisLogoInput" type="file" name="logo" accept="image/png,image/jpeg,image/webp,image/gif">
                      <div class="fixture-mobilis-upload-note">JPG, PNG ou GIF<br>Taille max. 2 Mo</div>
                    </div>
                  </div>
                </label>
                <footer class="drawer-actions"><a class="outline-button" href="/mobilis">Annuler</a><button type="submit">Enregistrer</button></footer>
              </form>
            </aside>'''
        elif view:
            panel = f'''
            <aside class="entity-drawer mobilis-side-panel mobilis-details-panel" aria-label="Détails de la direction">
              <header class="drawer-header">
                <div>
                  <small>Détails de la direction régionale</small>
                  <h3>{h(view['direction_regionale'] or '')}</h3>
                </div>
                <a class="drawer-close" href="/mobilis" aria-label="Fermer">×</a>
              </header>
              <div class="mobilis-direction-details">
                <div class="mobilis-detail-logo">{panel_logo(view['logo_path'])}</div>
                <dl>
                  <div><dt>DOIT</dt><dd>{h(client['doit'])}</dd></div>
                  <div><dt>NIF</dt><dd>{h(client['nif'])}</dd></div>
                  <div><dt>NIS</dt><dd>{h(client['nis'])}</dd></div>
                  <div><dt>Direction régionale</dt><dd>{h(view['direction_regionale'] or '—')}</dd></div>
                  <div><dt>Adresse</dt><dd>{h(view['adresse'] or '—')}</dd></div>
                </dl>
                <footer class="drawer-actions mobilis-detail-actions"><a class="outline-button" href="/mobilis">Fermer</a><a class="button-link" href="/mobilis?edit_id={view['id']}">Modifier</a></footer>
              </div>
            </aside>'''
        else:
            panel = '''
            <aside class="entity-drawer mobilis-side-panel mobilis-empty-panel" aria-label="Panneau direction Mobilis">
              <header class="drawer-header">
                <div>
                  <small>Direction Mobilis</small>
                  <h3>Détails de la direction</h3>
                </div>
              </header>
              <div class="mobilis-panel-placeholder">
                <span class="mobilis-placeholder-icon"><i data-lucide="building-2"></i></span>
                <strong>Sélectionnez une direction</strong>
                <p>Ouvrez une direction pour afficher ses informations, ou ajoutez-en une nouvelle.</p>
                <a class="button-link" href="/mobilis?new=1">＋ Ajouter une direction</a>
              </div>
            </aside>'''

        pager = f'''
          <footer class="pager">
            <span>Affichage de 1 à {len(rows)} sur {len(rows)} directions</span>
            <span class="pager-pages"><span>‹</span><span class="current">1</span><span>›</span></span>
          </footer>'''
        content = (
            f'<div class="mobilis-content">'
            f'<section class="mobilis-main-card">'
            f'{toolbar}'
            f'<div class="mobilis-master-detail">'
            f'<section class="entity-list">{listing}{pager}</section>'
            f'{panel}'
            f'</div>'
            f'</section>'
            f'</div>'
        )
        self.respond(layout("Directions Mobilis", content))

    def save_mobilis(self):

        values, files = self.multipart_form()
        logo_path = ""
        if "logo" in files:
            logo_path = save_upload(files["logo"], "logos", {".png", ".jpg", ".jpeg", ".webp"})
        with db() as con:
            client = mobilis_client_settings(con)
            if not int(client["is_configured"] or 0):
                self.redirect("/mobilis")
                return
            if values.get("id"):
                current = con.execute("SELECT logo_path, rgc FROM mobilis_directions WHERE id=?", (values.get("id"),)).fetchone()
                con.execute("""
                    UPDATE mobilis_directions
                    SET doit_nom=?, direction_regionale=?, adresse=?, rgc=?, nif=?, logo_path=?, updated_by=?, updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                """, (
                    client["doit"],
                    values.get("direction_regionale"),
                    values.get("adresse", ""),
                    current["rgc"] if current else "",
                    client["nif"],
                    logo_path or (current["logo_path"] if current else ""),
                    current_actor(),
                    values.get("id"),
                ))
            else:
                con.execute("""
                    INSERT INTO mobilis_directions(doit_nom,direction_regionale,adresse,rgc,nif,logo_path,created_by,updated_by)
                    VALUES(?,?,?,?,?,?,?,?)
                """, (client["doit"], values.get("direction_regionale"), values.get("adresse", ""), "", client["nif"], logo_path, current_actor(), current_actor()))
        self.redirect("/mobilis")

    def save_mobilis_client_settings(self):
        values = self.form()
        doit = values.get("doit", "").strip()
        nif = values.get("nif", "").strip()
        nis = values.get("nis", "").strip()
        initial_setup = values.get("initial_setup") == "1"
        if not doit or not nif or not nis:
            self.redirect("/mobilis?error=1" if initial_setup else "/mobilis?client_settings=1&error=1")
            return
        with db() as con:
            mobilis_client_settings(con)
            con.execute("""
                UPDATE mobilis_client_settings
                SET doit=?, nif=?, nis=?, is_configured=1, updated_by=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=1
            """, (doit, nif, nis, current_actor()))
            con.execute("""
                UPDATE mobilis_directions
                SET doit_nom=?, nif=?, updated_by=?, updated_at=CURRENT_TIMESTAMP
                WHERE deleted_at IS NULL
            """, (doit, nif, current_actor()))
        self.redirect("/mobilis")

    def contract(self):
        with db() as con:
            row = con.execute("SELECT * FROM contract_settings WHERE id=1").fetchone()
        content = f'<form method="post" class="panel grid">{field("reference_contrat", "Reference Contrat", row["reference_contrat"], required=True)}<button type="submit">Enregistrer</button></form>'
        self.respond(layout("Contrat", content))

    def save_contract(self):
        values = self.form()
        with db() as con:
            con.execute("UPDATE contract_settings SET reference_contrat=? WHERE id=1", (values.get("reference_contrat", ""),))
        self.redirect("/contract")

    def bpu(self):
        query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        message = query.get("message", [""])[0]
        search = query.get("q", [""])[0].strip()
        category = query.get("category", [""])[0].strip()
        unit = query.get("unit", [""])[0].strip()
        try:
            page = max(1, int(query.get("page", ["1"])[0] or 1))
        except (TypeError, ValueError):
            page = 1
        per_page = 10

        where = []
        params = []
        if search:
            where.append("(CAST(article_number AS TEXT) LIKE ? OR designation LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])
        if category:
            where.append("LOWER(TRIM(categorie))=LOWER(TRIM(?))")
            params.append(category)
        if unit:
            where.append("LOWER(TRIM(unite))=LOWER(TRIM(?))")
            params.append(unit)
        where_sql = 'WHERE ' + ' AND '.join(where) if where else ''

        with db() as con:
            filtered_total = con.execute(
                f"SELECT COUNT(*) FROM bpu_items {where_sql}", params
            ).fetchone()[0]
            total_pages = max(1, (filtered_total + per_page - 1) // per_page)
            page = min(page, total_pages)
            offset = (page - 1) * per_page
            rows = con.execute(
                f"SELECT * FROM bpu_items {where_sql} ORDER BY article_number LIMIT ? OFFSET ?",
                [*params, per_page, offset],
            ).fetchall()
            total_items = con.execute("SELECT COUNT(*) FROM bpu_items").fetchone()[0]
            latest = con.execute("SELECT MAX(updated_at) FROM bpu_items").fetchone()[0]
            unit_rows = con.execute("""
                SELECT MIN(unite) AS unite
                FROM bpu_items
                WHERE TRIM(COALESCE(unite, '')) <> ''
                GROUP BY LOWER(TRIM(unite))
                ORDER BY LOWER(TRIM(unite))
            """).fetchall()
            unit_options = [row["unite"] for row in unit_rows]

        table_head = """<table class="bpu-data-table"><thead><tr>
            <th>N° Article</th>
            <th>Désignation</th>
            <th>Unité</th>
            <th>PU/HT</th>
            <th>Catégorie</th>
            <th>Statut</th>
        </tr></thead><tbody>"""
        table_rows = []
        if rows:
            for r in rows:
                table_rows.append(
                    "<tr>"
                    f'<td>{h(r['article_number'])}</td>'
                    f'<td>{h(r['designation'])}</td>'
                    f'<td>{h(r['unite'])}</td>'
                    f'<td><span class="locked-price"><i data-lucide="lock-keyhole" aria-hidden="true"></i>{money(r["pu_ht"])}</span></td>'
                    f'<td>{h(r['categorie'])}</td>'
                    '<td><span class="status-pill">Actif</span></td>'
                    "</tr>"
                )
        else:
            table_rows.append('<tr><td colspan="6" class="empty">Aucune donnee</td></tr>')
        listing = table_head + ''.join(table_rows) + "</tbody></table>"
        alert = f'<section class="alert">{h(message)}</section>' if message else ""
        form_html = f"""
        <section class="bpu-status"><span class="status-dot success">BPU actif</span><strong>{total_items} articles</strong><span>◷ Dernier import : {h(latest or '—')} par {h(current_actor())}</span></section>
        <div class="bpu-actions">
          <form id="bpu-import-form" method="post" enctype="multipart/form-data">
            <label class="upload-button"><span><i data-lucide="upload" aria-hidden="true"></i>Importer un fichier Excel (.xlsx)</span><input id="bpu-file" type="file" name="bpu_file" accept=".xlsx" required></label>
          </form>
          <a class="outline-button" href="#"><i data-lucide="download" aria-hidden="true"></i>Télécharger le modèle</a>
          <a class="outline-button" href="#"><i data-lucide="history" aria-hidden="true"></i>Historique des imports</a>
        </div>
        <form method="get" class="bpu-filters" id="bpu-filter-form">
          <label class="search-field bpu-floating-field"><span class="bpu-floating-label">Recherche</span><i data-lucide="search" aria-hidden="true"></i><input name="q" value="{h(search)}" placeholder="Rechercher par N° article ou désignation" autocomplete="off"></label>
          <label class="bpu-floating-field"><span class="bpu-floating-label">Catégorie</span><select name="category">{''.join(f'<option value="{h(value)}"{' selected' if str(value)==str(category) else ''}>{h(label)}</option>' for value,label in [('', 'Toutes'), ('acquisition','Acquisition'), ('fourniture','Fourniture'), ('prestation','Prestation')])}</select></label>
          <label class="bpu-floating-field"><span class="bpu-floating-label">Unité</span><select name="unit"><option value="">Toutes</option>{''.join(f'<option value="{h(value)}"{' selected' if value.casefold()==unit.casefold() else ''}>{h(value)}</option>' for value in unit_options)}</select></label>
          <a class="outline-button" href="/bpu">Réinitialiser ↻</a>
        </form>
        <script>
          (() => {{
            const form = document.getElementById('bpu-filter-form');
            if (!form) return;
            const searchInput = form.querySelector('input[name="q"]');
            const selects = form.querySelectorAll('select[name]');
            selects.forEach((select) => select.addEventListener('change', () => form.requestSubmit()));
            let searchTimer = null;
            if (searchInput) {{
              searchInput.addEventListener('input', () => {{
                clearTimeout(searchTimer);
                searchTimer = setTimeout(() => form.requestSubmit(), 450);
              }});
            }}
          }})();
        </script>"""
        validation = f"""
          <aside class="bpu-validation is-hidden" id="bpu-validation" aria-hidden="true">
            <header class="drawer-header"><h3>Validation de l'import</h3><button type="button" class="drawer-close" id="close-bpu-validation" aria-label="Fermer">×</button></header>
            <p>Fichier : <strong id="pending-bpu-name">Aucun fichier sélectionné</strong></p><p id="pending-bpu-meta">Sélectionnez un fichier Excel pour afficher cette validation.</p>
            <h3>Correspondance des colonnes</h3><p class="file-note">Vérifiez la correspondance entre les colonnes de votre fichier et les champs BPU.</p>
            {''.join(f'<div class="mapping-row"><span>{label}</span><b>→</b><select><option>{label}</option></select><strong>✓</strong></div>' for label in ['N° Article','Désignation','Unité','PU/HT','Catégorie'])}
            <div class="validation-ok">✓ <span>Après confirmation, le fichier sélectionné sera importé et remplacera la base BPU actuelle.</span></div>
            <button type="button" class="dark-button" id="confirm-bpu-import">Confirmer l'import</button><button type="button" class="outline-button" id="cancel-bpu-import">Annuler</button>
            <script>
              document.addEventListener('DOMContentLoaded', () => {{
                const bpuFile = document.querySelector('#bpu-file');
                const bpuForm = document.querySelector('#bpu-import-form');
                const validationDrawer = document.querySelector('#bpu-validation');
                const validationLayout = document.querySelector('.bpu-reference-layout');
                const pageBody = document.body;
                const pendingName = document.querySelector('#pending-bpu-name');
                const pendingMeta = document.querySelector('#pending-bpu-meta');
                const closeButton = document.querySelector('#close-bpu-validation');
                const cancelButton = document.querySelector('#cancel-bpu-import');
                const confirmButton = document.querySelector('#confirm-bpu-import');

                const openValidation = () => {{
                  validationDrawer.classList.remove('is-hidden');
                  validationDrawer.setAttribute('aria-hidden', 'false');
                  validationLayout.classList.add('import-preview-open');
                  pageBody.classList.add('bpu-import-preview-open');
                }};

                const resetValidation = () => {{
                  bpuFile.value = '';
                  pendingName.textContent = 'Aucun fichier sélectionné';
                  pendingMeta.textContent = 'Sélectionnez un fichier Excel pour afficher cette validation.';
                  validationDrawer.classList.add('is-hidden');
                  validationDrawer.setAttribute('aria-hidden', 'true');
                  validationLayout.classList.remove('import-preview-open');
                  pageBody.classList.remove('bpu-import-preview-open');
                }};

                bpuFile.addEventListener('change', () => {{
                  const file = bpuFile.files && bpuFile.files[0];
                  if (!file) {{
                    resetValidation();
                    return;
                  }}
                  pendingName.textContent = file.name;
                  pendingMeta.textContent = 'Fichier prêt à être validé avant import.';
                  openValidation();
                }});

                confirmButton.addEventListener('click', () => {{
                  if (!bpuFile.files || !bpuFile.files[0]) {{
                    bpuFile.click();
                    return;
                  }}
                  bpuForm.requestSubmit();
                }});

                closeButton.addEventListener('click', resetValidation);
                cancelButton.addEventListener('click', resetValidation);
              }});
            </script>
          </aside>
        """

        def page_url(target_page):
            values = {}
            if search:
                values["q"] = search
            if category:
                values["category"] = category
            if unit:
                values["unit"] = unit
            if target_page > 1:
                values["page"] = target_page
            return "/bpu" + (("?" + urlencode(values)) if values else "")

        if total_pages <= 7:
            page_sequence = list(range(1, total_pages + 1))
        elif page <= 4:
            page_sequence = [1, 2, 3, 4, 5, "…", total_pages]
        elif page >= total_pages - 3:
            page_sequence = [1, "…", total_pages - 4, total_pages - 3, total_pages - 2, total_pages - 1, total_pages]
        else:
            page_sequence = [1, "…", page - 1, page, page + 1, "…", total_pages]

        pager_bits = []
        pager_bits.append(f'<a class="pager-nav{" disabled" if page == 1 else ""}" href="{h(page_url(1) if page > 1 else "#")}" aria-label="Première page">«</a>')
        pager_bits.append(f'<a class="pager-nav{" disabled" if page == 1 else ""}" href="{h(page_url(page - 1) if page > 1 else "#")}" aria-label="Page précédente">‹</a>')
        for item in page_sequence:
            if item == "…":
                pager_bits.append('<span class="pager-ellipsis">…</span>')
            elif item == page:
                pager_bits.append(f'<span class="current">{item}</span>')
            else:
                pager_bits.append(f'<a href="{h(page_url(item))}">{item}</a>')
        pager_bits.append(f'<a class="pager-nav{" disabled" if page == total_pages else ""}" href="{h(page_url(page + 1) if page < total_pages else "#")}" aria-label="Page suivante">›</a>')
        pager_bits.append(f'<a class="pager-nav{" disabled" if page == total_pages else ""}" href="{h(page_url(total_pages) if page < total_pages else "#")}" aria-label="Dernière page">»</a>')

        start_item = offset + 1 if filtered_total else 0
        end_item = offset + len(rows) if filtered_total else 0
        pager = (
            f'<footer class="pager">'
            f'<span>{start_item} à {end_item} sur {filtered_total} articles</span>'
            f'<span class="pager-pages">{"".join(pager_bits)}</span>'
            f'<span class="bpu-per-page">10 / page</span>'
            f'</footer>'
        )

        main = (
            f'<section class="bpu-main-card">'
            f'{alert}{form_html}'
            f'<section class="panel bpu-list"><div class="bpu-table-scroll">{listing}</div>{pager}</section>'
            f'</section>'
        )
        self.respond(layout("Bordereau des prix unitaires (BPU)", f'<div class="bpu-reference-layout"><div>{main}</div>{validation}</div>'))

    def save_bpu(self):
        try:
            _, files = self.multipart_form()
            if "bpu_file" in files:
                bpu_path = save_upload(files["bpu_file"], "bpu", {".xlsx"})
                imported = import_bpu(ROOT_DIR / bpu_path)
                with db() as con:
                    con.execute("DELETE FROM bpu_items")
                    con.executemany("""
                        INSERT INTO bpu_items(article_number,designation,unite,pu_ht,categorie)
                        VALUES(?,?,?,?,?)
                    """, imported)
                self.redirect(f"/bpu?message={len(imported)} articles BPU importes")
                return
        except Exception as exc:
            self.redirect(f"/bpu?message=Erreur import BPU: {exc}")
            return
        self.redirect("/bpu")

    def purchase_orders(self):
        query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        message = query.get("message", [""])[0]
        edit_id = query.get("edit_id", [""])[0]
        view_id = query.get("view_id", [""])[0]
        site_edit_id = query.get("site_edit_id", [""])[0]
        new_mode = query.get("new", [""])[0] == "1"

        search = query.get("q", [""])[0].strip()
        direction_filter = query.get("direction", [""])[0]
        type_filter = query.get("type", [""])[0]
        year_filter = query.get("year", [""])[0]
        sort_key = query.get("sort", ["date"])[0]
        sort_order = query.get("order", ["desc"])[0].lower()
        if sort_order not in {"asc", "desc"}:
            sort_order = "desc"
        try:
            page = max(1, int(query.get("page", ["1"])[0]))
        except ValueError:
            page = 1
        try:
            per_page = int(query.get("per_page", ["10"])[0])
        except ValueError:
            per_page = 10
        if per_page not in {10, 20, 50}:
            per_page = 10

        def po_url(**overrides):
            state = {
                "q": search,
                "direction": direction_filter,
                "type": type_filter,
                "year": year_filter,
                "sort": sort_key,
                "order": sort_order,
                "page": page,
                "per_page": per_page,
            }
            state.update(overrides)
            params = {k: v for k, v in state.items() if v not in ("", None)}
            return "/purchase-orders?" + urlencode(params)

        def natural_number(value):
            parts = re.findall(r"\d+|\D+", str(value or ""))
            return tuple(int(part) if part.isdigit() else part.lower() for part in parts)

        sorters = {
            "numero": lambda r: natural_number(r["numero_bc"]),
            "date": lambda r: str(r["date_bc"] or ""),
            "direction": lambda r: str(r["direction_regionale"] or "").lower(),
            "type": lambda r: str(display_type(r["type_bc"])).lower(),
            "amount": lambda r: float(r["montant_ttc"] or 0),
            "sites": lambda r: int(r["site_count"] or 0),
        }
        if sort_key not in sorters:
            sort_key = "date"

        with db() as con:
            edit = con.execute("SELECT * FROM purchase_orders WHERE id=? AND deleted_at IS NULL", (edit_id,)).fetchone() if edit_id else None
            directions = con.execute("SELECT id, direction_regionale FROM mobilis_directions WHERE deleted_at IS NULL ORDER BY direction_regionale").fetchall()
            all_rows = con.execute("""
                SELECT po.*, md.direction_regionale,
                       COUNT(DISTINCT s.id) AS site_count,
                       COUNT(DISTINCT CASE WHEN EXISTS (
                           SELECT 1 FROM invoices i WHERE i.deleted_at IS NULL AND i.site_id=s.id
                       ) OR EXISTS (
                           SELECT 1 FROM invoice_sites xis JOIN invoices ni ON ni.id=xis.invoice_id
                           WHERE ni.deleted_at IS NULL AND xis.site_id=s.id
                       ) THEN s.id END) AS invoiced_count
                FROM purchase_orders po
                JOIN mobilis_directions md ON md.id = po.mobilis_direction_id
                LEFT JOIN sites s ON s.purchase_order_id=po.id AND s.deleted_at IS NULL
                WHERE po.deleted_at IS NULL
                GROUP BY po.id
            """).fetchall()

            years = sorted({str(r["date_bc"] or "")[:4] for r in all_rows if r["date_bc"]}, reverse=True)
            rows = list(all_rows)
            if search:
                needle = search.lower()
                rows = [r for r in rows if needle in str(r["numero_bc"] or "").lower()
                        or needle in str(r["direction_regionale"] or "").lower()
                        or needle in str(r["objet"] or "").lower()]
            if direction_filter:
                rows = [r for r in rows if str(r["mobilis_direction_id"]) == str(direction_filter)]
            if type_filter:
                rows = [r for r in rows if str(r["type_bc"]) == type_filter]
            if year_filter:
                rows = [r for r in rows if str(r["date_bc"] or "").startswith(year_filter)]

            rows.sort(key=sorters[sort_key], reverse=(sort_order == "desc"))
            total_rows = len(rows)
            total_pages = max(1, (total_rows + per_page - 1) // per_page)
            page = min(page, total_pages)
            start_index = (page - 1) * per_page
            page_rows = rows[start_index:start_index + per_page]

            selected_id = view_id or edit_id or (str(page_rows[0]["id"]) if page_rows else (str(rows[0]["id"]) if rows else ""))
            selected = con.execute("""
                SELECT po.*, md.direction_regionale FROM purchase_orders po
                JOIN mobilis_directions md ON md.id=po.mobilis_direction_id
                WHERE po.id=? AND po.deleted_at IS NULL
            """, (selected_id,)).fetchone() if selected_id else None
            site_edit = con.execute("SELECT * FROM sites WHERE id=? AND deleted_at IS NULL", (site_edit_id,)).fetchone() if site_edit_id else None
            selected_sites = con.execute("""
                SELECT s.*,
                       CASE WHEN EXISTS (SELECT 1 FROM invoices i WHERE i.deleted_at IS NULL AND i.site_id=s.id)
                                  OR EXISTS (SELECT 1 FROM invoice_sites xis JOIN invoices ni ON ni.id=xis.invoice_id WHERE ni.deleted_at IS NULL AND xis.site_id=s.id)
                            THEN 1 ELSE 0 END AS invoiced
                FROM sites s WHERE s.purchase_order_id=? AND s.deleted_at IS NULL ORDER BY s.code_site
            """, (selected_id,)).fetchall() if selected_id else []

        form_html = f"""
        <section class="master-detail-form">
          <header class="drawer-header"><div><small>Bon de commande</small><h3>{'Modifier le BC' if edit else 'Nouveau bon de commande'}</h3></div><a href="/purchase-orders" class="drawer-close" title="Fermer">×</a></header>
        <form method="post" enctype="multipart/form-data" class="grid po-drawer-form" id="po-form">
          <input type="hidden" name="id" value="{h(edit['id'] if edit else '')}">
          {field('numero_bc', 'N° Bon de commande', edit['numero_bc'] if edit else '', required=True)}
          {field('date_bc', 'Date BC', edit['date_bc'] if edit else '', field_type='date')}
          {select_field('mobilis_direction_id', 'Direction Mobilis', [(r["id"], r["direction_regionale"]) for r in directions], edit['mobilis_direction_id'] if edit else '')}
          {select_field('type_bc', 'Type BC', [('ACQUISITION','ACQUISITION'),('CONSTRUCTION','CONSTRUCTION'),('CONST_ACQUIS','CONST/ACQUIS'),('NDC','NDC')], edit['type_bc'] if edit else '')}
          {field('objet', 'Objet', edit['objet'] if edit else '')}
          {decimal_text_field('montant_ttc', 'Montant TTC', edit['montant_ttc'] if edit else '')}
          {file_field('bc_file', 'Fichier BC PDF/JPG', '.pdf,.jpg,.jpeg,.png')}
          {textarea_field('code_sites', 'Code site - NDC', 'Un code par ligne. Nombre de sites: 0')}
          <footer class="drawer-actions"><a class="outline-button" href="/purchase-orders">Annuler</a><button type="submit">{'Enregistrer' if edit else 'Créer le BC'}</button></footer>
        </form>
        </section>
        <script>
          const typeBc = document.querySelector('select[name="type_bc"]');
          const codeBox = document.querySelector('textarea[name="code_sites"]');
          const codeLabel = codeBox.closest('label');
          const hint = codeLabel.querySelector('small');
          function syncNdcCodes() {{
            const isNdc = typeBc.value === 'NDC';
            codeLabel.style.display = isNdc ? 'grid' : 'none';
            const count = codeBox.value.split(/[\\n,;]/).map(x => x.trim()).filter(Boolean).length;
            hint.textContent = `Un code par ligne. Nombre de sites: ${{count}}`;
          }}
          typeBc.addEventListener('change', syncNdcCodes);
          codeBox.addEventListener('input', syncNdcCodes);
          syncNdcCodes();
        </script>"""

        def sortable_th(label, key):
            active = sort_key == key
            next_order = "desc" if active and sort_order == "asc" else "asc"
            arrow = " ↑" if active and sort_order == "asc" else (" ↓" if active else "")
            return f'<th><a class="po-sort-link" href="{h(po_url(sort=key, order=next_order, page=1))}">{h(label)}<span>{arrow}</span></a></th>'

        listing_rows = "".join(f"""
          <tr class="{'selected-row' if str(r['id']) == str(selected_id) else ''}" data-href="/purchase-orders?view_id={r['id']}">
            <td><a class="table-primary-link" href="/purchase-orders?view_id={r['id']}">{h(r['numero_bc'])}</a></td>
            <td>{h(date_fr(r['date_bc']))}</td>
            <td>{h(r['direction_regionale'])}</td>
            <td>{h(display_type(r['type_bc']))}</td>
            <td class="money-cell">{money(r['montant_ttc'])} DA</td>
            <td>{r['site_count']} sites</td>
          </tr>
        """ for r in page_rows)

        start_display = start_index + 1 if total_rows else 0
        end_display = min(start_index + len(page_rows), total_rows)
        page_links = []
        if total_pages > 1:
            page_links.append(f'<a href="{h(po_url(page=1))}" title="Première page">|‹</a>')
            page_links.append(f'<a href="{h(po_url(page=max(1,page-1)))}" title="Page précédente">‹</a>')
            left = max(1, page - 2)
            right = min(total_pages, page + 3)
            for pnum in range(left, right + 1):
                cls = ' class="current"' if pnum == page else ''
                page_links.append(f'<a{cls} href="{h(po_url(page=pnum))}">{pnum}</a>')
            page_links.append(f'<a href="{h(po_url(page=min(total_pages,page+1)))}" title="Page suivante">›</a>')
            page_links.append(f'<a href="{h(po_url(page=total_pages))}" title="Dernière page">›|</a>')
        else:
            page_links.append('<span class="current">1</span>')
        listing = f"""
          <div class="po-master-table-wrap"><table class="po-master-table"><thead><tr>
            {sortable_th('N° BC','numero')}{sortable_th('DATE','date')}{sortable_th('DIRECTION','direction')}{sortable_th('TYPE','type')}{sortable_th('MONTANT TTC','amount')}{sortable_th('SITES','sites')}
          </tr></thead><tbody>{listing_rows or '<tr><td colspan="6" class="empty">Aucun bon de commande</td></tr>'}</tbody></table></div>
          <footer class="pager po-pager"><span>{start_display} à {end_display} sur {total_rows}</span><span class="pager-pages">{''.join(page_links)}</span>
            <select class="po-per-page" aria-label="Lignes par page" onchange="location.href=this.value">{''.join(f'<option value="{h(po_url(per_page=n,page=1))}"{" selected" if n == per_page else ""}>{n} par page</option>' for n in (10,20,50))}</select>
          </footer>
          <script>document.querySelectorAll('.po-master-table tbody tr[data-href]').forEach(row=>{{row.addEventListener('click',e=>{{if(!e.target.closest('a,button')) location.href=row.dataset.href;}})}});</script>
        """

        selected_panel = ""
        if selected:
            allow_bet = selected["type_bc"] in {"ACQUISITION", "CONST_ACQUIS"}
            site_rows_html = []
            for site in selected_sites:
                edit_link = f'/purchase-orders?view_id={selected["id"]}&site_edit_id={site["id"]}'
                delete_link = f'/purchase-orders/site/delete?id={site["id"]}&po_id={selected["id"]}'
                open_link = f'/table-facturation-new?q={quote(str(site["code_site"]))}'
                facturation = '<span class="po-status-badge factured">Facturé</span>' if site["invoiced"] else '<span class="po-status-badge to-invoice">À facturer</span>'
                bet_value = h(site["bet"] or '—') if allow_bet else '—'
                site_rows_html.append(f"""<tr>
                  <td>{h(site["code_site"])}</td><td>{h(site["nom_site"])}</td><td>{h(site["region"])}</td><td>{h(site["typologie_site"])}</td><td>{bet_value}</td><td>{facturation}</td>
                  <td><span class="po-site-actions"><a href="{open_link}" title="Ouvrir"><i data-lucide="external-link"></i></a><a href="{edit_link}" title="Modifier"><i data-lucide="pencil"></i></a><a class="danger" href="{delete_link}" title="Supprimer" onclick="return confirm('Supprimer ce site ?')"><i data-lucide="trash-2"></i></a></span></td>
                </tr>""")

            if selected["attachment_path"]:
                stored_path = ROOT_DIR / selected["attachment_path"]
                ext = stored_path.suffix.lower() or '.pdf'
                clean_name = f"BC_{str(selected['numero_bc']).replace('/', '-')}{ext}"
                try:
                    size_kb = max(1, round(stored_path.stat().st_size / 1024))
                    size_text = f"{size_kb} Ko"
                except OSError:
                    size_text = "—"
                updated_date = date_fr(str(selected["updated_at"] or "")[:10]) if selected["updated_at"] else "—"
                author = selected["updated_by"] or selected["created_by"] or current_actor()
                attachment = f"""
                <div class="po-file-card">
                  <div class="po-pdf-icon">PDF</div>
                  <div class="po-file-meta"><strong>{h(clean_name)}</strong><small>{h(size_text)} &nbsp;•&nbsp; PDF &nbsp;•&nbsp; Ajouté le {h(updated_date)} par {h(author)}</small></div>
                  <div class="po-file-actions">
                    <a href="/{h(selected['attachment_path'])}" target="_blank" title="Ouvrir"><i data-lucide="external-link"></i></a>
                    <form method="post" action="/purchase-orders/document" enctype="multipart/form-data"><input type="hidden" name="po_id" value="{selected['id']}"><label title="Remplacer"><i data-lucide="upload"></i><input type="file" name="bc_file" accept=".pdf,.jpg,.jpeg,.png" onchange="this.form.submit()"></label></form>
                    <a class="danger" href="/purchase-orders/document/delete?id={selected['id']}" title="Supprimer" onclick="return confirm('Supprimer le document BC ?')"><i data-lucide="trash-2"></i></a>
                  </div>
                </div>"""
            else:
                attachment = f"""
                <div class="po-file-card empty-document">
                  <div class="po-pdf-icon muted">PDF</div><div class="po-file-meta"><strong>Aucun document joint</strong><small>Ajoutez un PDF, JPG ou PNG</small></div>
                  <div class="po-file-actions"><form method="post" action="/purchase-orders/document" enctype="multipart/form-data"><input type="hidden" name="po_id" value="{selected['id']}"><label title="Ajouter"><i data-lucide="upload"></i><input type="file" name="bc_file" accept=".pdf,.jpg,.jpeg,.png" onchange="this.form.submit()"></label></form></div>
                </div>"""

            bet_field = field('bet', "Nom du bureau d'étude (BET)", site_edit['bet'] if site_edit else '') if allow_bet else '<input type="hidden" name="bet" value="">'
            selected_panel = f"""
            <section class="po-detail">
              <div class="po-detail-fields">
                <label><span>N° Bon de commande</span><input value="{h(selected['numero_bc'])}" readonly></label>
                <label class="po-date-field"><span>Date BC</span><input value="{h(date_fr(selected['date_bc']))}" readonly><i data-lucide="calendar-days"></i></label>
                <label><span>Direction Mobilis</span><input value="{h(selected['direction_regionale'])}" readonly></label>
                <label><span>Type BC</span><input value="{h(display_type(selected['type_bc']))}" readonly></label>
                <label><span>Montant TTC</span><input value="{money(selected['montant_ttc'])} DA" readonly></label>
                <label class="po-object"><span>Objet</span><textarea readonly>{h(selected['objet'])}</textarea><small>{len(str(selected['objet'] or ''))} / 250</small></label>
              </div>
              <section class="po-document"><h3>Document BC</h3>{attachment}</section>
              <div class="section-header sites-heading"><h3>Sites du bon de commande ({len(selected_sites)})</h3><button type="button" class="outline-button site-toggle"><i data-lucide="plus"></i> Ajouter un site</button></div>
              <form method="post" action="/sites" class="site-inline-form{' editing' if site_edit else ''}">
                <input type="hidden" name="id" value="{h(site_edit['id'] if site_edit else '')}">
                <input type="hidden" name="purchase_order_id" value="{selected['id']}">
                {field('code_site', 'Code de site', site_edit['code_site'] if site_edit else '', required=True)}
                {field('nom_site', 'Nom de site', site_edit['nom_site'] if site_edit else '', required=True)}
                {field('region', 'Région', site_edit['region'] if site_edit else '')}
                {field('typologie_site', 'Typologie', site_edit['typologie_site'] if site_edit else '')}
                {bet_field}
                <button type="submit">{'Enregistrer' if site_edit else 'Ajouter le site'}</button>
              </form>
              <div class="po-sites-wrap"><table class="po-sites-table"><thead><tr><th>CODE SITE</th><th>NOM DU SITE</th><th>RÉGION</th><th>TYPOLOGIE</th><th>BET</th><th>FACTURATION</th><th>ACTIONS</th></tr></thead><tbody>{''.join(site_rows_html) or '<tr><td colspan="7" class="empty">Aucun site</td></tr>'}</tbody></table></div>
              <aside class="info-banner"><span class="info-icon">i</span><span>Pour le Type NDC, le champ multi-saisie « Codes sites » affiche automatiquement le « Nombre de sites ».</span></aside>
              <script>document.querySelector('.site-toggle')?.addEventListener('click',()=>document.querySelector('.site-inline-form')?.classList.toggle('editing'));</script>
            </section>"""

        alert = f'<section class="alert">{h(message)}</section>' if message else ""
        direction_options = ''.join(f'<option value="{h(r["id"])}"{" selected" if str(r["id"])==str(direction_filter) else ""}>{h(r["direction_regionale"])}</option>' for r in directions)
        type_options = ''.join(f'<option value="{h(value)}"{" selected" if value==type_filter else ""}>{h(label)}</option>' for value,label in [('', 'Tous'),('ACQUISITION','ACQUISITION'),('CONSTRUCTION','CONSTRUCTION'),('CONST_ACQUIS','CONST/ACQUIS'),('NDC','NDC')])
        year_options = ''.join(f'<option value="{h(y)}"{" selected" if y==year_filter else ""}>{h(y)}</option>' for y in years)
        filters = f"""
          <form class="po-filterbar" method="get">
            <label class="search-field"><span class="sr-only">Recherche</span><i data-lucide="search"></i><input name="q" value="{h(search)}" placeholder="Rechercher un BC"></label>
            <label><span>Direction Mobilis</span><select name="direction"><option value="">Toutes</option>{direction_options}</select></label>
            <label><span>Type BC</span><select name="type">{type_options}</select></label>
            <label class="po-date-filter"><span>Date</span><div><i data-lucide="calendar-days"></i><select name="year"><option value="">Toutes</option>{year_options}</select></div></label>
            <span class="po-filter-spacer"></span><button class="outline-button po-filter-submit" type="submit"><i data-lucide="list-filter"></i> Filtres</button><a class="outline-button po-reset" href="/purchase-orders" title="Réinitialiser"><i data-lucide="rotate-ccw"></i></a>
          </form>
        """
        top = '<div class="page-action-row"><span></span><a class="button-link primary-blue" href="/purchase-orders?new=1"><i data-lucide="plus"></i> Nouveau BC</a></div>'
        right_panel = form_html if (edit or new_mode) else selected_panel
        content = alert + top + filters + f'<section class="panel po-master-detail"><div class="po-master-list">{listing}</div><div class="po-detail-pane">{right_panel}</div></section>'
        self.respond(layout("Bons de commande", content))

    def save_purchase_order_document(self):
        try:
            values, files = self.multipart_form()
            po_id = values.get("po_id", "")
            if not po_id or "bc_file" not in files:
                return self.redirect(f"/purchase-orders?view_id={quote(po_id)}&message={quote('Aucun fichier selectionne')}")
            attachment_path = save_upload(files["bc_file"], "purchase_orders", {".pdf", ".jpg", ".jpeg", ".png"})
            with db() as con:
                con.execute("UPDATE purchase_orders SET attachment_path=?, updated_by=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND deleted_at IS NULL",
                            (attachment_path, current_actor(), po_id))
            self.redirect(f"/purchase-orders?view_id={quote(po_id)}&message={quote('Document BC mis a jour')}")
        except Exception as exc:
            self.redirect(f"/purchase-orders?message={quote('Erreur document BC: ' + str(exc))}")

    def delete_purchase_order_document(self):
        query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        po_id = query.get("id", [""])[0]
        if po_id:
            with db() as con:
                con.execute("UPDATE purchase_orders SET attachment_path=NULL, updated_by=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND deleted_at IS NULL",
                            (current_actor(), po_id))
        self.redirect(f"/purchase-orders?view_id={quote(po_id)}&message={quote('Document BC supprime')}")

    def save_purchase_order(self):
        try:
            values, files = self.multipart_form()
            attachment_path = save_upload(files["bc_file"], "purchase_orders", {".pdf", ".jpg", ".jpeg", ".png"}) if "bc_file" in files else ""
            codes = parse_code_sites(values.get("code_sites", ""))
            montant_ttc = parse_amount(values.get("montant_ttc"))
            with db() as con:
                if values.get("id"):
                    current = con.execute("SELECT attachment_path FROM purchase_orders WHERE id=?", (values.get("id"),)).fetchone()
                    con.execute("""
                        UPDATE purchase_orders
                        SET numero_bc=?, date_bc=?, mobilis_direction_id=?, type_bc=?, objet=?, montant_ttc=?, attachment_path=?, updated_by=?, updated_at=CURRENT_TIMESTAMP
                        WHERE id=?
                    """, (values.get("numero_bc"), values.get("date_bc") or None, values.get("mobilis_direction_id"), values.get("type_bc"), values.get("objet", ""), montant_ttc, attachment_path or current["attachment_path"], current_actor(), values.get("id")))
                    po_id = values.get("id")
                else:
                    cursor = con.execute("""
                        INSERT INTO purchase_orders(numero_bc,date_bc,mobilis_direction_id,type_bc,objet,montant_ttc,attachment_path,created_by,updated_by)
                        VALUES(?,?,?,?,?,?,?,?,?)
                    """, (values.get("numero_bc"), values.get("date_bc") or None, values.get("mobilis_direction_id"), values.get("type_bc"), values.get("objet", ""), montant_ttc, attachment_path, current_actor(), current_actor()))
                    po_id = cursor.lastrowid
                if values.get("type_bc") == "NDC" and not values.get("id"):
                    con.executemany("INSERT INTO sites(purchase_order_id,code_site,nom_site,created_by,updated_by) VALUES(?,?,?,?,?)", [(po_id, code, code, current_actor(), current_actor()) for code in codes])
            self.redirect("/purchase-orders?message=Bon de commande ajoute")
        except Exception as exc:
            self.redirect(f"/purchase-orders?message={quote('Erreur ajout BC: ' + str(exc))}")

    def sites(self):
        query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        edit_id = query.get("edit_id", [""])[0]
        with db() as con:
            edit = con.execute("SELECT * FROM sites WHERE id=? AND deleted_at IS NULL", (edit_id,)).fetchone() if edit_id else None
            pos = con.execute("SELECT id, numero_bc FROM purchase_orders WHERE deleted_at IS NULL ORDER BY id DESC").fetchall()
            rows = con.execute("""
                SELECT s.*, po.numero_bc
                FROM sites s
                JOIN purchase_orders po ON po.id = s.purchase_order_id
                WHERE s.deleted_at IS NULL
                ORDER BY s.id DESC
            """).fetchall()
        form_html = f"""
        <form method="post" class="panel grid">
          <input type="hidden" name="id" value="{h(edit['id'] if edit else '')}">
          {select_field('purchase_order_id', 'Bon de commande', [(r["id"], r["numero_bc"]) for r in pos], edit['purchase_order_id'] if edit else '')}
          {field('code_site', 'Code de site', edit['code_site'] if edit else '', required=True)}
          {field('nom_site', 'Nom de site', edit['nom_site'] if edit else '', required=True)}
          {field('region', 'Region', edit['region'] if edit else '')}
          {field('typologie_site', 'Typologie de site', edit['typologie_site'] if edit else '')}
          <button type="submit">{'Modifier' if edit else 'Ajouter'}</button>
        </form>"""
        listing = table(["Code site", "Nom site", "Region", "Typologie", "BC", "MAJ par", "MAJ le", "Actions"], [
            [h(r["code_site"]), h(r["nom_site"]), h(r["region"]), h(r["typologie_site"]), h(r["numero_bc"]), h(r["updated_by"] or r["created_by"]), h(r["updated_at"]), action_links("/sites", r["id"])] for r in rows
        ])
        self.respond(layout("Sites", form_html + listing))

    def save_site(self):
        values = self.form()
        with db() as con:
            if values.get("id"):
                con.execute("""
                    UPDATE sites
                    SET purchase_order_id=?, code_site=?, nom_site=?, region=?, typologie_site=?, bet=?, updated_by=?, updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                """, (values.get("purchase_order_id"), values.get("code_site"), values.get("nom_site"), values.get("region", ""), values.get("typologie_site", ""), values.get("bet", ""), current_actor(), values.get("id")))
            else:
                con.execute("""
                    INSERT INTO sites(purchase_order_id,code_site,nom_site,region,typologie_site,bet,created_by,updated_by)
                    VALUES(?,?,?,?,?,?,?,?)
                """, (values.get("purchase_order_id"), values.get("code_site"), values.get("nom_site"), values.get("region", ""), values.get("typologie_site", ""), values.get("bet", ""), current_actor(), current_actor()))
        self.redirect(f"/purchase-orders?view_id={quote(values.get('purchase_order_id', ''))}&message=Site enregistre")

    def invoices(self):
        message = parse_qs(self.path.split("?", 1)[1]).get("message", [""])[0] if "?" in self.path else ""
        query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        edit_id = query.get("edit_id", [""])[0]
        invoice_search = query.get("q", [""])[0].strip()
        invoice_type_filter = query.get("list_type", [""])[0].strip()
        invoice_site_filter = query.get("list_site", [""])[0].strip()
        invoice_date_from = query.get("date_from", [""])[0].strip()
        invoice_date_to = query.get("date_to", [""])[0].strip()
        list_where = ["i.deleted_at IS NULL"]
        list_params = []
        if invoice_search:
            list_where.append("(i.invoice_number LIKE ? OR po.numero_bc LIKE ? OR s.code_site LIKE ?)")
            list_params.extend([f"%{invoice_search}%"] * 3)
        if invoice_type_filter:
            list_where.append("i.invoice_type=?")
            list_params.append(invoice_type_filter)
        if invoice_site_filter:
            list_where.append("i.site_id=?")
            list_params.append(invoice_site_filter)
        if invoice_date_from:
            list_where.append("i.invoice_date>=?")
            list_params.append(invoice_date_from)
        if invoice_date_to:
            list_where.append("i.invoice_date<=?")
            list_params.append(invoice_date_to)
        with db() as con:
            edit = con.execute("SELECT * FROM invoices WHERE id=? AND deleted_at IS NULL", (edit_id,)).fetchone() if edit_id else None
            edit_lines = con.execute("SELECT article_number, quantite FROM invoice_lines WHERE invoice_id=? ORDER BY id", (edit_id,)).fetchall() if edit_id else []
            edit_ndc_sites = {row[0] for row in con.execute("SELECT site_id FROM invoice_sites WHERE invoice_id=?", (edit_id,)).fetchall()} if edit_id else set()
            purchase_orders = con.execute("SELECT id, numero_bc, type_bc FROM purchase_orders WHERE deleted_at IS NULL ORDER BY id DESC").fetchall()
            sites = con.execute("""
                SELECT s.id, s.code_site, s.nom_site, s.purchase_order_id, po.numero_bc
                FROM sites s
                JOIN purchase_orders po ON po.id = s.purchase_order_id
                WHERE s.deleted_at IS NULL
                ORDER BY po.numero_bc, s.code_site
            """).fetchall()
            ndc_used = {row[0] for row in con.execute("SELECT site_id FROM invoice_sites").fetchall()}
            rows = con.execute("""
                SELECT i.*, po.numero_bc, s.code_site
                FROM invoices i
                JOIN purchase_orders po ON po.id = i.purchase_order_id
                LEFT JOIN sites s ON s.id = i.site_id
                WHERE """ + " AND ".join(list_where) + """
                ORDER BY i.id DESC
            """, list_params).fetchall()
        po_option_html = []
        for r in purchase_orders:
            selected = " selected" if edit and str(edit["purchase_order_id"]) == str(r["id"]) else ""
            po_option_html.append(f'<option value="{r["id"]}" data-type="{h(r["type_bc"])}"{selected}>{h(r["numero_bc"])} - {h(display_type(r["type_bc"]))}</option>')
        site_option_html = []
        for r in sites:
            selected = " selected" if edit and str(edit["site_id"] or "") == str(r["id"]) else ""
            site_option_html.append(f'<option value="{r["id"]}" data-po="{r["purchase_order_id"]}"{selected}>{h(r["code_site"])} - {h(r["nom_site"])}</option>')
        ndc_checks = []
        for site in sites:
            checked = " checked" if site["id"] in edit_ndc_sites else ""
            disabled = " disabled" if site["id"] in ndc_used and site["id"] not in edit_ndc_sites else ""
            label = f'{site["numero_bc"]} / {site["code_site"]} - {site["nom_site"]}'
            ndc_checks.append(f'<label class="check-row" data-po="{site["purchase_order_id"]}"><input type="checkbox" name="ndc_site_ids" value="{site["id"]}"{checked}{disabled}><span>{h(label)}</span></label>')
        form_html = f"""
        <form method="post" class="panel invoice-form" id="invoice-form">
          <input type="hidden" name="id" value="{h(edit['id'] if edit else '')}">
          <div class="invoice-head grid">
            {field('invoice_number', 'N° Facture', edit['invoice_number'] if edit else '', required=True)}
            {field('invoice_date', 'Date facture', edit['invoice_date'] if edit else '', field_type='date')}
            {select_field('invoice_type', 'Type facture', [('ACQUISITION','ACQUISITION'),('CONSTRUCTION','CONSTRUCTION'),('CONST_ACQUIS','CONST/ACQUIS'),('NDC','NDC')], edit['invoice_type'] if edit else '')}
            <label><span>Bon de commande</span><select name="purchase_order_id" required>{''.join(po_option_html)}</select></label>
            <label class="site-head-field"><span>Site</span><select name="site_id" required>{''.join(site_option_html)}</select></label>
          </div>
          <section class="regular-fields wide">
            <input type="hidden" name="lines" id="invoice-lines">
            <div class="invoice-entry">
              <table class="entry-table">
                <thead>
                  <tr>
                    <th>N° Article</th>
                    <th>Désignation</th>
                    <th>Unité</th>
                    <th>PU/HT</th>
                    <th>Quantité</th>
                    <th>Montant HT</th>
                    <th title="Actions">⚙</th>
                  </tr>
                </thead>
                <tbody id="invoice-lines-body"></tbody>
              </table>
              <div class="invoice-compose-footer">
                <div class="compose-actions"><button type="button" id="add-line" hidden>＋ Ajouter ligne</button><button type="submit" class="create-invoice-button"><i data-lucide="file-plus-2"></i>{'Modifier la facture' if edit else 'Créer la facture'}</button><button type="button" class="outline-button" id="reset-lines" title="Réinitialiser"><i data-lucide="rotate-ccw"></i></button></div>
                <dl class="live-totals">
                  <div><dt>Total HT</dt><dd id="live-total-ht">0,00</dd></div>
                  <div><dt>RG 5%</dt><dd id="live-rg">0,00</dd></div>
                  <div><dt>Montant HT après RG</dt><dd id="live-after-rg">0,00</dd></div>
                  <div><dt>TVA 19%</dt><dd id="live-tva">0,00</dd></div>
                  <div class="grand-total"><dt>Total TTC</dt><dd id="live-ttc">0,00</dd></div>
                </dl>
              </div>
            </div>
          </section>
          <section class="ndc-fields wide">
            <div class="check-list">{"".join(ndc_checks) if ndc_checks else '<p class="empty">Aucun site disponible</p>'}</div>
            <small id="ndc-count">Nombre de sites: 0</small>
          </section>
        </form>
        <script>
          const initialLines = {json.dumps([{"article": r["article_number"], "quantity": r["quantite"]} for r in edit_lines], ensure_ascii=False)};
          const invoiceType = document.querySelector('select[name="invoice_type"]');
          const regularFields = document.querySelector('.regular-fields');
          const ndcFields = document.querySelector('.ndc-fields');
          const ndcCount = document.querySelector('#ndc-count');
          function syncInvoiceForm() {{
            const isNdc = invoiceType.value === 'NDC';
            regularFields.style.display = isNdc ? 'none' : 'grid';
            ndcFields.style.display = isNdc ? 'block' : 'none';
            document.querySelector('.site-head-field').style.display = isNdc ? 'none' : 'grid';
            const checked = document.querySelectorAll('input[name="ndc_site_ids"]:checked').length;
            ndcCount.textContent = `Nombre de sites: ${{checked}}`;
          }}
          invoiceType.addEventListener('change', syncInvoiceForm);
          document.querySelectorAll('input[name="ndc_site_ids"]').forEach(x => x.addEventListener('change', syncInvoiceForm));
          syncInvoiceForm();

          const linesBody = document.querySelector('#invoice-lines-body');
          const linesInput = document.querySelector('#invoice-lines');
          const addLineButton = document.querySelector('#add-line');
          const invoiceForm = document.querySelector('#invoice-form');

          function formatMoney(value) {{
            return Number(value || 0).toLocaleString('fr-FR', {{ minimumFractionDigits: 2, maximumFractionDigits: 2 }});
          }}

          function allowedCategories(type) {{
            if (type === 'ACQUISITION') return ['acquisition'];
            if (type === 'CONSTRUCTION') return ['fourniture', 'prestation'];
            if (type === 'CONST_ACQUIS') return ['acquisition', 'fourniture', 'prestation'];
            if (type === 'NDC') return ['ndc'];
            return [];
          }}

          function syncTypeFromPurchaseOrder() {{
            const poSelect = document.querySelector('select[name="purchase_order_id"]');
            const type = poSelect.options[poSelect.selectedIndex]?.dataset.type || '';
            if (['ACQUISITION', 'CONSTRUCTION', 'CONST_ACQUIS', 'NDC'].includes(type)) {{
              invoiceType.value = type;
              syncInvoiceForm();
            }}
            const poId = poSelect.value;
            const siteSelect = document.querySelector('select[name="site_id"]');
            let firstVisible = null;
            Array.from(siteSelect.options).forEach(option => {{
              const visible = option.dataset.po === poId;
              option.hidden = !visible;
              option.disabled = !visible;
              if (visible && !firstVisible) firstVisible = option;
            }});
            if (siteSelect.selectedOptions[0]?.disabled && firstVisible) firstVisible.selected = true;
            document.querySelectorAll('.check-row[data-po]').forEach(row => {{
              row.style.display = row.dataset.po === poId ? 'flex' : 'none';
              if (row.dataset.po !== poId) row.querySelector('input').checked = false;
            }});
          }}

          function addInvoiceLine(shouldFocus = true) {{
            const tr = document.createElement('tr');
            tr.innerHTML = `
              <td><input class="article-input" type="number" min="1"></td>
              <td class="designation-cell"><input class="designation-ghost" type="text" placeholder="Rechercher un article..." tabindex="-1" readonly></td>
              <td class="unite-cell"><select class="unite-ghost" tabindex="-1" disabled><option></option></select></td>
              <td class="pu-cell" data-value="0">0,00</td>
              <td><input class="quantity-input" type="number" min="0.01" step="0.01"></td>
              <td class="montant-cell" data-value="0">0,00</td>
              <td><button type="button" class="remove-line" title="Supprimer la ligne" aria-label="Supprimer la ligne"><i data-lucide="trash-2"></i></button></td>
            `;
            linesBody.appendChild(tr);
            if (window.lucide) lucide.createIcons({{nodes: [tr]}});
            if (shouldFocus) tr.querySelector('.article-input').focus();
          }}

          async function loadArticle(input) {{
            const tr = input.closest('tr');
            const article = input.value.trim();
            if (!article) return;
            const response = await fetch(`/bpu/item?article_number=${{encodeURIComponent(article)}}`);
            if (!response.ok) {{
              tr.querySelector('.designation-cell').textContent = 'Article introuvable';
              tr.querySelector('.unite-cell').textContent = '';
              tr.querySelector('.pu-cell').textContent = '';
              tr.querySelector('.pu-cell').dataset.value = '0';
              return;
            }}
            const item = await response.json();
            const allowed = allowedCategories(invoiceType.value);
            if (!allowed.includes(item.categorie)) {{
              tr.querySelector('.designation-cell').textContent = `Article non autorisé pour ${{invoiceType.value}} (${{item.categorie}})`;
              tr.querySelector('.unite-cell').textContent = '';
              tr.querySelector('.pu-cell').textContent = '';
              tr.querySelector('.pu-cell').dataset.value = '0';
              tr.querySelector('.montant-cell').textContent = '';
              tr.querySelector('.quantity-input').value = '';
              input.focus();
              input.select();
              return;
            }}
            tr.querySelector('.designation-cell').textContent = item.designation;
            tr.querySelector('.unite-cell').textContent = item.unite;
            tr.querySelector('.pu-cell').dataset.value = item.pu_ht;
            tr.querySelector('.pu-cell').textContent = formatMoney(item.pu_ht);
            updateLineAmount(tr);
            tr.querySelector('.quantity-input').focus();
            tr.querySelector('.quantity-input').select();
          }}

          function updateLineAmount(tr) {{
            const pu = Number(tr.querySelector('.pu-cell').dataset.value || 0);
            const quantity = Number(tr.querySelector('.quantity-input').value || 0);
            const amount = pu * quantity;
            tr.querySelector('.montant-cell').dataset.value = amount;
            tr.querySelector('.montant-cell').textContent = amount ? formatMoney(amount) : '';
            updateFormTotals();
          }}

          function updateFormTotals() {{
            const totalHt = Array.from(linesBody.querySelectorAll('.montant-cell')).reduce((sum, cell) => sum + Number(cell.dataset.value || 0), 0);
            const rg = totalHt * 0.05;
            const afterRg = totalHt - rg;
            const tva = afterRg * 0.19;
            const ttc = afterRg + tva;
            document.querySelector('#live-total-ht').textContent = formatMoney(totalHt);
            document.querySelector('#live-rg').textContent = formatMoney(rg);
            document.querySelector('#live-after-rg').textContent = formatMoney(afterRg);
            document.querySelector('#live-tva').textContent = formatMoney(tva);
            document.querySelector('#live-ttc').textContent = formatMoney(ttc);
          }}

          function serializeLines() {{
            const lines = [];
            linesBody.querySelectorAll('tr').forEach(tr => {{
              const article = tr.querySelector('.article-input').value.trim();
              const quantity = tr.querySelector('.quantity-input').value.trim();
              if (article && quantity) lines.push(`${{article}},${{quantity}}`);
            }});
            linesInput.value = lines.join('\\n');
          }}

          linesBody.addEventListener('keydown', event => {{
            if (event.target.classList.contains('article-input') && (event.key === 'Enter' || event.key === 'Tab')) {{
              event.preventDefault();
              loadArticle(event.target);
            }}
            if (event.target.classList.contains('quantity-input') && event.key === 'Enter') {{
              event.preventDefault();
              addInvoiceLine();
            }}
          }});
          linesBody.addEventListener('change', event => {{
            if (event.target.classList.contains('article-input')) loadArticle(event.target);
            if (event.target.classList.contains('quantity-input')) updateLineAmount(event.target.closest('tr'));
          }});
          linesBody.addEventListener('input', event => {{
            if (event.target.classList.contains('quantity-input')) updateLineAmount(event.target.closest('tr'));
          }});
          linesBody.addEventListener('click', event => {{
            if (event.target.classList.contains('remove-line')) {{
              event.target.closest('tr').remove();
              updateFormTotals();
            }}
          }});
          addLineButton.addEventListener('click', addInvoiceLine);
          document.querySelector('#reset-lines').addEventListener('click', () => {{ linesBody.innerHTML=''; addInvoiceLine(); updateFormTotals(); }});
          document.querySelector('select[name="purchase_order_id"]').addEventListener('change', syncTypeFromPurchaseOrder);
          invoiceForm.addEventListener('submit', event => {{
            serializeLines();
            if (invoiceType.value !== 'NDC' && !linesInput.value) {{
              event.preventDefault();
              alert('Ajoutez au moins une ligne article avec quantite.');
            }}
          }});
          syncTypeFromPurchaseOrder();
          if (initialLines.length) {{
            initialLines.forEach(line => {{
              addInvoiceLine(false);
              const tr = linesBody.querySelector('tr:last-child');
              tr.querySelector('.article-input').value = line.article;
              tr.querySelector('.quantity-input').value = line.quantity;
              loadArticle(tr.querySelector('.article-input'));
            }});
            addInvoiceLine(false);
          }} else {{
            // Four visible entry rows reproduce the reference invoice workspace while
            // serialization continues to ignore completely empty rows.
            for (let index = 0; index < 4; index += 1) addInvoiceLine(index === 0);
          }}
        </script>"""
        listing = table(["N° Facture", "Type", "BC", "Site(s)", "Total HT", "TTC", "Sorties", "MAJ par", "MAJ le", "Actions"], [
            [
                h(r["invoice_number"]),
                h(display_type(r["invoice_type"])),
                h(r["numero_bc"]),
                h(r["code_site"] or "NDC multi-sites"),
                money(r["total_ht"]),
                money(r["total_ttc"]),
                f'<span class="action-icons"><a class="button-link icon-link output-link" title="Generer et ouvrir Facture" href="/invoices/pdf-ready/facture/{r["id"]}">FACT</a><a class="button-link icon-link output-link" title="Generer et ouvrir Devis Quantitatif" href="/invoices/pdf-ready/devis-quantitatif/{r["id"]}">DQ</a><a class="button-link icon-link output-link" title="Generer et ouvrir Devis Estimatif" href="/invoices/pdf-ready/devis-estimatif/{r["id"]}">DE</a></span>',
                h(r["updated_by"] or r["created_by"]),
                h(r["updated_at"]),
                f'<span class="action-icons"><a class="button-link icon-link" href="/invoices/pdf-ready/facture/{r["id"]}" title="Ouvrir"><i data-lucide="external-link"></i></a><a class="button-link icon-link" href="/invoices?edit_id={r["id"]}" title="Modifier"><i data-lucide="pencil"></i></a><a class="button-link icon-link danger-link" href="/invoices/delete?id={r["id"]}" title="Supprimer" onclick="return confirm(\'Supprimer cette facture ?\')"><i data-lucide="trash-2"></i></a></span>',
            ] for r in rows
        ])
        alert = f'<section class="alert">{h(message)}</section>' if message else ""
        date_from_type = "date" if invoice_date_from else "text"
        date_to_type = "date" if invoice_date_to else "text"
        list_toolbar = f"""
          <header class="section-header invoices-title"><h3>Factures enregistrées</h3></header>
          <form class="invoice-list-toolbar" method="get">
            <label class="search-field"><span class="sr-only">Recherche</span><i data-lucide="search"></i><input name="q" value="{h(invoice_search)}" placeholder="Rechercher..."></label>
            <label class="date-filter-field"><span class="sr-only">Du</span><input name="date_from" value="{h(invoice_date_from)}" type="{date_from_type}" placeholder="Du" title="Du" onfocus="this.type='date'" onblur="if(!this.value)this.type='text'"></label>
            <label class="date-filter-field"><span class="sr-only">Au</span><input name="date_to" value="{h(invoice_date_to)}" type="{date_to_type}" placeholder="Au" title="Au" onfocus="this.type='date'" onblur="if(!this.value)this.type='text'"></label>
            {select_field('list_type','Type',[('', 'Type facture'),('ACQUISITION','ACQUISITION'),('CONSTRUCTION','CONSTRUCTION'),('CONST_ACQUIS','CONST/ACQUIS'),('NDC','NDC')], invoice_type_filter)}
            {select_field('list_site','Site',[('', 'Site')] + [(r['id'], f'{r["code_site"]} - {r["nom_site"]}') for r in sites], invoice_site_filter)}
            <a class="outline-button reset-list-button" href="/invoices"><i data-lucide="rotate-ccw"></i>Réinitialiser</a>
            <span class="invoice-toolbar-spacer" aria-hidden="true"></span>
            <button class="outline-button export-list-button" type="button" title="Exporter la liste"><i data-lucide="download"></i>Exporter</button>
          </form>
          <script>document.querySelectorAll('.invoice-list-toolbar input[name="date_from"], .invoice-list-toolbar input[name="date_to"], .invoice-list-toolbar select').forEach(element => element.addEventListener('change', () => element.form.submit()));</script>
        """
        pager = f'<footer class="pager"><span>Affichage 1 à {len(rows)} sur {len(rows)} entrées</span><span class="pager-pages"><select><option>10</option></select><span>‹</span><span class="current">1</span><span>›</span></span></footer>'
        # The dedicated Table Facturation screen is the single professional
        # listing/monitoring surface.  Keep this page focused on creating and
        # editing one invoice to avoid duplicate controls and unnecessary page
        # height/scrolling.
        self.respond(layout("Factures", alert + form_html))

    def export_invoice(self):
        query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        invoice_id = int(query.get("id", ["0"])[0])
        invoice_number = invoice_export_data(invoice_id)[0]["invoice_number"]
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            temp_path = Path(tmp.name)
        python_exe = BUNDLED_PYTHON if BUNDLED_PYTHON.exists() else Path(sys.executable)
        result = subprocess.run(
            [str(python_exe), str(ROOT_DIR / "scripts" / "export_invoice_template.py"), str(invoice_id), str(temp_path)],
            cwd=ROOT_DIR,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            temp_path.unlink(missing_ok=True)
            return self.respond(f"Erreur export Excel: {h(result.stderr or result.stdout)}", status=500)
        content = temp_path.read_bytes()
        temp_path.unlink(missing_ok=True)
        filename = f"Facture_{invoice_number}.xlsx".replace("/", "-").replace("\\", "-")
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def parse_pdf_request(self, path):
        parts = path.strip("/").split("/")
        if len(parts) == 5 and parts[:2] == ["invoices", "pdf"] and parts[2] in {"facture", "devis-quantitatif", "devis-estimatif"}:
            try:
                invoice_id = int(parts[3])
            except ValueError:
                return None
            if parts[4].lower().endswith(".pdf"):
                return parts[2], invoice_id
        return None

    def parse_pdf_ready_request(self, path):
        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[:2] == ["invoices", "pdf-ready"] and parts[2] in {"facture", "devis-quantitatif", "devis-estimatif"}:
            try:
                return parts[2], int(parts[3])
            except ValueError:
                return None
        return None

    def pdf_filename(self, document, invoice_id):
        invoice_number = invoice_export_data(invoice_id)[0]["invoice_number"]
        prefixes = {
            "facture": "Facture",
            "devis-quantitatif": "Devis_Quantitatif",
            "devis-estimatif": "Devis_Estimatif",
        }
        prefix = prefixes.get(document, "Document")
        return f"{prefix}_{invoice_number}.pdf".replace("/", "-").replace("\\", "-")

    def pdf_head(self, document, invoice_id=None):
        if invoice_id is None:
            query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            invoice_id = int(query.get("id", ["0"])[0])
        filename = self.pdf_filename(document, invoice_id)
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def render_preview_pdf_file(self, document, invoice_id, output_path, server_port=None):
        preview_path = f"/invoices/preview/{document}?id={invoice_id}"
        url = f"http://127.0.0.1:{server_port or self.server.server_port}{preview_path}"
        node_exe = BUNDLED_NODE if BUNDLED_NODE.exists() else Path("node")
        env = os.environ.copy()
        if NODE_MODULES.exists():
            env["NODE_PATH"] = str(NODE_MODULES)
        result = subprocess.run(
            [str(node_exe), str(ROOT_DIR / "scripts" / "render_pdf.js"), url, str(output_path)],
            cwd=ROOT_DIR,
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
        if result.returncode != 0:
            try:
                output_path.unlink(missing_ok=True)
            except PermissionError:
                pass
            raise RuntimeError(result.stderr or result.stdout or "Erreur export PDF")

    def regenerate_invoice_pdfs(self, invoice_id):
        server_port = self.server.server_port

        def worker():
            for document in ("facture", "devis-quantitatif", "devis-estimatif"):
                try:
                    base_filename = self.pdf_filename(document, invoice_id)
                    stem = Path(base_filename).stem
                    suffix = Path(base_filename).suffix
                    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
                    output_path = (EXPORT_DIR / f"{stem}_{uuid4().hex[:8]}{suffix}").resolve()
                    self.render_preview_pdf_file(document, invoice_id, output_path, server_port=server_port)
                    cleanup_old_exports(stem, suffix)
                except Exception as error:
                    print(f"PDF regeneration failed for invoice {invoice_id} ({document}): {error}", file=sys.stderr)

        threading.Thread(target=worker, daemon=True).start()

    def save_preview_pdf_to_downloads(self, document, invoice_id):
        filename = self.pdf_filename(document, invoice_id)
        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        output_path = DOWNLOAD_DIR / filename
        if output_path.exists():
            stem = output_path.stem
            suffix = output_path.suffix
            counter = 2
            while output_path.exists():
                output_path = DOWNLOAD_DIR / f"{stem} ({counter}){suffix}"
                counter += 1
        try:
            self.render_preview_pdf_file(document, invoice_id, output_path)
        except RuntimeError as error:
            return self.respond(f"Erreur export PDF: {h(str(error))}", status=500)
        return self.redirect(f"/invoices?message=PDF enregistre: {quote(str(output_path))}")

    def open_preview_pdf(self, document, invoice_id):
        base_filename = self.pdf_filename(document, invoice_id)
        stem = Path(base_filename).stem
        suffix = Path(base_filename).suffix
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        source_timestamp = pdf_source_timestamp(document, invoice_id)
        existing_files = sorted(
            (path for path in EXPORT_DIR.glob(f"{stem}_*{suffix}") if path.stat().st_mtime >= source_timestamp),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if existing_files:
            output_path = existing_files[0].resolve()
        else:
            filename = f"{stem}_{uuid4().hex[:8]}{suffix}"
            output_path = (EXPORT_DIR / filename).resolve()
            try:
                self.render_preview_pdf_file(document, invoice_id, output_path)
            except RuntimeError as error:
                return self.respond(f"Erreur export PDF: {h(str(error))}", status=500)
        def open_later():
            open_pdf_with_system_viewer(output_path)
        threading.Timer(0.1, open_later).start()
        cleanup_old_exports(stem, suffix)
        return self.redirect(f"/invoices?message=PDF ouvert: {quote(str(output_path))}")

    def export_preview_pdf(self, document, invoice_id=None):
        if invoice_id is None:
            query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            invoice_id = int(query.get("id", ["0"])[0])
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            temp_path = Path(tmp.name)
        try:
            self.render_preview_pdf_file(document, invoice_id, temp_path)
        except RuntimeError as error:
            return self.respond(f"Erreur export PDF: {h(str(error))}", status=500)
        content = temp_path.read_bytes()
        temp_path.unlink(missing_ok=True)
        filename = self.pdf_filename(document, invoice_id)
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def preview_facture(self):
        query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        invoice_id = int(query.get("id", ["0"])[0])
        invoice, company, contract, lines, ndc_sites = invoice_export_data(invoice_id)
        first_lines = [line for line in lines if line["categorie_snapshot"] in {"fourniture", "acquisition", "ndc"}]
        second_lines = [line for line in lines if line["categorie_snapshot"] == "prestation"]
        client_logo = f'/{h(invoice["client_logo_path"])}' if invoice["client_logo_path"] else ""
        company_logo = f'/{h(company["logo_path"])}' if company["logo_path"] else ""
        site_label = invoice["code_site"] or ", ".join(site["code_site"] for site in ndc_sites)
        site_name = invoice["nom_site"] or f"{len(ndc_sites)} sites NDC"

        def logo_html(src, label):
            return f'<img src="{src}" alt="{h(label)}">' if src else ""

        def section_rows(label, rows, prefix):
            if not rows:
                return ""
            body = [f'<tr class="section"><td>{h(prefix)}</td><td colspan="5">{h(label)}</td></tr>']
            for line in rows:
                body.append(
                    "<tr>"
                    f"<td>{h(line['article_number'])}</td>"
                    f"<td>{h(line['designation_snapshot'])}</td>"
                    f"<td>{h(line['unite_snapshot'])}</td>"
                    f"<td>{h(line['quantite'])}</td>"
                    f"<td>{money(line['pu_ht_snapshot'])}</td>"
                    f"<td>{money(line['montant_ht'])}</td>"
                    "</tr>"
                )
            total = sum(float(line["montant_ht"] or 0) for line in rows)
            body.append(f'<tr class="section-total"><td colspan="5">TOTAL {h(label)} HT</td><td>{money(total)}</td></tr>')
            return "".join(body)

        rows_html = section_rows("FOURNITURES", first_lines, "A") + section_rows("PRESTATION", second_lines, "B")
        amount_words = (invoice["montant_en_lettres"] or amount_to_french(invoice["total_ttc"])).upper()
        html = f"""<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <title>Facture - {h(invoice['invoice_number'])}</title>
  <style>
    @page {{ size: A4 portrait; margin: 12mm 13mm; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #2b2b2b; font-family: "Times New Roman", serif; color: #000; }}
    .page {{ width: 794px; min-height: 1123px; margin: 18px auto; background: white; padding: 24px 28px; }}
    .logos {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 26px; }}
    .logos img {{ width: 170px; height: 85px; object-fit: contain; }}
    .top-info {{ display: grid; grid-template-columns: 1fr 1fr; gap: 70px; font-size: 13px; font-weight: 700; line-height: 1.25; }}
    .title {{ margin: 18px 0; background: #d9d9d9; text-align: center; font-weight: 700; padding: 9px; }}
    .site-info {{ font-size: 13px; font-weight: 700; line-height: 1.25; margin-bottom: 18px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 11px; }}
    th, td {{ border: 1px solid #000; padding: 3px 5px; vertical-align: middle; }}
    th {{ background: #9dc3e6; text-align: center; font-weight: 700; }}
    td:nth-child(1) {{ width: 40px; text-align: center; }}
    td:nth-child(3) {{ width: 52px; text-align: center; font-weight: 700; }}
    td:nth-child(4) {{ width: 72px; text-align: center; }}
    td:nth-child(5), td:nth-child(6) {{ width: 112px; text-align: right; }}
    .section td, .section-total td {{ background: #92d050; font-weight: 700; text-align: center; }}
    .section-total td {{ text-align: right; }}
    .totals {{ margin-top: 22px; margin-left: auto; width: 295px; font-size: 12px; font-weight: 700; }}
    .totals div {{ display: grid; grid-template-columns: 1fr 112px; border: 1px solid #000; border-bottom: 0; }}
    .totals div:last-child {{ border-bottom: 1px solid #000; }}
    .totals span {{ padding: 3px 6px; text-align: right; }}
    .totals span:last-child {{ border-left: 1px solid #000; }}
    .amount-label {{ margin-top: 20px; font-size: 11px; text-decoration: underline; }}
    .amount-words {{ margin-top: 12px; font-size: 14px; font-weight: 700; }}
    .signature {{ margin-top: 34px; text-align: right; font-weight: 700; }}
    @media print {{ body {{ background: white; }} .page {{ margin: 0; width: auto; min-height: auto; }} }}
  </style>
</head>
<body>
  <main class="page">
    <section class="logos"><div>{logo_html(client_logo, 'Mobilis')}</div><div>{logo_html(company_logo, company['nom'] or 'Entreprise')}</div></section>
    <section class="top-info">
      <div>RGC: {h(company['rgc'])}<br>NIF: {h(company['nif'])}<br>ART: {h(company['art'])}<br>ADRESSE: {h(company['adresse'])}<br>N° COMPTE: {h(company['numero_compte'])}</div>
      <div>DOIT : {h(invoice['doit_nom'])}<br>{h(invoice['direction_regionale'])}<br>{h(invoice['client_adresse'])}<br>RGC N°: {h(invoice['client_rgc'])}<br>NIF N°: {h(invoice['client_nif'])}</div>
    </section>
    <section class="title">FACTURE N° : {h(invoice['invoice_number'])}</section>
    <section class="site-info">
      Référence Contrat: {h(contract['reference_contrat'])}<br>
      Code de site: {h(site_label)}<br>
      Nom de site: {h(site_name)}<br>
      Région: {h(invoice['region'] or '')}<br>
      Typologie de site: {h(invoice['typologie_site'] or '')}<br>
      Bon de commande: {h(invoice['numero_bc'])}
    </section>
    <table><thead><tr><th>N°</th><th>Désignation</th><th>Unité</th><th>Quantités</th><th>PU/HT</th><th>Montant/HT</th></tr></thead><tbody>{rows_html}</tbody></table>
    <section class="totals">
      <div><span>TOTAL EN H.T</span><span>{money(invoice['total_ht'])}</span></div>
      <div><span>RETENUE DE GARANTIE 5%</span><span>{money(invoice['retenue_garantie'])}</span></div>
      <div><span>MONTANT HT APRES RG</span><span>{money(invoice['montant_ht_apres_rg'])}</span></div>
      <div><span>T V A 19%</span><span>{money(invoice['tva'])}</span></div>
      <div><span>TOTAL EN T T C</span><span>{money(invoice['total_ttc'])}</span></div>
    </section>
    <section class="amount-label">Arrêté la présente Facture à la somme de:</section>
    <section class="amount-words">{h(amount_words)}</section>
    <section class="signature">L'ENTREPRISE/{h(company['nom'])}</section>
  </main>
</body>
</html>"""
        self.respond(html)

    def preview_devis_quantitatif(self):
        query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        invoice_id = int(query.get("id", ["0"])[0])
        invoice, company, contract, lines, ndc_sites = invoice_export_data(invoice_id)
        site_label = invoice["code_site"] or ", ".join(site["code_site"] for site in ndc_sites)
        site_name = invoice["nom_site"] or f"{len(ndc_sites)} sites NDC"
        first_lines = [line for line in lines if line["categorie_snapshot"] in {"fourniture", "acquisition", "ndc"}]
        second_lines = [line for line in lines if line["categorie_snapshot"] == "prestation"]
        client_logo = f'/{h(invoice["client_logo_path"])}' if invoice["client_logo_path"] else ""
        company_logo = f'/{h(company["logo_path"])}' if company["logo_path"] else ""

        def logo_html(src, label):
            return f'<img src="{src}" alt="{h(label)}">' if src else ""

        def section_rows(label, rows, prefix):
            if not rows:
                return ""
            body = [f'<tr class="section"><td>{h(prefix)}</td><td colspan="3">{h(label)}</td></tr>']
            for line in rows:
                body.append(
                    "<tr>"
                    f"<td>{h(line['article_number'])}</td>"
                    f"<td>{h(line['designation_snapshot'])}</td>"
                    f"<td>{h(line['unite_snapshot'])}</td>"
                    f"<td>{h(line['quantite'])}</td>"
                    "</tr>"
                )
            return "".join(body)

        rows_html = section_rows("FOURNITURES", first_lines, "A") + section_rows("PRESTATION", second_lines, "B")
        html = f"""<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <title>Devis Quantitatif - {h(invoice['invoice_number'])}</title>
  <style>
    @page {{ size: A4 portrait; margin: 12mm 13mm; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #2b2b2b; font-family: "Times New Roman", serif; color: #000; }}
    .toolbar {{ position: sticky; top: 0; padding: 10px; background: #111; text-align: right; }}
    .toolbar button {{ padding: 8px 14px; font-weight: 700; }}
    .page {{ width: 794px; min-height: 1123px; margin: 18px auto; background: white; padding: 58px 50px; }}
    .doc-header {{ display: grid; grid-template-columns: 164px 1fr 164px; align-items: center; gap: 18px; margin-bottom: 28px; }}
    .doc-header img {{ max-width: 164px; max-height: 86px; object-fit: contain; display: block; margin: auto; }}
    .devis-title {{ min-height: 88px; border: 2px solid #25568e; border-radius: 14px; background: #4472c4; color: white; display: grid; place-items: center; text-align: center; font: 700 18px Arial, sans-serif; line-height: 1.35; padding: 8px 16px; }}
    .devis-title span {{ display: block; white-space: nowrap; }}
    .site-info {{ font-size: 16px; font-weight: 700; line-height: 1.18; margin: 0 0 22px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ border: 1px solid #000; padding: 3px 6px; vertical-align: middle; }}
    th {{ background: #8a8a8a; text-align: center; font-weight: 700; }}
    td:nth-child(1) {{ width: 48px; text-align: center; }}
    td:nth-child(3) {{ width: 74px; text-align: center; font-weight: 700; }}
    td:nth-child(4) {{ width: 96px; text-align: center; font-size: 16px; }}
    .section td {{ background: #9db7e5; font-size: 16px; font-weight: 700; text-align: center; padding: 2px 6px; }}
    .validation {{ margin-top: 22px; display: flex; justify-content: space-between; font-weight: 700; text-decoration: underline; }}
    @media print {{
      body {{ background: white; }}
      .toolbar {{ display: none; }}
      .page {{ margin: 0; padding: 0; width: auto; min-height: auto; }}
    }}
  </style>
</head>
<body>
  <div class="toolbar"><button onclick="window.print()">Imprimer / PDF</button></div>
  <main class="page">
    <header class="doc-header">
      <div>{logo_html(client_logo, 'Mobilis')}</div>
      <div class="devis-title"><span>DEVIS QUANTITATIF {h(display_type(invoice['invoice_type']))}</span><span>ATTACHEMENT</span></div>
      <div>{logo_html(company_logo, company['nom'] or 'Entreprise')}</div>
    </header>
    <section class="site-info">
      <div>Code de site : {h(site_label)}</div>
      <div>Nom de site : {h(site_name)}</div>
      <div>Région: {h(invoice['region'] or '')}</div>
      <div>Typologie de site : {h(invoice['typologie_site'] or '')}</div>
    </section>
    <table>
      <thead><tr><th>N°</th><th>Désignations</th><th>Unité</th><th>Quantités</th></tr></thead>
      <tbody>{rows_html}</tbody>
    </table>
    <section class="validation">
      <span>VALIDATION MOBILIS</span>
      <span>VALIDATION EPE SAPTA</span>
    </section>
  </main>
</body>
</html>"""
        self.respond(html)

    def preview_devis_estimatif(self):
        query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        invoice_id = int(query.get("id", ["0"])[0])
        invoice, company, contract, lines, ndc_sites = invoice_export_data(invoice_id)
        site_label = invoice["code_site"] or ", ".join(site["code_site"] for site in ndc_sites)
        site_name = invoice["nom_site"] or f"{len(ndc_sites)} sites NDC"
        first_lines = [line for line in lines if line["categorie_snapshot"] in {"fourniture", "acquisition", "ndc"}]
        second_lines = [line for line in lines if line["categorie_snapshot"] == "prestation"]
        client_logo = f'/{h(invoice["client_logo_path"])}' if invoice["client_logo_path"] else ""
        company_logo = f'/{h(company["logo_path"])}' if company["logo_path"] else ""

        def logo_html(src, label):
            return f'<img src="{src}" alt="{h(label)}">' if src else ""

        def section_rows(label, rows, prefix):
            if not rows:
                return ""
            body = [f'<tr class="section"><td>{h(prefix)}</td><td colspan="5">{h(label)}</td></tr>']
            for line in rows:
                body.append(
                    "<tr>"
                    f"<td>{h(line['article_number'])}</td>"
                    f"<td>{h(line['designation_snapshot'])}</td>"
                    f"<td>{h(line['unite_snapshot'])}</td>"
                    f"<td>{h(line['quantite'])}</td>"
                    f"<td>{money(line['pu_ht_snapshot'])}</td>"
                    f"<td>{money(line['montant_ht'])}</td>"
                    "</tr>"
                )
            total = sum(float(line["montant_ht"] or 0) for line in rows)
            body.append(f'<tr class="section-total"><td colspan="5">{h(label)} TOTAL</td><td>{money(total)}</td></tr>')
            return "".join(body)

        rows_html = section_rows("FOURNITURES", first_lines, "A") + section_rows("PRESTATION", second_lines, "B")
        amount_words = amount_to_french(invoice["total_ht"]).upper()
        html = f"""<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <title>Devis Estimatif - {h(invoice['invoice_number'])}</title>
  <style>
    @page {{ size: A4 portrait; margin: 12mm 13mm; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #2b2b2b; font-family: "Times New Roman", serif; color: #000; }}
    .toolbar {{ position: sticky; top: 0; padding: 10px; background: #111; text-align: right; }}
    .toolbar button {{ padding: 8px 14px; font-weight: 700; }}
    .page {{ width: 794px; min-height: 1123px; margin: 18px auto; background: white; padding: 58px 50px; }}
    .doc-header {{ display: grid; grid-template-columns: 164px 1fr 164px; align-items: center; gap: 18px; margin-bottom: 28px; }}
    .doc-header img {{ max-width: 164px; max-height: 86px; object-fit: contain; display: block; margin: auto; }}
    .devis-title {{ min-height: 88px; border: 2px solid #25568e; border-radius: 14px; background: #4472c4; color: white; display: grid; place-items: center; text-align: center; font: 700 18px Arial, sans-serif; line-height: 1.35; padding: 8px 16px; }}
    .devis-title span {{ display: block; white-space: nowrap; }}
    .site-info {{ font-size: 16px; font-weight: 700; line-height: 1.18; margin: 0 0 22px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
    th, td {{ border: 1px solid #000; padding: 3px 6px; vertical-align: middle; }}
    th {{ background: #8a8a8a; text-align: center; font-weight: 700; }}
    td:nth-child(1) {{ width: 44px; text-align: center; }}
    td:nth-child(3) {{ width: 64px; text-align: center; font-weight: 700; }}
    td:nth-child(4) {{ width: 76px; text-align: center; }}
    td:nth-child(5), td:nth-child(6) {{ width: 116px; text-align: right; }}
    .section td {{ background: #9db7e5; font-size: 15px; font-weight: 700; text-align: center; padding: 2px 6px; }}
    .section-total td {{ background: #9ad75d; font-weight: 700; text-align: right; }}
    .general-total {{ margin-top: 18px; display: grid; grid-template-columns: 1fr 160px; gap: 0; }}
    .general-total div {{ border: 1px solid #000; background: #9ad75d; text-align: center; font-weight: 700; padding: 4px 8px; }}
    .amount-label {{ margin-top: 18px; font-size: 12px; }}
    .amount-words {{ margin-top: 4px; font-size: 14px; font-weight: 700; }}
    .signature {{ margin-top: 34px; text-align: right; font-weight: 700; }}
    @media print {{
      body {{ background: white; }}
      .toolbar {{ display: none; }}
      .page {{ margin: 0; padding: 0; width: auto; min-height: auto; }}
    }}
  </style>
</head>
<body>
  <div class="toolbar"><button onclick="window.print()">Imprimer / PDF</button></div>
  <main class="page">
    <header class="doc-header">
      <div>{logo_html(client_logo, 'Mobilis')}</div>
      <div class="devis-title"><span>DEVIS ESTIMATIF {h(display_type(invoice['invoice_type']))}</span></div>
      <div>{logo_html(company_logo, company['nom'] or 'Entreprise')}</div>
    </header>
    <section class="site-info">
      <div>Code de site : {h(site_label)}</div>
      <div>Nom de site : {h(site_name)}</div>
      <div>Région: {h(invoice['region'] or '')}</div>
      <div>Typologie de site : {h(invoice['typologie_site'] or '')}</div>
    </section>
    <table>
      <thead><tr><th>N°</th><th>Désignations</th><th>Unité</th><th>Quantités</th><th>PU/HT</th><th>Montant/HT</th></tr></thead>
      <tbody>{rows_html}</tbody>
    </table>
    <section class="general-total">
      <div>TOTAL GENERAL</div>
      <div>{money(invoice['total_ht'])}</div>
    </section>
    <section class="amount-label">Arrêté la présente Facture à la somme de:</section>
    <section class="amount-words">{h(amount_words)}</section>
    <section class="signature">L'ENTREPRISE/{h(company['nom'])}</section>
  </main>
</body>
</html>"""
        self.respond(html)

    def save_invoice(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            data = self.rfile.read(length).decode("utf-8")
            parsed = parse_qs(data)
            values = {key: vals[0].strip() for key, vals in parsed.items()}
            if not values.get("invoice_number"):
                raise ValueError("Le numero de facture est obligatoire.")
            invoice_type = values.get("invoice_type")
            if invoice_type not in {"ACQUISITION", "CONSTRUCTION", "CONST_ACQUIS", "NDC"}:
                raise ValueError("Type de facture invalide.")
            purchase_order_id = int(values.get("purchase_order_id"))

            with db() as con:
                purchase_order = con.execute("SELECT * FROM purchase_orders WHERE id=? AND deleted_at IS NULL", (purchase_order_id,)).fetchone()
                if not purchase_order:
                    raise ValueError("Bon de commande introuvable.")
                if purchase_order["type_bc"] != invoice_type:
                    raise ValueError("Le type de facture doit correspondre au type du bon de commande.")
                if invoice_type == "NDC":
                    site_ids = [int(x) for x in parsed.get("ndc_site_ids", [])]
                    if not site_ids:
                        raise ValueError("Selectionnez au moins un site pour NDC.")
                    linked_count = con.execute(
                        f"SELECT COUNT(*) FROM sites WHERE purchase_order_id=? AND id IN ({','.join('?' for _ in site_ids)})",
                        [purchase_order_id, *site_ids],
                    ).fetchone()[0]
                    if linked_count != len(site_ids):
                        raise ValueError("Un ou plusieurs sites ne correspondent pas au bon de commande NDC.")
                    own_invoice_id = int(values.get("id") or 0)
                    used = con.execute(
                        f"SELECT site_id FROM invoice_sites WHERE invoice_id<>? AND site_id IN ({','.join('?' for _ in site_ids)})",
                        [own_invoice_id, *site_ids],
                    ).fetchall()
                    if used:
                        raise ValueError("Un ou plusieurs sites sont deja factures en NDC.")
                    bpu = con.execute("SELECT * FROM bpu_items WHERE article_number=6").fetchone()
                    if not bpu:
                        raise ValueError("Article 6 introuvable dans BPU.")
                    quantity = len(site_ids)
                    lines = [{
                        "article_number": 6,
                        "designation": bpu["designation"],
                        "unite": bpu["unite"],
                        "pu_ht": float(bpu["pu_ht"]),
                        "categorie": "ndc",
                        "quantite": quantity,
                        "montant_ht": round(float(bpu["pu_ht"]) * quantity, 2),
                    }]
                    site_id = None
                else:
                    site_id = int(values.get("site_id"))
                    site = con.execute("SELECT * FROM sites WHERE id=?", (site_id,)).fetchone()
                    if not site or int(site["purchase_order_id"]) != purchase_order_id:
                        raise ValueError("Le site ne correspond pas au bon de commande.")
                    allowed = {
                        "ACQUISITION": {"acquisition"},
                        "CONSTRUCTION": {"fourniture", "prestation"},
                        "CONST_ACQUIS": {"acquisition", "fourniture", "prestation"},
                    }[invoice_type]
                    lines = []
                    for article_number, quantity in parse_invoice_lines(values.get("lines", "")):
                        bpu = con.execute("SELECT * FROM bpu_items WHERE article_number=?", (article_number,)).fetchone()
                        if not bpu:
                            raise ValueError(f"Article BPU introuvable: {article_number}")
                        if bpu["categorie"] not in allowed:
                            raise ValueError(f"Article {article_number} non autorise pour {invoice_type}.")
                        pu_ht = float(bpu["pu_ht"])
                        lines.append({
                            "article_number": article_number,
                            "designation": bpu["designation"],
                            "unite": bpu["unite"],
                            "pu_ht": pu_ht,
                            "categorie": bpu["categorie"],
                            "quantite": quantity,
                            "montant_ht": round(pu_ht * quantity, 2),
                        })
                    if not lines:
                        raise ValueError("Ajoutez au moins une ligne article avec quantite.")

                total_ht, retenue, ht_after_rg, tva, total_ttc = totals_from_lines(lines)
                if values.get("id"):
                    invoice_id = int(values.get("id"))
                    con.execute("""
                        UPDATE invoices
                        SET invoice_number=?, invoice_type=?, purchase_order_id=?, site_id=?, invoice_date=?,
                            total_ht=?, retenue_garantie=?, montant_ht_apres_rg=?, tva=?, total_ttc=?,
                            montant_en_lettres=?, updated_by=?, updated_at=CURRENT_TIMESTAMP
                        WHERE id=?
                    """, (
                        values.get("invoice_number"),
                        invoice_type,
                        purchase_order_id,
                        site_id,
                        values.get("invoice_date") or None,
                        total_ht,
                        retenue,
                        ht_after_rg,
                        tva,
                        total_ttc,
                        amount_words_placeholder(total_ttc),
                        current_actor(),
                        invoice_id,
                    ))
                    con.execute("DELETE FROM invoice_lines WHERE invoice_id=?", (invoice_id,))
                    con.execute("DELETE FROM invoice_sites WHERE invoice_id=?", (invoice_id,))
                else:
                    cursor = con.execute("""
                        INSERT INTO invoices(invoice_number,invoice_type,purchase_order_id,site_id,invoice_date,total_ht,retenue_garantie,montant_ht_apres_rg,tva,total_ttc,montant_en_lettres,created_by,updated_by)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        values.get("invoice_number"),
                        invoice_type,
                        purchase_order_id,
                        site_id,
                        values.get("invoice_date") or None,
                        total_ht,
                        retenue,
                        ht_after_rg,
                        tva,
                        total_ttc,
                        amount_words_placeholder(total_ttc),
                        current_actor(),
                        current_actor(),
                    ))
                    invoice_id = cursor.lastrowid
                con.executemany("""
                    INSERT INTO invoice_lines(invoice_id,article_number,designation_snapshot,unite_snapshot,pu_ht_snapshot,categorie_snapshot,quantite,montant_ht)
                    VALUES(?,?,?,?,?,?,?,?)
                """, [
                    (invoice_id, line["article_number"], line["designation"], line["unite"], line["pu_ht"], line["categorie"], line["quantite"], line["montant_ht"])
                    for line in lines
                ])
                if invoice_type == "NDC":
                    con.executemany("INSERT INTO invoice_sites(invoice_id,site_id) VALUES(?,?)", [(invoice_id, current_site_id) for current_site_id in site_ids])
            self.regenerate_invoice_pdfs(invoice_id)
            self.redirect("/invoices?message=Facture creee avec succes - sorties regenerees")
        except Exception as exc:
            self.redirect(f"/invoices?message={quote('Erreur creation facture: ' + str(exc))}")

    def style(self):
        self.respond((ROOT_DIR / "static" / "style.css").read_text(encoding="utf-8"), content_type="text/css; charset=utf-8")


def main():
    host = "127.0.0.1"
    port = 8000
    server = ThreadingHTTPServer((host, port), App)
    print(f"POS AI running on http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()

