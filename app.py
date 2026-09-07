from html import escape
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode
from uuid import uuid4
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import re
import shutil
import subprocess
import sys
import tempfile
import json
import logging
import os
import threading
import hmac as hmac_lib
from datetime import datetime, timedelta

from db import DB_PATH, DEFAULT_TEMPLATE_SETTINGS, app_settings, audit, current_actor, db, ensure_template_defaults
from services.billing import (
    amount_to_french,
    amount_words_placeholder,
    totals_from_lines,
)
from services.money import (
    decimal_value,
    format_money,
    format_rate,
    fraction_to_percent,
    line_total,
    localized_decimal,
    money_storage,
    percent_to_fraction,
    rate_percent,
)
from services.auth import (
    DEFAULT_ADMIN_USERNAME,
    SESSION_COOKIE,
    authenticate,
    change_own_password,
    complete_first_run_setup,
    create_session,
    create_user,
    delete_test_users,
    delete_session,
    delete_user,
    ensure_default_admin,
    first_run_required,
    get_session_user,
    list_users,
    session_csrf_token,
    set_user_active,
    set_user_role,
    unlock_user,
    update_user_password,
    user_can_write,
    user_has_permission,
    user_is_admin,
    user_is_super_admin,
)
from services.backups import BACKUP_DIR, create_backup, list_backups, restore_backup_content
from services.bpu import import_bpu, replace_bpu_catalog
from services.bpu_mapping import (
    activate_bpu_st_version,
    active_mapping_version,
    confirm_bpu_st_mapping,
    import_bpu_st_version,
    parse_invoice_line_payload,
    resolve_invoice_line,
    search_bpu_items,
    seed_bpu_st_mapping,
)
from services.dashboard import (
    LIFECYCLE_STATUS_ORDER,
    load_dashboard,
    normalize_dashboard_filters,
)
from services.invoice_exports import invoice_export_data, invoice_xlsx
from services.invoice_lifecycle import (
    AWAITING_PAYMENT,
    BROUILLON,
    CANCELLED,
    DEPOSITED_DTC,
    PAID,
    READY_DTC,
    STATUS_LABELS,
    InvoiceLifecycleError,
    InvoicePermissionError,
    cancel_invoice,
    get_invoice_lifecycle,
    record_invoice_export,
    restore_cancelled_invoice,
    update_payment_reference,
    update_tracking_dates,
)
from services.invoice_types import (
    PURCHASE_ORDER_TYPES,
    allowed_bpu_categories,
    invoice_type_for_purchase_order,
    invoice_type_label,
    purchase_order_type_label,
)
from services.typologies import (
    active_typologies,
    resolve_invoice_typology,
    typology_by_sigle,
    typology_export_label,
    validate_typology_values,
)
from services.site_partners import (
    direction_area_from_sigle,
    site_partner_requirements,
    validate_partner_values,
    validate_site_partners,
)
from services.data_imports import (
    apply_partner_import,
    apply_purchase_order_import,
    import_report,
    load_import_batch,
    parse_partner_workbook,
    parse_purchase_order_workbook,
    partner_template,
    purchase_order_export,
    purchase_order_template,
    save_import_batch,
)
from services.licensing import install_license, license_status, require_feature
from services.machine_identity import activation_request
from services.pdf_files import archive_metadata, cleanup_old_exports, open_pdf_with_system_viewer
from services.uploads import save_upload as save_upload_file
from services.xlsx import make_xlsx, styled


ROOT_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = Path(os.environ.get("PHOENIX_UPLOAD_DIR", ROOT_DIR / "uploads"))
EXPORT_DIR = Path(os.environ.get("PHOENIX_EXPORT_DIR", ROOT_DIR / "exports"))
PUBLIC_CSRF_COOKIE = "phoenix_public_csrf"
APP_VERSION = "2.5.2"
DOWNLOAD_DIR = Path(os.environ.get("PHOENIX_DOWNLOAD_DIR", Path.home() / "Downloads"))
MAX_REQUEST_BYTES = 25 * 1024 * 1024
MAX_MULTIPART_PARTS = 250


def _first_existing_path(*candidates):
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return Path(candidate)
    return Path(candidates[0] or "") if candidates else Path()


_codex_node_root = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "node"
BUNDLED_NODE = _first_existing_path(
    os.environ.get("PHOENIX_NODE_EXE"),
    ROOT_DIR / "runtime" / "node" / "node.exe",
    shutil.which("node"),
    _codex_node_root / "bin" / "node.exe",
)
NODE_MODULES = _first_existing_path(
    os.environ.get("PHOENIX_NODE_MODULES"),
    ROOT_DIR / "runtime" / "node" / "node_modules",
    _codex_node_root / "node_modules",
)
MOBILIS_DOIT = "Algérie Télécom Mobile / Mobilis"
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
            {"id": "site_info", "title": "Site", "x": 28, "y": 110, "w": 500, "h": 105, "font": 13, "content": "Code de site: {{Code_Site}}\nNom de site: {{Nom_Site}}\nTypologie de site: {{Typologie}}"},
            {"id": "articles_table", "title": "Table quantitatif", "x": 28, "y": 250, "w": 740, "h": 230, "font": 11, "content": "N? | Designation | Unite | Quantites"},
        ]
    if document_type == "devis_estimatif":
        return common_logos + [
            {"id": "devis_title", "title": "Titre devis", "x": 205, "y": 20, "w": 380, "h": 72, "font": 16, "content": "DEVIS ESTIMATIF {{FACTURE.TYPE}}"},
            {"id": "site_info", "title": "Site", "x": 28, "y": 110, "w": 500, "h": 105, "font": 13, "content": "Code de site: {{Code_Site}}\nNom de site: {{Nom_Site}}\nTypologie de site: {{Typologie}}"},
            {"id": "articles_table", "title": "Table estimatif", "x": 28, "y": 250, "w": 740, "h": 260, "font": 11, "content": "N? | Designation | Unite | Quantites | PU/HT | Montant/HT"},
            {"id": "totals_table", "title": "Total general", "x": 470, "y": 540, "w": 300, "h": 45, "font": 12, "content": "TOTAL GENERAL"},
            {"id": "amount_words", "title": "Montant en lettres", "x": 28, "y": 630, "w": 680, "h": 65, "font": 24, "content": "{{Montant_En_Lettres}}"},
            {"id": "signature", "title": "Signature", "x": 610, "y": 745, "w": 160, "h": 40, "font": 11, "content": "L'ENTREPRISE/{{Entreprise.Nom}}"},
        ]
    return common_logos + [
        {"id": "enterprise_info", "title": "Entreprise", "x": 28, "y": 120, "w": 350, "h": 90, "font": 13, "content": "RGC: {{Entreprise.RGC}}\nNIF: {{Entreprise.NIF}}\nART: {{Entreprise.ART}}\nADRESSE: {{Entreprise.Adresse}}\nN? COMPTE: {{Entreprise.RIB}}"},
        {"id": "client_info", "title": "Client", "x": 470, "y": 120, "w": 300, "h": 90, "font": 13, "content": "DOIT : {{Client.Nom}}\n{{Client.Direction}}\n{{Client.Adresse}}\nRGC N?: {{Client.RGC}}\nNIF N?: {{Client.NIF}}"},
        {"id": "invoice_title", "title": "Titre facture", "x": 28, "y": 235, "w": 740, "h": 35, "font": 14, "content": "FACTURE N? : {{N_Facture}}"},
        {"id": "site_info", "title": "Site / BC", "x": 28, "y": 292, "w": 500, "h": 105, "font": 13, "content": "Reference Contrat: {{Ref_Contrat}}\nCode de site: {{Code_Site}}\nNom de site: {{Nom_Site}}\nTypologie de site: {{Typologie}}\nBon de commande: {{N_BC}}"},
        {"id": "articles_table", "title": "Table articles", "x": 28, "y": 430, "w": 740, "h": 170, "font": 11, "content": "N? | Designation | Unite | Quantites | PU/HT | Montant/HT"},
        {"id": "totals_table", "title": "Table montants", "x": 470, "y": 620, "w": 300, "h": 115, "font": 12, "content": "TOTAL EN HT\nRETENUE DE GARANTIE {{RG_RATE}}%\nMONTANT HT APRES RG\nTVA {{TVA_RATE}}%\nTOTAL EN TTC"},
        {"id": "amount_words", "title": "Montant en lettres", "x": 28, "y": 765, "w": 680, "h": 65, "font": 24, "content": "{{Montant_En_Lettres}}"},
        {"id": "signature", "title": "Signature", "x": 610, "y": 875, "w": 160, "h": 40, "font": 11, "content": "L'ENTREPRISE/{{Entreprise.Nom}}"},
    ]

def h(value) -> str:
    return escape("" if value is None else str(value), quote=True)


def hmac_compare(left, right) -> bool:
    return hmac_lib.compare_digest(str(left or ""), str(right or ""))


def row_value(row, key, default=""):
    try:
        return row[key]
    except (KeyError, IndexError):
        return default


def money(value) -> str:
    return format_money(value)


def display_type(value) -> str:
    return invoice_type_label(value)


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
                client.updated_at AS client_updated,
                COALESCE(s.updated_at, '') AS site_updated,
                c.updated_at AS company_updated,
                COALESCE(mcs.updated_at, '') AS mobilis_client_updated,
                COALESCE(MAX(il.created_at), '') AS lines_updated,
                (SELECT COALESCE(MAX(updated_at), '') FROM template_settings) AS template_updated
            FROM invoices i
            JOIN purchase_orders po ON po.id = i.purchase_order_id
            JOIN client_directions md ON md.id = po.client_direction_id
            JOIN clients client ON client.id=md.client_id
            LEFT JOIN sites s ON s.id = i.site_id
            CROSS JOIN company_settings c
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


def parse_amount(value):
    raw = str(value or "0").strip().replace("\u00a0", "").replace(" ", "")
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".")
    else:
        raw = raw.replace(",", ".")
    return float(raw or 0)


def field(name, label, value="", field_type="text", required=False, autocomplete=""):
    req = " required" if required else ""
    autocomplete_attr = f' autocomplete="{h(autocomplete)}"' if autocomplete else ""
    return f'<label><span>{h(label)}</span><input type="{field_type}" name="{h(name)}" value="{h(value)}"{autocomplete_attr}{req}></label>'


def decimal_text_field(name, label, value="", required=False):
    req = " required" if required else ""
    return f'<label><span>{h(label)}</span><input type="text" inputmode="decimal" name="{h(name)}" value="{h(value)}"{req}></label>'


def normalized_sigle(value):
    sigle = re.sub(r"\s+", " ", str(value or "").strip().upper())
    if not sigle or not re.fullmatch(r"[A-Z0-9][A-Z0-9 -]*", sigle):
        raise ValueError("Le sigle accepte uniquement les lettres majuscules, chiffres, espaces et tirets.")
    return sigle


def normalized_client_sigle(value):
    sigle = re.sub(r"\s+", " ", str(value or "").strip())
    if not 2 <= len(sigle) <= 30 or not re.fullmatch(r"[\wÀ-ÖØ-öø-ÿ][\wÀ-ÖØ-öø-ÿ -]*", sigle, re.UNICODE):
        raise ValueError("Le sigle client accepte les lettres, chiffres, espaces et tirets (2 à 30 caractères).")
    return sigle


def file_field(name, label, accept):
    return f'<label><span>{h(label)}</span><input type="file" name="{h(name)}" accept="{h(accept)}"></label>'


def textarea_field(name, label, hint="", value=""):
    return f'<label class="wide"><span>{h(label)}</span><textarea name="{h(name)}" rows="5">{h(value)}</textarea><small>{h(hint)}</small></label>'


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
        f'<form method="post" action="{base_path}/delete" class="inline-action-form"><input type="hidden" name="id" value="{row_id}"><button type="submit" class="button-link icon-link danger-link" title="Supprimer" aria-label="Supprimer" onclick="return confirm(\'Supprimer cet element ?\')">×</button></form>'
        f'</span>'
    )


def layout(title, content, subtitle=""):
    nav = [
        ("/", "Tableau de bord", "i-dashboard"),
        ("/table-facturation-new", "Table Facturation", "i-table"),
        ("/invoices", "Factures", "i-file"),
        ("/purchase-orders", "Bons de commande", "i-clipboard"),
        ("/clients", "Clients", "i-radio"),
        ("/bpu", "BPU", "i-calculator"),
        ("/settings", "Parametres", "i-template"),
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
    user_name = os.environ.get("PHOENIX_CURRENT_USER", current_actor())
    user_initials = initials(user_name)
    license_info = license_status()
    license_badge = f"{license_info['edition'].upper()}" + (f" - {license_info.get('days_left', 0)} jours" if license_info.get("is_demo") else "")
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
      <span class="sapta-user-copy"><strong>{h(user_name)}</strong><small><form method="post" action="/logout" class="inline-action-form"><button type="submit" class="link-button">Deconnexion</button></form></small></span>
      <svg class="sapta-user-chevron"><use href="#i-chevron-down"/></svg>
    </div>
    <div class="sapta-version">Version {h(APP_VERSION)}</div>
    <div class="sapta-version">{h(license_badge)}</div>
  </aside>

  <main class="sapta-main legacy-main">
    <div class="sapta-page legacy-page-content">
      <div class="sapta-title-row">{title_markup}{title_action}</div>
      <div class="legacy-content">{content}</div>
    </div>
  </main>

  <script src="/static/vendor/lucide/lucide.min.js"></script>
  <script src="/static/app-ui.js"></script>
  <script>
    if (window.lucide) lucide.createIcons();
    const currentPath = window.location.pathname;
    document.querySelectorAll('.sapta-nav a').forEach(link => {{
      const path = link.dataset.path;
      const settingsPaths = ['/settings','/company','/templates','/users','/backup','/license','/about','/status'];
      if ((path === '/' && currentPath === '/') || (path !== '/' && currentPath.startsWith(path)) || (path === '/settings' && settingsPaths.includes(currentPath))) link.classList.add('active');
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
        window.showAppToast('Erreur PDF: ' + error.message, 'error');
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
    return save_upload_file(upload, folder, allowed_suffixes, ROOT_DIR, UPLOAD_DIR)


def _path_inside(root, relative_path):
    root = Path(root).resolve()
    target = (root / relative_path).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise ValueError("Chemin de fichier invalide.")
    return target


def resolve_stored_upload_path(stored_path):
    raw_path = str(stored_path or "").replace("\\", "/").lstrip("/")
    if raw_path.startswith("uploads/"):
        raw_path = raw_path[len("uploads/"):]
    return _path_inside(UPLOAD_DIR, raw_path)


def resolve_export_path(stored_path):
    raw_path = str(stored_path or "").replace("\\", "/").lstrip("/")
    if raw_path.startswith("exports/"):
        raw_path = raw_path[len("exports/"):]
    return _path_inside(EXPORT_DIR, raw_path)


def parse_code_sites(raw):
    codes = []
    seen = set()
    for part in raw.replace(",", "\n").replace(";", "\n").splitlines():
        code = part.strip()
        if code and code not in seen:
            seen.add(code)
            codes.append(code)
    return codes


class App(BaseHTTPRequestHandler):
    public_paths = {"/login", "/setup", "/favicon.ico"}
    state_changing_paths = {
        "/logout",
        "/mobilis/delete",
        "/purchase-orders/delete",
        "/purchase-orders/document/delete",
        "/purchase-orders/site/delete",
        "/sites/delete",
        "/invoices/delete",
        "/archives/delete",
    }

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "base-uri 'self'; frame-ancestors 'none'; object-src 'none'")
        if b"cache-control:" not in b"".join(getattr(self, "_headers_buffer", [])).lower():
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, message_format, *args):
        logging.getLogger("phoenix.http.access").info(
            "%s:%s %s",
            self.client_address[0],
            self.client_address[1],
            message_format % args,
        )

    def cookie_value(self, name):
        cookie_header = self.headers.get("Cookie", "")
        for part in cookie_header.split(";"):
            if "=" not in part:
                continue
            key, value = part.strip().split("=", 1)
            if key == name:
                return value
        return ""

    def current_user(self):
        return get_session_user(self.cookie_value(SESSION_COOKIE))

    def visible_company_branch_id(self):
        user = self.current_user()
        if not user or user_is_super_admin(user):
            return None
        return row_value(user, "company_branch_id")

    def public_csrf_token(self):
        token = self.cookie_value(PUBLIC_CSRF_COOKIE)
        return token if len(token) >= 32 else uuid4().hex + uuid4().hex

    def csrf_input(self):
        token = session_csrf_token(self.cookie_value(SESSION_COOKIE))
        return f'<input type="hidden" name="csrf_token" value="{h(token)}">' if token else ""

    def inject_csrf_inputs(self, body):
        if self.path.split("?", 1)[0] in {"/login", "/setup"}:
            return body
        token = self.csrf_input()
        if not token or not isinstance(body, str) or "<form" not in body:
            return body
        return re.sub(
            r'(<form\b[^>]*method=["\']?post["\']?[^>]*>)',
            r'\1' + token,
            body,
            flags=re.IGNORECASE,
        )

    def require_login(self, path):
        if path in self.public_paths or path.startswith("/static/"):
            return None
        if first_run_required():
            self.redirect("/setup")
            return None
        user = self.current_user()
        if user:
            os.environ["PHOENIX_CURRENT_USER"] = user["username"]
            if user["must_change_password"] and path not in {"/change-password", "/logout"}:
                self.redirect("/change-password?message=Changez le mot de passe avant de continuer")
                return None
            return user
        self.redirect("/login")
        return None

    def require_write_access(self):
        user = self.current_user()
        if user_can_write(user):
            os.environ["PHOENIX_CURRENT_USER"] = user["username"]
            return True
        return False

    def require_admin_access(self):
        user = self.current_user()
        if user_is_admin(user):
            os.environ["PHOENIX_CURRENT_USER"] = user["username"]
            return True
        return False

    def require_permission(self, permission_code):
        user = self.current_user()
        if user_has_permission(user, permission_code):
            os.environ["PHOENIX_CURRENT_USER"] = user["username"]
            return True
        return False

    def valid_same_origin_post(self):
        expected = f"http://{self.headers.get('Host', '')}"
        for header in ("Origin", "Referer"):
            value = self.headers.get(header)
            if value and not value.startswith(expected):
                return False
        return True

    def valid_csrf_post(self, path):
        if not self.valid_same_origin_post():
            return False
        if path in {"/login", "/setup"}:
            expected = self.cookie_value(PUBLIC_CSRF_COOKIE)
            values = self.form()
            return bool(expected and hmac_compare(values.get("csrf_token", ""), expected))
        expected = session_csrf_token(self.cookie_value(SESSION_COOKIE))
        if not expected:
            return False
        content_type = self.headers.get("Content-Type", "")
        values = self.multipart_form()[0] if "multipart/form-data" in content_type else self.form()
        return hmac_compare(values.get("csrf_token", ""), expected)

    def csrf_rejected(self, path):
        referer = self.headers.get("Referer", "")
        expected = f"http://{self.headers.get('Host', '')}"
        if referer.startswith(expected):
            safe_path = "/" + referer.removeprefix(expected).lstrip("/")
            safe_path = safe_path.split("#", 1)[0] or "/"
        else:
            safe_path = "/login" if path in {"/login", "/setup"} else "/"
        separator = "&" if "?" in safe_path else "?"
        return self.redirect(f"{safe_path}{separator}message={quote('Session expiree, veuillez reessayer.')}")

    def do_HEAD(self):
        path = self.path.split("?")[0]
        pdf_request = self.parse_pdf_request(path)
        if pdf_request:
            return self.pdf_head(*pdf_request)
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        ensure_default_admin()
        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if path == "/login":
            return self.login()
        if path == "/setup":
            return self.first_run_setup()
        if path.startswith("/static/"):
            return self.static_file(path)
        if path.startswith("/docs/"):
            return self.docs_file(path)
        if not self.require_login(path):
            return
        if path in self.state_changing_paths:
            return self.respond(
                "Method Not Allowed",
                status=405,
                content_type="text/plain",
                headers={"Allow": "POST"},
            )
        invoice_export_request = (
            path in {"/table-facturation-new/export", "/invoices/export"}
            or self.parse_pdf_request(path)
            or self.parse_pdf_ready_request(path)
        )
        if invoice_export_request and not self.require_permission("invoice.export"):
            return self.respond("Forbidden", status=403, content_type="text/plain")
        if path == "/status" and not self.require_permission("audit.read"):
            return self.respond("Forbidden", status=403, content_type="text/plain")
        if path == "/clients" and not self.require_permission("client.manage"):
            return self.respond("Forbidden", status=403, content_type="text/plain")
        if path == "/company-branches" and not self.require_permission("company_branch.manage"):
            return self.respond("Forbidden", status=403, content_type="text/plain")
        if path.startswith("/settings/partners/") or path.startswith("/purchase-orders/import") or path == "/purchase-orders/export":
            if not user_is_super_admin(self.current_user()):
                return self.respond("Forbidden", status=403, content_type="text/plain")
        if path == "/purchase-orders" and "edit_id=" in self.path and not user_is_super_admin(self.current_user()):
            return self.respond("Forbidden", status=403, content_type="text/plain")
        if path in {"/invoices", "/table-facturation", "/table-facturation-new"} and not self.require_permission("invoice.read"):
            return self.respond("Forbidden", status=403, content_type="text/plain")
        if path.startswith("/uploads/"):
            return self.uploaded_file(path)
        if path.startswith("/exports/"):
            return self.exported_file(path)
        if path.startswith("/backups/"):
            return self.backup_file(path)
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
        if path == "/bpu/search":
            return self.bpu_search()
        if path == "/settings/partners/template":
            return self.partner_import_template()
        if path == "/settings/partners/report":
            return self.import_batch_report("partner")
        if path == "/purchase-orders/import/template":
            return self.purchase_order_import_template()
        if path == "/purchase-orders/import/report":
            return self.import_batch_report("purchase_orders")
        if path == "/purchase-orders/export":
            return self.purchase_order_export_excel()
        if path == "/archives/open":
            return self.open_archive()
        routes = {
            "/": self.dashboard,
            "/table-facturation": self.table_facturation,
            "/table-facturation-new": self.table_facturation_new,
            "/company": self.company,
            "/mobilis": self.mobilis,
            "/clients": self.clients,
            "/company-branches": self.company_branches,
            "/bpu": self.bpu,
            "/purchase-orders": self.purchase_orders,
            "/invoices": self.invoices,
            "/settings": self.settings,
            "/templates": self.templates,
            "/users": self.users,
            "/change-password": self.change_password,
            "/backup": self.backup,
            "/license": self.license_page,
            "/license/request": self.license_request,
            "/about": self.about,
            "/status": self.status,
            "/static/style.css": self.style,
        }
        if path == "/mobilis":
            return self.redirect("/clients")
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

    def settings(self):
        query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        section = query.get("section", [""])[0]
        edit_company = query.get("edit_company", [""])[0] == "1"
        branch_id = query.get("branch_id", [""])[0]
        new_branch = query.get("new_branch", [""])[0] == "1"
        typology_id = query.get("typology_id", [""])[0]
        new_typology = query.get("new_typology", [""])[0] == "1"
        partner_id = query.get("partner_id", [""])[0]
        new_partner = query.get("new_partner", [""])[0] == "1"
        import_token = query.get("import_token", [""])[0]
        message = query.get("message", [""])[0]
        can_manage_typologies = user_is_super_admin(self.current_user())
        with db() as con:
            company = con.execute("SELECT * FROM company_settings WHERE id=1").fetchone()
            financial = app_settings(con)
            branches = con.execute(
                """SELECT cb.*,
                          (SELECT COUNT(*) FROM users u WHERE u.company_branch_id=cb.id) AS user_count,
                          (SELECT COUNT(*) FROM purchase_orders po WHERE po.company_branch_id=cb.id AND po.deleted_at IS NULL) AS bc_count
                   FROM company_branches cb ORDER BY cb.sigle, cb.name"""
            ).fetchall()
            branch_edit = con.execute("SELECT * FROM company_branches WHERE id=?", (branch_id,)).fetchone() if branch_id else None
            typologies = con.execute(
                "SELECT * FROM typologies ORDER BY sigle COLLATE NOCASE"
            ).fetchall()
            typology_edit = con.execute(
                "SELECT * FROM typologies WHERE id=?", (typology_id,)
            ).fetchone() if typology_id else None
            partner_table = "subcontractors" if section == "subcontractors" else "design_offices"
            partners = con.execute(
                f"SELECT * FROM {partner_table} ORDER BY raison_sociale COLLATE NOCASE"
            ).fetchall() if section in {"subcontractors", "design-offices"} else []
            partner_edit = con.execute(
                f"SELECT * FROM {partner_table} WHERE id=?", (partner_id,)
            ).fetchone() if partner_id and section in {"subcontractors", "design-offices"} else None
            partner_import = None
            if import_token and section in {"subcontractors", "design-offices"}:
                expected_kind = "subcontractors" if section == "subcontractors" else "design_offices"
                try:
                    _, partner_import = load_import_batch(con, import_token, expected_kind)
                except ValueError:
                    partner_import = None
        branch_rows = [[
            h(row["sigle"]), h(row["name"]), h(row["address"] or "—"),
            str(row["user_count"]), str(row["bc_count"]),
            '<span class="status-pill">Active</span>' if row["is_active"] else '<span class="status-pill inactive">Inactive</span>',
            f'<a class="icon-link" href="/settings?section=enterprise&branch_id={row["id"]}" title="Modifier"><i data-lucide="pencil"></i></a>',
        ] for row in branches]
        company_drawer = ""
        if edit_company:
            company_drawer = f"""<aside class="entity-drawer settings-drawer"><header class="drawer-header"><div><small>Paramètres</small><h3>Modifier l’entreprise</h3></div><a class="drawer-close" href="/settings?section=enterprise">×</a></header>
            <form method="post" action="/company" enctype="multipart/form-data" class="drawer-form">
              {field('nom', 'Nom', company['nom'], required=True)}{field('rgc', 'RGC', company['rgc'])}{field('nif', 'NIF', company['nif'])}{field('art', 'ART', company['art'])}
              {textarea_field('adresse', 'Adresse', value=company['adresse'])}{field('numero_compte', 'N° Compte', company['numero_compte'])}
              {decimal_text_field('retention_rate', 'Retenue de garantie (%)', fraction_to_percent(financial['retention_rate']))}
              {decimal_text_field('tax_rate', 'TVA (%)', fraction_to_percent(financial['tax_rate']))}
              {select_field('currency_code', 'Devise', [('DZD','DA - Dinar'),('EUR','EUR - Euro'),('USD','USD - Dollar')], financial['currency_code'])}
              {file_field('logo', 'Logo', '.png,.jpg,.jpeg,.webp')}
              <footer class="drawer-actions"><a class="outline-button" href="/settings?section=enterprise">Annuler</a><button type="submit">Enregistrer</button></footer>
            </form></aside>"""
        branch_drawer = ""
        if new_branch or branch_edit:
            branch_drawer = f"""<aside class="entity-drawer settings-drawer"><header class="drawer-header"><div><small>Direction {h(company['nom'])}</small><h3>{'Modifier la direction' if branch_edit else 'Nouvelle direction'}</h3></div><a class="drawer-close" href="/settings?section=enterprise">×</a></header>
            <form method="post" action="/company-branches/save" class="drawer-form">
              <input type="hidden" name="id" value="{h(branch_edit['id'] if branch_edit else '')}">
              {field('sigle', 'Sigle', branch_edit['sigle'] if branch_edit else '', required=True)}
              {field('name', 'Nom', branch_edit['name'] if branch_edit else '', required=True)}
              {field('address', 'Adresse', branch_edit['address'] if branch_edit else '')}
              {select_field('is_active', 'État', [('1','Active'),('0','Inactive')], str(branch_edit['is_active']) if branch_edit else '1')}
              <footer class="drawer-actions"><a class="outline-button" href="/settings?section=enterprise">Annuler</a><button type="submit">Enregistrer</button></footer>
            </form></aside>"""
        cards = [
            ("/settings?section=enterprise", "Entreprise", "Informations légales et directions opérationnelles."),
            ("/settings?section=subcontractors", "Sous-traitants", "Entreprises chargées des travaux CONST et MGC."),
            ("/settings?section=design-offices", "Bureaux d’études", "BET affectés aux sites ACQ, NDC et CONST/ACQ."),
            ("/templates", "Templates", "Modèles et mise en page des documents."),
            ("/users", "Utilisateurs", "Comptes, rôles et accès par direction."),
            ("/backup", "Sécurité et sauvegardes", "Sauvegarde, téléchargement et restauration."),
            ("/license", "Licence et version", f"Version {APP_VERSION}, mode démo et activation."),
            ("/about", "Support", "Guide utilisateur et diagnostic de support."),
            ("/status", "État technique", "Santé de l’application et de la base de données."),
        ]
        logo = f'<img src="/{h(company["logo_path"])}" alt="Logo">' if company["logo_path"] else '<i data-lucide="building-2"></i>'
        alert = f'<section class="alert">{h(message)}</section>' if message else ''
        if section in {"subcontractors", "design-offices"}:
            is_subcontractor = section == "subcontractors"
            title = "Sous-traitants" if is_subcontractor else "Bureaux d’études"
            singular = "sous-traitant" if is_subcontractor else "BET"
            partner_rows = [[
                h(row["raison_sociale"]), h(row["contact"] or "—"), h(row["telephone"] or "—"),
                h(row["email"] or "—"), h(row["adresse"] or "—"),
                '<span class="status-pill">Actif</span>' if row["is_active"] else '<span class="status-pill inactive">Inactif</span>',
                f'<a class="icon-link" href="/settings?section={section}&partner_id={row["id"]}" title="Modifier"><i data-lucide="pencil"></i></a>' if can_manage_typologies else '—',
            ] for row in partners]
            partner_drawer = ""
            if new_partner or partner_edit:
                partner_drawer = f"""<aside class="entity-drawer settings-drawer"><header class="drawer-header"><div><small>Référentiel interne</small><h3>{'Modifier' if partner_edit else 'Nouveau'} {h(singular)}</h3></div><a class="drawer-close" href="/settings?section={section}">×</a></header>
                <form method="post" action="/settings/partners/save" class="drawer-form">
                  <input type="hidden" name="kind" value="{'subcontractor' if is_subcontractor else 'design_office'}"><input type="hidden" name="id" value="{h(partner_edit['id'] if partner_edit else '')}">
                  {field('raison_sociale', 'Raison sociale', partner_edit['raison_sociale'] if partner_edit else '', required=True)}
                  {field('contact', 'Contact', partner_edit['contact'] if partner_edit else '')}
                  {field('telephone', 'Téléphone', partner_edit['telephone'] if partner_edit else '')}
                  {field('email', 'E-mail', partner_edit['email'] if partner_edit else '', field_type='email')}
                  {textarea_field('adresse', 'Adresse', value=partner_edit['adresse'] if partner_edit else '')}
                  {select_field('is_active', 'État', [('1','Actif'),('0','Inactif')], str(partner_edit['is_active']) if partner_edit else '1')}
                  <footer class="drawer-actions"><a class="outline-button" href="/settings?section={section}">Annuler</a><button type="submit">Enregistrer</button></footer>
                </form></aside>"""
            import_preview = ""
            if partner_import:
                preview_rows = [[
                    str(row['row']), h(row['raison_sociale']), h(row['contact'] or '—'),
                    h(row['telephone'] or '—'), h(row['email'] or '—'),
                    f'<span class="status-pill{" inactive" if row["error"] else ""}">{h(row["status"])}</span>',
                    h(row['error'] or '—'),
                ] for row in partner_import['rows']]
                confirm_button = '' if partner_import['error_count'] else f'''<form method="post" action="/settings/partners/import/confirm"><input type="hidden" name="kind" value="{partner_import['kind']}"><input type="hidden" name="token" value="{h(import_token)}"><button type="submit"><i data-lucide="check"></i> Confirmer l’import</button></form>'''
                import_preview = f'''<section class="panel import-preview"><header><div><h3>Aperçu avant import</h3><small>{len(preview_rows)} lignes, {partner_import['error_count']} erreur(s).</small></div><div class="page-actions"><a class="outline-button" href="/settings/partners/report?token={h(import_token)}"><i data-lucide="file-spreadsheet"></i> Rapport</a>{confirm_button}</div></header>{table(['Ligne','Raison sociale','Contact','Téléphone','E-mail','Statut','Erreur'], preview_rows)}</section>'''
            import_controls = f'''<div class="partner-import-actions"><a class="outline-button" href="/settings/partners/template?kind={'subcontractors' if is_subcontractor else 'design_offices'}"><i data-lucide="download"></i> Télécharger le modèle</a><form method="post" action="/settings/partners/import/preview" enctype="multipart/form-data"><input type="hidden" name="kind" value="{'subcontractors' if is_subcontractor else 'design_offices'}"><label class="outline-button file-action-button"><i data-lucide="upload"></i> Importer Excel<input type="file" name="excel_file" accept=".xlsx" required onchange="this.form.submit()"></label></form><a class="button-link primary-blue" href="/settings?section={section}&new_partner=1"><i data-lucide="plus"></i> Nouveau</a></div>''' if can_manage_typologies else ''
            content = f"""{alert}<div class="settings-section-heading"><a class="outline-button" href="/settings"><i data-lucide="arrow-left"></i> Paramètres</a></div>
            <section class="panel settings-branches"><header><div><h2>{h(title)}</h2><small>Référentiel interne utilisé lors de l’affectation des sites.</small></div>{import_controls}</header>{table(['Raison sociale','Contact','Téléphone','E-mail','Adresse','État','Actions'], partner_rows)}</section>{import_preview}{partner_drawer}"""
        elif section == "__removed_typologies":
            typology_rows = [[
                h(row["sigle"]), h(row["libelle_complet"]),
                '<span class="status-pill">Active</span>' if row["is_active"] else '<span class="status-pill inactive">Inactive</span>',
                f'<a class="icon-link" href="/settings?section=typologies&typology_id={row["id"]}" title="Modifier"><i data-lucide="pencil"></i></a>' if can_manage_typologies else '—',
            ] for row in typologies]
            typology_drawer = ""
            if new_typology or typology_edit:
                typology_drawer = f"""<aside class="entity-drawer settings-drawer"><header class="drawer-header"><div><small>Référentiel</small><h3>{'Modifier la typologie' if typology_edit else 'Nouvelle typologie'}</h3></div><a class="drawer-close" href="/settings?section=typologies">×</a></header>
                <form method="post" action="/settings/typologies/save" class="drawer-form">
                  <input type="hidden" name="id" value="{h(typology_edit['id'] if typology_edit else '')}">
                  {field('sigle', 'Sigle', typology_edit['sigle'] if typology_edit else '', required=True)}
                  {textarea_field('libelle_complet', 'Libellé complet', 'Utilisé dans les PDF et fichiers Excel.', typology_edit['libelle_complet'] if typology_edit else '')}
                  {select_field('is_active', 'État', [('1','Active'),('0','Inactive')], str(typology_edit['is_active']) if typology_edit else '1')}
                  <footer class="drawer-actions"><a class="outline-button" href="/settings?section=typologies">Annuler</a><button type="submit">Enregistrer</button></footer>
                </form></aside>"""
            content = f"""{alert}<div class="settings-section-heading"><a class="outline-button" href="/settings"><i data-lucide="arrow-left"></i> Paramètres</a></div>
            <section class="panel settings-branches"><header><div><h2>Typologies</h2><small>Le sigle apparaît dans l’application. Le libellé complet apparaît dans les documents.</small></div>{'<a class="button-link primary-blue" href="/settings?section=typologies&new_typology=1"><i data-lucide="plus"></i> Nouvelle typologie</a>' if can_manage_typologies else ''}</header>{table(['Sigle','Libellé complet','État','Actions'], typology_rows)}</section>{typology_drawer}"""
        elif section == "enterprise" or edit_company or new_branch or branch_edit:
            content = f"""{alert}<div class="settings-section-heading"><a class="outline-button" href="/settings"><i data-lucide="arrow-left"></i> Paramètres</a></div><section class="settings-company-summary"><div class="settings-company-logo">{logo}</div><div><small>Entreprise</small><h2>{h(company['nom'] or 'Entreprise')}</h2><p>{h(company['adresse'] or 'Adresse non renseignée')}</p></div><dl><div><dt>RGC</dt><dd>{h(company['rgc'] or '—')}</dd></div><div><dt>NIF</dt><dd>{h(company['nif'] or '—')}</dd></div><div><dt>Compte</dt><dd>{h(company['numero_compte'] or '—')}</dd></div></dl><a class="icon-link" href="/settings?section=enterprise&edit_company=1" title="Modifier"><i data-lucide="pencil"></i></a></section>
            <section class="panel settings-branches"><header><div><h2>Directions de {h(company['nom'] or 'l’entreprise')}</h2><small>Organisation opérationnelle et isolation des données.</small></div><a class="button-link primary-blue" href="/settings?section=enterprise&new_branch=1"><i data-lucide="plus"></i> Nouvelle direction</a></header>{table(['Sigle','Direction','Adresse','Utilisateurs','BC','État','Actions'], branch_rows)}</section>{company_drawer}{branch_drawer}"""
        else:
            content = f"""{alert}<section class="settings-grid">{''.join(f'<a class="settings-card" href="{url}"><strong>{h(title)}</strong><span>{h(description)}</span></a>' for url,title,description in cards)}</section>"""
        self.respond(layout("Parametres", content, subtitle="Administration et configuration du produit"))

    def do_POST(self):
        path = self.path.split("?")[0]
        ensure_default_admin()
        try:
            if self.content_length() > MAX_REQUEST_BYTES:
                return self.respond("Request Entity Too Large", status=413, content_type="text/plain")
            if not self.valid_csrf_post(path):
                return self.csrf_rejected(path)
        except ValueError:
            return self.respond("Bad Request", status=400, content_type="text/plain")
        if path == "/setup":
            return self.setup_post()
        if path == "/login":
            return self.login_post()
        if path in {"/logout", "/change-password"}:
            if not self.current_user():
                return self.respond("Forbidden", status=403, content_type="text/plain")
        elif path in {"/backup/create", "/backup/restore", "/users/create", "/users/password", "/users/role", "/users/active", "/users/unlock", "/users/delete", "/users/cleanup-tests", "/license/install"}:
            if not self.require_admin_access():
                return self.respond("Forbidden", status=403, content_type="text/plain")
        elif path == "/invoices":
            permission = "invoice.edit_draft" if self.form().get("id") else "invoice.create"
            if not self.require_permission(permission):
                return self.respond("Forbidden", status=403, content_type="text/plain")
        elif path == "/table-facturation":
            if not self.require_permission("invoice.edit_draft"):
                return self.respond("Forbidden", status=403, content_type="text/plain")
        elif path == "/table-facturation-new/update":
            if not self.current_user():
                return self.respond("Forbidden", status=403, content_type="text/plain")
        elif path in {"/purchase-orders", "/purchase-orders/document", "/sites"}:
            if path == "/purchase-orders" and self.form().get("id"):
                if not self.require_permission("purchase_order.edit"):
                    return self.respond("Forbidden", status=403, content_type="text/plain")
            elif path == "/purchase-orders":
                if not self.require_write_access():
                    return self.respond("Forbidden", status=403, content_type="text/plain")
            elif not self.require_permission("purchase_order.edit"):
                return self.respond("Forbidden", status=403, content_type="text/plain")
        elif path in {"/clients/save", "/clients/directions/save"}:
            if not self.require_permission("client.manage"):
                return self.respond("Forbidden", status=403, content_type="text/plain")
        elif path == "/invoices/delete":
            if not self.require_permission("invoice.cancel"):
                return self.respond("Forbidden", status=403, content_type="text/plain")
        elif path in {"/purchase-orders/delete", "/purchase-orders/document/delete", "/purchase-orders/site/delete"}:
            if not self.require_permission("purchase_order.edit"):
                return self.respond("Forbidden", status=403, content_type="text/plain")
        elif path in {"/mobilis/delete", "/sites/delete", "/archives/delete"}:
            if not self.require_write_access():
                return self.respond("Forbidden", status=403, content_type="text/plain")
        elif path == "/company-branches/save":
            if not self.require_permission("company_branch.manage"):
                return self.respond("Forbidden", status=403, content_type="text/plain")
        elif path in {
            "/settings/typologies/save", "/settings/partners/save",
            "/settings/partners/import/preview", "/settings/partners/import/confirm",
            "/purchase-orders/import/preview", "/purchase-orders/import/confirm",
        }:
            if not user_is_super_admin(self.current_user()):
                return self.respond("Forbidden", status=403, content_type="text/plain")
        elif path in {"/bpu/st/import", "/bpu/st/confirm", "/bpu/st/activate"}:
            if not self.require_permission("bpu_st.manage"):
                return self.respond("Forbidden", status=403, content_type="text/plain")
        elif not self.require_write_access():
            return self.respond("Forbidden", status=403, content_type="text/plain")
        routes = {
            "/logout": self.logout,
            "/change-password": self.change_own_password_post,
            "/backup/create": self.create_backup_post,
            "/backup/restore": self.restore_backup_post,
            "/license/install": self.install_license_post,
            "/users/create": self.create_user_post,
            "/users/password": self.change_user_password_post,
            "/users/role": self.change_user_role_post,
            "/users/active": self.change_user_active_post,
            "/users/unlock": self.unlock_user_post,
            "/users/delete": self.delete_user_post,
            "/users/cleanup-tests": self.cleanup_test_users_post,
            "/table-facturation": self.save_table_facturation,
            "/table-facturation-new/update": self.update_table_facturation_new,
            "/company": self.save_company,
            "/mobilis": self.save_mobilis,
            "/mobilis/client-settings": self.save_mobilis_client_settings,
            "/clients/save": self.save_client,
            "/clients/directions/save": self.save_client_direction,
            "/mobilis/delete": lambda: self.soft_delete("mobilis_directions", "/mobilis"),
            "/purchase-orders/delete": lambda: self.soft_delete("purchase_orders", "/purchase-orders"),
            "/purchase-orders/document/delete": self.delete_purchase_order_document,
            "/purchase-orders/site/delete": self.delete_purchase_order_site_post,
            "/sites/delete": lambda: self.soft_delete("sites", "/sites"),
            "/invoices/delete": lambda: self.soft_delete("invoices", "/invoices"),
            "/archives/delete": self.delete_archive,
            "/company-branches/save": self.save_company_branch,
            "/settings/typologies/save": self.save_typology,
            "/settings/partners/save": self.save_site_partner,
            "/settings/partners/import/preview": self.preview_partner_import,
            "/settings/partners/import/confirm": self.confirm_partner_import,
            "/contract": self.save_contract,
            "/bpu": self.save_bpu,
            "/bpu/st/import": self.import_bpu_st_post,
            "/bpu/st/confirm": self.confirm_bpu_st_post,
            "/bpu/st/activate": self.activate_bpu_st_post,
            "/purchase-orders": self.save_purchase_order,
            "/purchase-orders/import/preview": self.preview_purchase_order_import,
            "/purchase-orders/import/confirm": self.confirm_purchase_order_import,
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
        if hasattr(self, "_cached_form"):
            return self._cached_form
        length = self.content_length()
        if length > MAX_REQUEST_BYTES:
            raise ValueError("Requete trop volumineuse.")
        if not hasattr(self, "_cached_body"):
            self._cached_body = self.rfile.read(length)
        data = self._cached_body.decode("utf-8")
        self._cached_form = {key: values[0].strip() for key, values in parse_qs(data).items()}
        return self._cached_form

    def multipart_form(self):
        if hasattr(self, "_cached_multipart"):
            return self._cached_multipart
        values, files = {}, {}
        content_type = self.headers.get("Content-Type", "")
        boundary_token = "boundary="
        if boundary_token not in content_type:
            return self.form(), files
        boundary = ("--" + content_type.split(boundary_token, 1)[1].split(";", 1)[0].strip().strip('"')).encode()
        length = self.content_length()
        if length > MAX_REQUEST_BYTES:
            raise ValueError("Requete trop volumineuse.")
        if not hasattr(self, "_cached_body"):
            self._cached_body = self.rfile.read(length)
        body = self._cached_body
        parts = body.split(boundary)
        if len(parts) > MAX_MULTIPART_PARTS + 2:
            raise ValueError("Trop de champs multipart.")
        for part in parts:
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
        self._cached_multipart = (values, files)
        return self._cached_multipart

    def content_length(self):
        raw = self.headers.get("Content-Length", "0")
        try:
            length = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("Content-Length invalide.") from exc
        if length < 0:
            raise ValueError("Content-Length invalide.")
        return length

    def redirect(self, path):
        self.send_response(303)
        self.send_header("Location", path)
        self.end_headers()

    def soft_delete(self, table, redirect_to):
        if table not in {"mobilis_directions", "purchase_orders", "sites", "invoices"}:
            return self.respond("Bad Request", status=400, content_type="text/plain")
        row_id = self.form().get("id", "")
        changed = False
        if row_id:
            with db() as con:
                if table == "invoices":
                    row = con.execute("SELECT depos FROM invoices WHERE id=? AND deleted_at IS NULL", (row_id,)).fetchone()
                    if row and row["depos"]:
                        return self.redirect(f"{redirect_to}?message=Facture deposee: suppression interdite")
                if table == "purchase_orders":
                    used = con.execute(
                        "SELECT 1 FROM invoices WHERE purchase_order_id=? AND deleted_at IS NULL LIMIT 1",
                        (row_id,),
                    ).fetchone()
                    if used:
                        return self.redirect(f"{redirect_to}?message=BC utilise: suppression interdite")
                if table == "sites":
                    used = con.execute(
                        """
                        SELECT 1 FROM invoices WHERE site_id=? AND deleted_at IS NULL
                        UNION ALL
                        SELECT 1 FROM invoice_sites xis JOIN invoices i ON i.id=xis.invoice_id
                        WHERE xis.site_id=? AND i.deleted_at IS NULL
                        LIMIT 1
                        """,
                        (row_id, row_id),
                    ).fetchone()
                    if used:
                        return self.redirect(f"{redirect_to}&message=Site utilise: suppression interdite" if "?" in redirect_to else f"{redirect_to}?message=Site utilise: suppression interdite")
                cursor = con.execute(
                    f"UPDATE {table} SET deleted_at=CURRENT_TIMESTAMP, deleted_by=?, updated_by=? WHERE id=? AND deleted_at IS NULL",
                    (current_actor(), current_actor(), row_id),
                )
                changed = cursor.rowcount == 1
            if changed:
                audit("soft_delete", table, row_id)
        self.redirect(f"{redirect_to}?message=Element supprime")

    def delete_purchase_order_site_post(self):
        values = self.form()
        po_id = values.get("po_id", "")
        return self.soft_delete("sites", f"/purchase-orders?view_id={quote(po_id)}")

    def respond(self, body, status=200, content_type="text/html; charset=utf-8", headers=None):
        if content_type.startswith("text/html"):
            body = self.inject_csrf_inputs(body)
        encoded = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
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

    def docs_file(self, path):
        docs_root = (ROOT_DIR / "docs").resolve()
        target = (ROOT_DIR / path.lstrip("/")).resolve()
        if not str(target).startswith(str(docs_root)) or not target.exists() or not target.is_file():
            return self.respond("Not found", status=404, content_type="text/plain")
        content_type = "application/pdf" if target.suffix.lower() == ".pdf" else "application/octet-stream"
        self.respond(target.read_bytes(), content_type=content_type)

    def uploaded_file(self, path):
        try:
            target = resolve_stored_upload_path(path)
        except ValueError:
            return self.respond("Not found", status=404, content_type="text/plain")
        if not target.exists() or not target.is_file():
            return self.respond("Not found", status=404, content_type="text/plain")
        content_types = {
            ".pdf": "application/pdf",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".gif": "image/gif",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".xls": "application/vnd.ms-excel",
        }
        content_type = content_types.get(target.suffix.lower(), "application/octet-stream")
        headers = {}
        if target.suffix.lower() in {".xls", ".xlsx"}:
            headers["Content-Disposition"] = f'attachment; filename="{target.name}"'
        self.respond(target.read_bytes(), content_type=content_type, headers=headers)

    def exported_file(self, path):
        try:
            target = resolve_export_path(path)
        except ValueError:
            return self.respond("Not found", status=404, content_type="text/plain")
        if not target.exists() or not target.is_file() or target.suffix.lower() != ".pdf":
            return self.respond("Not found", status=404, content_type="text/plain")
        content = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Disposition", f'inline; filename="{target.name}"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def archives(self):
        query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        message = query.get("message", [""])[0]
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        files = sorted(EXPORT_DIR.glob("*.pdf"), key=lambda path: path.stat().st_mtime, reverse=True)
        grouped = {}
        for path in files:
            stat = path.stat()
            document_type, invoice_number = archive_metadata(path)
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
        filename = Path(self.form().get("file", "")).name
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

    def login(self):
        if first_run_required():
            return self.redirect("/setup")
        query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        message = query.get("message", [""])[0]
        alert = f'<section class="alert">{h(message)}</section>' if message else ""
        csrf_token = self.public_csrf_token()
        body = f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Connexion - PhoEniX BPU</title><link rel="stylesheet" href="/static/style.css"></head>
<body class="auth-page"><main class="auth-card">
<div class="auth-brand"><strong>PhoEniX BPU</strong><span>Facturation</span></div>
<h1>Connexion</h1>{alert}
<form method="post" action="/login" class="auth-form">
  <input type="hidden" name="csrf_token" value="{h(csrf_token)}">
  {field('username', 'Utilisateur', DEFAULT_ADMIN_USERNAME, required=True, autocomplete='username')}
  {field('password', 'Mot de passe', '', field_type='password', required=True, autocomplete='current-password')}
  <button type="submit">Se connecter</button>
</form>
</main><script src="/static/app-ui.js"></script></body></html>"""
        self.respond(body, headers={"Set-Cookie": f"{PUBLIC_CSRF_COOKIE}={csrf_token}; HttpOnly; SameSite=Lax; Path=/"})

    def first_run_setup(self):
        if not first_run_required():
            return self.redirect("/login")
        query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        message = query.get("message", [""])[0]
        alert = f'<section class="alert">{h(message)}</section>' if message else ""
        csrf_token = self.public_csrf_token()
        body = f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Configuration initiale - PhoEniX BPU</title><link rel="stylesheet" href="/static/style.css"></head>
<body class="auth-page"><main class="auth-card auth-card-wide">
<div class="auth-brand"><strong>PhoEniX BPU</strong><span>Facturation</span></div>
<h1>Configuration initiale</h1>{alert}
<form method="post" action="/setup" class="auth-form">
  <input type="hidden" name="csrf_token" value="{h(csrf_token)}">
  {field('new_password', 'Nouveau mot de passe admin', '', field_type='password', required=True, autocomplete='new-password')}
  {field('confirm_password', 'Confirmation', '', field_type='password', required=True, autocomplete='new-password')}
  <button type="submit">Initialiser</button>
</form>
<p><small>Le mot de passe doit contenir au moins 8 caracteres, avec lettres et chiffres.</small></p>
</main><script src="/static/app-ui.js"></script></body></html>"""
        self.respond(body, headers={"Set-Cookie": f"{PUBLIC_CSRF_COOKIE}={csrf_token}; HttpOnly; SameSite=Lax; Path=/"})

    def setup_post(self):
        if not first_run_required():
            return self.redirect("/login")
        values = self.form()
        if values.get("new_password") != values.get("confirm_password"):
            return self.redirect("/setup?message=Confirmation invalide")
        try:
            user = complete_first_run_setup(values.get("new_password", ""))
        except ValueError as exc:
            return self.redirect(f"/setup?message={quote(str(exc))}")
        token = create_session(user["id"])
        audit("first_run_setup", "user", user["id"], user["username"])
        self.send_response(303)
        self.send_header("Location", "/change-password" if user["must_change_password"] else "/")
        self.send_header("Set-Cookie", f"{SESSION_COOKIE}={token}; HttpOnly; SameSite=Lax; Path=/")
        self.send_header("Set-Cookie", f"{PUBLIC_CSRF_COOKIE}=; Max-Age=0; HttpOnly; SameSite=Lax; Path=/")
        self.end_headers()

    def change_password(self):
        user = self.current_user()
        if not user:
            return self.redirect("/login")
        query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        message = query.get("message", [""])[0]
        alert = f'<section class="alert">{h(message)}</section>' if message else ""
        content = f"""{alert}<section class="panel auth-card auth-card-wide">
        <h2>Changement obligatoire du mot de passe</h2>
        <p>Définissez un mot de passe personnel avant d'accéder aux données métier.</p>
        <form method="post" action="/change-password" class="auth-form">
          {field('current_password', 'Mot de passe actuel', '', field_type='password', required=True, autocomplete='current-password')}
          {field('new_password', 'Nouveau mot de passe', '', field_type='password', required=True, autocomplete='new-password')}
          {field('confirm_password', 'Confirmation', '', field_type='password', required=True, autocomplete='new-password')}
          <button type="submit">Changer le mot de passe</button>
        </form></section>"""
        self.respond(layout("Mot de passe", content))

    def change_own_password_post(self):
        user = self.current_user()
        if not user:
            return self.respond("Forbidden", status=403, content_type="text/plain")
        values = self.form()
        if values.get("new_password") != values.get("confirm_password"):
            return self.redirect("/change-password?message=Confirmation invalide")
        try:
            change_own_password(user["id"], values.get("current_password", ""), values.get("new_password", ""))
        except ValueError as exc:
            return self.redirect(f"/change-password?message={quote(str(exc))}")
        audit("password_change", "user", user["id"], user["username"])
        self.send_response(303)
        self.send_header("Location", "/login?message=Mot de passe modifie, reconnectez-vous")
        self.send_header("Set-Cookie", f"{SESSION_COOKIE}=; Max-Age=0; HttpOnly; SameSite=Lax; Path=/")
        self.end_headers()

    def login_post(self):
        values = self.form()
        user = authenticate(values.get("username", ""), values.get("password", ""))
        if not user:
            return self.redirect("/login?message=Identifiants invalides")
        token = create_session(user["id"])
        audit("login", "user", user["id"], user["username"])
        self.send_response(303)
        self.send_header("Location", "/change-password" if user["must_change_password"] else "/")
        self.send_header("Set-Cookie", f"{SESSION_COOKIE}={token}; HttpOnly; SameSite=Lax; Path=/")
        self.end_headers()

    def logout(self):
        user = self.current_user()
        delete_session(self.cookie_value(SESSION_COOKIE))
        if user:
            audit("logout", "user", user["id"], user["username"])
        self.send_response(303)
        self.send_header("Location", "/login")
        self.send_header("Set-Cookie", f"{SESSION_COOKIE}=; Max-Age=0; HttpOnly; SameSite=Lax; Path=/")
        self.end_headers()

    def users(self):
        if not self.require_admin_access():
            return self.respond("Forbidden", status=403, content_type="text/plain")
        query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        message = query.get("message", [""])[0]
        search = query.get("q", [""])[0].strip()
        role_filter = query.get("role", [""])[0]
        branch_filter = query.get("branch", [""])[0]
        status_filter = query.get("status", [""])[0]
        edit_id = query.get("edit_id", [""])[0]
        password_id = query.get("password_id", [""])[0]
        new_mode = query.get("new", [""])[0] == "1"
        current_user = self.current_user()
        can_create_super_admin = user_is_super_admin(current_user)
        with db() as con:
            branches = con.execute(
                "SELECT id, name, sigle FROM company_branches WHERE is_active=1 ORDER BY sigle, name"
            ).fetchall()
            users = con.execute(
                """SELECT u.id,u.username,u.role,u.company_branch_id,u.is_active,u.created_at,
                          u.last_login_at,u.locked_until,cb.name AS branch_name,cb.sigle AS branch_sigle
                   FROM users u LEFT JOIN company_branches cb ON cb.id=u.company_branch_id
                   ORDER BY u.username"""
            ).fetchall()
        role_options = ([('super_admin', 'Super Admin')] if can_create_super_admin else []) + [
            ('admin', 'Admin'), ('editor', 'Éditeur'), ('viewer', 'Lecteur')
        ]
        role_labels = dict(role_options + [('super_admin', 'Super Admin')])
        branch_options = [(row['id'], f"{row['sigle']} — {row['name']}") for row in branches]
        filtered_users = []
        for user in users:
            status = "locked" if user["locked_until"] else ("active" if user["is_active"] else "inactive")
            if search and search.casefold() not in user["username"].casefold():
                continue
            if role_filter and user["role"] != role_filter:
                continue
            if branch_filter and str(user["company_branch_id"] or "") != branch_filter:
                continue
            if status_filter and status != status_filter:
                continue
            filtered_users.append((user, status))
        stats = {
            "active": sum(1 for user in users if user["is_active"]),
            "inactive": sum(1 for user in users if not user["is_active"]),
            "locked": sum(1 for user in users if user["locked_until"]),
            "super_admin": sum(1 for user in users if user["role"] == "super_admin"),
        }
        rows = []
        for user, status in filtered_users:
            is_active = bool(user["is_active"])
            user_id = user["id"]
            username = user["username"]
            status_label = {"active":"Actif", "inactive":"Inactif", "locked":"Verrouillé"}[status]
            unlock_action = f'<form method="post" action="/users/unlock"><input type="hidden" name="id" value="{user_id}"><button type="submit">Déverrouiller</button></form>' if status == "locked" else ''
            rows.append([
                f'<span class="user-identity"><b>{h(initials(username))}</b><strong>{h(username)}</strong></span>',
                f'<span class="role-badge role-{h(user["role"])}">{h(role_labels.get(user["role"], user["role"]))}</span>',
                h(user["branch_sigle"] or "Toutes"),
                f'<span class="user-status user-status-{status}">{status_label}</span>',
                h(user["last_login_at"] or "Jamais"),
                h(user["locked_until"] or "—"),
                f'''<div class="user-actions-menu"><button type="button" class="icon-link user-menu-trigger" aria-label="Actions">⋮</button><div class="user-menu" hidden>
                  <a href="/users?edit_id={user_id}">Modifier</a><a href="/users?password_id={user_id}">Réinitialiser le mot de passe</a>
                  <form method="post" action="/users/active"><input type="hidden" name="id" value="{user_id}"><input type="hidden" name="active" value="{'0' if is_active else '1'}"><button type="submit">{'Désactiver' if is_active else 'Activer'}</button></form>
                  {unlock_action}<form method="post" action="/users/delete"><input type="hidden" name="id" value="{user_id}"><button type="submit" class="danger-link" onclick="return confirm('Supprimer {h(username)} ?')">Supprimer</button></form>
                </div></div>''',
            ])
        alert = f'<section class="alert">{h(message)}</section>' if message else ""
        drawer = ""
        selected_user = next((user for user in users if str(user["id"]) in {edit_id, password_id}), None)
        if new_mode or selected_user:
            password_mode = bool(password_id)
            title = "Réinitialiser le mot de passe" if password_mode else ("Modifier l’utilisateur" if selected_user else "Nouvel utilisateur")
            if password_mode:
                form_action = "/users/password"
                form_fields = f'<input type="hidden" name="id" value="{selected_user["id"]}">{field("password", "Nouveau mot de passe", field_type="password", required=True)}'
            elif selected_user:
                form_action = "/users/role"
                form_fields = f'<input type="hidden" name="id" value="{selected_user["id"]}">{field("username_display", "Utilisateur", selected_user["username"])}{select_field("role", "Rôle", role_options, selected_user["role"])}{select_field("company_branch_id", "Direction entreprise", branch_options, selected_user["company_branch_id"] or "")}'
            else:
                form_action = "/users/create"
                form_fields = f'{field("username", "Utilisateur", required=True)}{field("password", "Mot de passe", field_type="password", required=True)}{select_field("role", "Rôle", role_options, "viewer")}{select_field("company_branch_id", "Direction entreprise", branch_options, branch_options[0][0] if branch_options else "")}'
            drawer = f'<aside class="entity-drawer users-drawer"><header class="drawer-header"><div><small>Utilisateurs</small><h3>{title}</h3></div><a class="drawer-close" href="/users">×</a></header><form method="post" action="{form_action}" class="drawer-form">{form_fields}<footer class="drawer-actions"><a class="outline-button" href="/users">Annuler</a><button type="submit">Enregistrer</button></footer></form></aside>'
        content = f"""{alert}<div class="entity-page-header"><h2>Utilisateurs</h2><a class="button-link primary-blue" href="/users?new=1"><i data-lucide="plus"></i> Nouvel utilisateur</a></div>
        <section class="user-stat-grid">{''.join(f'<article><span>{label}</span><strong>{stats[key]}</strong></article>' for key,label in [('active','Actifs'),('inactive','Désactivés'),('locked','Verrouillés'),('super_admin','Super Admin')])}</section>
        <form class="panel user-filter-bar" method="get"><label><span>Recherche</span><input name="q" value="{h(search)}" placeholder="Nom utilisateur"></label>{select_field('role','Rôle',[('', 'Tous les rôles'),*role_options],role_filter)}{select_field('branch','Direction entreprise',[('', 'Toutes'),*branch_options],branch_filter)}{select_field('status','État',[('', 'Tous'),('active','Actif'),('inactive','Inactif'),('locked','Verrouillé')],status_filter)}<button type="submit" class="outline-button">Filtrer</button><a class="icon-link" href="/users" title="Réinitialiser"><i data-lucide="rotate-ccw"></i></a></form>
        <section class="panel users-table-panel">{table(['Utilisateur','Rôle','Direction entreprise','État','Dernière connexion','Verrouillage','Actions'], rows)}</section>{drawer}
        <script>document.querySelectorAll('.user-menu-trigger').forEach(button=>button.addEventListener('click',event=>{{event.stopPropagation();const menu=button.nextElementSibling;document.querySelectorAll('.user-menu').forEach(other=>{{if(other!==menu)other.hidden=true}});menu.hidden=!menu.hidden;}}));document.addEventListener('click',()=>document.querySelectorAll('.user-menu').forEach(menu=>menu.hidden=true));</script>"""
        self.respond(layout("Utilisateurs", content))

    def create_user_post(self):
        values = self.form()
        try:
            require_feature("create_user")
            if values.get("role") == "super_admin" and not user_is_super_admin(self.current_user()):
                raise ValueError("Seul un Super Admin peut créer ce rôle.")
            create_user(
                values.get("username", ""), values.get("password", ""),
                values.get("role", "viewer"), values.get("company_branch_id") or None,
            )
            audit("user_create", "user", "", values.get("username", ""))
            self.redirect("/users?message=Utilisateur cree")
        except Exception as exc:
            self.redirect(f"/users?message={quote('Erreur utilisateur: ' + str(exc))}")

    def change_user_password_post(self):
        values = self.form()
        try:
            update_user_password(int(values.get("id", "0")), values.get("password", ""))
            audit("user_password_reset", "user", values.get("id", ""))
            self.redirect("/users?message=Mot de passe modifie")
        except Exception as exc:
            self.redirect(f"/users?message={quote('Erreur mot de passe: ' + str(exc))}")

    def change_user_role_post(self):
        values = self.form()
        try:
            if values.get("role") == "super_admin" and not user_is_super_admin(self.current_user()):
                raise ValueError("Seul un Super Admin peut attribuer ce rôle.")
            set_user_role(
                int(values.get("id", "0")), values.get("role", "viewer"),
                values.get("company_branch_id") or None,
            )
            audit("user_role_change", "user", values.get("id", ""), values.get("role", ""))
            self.redirect("/users?message=Role modifie")
        except Exception as exc:
            self.redirect(f"/users?message={quote('Erreur role: ' + str(exc))}")

    def change_user_active_post(self):
        values = self.form()
        try:
            set_user_active(int(values.get("id", "0")), values.get("active") == "1")
            audit("user_active_change", "user", values.get("id", ""))
            self.redirect("/users?message=Etat modifie")
        except Exception as exc:
            self.redirect(f"/users?message={quote('Erreur etat: ' + str(exc))}")

    def unlock_user_post(self):
        values = self.form()
        try:
            unlock_user(int(values.get("id", "0")))
            audit("user_unlock", "user", values.get("id", ""))
            self.redirect("/users?message=Utilisateur deverrouille")
        except Exception as exc:
            self.redirect(f"/users?message={quote('Erreur deverrouillage: ' + str(exc))}")

    def delete_user_post(self):
        values = self.form()
        try:
            user_id = int(values.get("id", "0"))
            delete_user(user_id)
            audit("user_delete", "user", user_id)
            self.redirect("/users?message=Utilisateur supprime")
        except Exception as exc:
            self.redirect(f"/users?message={quote('Erreur suppression: ' + str(exc))}")

    def cleanup_test_users_post(self):
        try:
            delete_test_users()
            audit("user_cleanup_tests", "user")
            self.redirect("/users?message=Utilisateurs de test nettoyes")
        except Exception as exc:
            self.redirect(f"/users?message={quote('Erreur nettoyage: ' + str(exc))}")

    def backup(self):
        if not self.require_admin_access():
            return self.respond("Forbidden", status=403, content_type="text/plain")
        rows = []
        for path in list_backups():
            stat = path.stat()
            rows.append([
                f'<a href="/backups/{quote(path.name)}">{h(path.name)}</a>',
                h(datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")),
                f"{stat.st_size / 1024:.1f} Ko",
            ])
        content = f"""
        <section class="panel">
          <h2>Sauvegarde</h2>
          <form method="post" action="/backup/create"><button type="submit">Creer une sauvegarde</button></form>
        </section>
        <section class="panel">
          <h2>Restaurer</h2>
          <form method="post" action="/backup/restore" enctype="multipart/form-data">
            {file_field('backup_file', 'Fichier SQLite', '.sqlite3,.db')}
            <button type="submit">Restaurer</button>
          </form>
        </section>
        <section class="panel"><h2>Historique</h2>{table(['Fichier', 'Date', 'Taille'], rows)}</section>
        """
        self.respond(layout("Backup", content))

    def backup_file(self, path):
        if not self.require_admin_access():
            return self.respond("Forbidden", status=403, content_type="text/plain")
        filename = Path(path.removeprefix("/backups/")).name
        target = next((item for item in list_backups() if item.name == filename), None)
        if not target or not target.exists():
            return self.respond("Not found", status=404, content_type="text/plain")
        content = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Disposition", f'attachment; filename="{target.name}"')
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def status(self):
        checks = []
        try:
            with db() as con:
                invoice_count = con.execute("SELECT COUNT(*) FROM invoices WHERE deleted_at IS NULL").fetchone()[0]
                schema_version = con.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
                ).fetchone()[0]
                permission_count = con.execute(
                    "SELECT COUNT(*) FROM permissions"
                ).fetchone()[0]
                lifecycle_views = con.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type='view'
                      AND name IN ('invoice_lifecycle', 'invoice_drafts', 'issued_invoices')
                    """
                ).fetchone()[0]
                locking_guards = con.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type='trigger'
                      AND (
                          name LIKE 'trg_invoices_lock_%'
                          OR name LIKE 'trg_invoices_no_%issued'
                          OR name LIKE 'trg_invoice_lines_lock_%'
                          OR name LIKE 'trg_invoice_sites_lock_%'
                          OR name='trg_invoice_tracking_no_cancelled_update'
                      )
                    """
                ).fetchone()[0]
                migration_reviews = con.execute(
                    """
                    SELECT COUNT(*) FROM invoice_tracking
                    WHERE migration_review_required=1
                    """
                ).fetchone()[0]
            checks.append(["Database", "OK"])
            checks.append(["Schema", f"V{schema_version}"])
            checks.append(["Lifecycle views", f"{lifecycle_views} / 3"])
            checks.append(["Invoice permissions", str(permission_count)])
            checks.append(["Locking guards", str(locking_guards)])
            checks.append(["Migration reviews", str(migration_reviews)])
            checks.append(["Invoices", str(invoice_count)])
        except Exception as exc:
            checks.append(["Database", "ERROR: " + str(exc)])
        backups = list_backups()
        checks.append(["Last backup", backups[0].name if backups else "Aucune"])
        checks.append(["Version", APP_VERSION])
        checks.append(["Host", f"{os.environ.get('PHOENIX_HOST', '127.0.0.1')}:{os.environ.get('PHOENIX_PORT', '8000')}"])
        self.respond(layout("Status", table(["Check", "Etat"], checks)))

    def about(self):
        content = f"""
        <section class="panel">
          <h2>PhoEniX BPU</h2>
          <p>Application locale de facturation BPU pour SAPTA / Mobilis.</p>
          {table(['Information', 'Valeur'], [
              ['Version', h(APP_VERSION)],
              ['Mode', 'Local Windows'],
              ['Base de donnees', h(str(DB_PATH))],
              ['Sauvegardes', h(str(BACKUP_DIR))],
          ])}
        </section>
        <section class="panel">
          <h2>Support</h2>
          <p>Avant toute intervention, creer une sauvegarde depuis la page Backup.</p>
          {table(['Action', 'Emplacement'], [
              ['Documentation utilisateur', '<a href="/static/user-guide.html">Ouvrir le guide</a>'],
              ['Guide PDF', '<a href="/docs/Guide_utilisateur_PhoEniX_BPU.pdf">Ouvrir le PDF</a>'],
              ['Changelog', '<a href="/static/changelog.html">Ouvrir le changelog</a>'],
              ['Diagnostic', '<a href="/status">Voir le statut technique</a>'],
          ])}
        </section>
        """
        self.respond(layout("A propos", content))

    def license_page(self):
        if not self.require_admin_access():
            return self.respond("Forbidden", status=403, content_type="text/plain")
        query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        message = query.get("message", [""])[0]
        status = license_status()
        expiration_label = "À vie" if status.get("is_perpetual") else status["expires_at"]
        invoice_limit = "Illimitées" if status.get("unlimited_invoices") else status["max_invoices"]
        rows = [
            ["Edition", h(status["edition"])],
            ["Client", h(status["customer"])],
            ["Expiration", h(expiration_label)],
            ["Utilisateurs", f"{status['user_count']} / {status['max_users']}"],
            ["Factures", f"{status['invoice_count']} / {invoice_limit}"],
            ["Identifiant appareil", f'<code class="machine-id">{h(status["machine_id"])}</code>'],
            ["Etat", "Valide" if status["is_valid"] else "Expiree"],
        ]
        if status.get("is_demo"):
            rows.append(["Demo", f"{status.get('days_left', 0)} jours restants"])
        alert = f'<section class="alert">{h(message)}</section>' if message else ""
        content = f"""
        {alert}
        <section class="panel"><h2>Licence</h2>{table(['Champ', 'Valeur'], rows)}</section>
        <section class="panel">
          <h2>Activer</h2>
          <p>Téléchargez la demande d’activation et transmettez-la à votre fournisseur. La licence reçue sera valable uniquement sur cet appareil.</p>
          <p><a class="button-link outline-button" href="/license/request"><i data-lucide="download"></i> Télécharger la demande d’activation</a></p>
          <form method="post" action="/license/install" enctype="multipart/form-data">
            {file_field('license_file', 'Fichier licence', '.json,.license')}
            <button type="submit">Activer</button>
          </form>
        </section>
        """
        self.respond(layout("Licence", content))

    def license_request(self):
        if not self.require_admin_access():
            return self.respond("Forbidden", status=403, content_type="text/plain")
        document = activation_request(APP_VERSION)
        machine_suffix = document["request"]["machine_id"].replace("PHX-", "")[:8]
        filename = f"PhoEniX_Activation_Request_{machine_suffix}.json"
        self.respond(
            json.dumps(document, ensure_ascii=False, indent=2),
            content_type="application/json; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )

    def install_license_post(self):
        _, files = self.multipart_form()
        if "license_file" not in files:
            return self.redirect("/license?message=Fichier manquant")
        try:
            payload = install_license(files["license_file"]["content"])
            audit("license_install", "license", payload.get("customer", ""))
            self.redirect("/license?message=Licence activee")
        except Exception as exc:
            self.redirect(f"/license?message={quote('Erreur licence: ' + str(exc))}")

    def create_backup_post(self):
        path = create_backup()
        audit("backup_create", "backup", path.name)
        self.redirect(f"/backup?message={quote('Sauvegarde creee: ' + path.name)}")

    def restore_backup_post(self):
        _, files = self.multipart_form()
        if "backup_file" not in files:
            return self.redirect("/backup?message=Fichier manquant")
        try:
            restore_backup_content(files["backup_file"]["content"])
            audit("backup_restore", "backup", files["backup_file"]["filename"])
            self.redirect("/backup?message=Base restauree")
        except Exception as exc:
            self.redirect(f"/backup?message={quote('Erreur restauration: ' + str(exc))}")

    def bpu_item(self):
        query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        article_number = query.get("article_number", [""])[0].strip()
        if not article_number:
            return self.respond_json({"error": "missing article_number"}, status=400)
        with db() as con:
            row = con.execute("""
                SELECT article_number, designation, unite, pu_ht, categorie
                FROM bpu_items
                WHERE article_number=? AND is_active=1
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

    def bpu_search(self):
        query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        search = query.get("q", [""])[0].strip()
        numbering_system = query.get("numbering_system", ["GENERAL"])[0].strip().upper()
        if not search:
            return self.respond_json({"items": []})
        with db() as con:
            items = search_bpu_items(con, search, numbering_system)
        return self.respond_json({"items": items, "numbering_system": numbering_system})

    def templates(self):
        with db() as con:
            settings = template_settings(con)
            company_logo_row = con.execute("SELECT logo_path FROM company_settings WHERE id=1").fetchone()
            client_logo_row = con.execute("SELECT logo_path FROM mobilis_directions WHERE deleted_at IS NULL AND logo_path IS NOT NULL AND logo_path<>'' ORDER BY id LIMIT 1").fetchone()
            sample_invoice = con.execute("""
                SELECT i.invoice_number, i.invoice_type, s.code_site, s.nom_site, s.typologie_site
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
                preview_html = f'<div class="sample-site"><b>Projet</b><span>:</span><span>Extension du réseau</span><b>Site</b><span>:</span><span>{h(sample_invoice["nom_site"] if sample_invoice and sample_invoice["nom_site"] else "Cité 500 Logements")}</span><b>Typologie</b><span>:</span><span>{h(sample_invoice["typologie_site"] if sample_invoice and sample_invoice["typologie_site"] else "A12")}</span></div>'
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
            "nature": query.get("nature", query.get("type", [""]))[0].strip(),
            "typology": query.get("typology", [""])[0].strip().upper(),
            "status": query.get("status", [""])[0].strip(),
            "direction": query.get("direction", [""])[0].strip(),
            "client": query.get("client", [""])[0].strip(),
            "company_branch": query.get("company_branch", [""])[0].strip(),
            "date_start": query.get("date_start", [""])[0].strip(),
            "date_end": query.get("date_end", [""])[0].strip(),
            "show_cancelled": query.get("show_cancelled", [""])[0] == "1",
            "message": query.get("message", [""])[0].strip(),
            "sort": query.get("sort", ["date"])[0].strip().lower(),
            "order": query.get("order", ["desc"])[0].strip().lower(),
        }
        scoped_branch = self.visible_company_branch_id()
        if scoped_branch is not None:
            state["company_branch"] = str(scoped_branch)
        allowed_sorts = {
            "site", "invoice_type", "typology", "bc", "invoice", "date",
            "ttc", "dtc", "mobilis", "ov", "status",
        }
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
                i.invoice_number LIKE ? OR po.numero_bc LIKE ? OR po.objet LIKE ? OR
                s.code_site LIKE ? OR i.numero_ordre_virement LIKE ? OR
                EXISTS (
                    SELECT 1 FROM invoice_sites xis
                    JOIN sites xs ON xs.id=xis.site_id
                    WHERE xis.invoice_id=i.id AND xs.code_site LIKE ?
                )
            )""")
            token = f"%{state['q']}%"
            params.extend([token, token, token, token, token, token])
        if state["nature"]:
            where.append("po.type_bc=?")
            params.append(state["nature"])
        if state["typology"]:
            where.append("""(
                s.typologie_site=? COLLATE NOCASE OR
                EXISTS (
                    SELECT 1 FROM invoice_sites xit
                    JOIN sites xst ON xst.id=xit.site_id
                    WHERE xit.invoice_id=i.id AND xst.typologie_site=? COLLATE NOCASE
                )
            )""")
            params.extend([state["typology"], state["typology"]])
        if state["status"] == CANCELLED:
            where.append("i.lifecycle_status=?")
            params.append(CANCELLED)
        elif state["status"] in STATUS_LABELS:
            where.append("i.lifecycle_status=?")
            params.append(state["status"])
        elif not state["show_cancelled"]:
            where.append("i.cancelled_at IS NULL")
        if state["direction"]:
            where.append("COALESCE(po.client_direction_id, site_po.client_direction_id)=?")
            params.append(state["direction"])
        if state["client"]:
            where.append("md.client_id=?")
            params.append(state["client"])
        if state["company_branch"]:
            where.append("COALESCE(po.company_branch_id, site_po.company_branch_id)=?")
            params.append(state["company_branch"])
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
            FROM invoice_lifecycle i
            LEFT JOIN purchase_orders po ON po.id=i.purchase_order_id
            LEFT JOIN sites s ON s.id=i.site_id
            LEFT JOIN purchase_orders site_po ON site_po.id=s.purchase_order_id
            LEFT JOIN client_directions md
              ON md.id=COALESCE(po.client_direction_id, site_po.client_direction_id)
            LEFT JOIN clients client ON client.id=md.client_id
            LEFT JOIN subcontractors sc ON sc.id=s.subcontractor_id
            LEFT JOIN design_offices design ON design.id=s.design_office_id
        """
        select_sql = """
            SELECT i.*, po.numero_bc, po.date_bc, po.type_bc, po.objet AS objet_bc,
                   md.name AS direction_regionale, md.sigle AS direction_sigle,
                   client.id AS client_id, client.raison_sociale, client.sigle AS client_sigle,
                   branch.name AS company_branch_name, branch.sigle AS company_branch_sigle,
                   po.type_bc AS effective_type_bc,
                   s.code_site, s.typologie_site,
                   sc.raison_sociale AS subcontractor_name, design.raison_sociale AS design_office_name,
                   (SELECT COUNT(*) FROM invoice_sites xis WHERE xis.invoice_id=i.id) AS ndc_site_count,
                   (SELECT GROUP_CONCAT(xs.code_site, ', ') FROM invoice_sites xis
                    JOIN sites xs ON xs.id=xis.site_id WHERE xis.invoice_id=i.id) AS ndc_codes,
                   (SELECT GROUP_CONCAT(DISTINCT d.raison_sociale) FROM invoice_sites xis
                    JOIN sites xs ON xs.id=xis.site_id LEFT JOIN design_offices d ON d.id=xs.design_office_id
                    WHERE xis.invoice_id=i.id) AS ndc_design_offices,
                   (SELECT GROUP_CONCAT(DISTINCT st.raison_sociale) FROM invoice_sites xis
                    JOIN sites xs ON xs.id=xis.site_id LEFT JOIN subcontractors st ON st.id=xs.subcontractor_id
                    WHERE xis.invoice_id=i.id) AS ndc_subcontractors,
                   (SELECT GROUP_CONCAT(DISTINCT NULLIF(TRIM(xs.typologie_site), '')) FROM invoice_sites xis
                    JOIN sites xs ON xs.id=xis.site_id WHERE xis.invoice_id=i.id) AS ndc_typologies,
                   (SELECT GROUP_CONCAT(
                        xs.code_site || ' — BET: ' || COALESCE(d.raison_sociale, '—') ||
                        ' — ST: ' || COALESCE(st.raison_sociale, '—') ||
                        ' — Typologie: ' || COALESCE(xs.typologie_site, '—'),
                        '|||'
                    )
                    FROM invoice_sites xid
                    JOIN sites xs ON xs.id=xid.site_id
                    LEFT JOIN design_offices d ON d.id=xs.design_office_id
                    LEFT JOIN subcontractors st ON st.id=xs.subcontractor_id
                    WHERE xid.invoice_id=i.id) AS ndc_site_details,
                   (SELECT COUNT(*) FROM invoice_lines il WHERE il.invoice_id=i.id) AS line_count
        """
        from_sql = from_sql + " LEFT JOIN company_branches branch ON branch.id=COALESCE(po.company_branch_id, site_po.company_branch_id) "

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
            if key == 'invoice_type':
                return natural_text(row['effective_type_bc'])
            if key == 'typology':
                return natural_text(row['ndc_typologies'] if is_ndc else row['typologie_site'])
            if key == 'bc':
                return natural_text(row['numero_bc'])
            if key == 'invoice':
                return natural_text(row['invoice_number'] or f"Brouillon #{row['id']}")
            if key == 'date':
                return str(row['invoice_date'] or '')
            if key == 'ttc':
                try:
                    return float(row['total_ttc'] or 0)
                except (TypeError, ValueError):
                    return 0.0
            if key == 'dtc':
                return str(row['date_depot_dtc'] or '')
            if key == 'mobilis':
                return str(row['date_depot_mobilis'] or '')
            if key == 'ov':
                return str(row['date_ov'] or '')
            if key == 'status':
                return natural_text(STATUS_LABELS.get(row['lifecycle_status'], row['lifecycle_status']))
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

            clients = con.execute(
                "SELECT id, raison_sociale, sigle FROM clients WHERE deleted_at IS NULL AND is_active=1 ORDER BY sigle, raison_sociale"
            ).fetchall()
            direction_where = ["deleted_at IS NULL"]
            direction_params = []
            if state["client"]:
                direction_where.append("client_id=?")
                direction_params.append(state["client"])
            directions = con.execute(
                f"SELECT id, client_id, name AS direction_regionale, sigle FROM client_directions WHERE {' AND '.join(direction_where)} ORDER BY sigle, name",
                direction_params,
            ).fetchall()
            branches = con.execute(
                "SELECT id, name, sigle FROM company_branches WHERE is_active=1 ORDER BY sigle, name"
            ).fetchall()
            typologies = active_typologies(con)
            subcontractors = con.execute(
                "SELECT * FROM subcontractors WHERE is_active=1 ORDER BY raison_sociale COLLATE NOCASE"
            ).fetchall()
            design_offices = con.execute(
                "SELECT * FROM design_offices WHERE is_active=1 ORDER BY raison_sociale COLLATE NOCASE"
            ).fetchall()
            company = con.execute("SELECT nom FROM company_settings WHERE id=1").fetchone()
            date_limits = con.execute(
                "SELECT MIN(DATE(invoice_date)), MAX(DATE(invoice_date)) FROM invoices WHERE deleted_at IS NULL AND invoice_date IS NOT NULL"
            ).fetchone()
        return rows, total_count, total_ttc, total_pages, clients, directions, branches, typologies, date_limits, (company[0] if company else "Entreprise")

    def table_facturation_new_url(self, state, **changes):
        values = {
            "q": state["q"], "nature": state["nature"], "typology": state["typology"],
            "status": state["status"], "direction": state["direction"],
            "client": state["client"],
            "company_branch": state["company_branch"],
            "date_start": state["date_start"], "date_end": state["date_end"],
            "show_cancelled": "1" if state["show_cancelled"] else "",
            "sort": state["sort"], "order": state["order"],
            "page": state["page"], "per_page": state["per_page"],
        }
        values.update(changes)
        values = {key: value for key, value in values.items() if str(value) != ""}
        return "/table-facturation-new?" + urlencode(values)

    def table_facturation_new(self):
        state = self.table_facturation_new_state()
        rows, total_count, total_ttc, total_pages, clients, directions, branches, typologies, date_limits, company_name = self.table_facturation_new_rows(state)
        user = self.current_user()
        can_edit_draft = user_has_permission(user, "invoice.edit_draft")
        can_cancel = user_has_permission(user, "invoice.cancel")
        tracking_permissions = {
            "date_depot_dtc": user_has_permission(user, "invoice.deposit_dtc"),
            "date_depot_mobilis": user_has_permission(user, "invoice.deposit_mobilis"),
            "date_ov": user_has_permission(user, "invoice.mark_paid"),
        }
        can_edit_remark = can_edit_draft or any(tracking_permissions.values())

        def tracking_date_input(row, field, permission):
            blocked_by_sequence = (
                (field == "date_depot_dtc" and row["lifecycle_status"] == BROUILLON)
                or (field == "date_depot_mobilis" and not row["date_depot_dtc"])
                or (field == "date_ov" and not row["date_depot_mobilis"])
            )
            disabled = " disabled" if not permission or row["lifecycle_status"] == CANCELLED or blocked_by_sequence else ""
            value = row[field] or ""
            return (
                f'<input class="tracking-date-input" type="date" value="{h(value)}" '
                f'data-tracking-date data-field="{field}" data-authorized="{str(bool(permission)).lower()}" '
                f'aria-label="{h(field)}"{disabled}>'
            )

        def payment_reference_input(row, permission):
            blocked = not row["date_depot_mobilis"]
            disabled = " disabled" if not permission or row["lifecycle_status"] == CANCELLED or blocked else ""
            return (
                f'<input class="payment-reference-input" type="text" '
                f'value="{h(row["numero_ordre_virement"] or "")}" maxlength="120" '
                f'data-payment-reference data-authorized="{str(bool(permission)).lower()}" '
                f'aria-label="N° ordre de virement"{disabled}>'
            )

        row_html = []
        for row in rows:
            is_ndc = row["invoice_type"] == "NDC"
            site_label = f"{row['ndc_site_count']} sites" if is_ndc else (row["code_site"] or "—")
            site_title = row["ndc_codes"] if is_ndc else row["code_site"]
            if is_ndc:
                ndc_details = "".join(
                    f"<li>{h(code.strip())}</li>"
                    for code in str(row["ndc_codes"] or "").split(",")
                    if code.strip()
                )
                site_cell = (
                    f'<details class="ndc-sites-details"><summary>{h(site_label)}</summary>'
                    f'<ul>{ndc_details}</ul></details>'
                )
            else:
                site_cell = h(site_label)
            bet_label = row["ndc_design_offices"] if is_ndc else row["design_office_name"]
            subcontractor_label = row["ndc_subcontractors"] if is_ndc else row["subcontractor_name"]
            typology_label = row["ndc_typologies"] if is_ndc else row["typologie_site"]
            invoice_label = row["invoice_number"] or f"Brouillon #{row['id']}"
            remark = row["remarque"] or ""
            status_code = row["lifecycle_status"]
            status_label = STATUS_LABELS.get(status_code, status_code)
            review_badge = (
                '<span class="migration-review" title="La date de dépôt historique doit être vérifiée">À vérifier</span>'
                if row["migration_review_required"] else ""
            )
            documents_disabled = status_code == BROUILLON or int(row["line_count"] or 0) == 0
            document_items = (
                '<span class="document-menu-item disabled" role="menuitem" aria-disabled="true"><span>Documents indisponibles</span><small>Brouillon</small></span>'
                if documents_disabled else
                f'<a class="document-menu-item" role="menuitem" href="/invoices/pdf-ready/facture/{row["id"]}"><span>Facture</span><small>FACT</small></a>'
                f'<a class="document-menu-item" role="menuitem" href="/invoices/pdf-ready/devis-quantitatif/{row["id"]}"><span>Devis quantitatif</span><small>DQ</small></a>'
                f'<a class="document-menu-item" role="menuitem" href="/invoices/pdf-ready/devis-estimatif/{row["id"]}"><span>Devis estimatif</span><small>DE</small></a>'
            )
            actions = [f'''<div class="action-menu-wrap">
              <button class="action-icon document-menu-trigger" type="button" data-document-menu-trigger aria-expanded="false" aria-controls="document-menu-{row['id']}" title="Documents"><svg><use href="#i-eye"/></svg></button>
              <div class="document-menu" id="document-menu-{row['id']}" role="menu" hidden><div class="document-menu-title">Documents</div>{document_items}</div>
            </div>''']
            if can_edit_draft and not row["is_issued"] and status_code != CANCELLED:
                actions.append(f'<a class="action-icon" href="/invoices?edit_id={row["id"]}" title="Modifier le brouillon"><svg><use href="#i-edit"/></svg></a>')
            if can_cancel and status_code != CANCELLED:
                action_label = "Annuler la facture" if row["is_issued"] else "Annuler le brouillon"
                actions.append(f'<button class="action-icon danger" type="button" data-lifecycle-action="cancel" title="{action_label}"><svg><use href="#i-trash"/></svg></button>')
            elif can_cancel and status_code == CANCELLED:
                actions.append('<button class="action-icon" type="button" data-lifecycle-action="restore" title="Restaurer la facture"><svg><use href="#i-edit"/></svg></button>')
            remark_button = (
                '<button class="remark-edit-button" type="button" data-edit-remark title="Modifier la remarque"><svg><use href="#i-edit"/></svg></button>'
                if can_edit_remark and status_code != CANCELLED else ""
            )
            row_html.append(f'''<tr data-invoice-id="{row['id']}" data-status-code="{h(status_code)}">
              <td class="nowrap" title="{h(row['direction_regionale'] or '')}">{h(row['direction_sigle'] or '—')}</td>
              <td class="nowrap">{h(row['numero_bc'] or '—')}</td>
              <td class="site-details-cell" title="{h(site_title)}">{site_cell}</td>
              <td class="nowrap">{h(purchase_order_type_label(row['effective_type_bc']) if row['effective_type_bc'] else '—')}</td>
              <td title="{h(row['objet_bc'] or '')}">{h(row['objet_bc'] or '—')}</td>
              <td class="nowrap" title="{h(bet_label or '')}">{h(bet_label or '—')}</td>
              <td class="nowrap" title="{h(subcontractor_label or '')}">{h(subcontractor_label or '—')}</td>
              <td class="nowrap" title="{h(typology_label or '')}">{h(typology_label or '—')}</td>
              <td class="nowrap sticky-invoice-cell">{h(invoice_label)}</td>
              <td class="nowrap">{h(date_fr(row['invoice_date']))}</td>
              <td class="money">{money(row['total_ttc'])} DA</td>
              <td>{tracking_date_input(row, 'date_depot_dtc', tracking_permissions['date_depot_dtc'])}</td>
              <td>{tracking_date_input(row, 'date_depot_mobilis', tracking_permissions['date_depot_mobilis'])}</td>
              <td>{tracking_date_input(row, 'date_ov', tracking_permissions['date_ov'])}</td>
              <td>{payment_reference_input(row, tracking_permissions['date_ov'])}</td>
              <td><span class="status-badge status-{h(status_code.lower())}" data-status>{h(status_label)}</span>{review_badge}</td>
              <td><div class="remark-editor"><span class="remark-text" data-remark-text>{h(remark or '—')}</span>{remark_button}<textarea class="remark-input" data-remark-input rows="2" hidden>{h(remark)}</textarea></div></td>
              <td class="actions-cell"><div class="action-buttons">{''.join(actions)}</div></td>
            </tr>''')
        if not row_html:
            row_html.append('<tr class="empty-row"><td colspan="18">Aucune facture ne correspond aux filtres.</td></tr>')

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

        direction_options = ''.join([option_html('', 'Toutes', state['direction'])] + [option_html(r['id'], r['sigle'], state['direction']) for r in directions])
        branch_options = ''.join([option_html('', 'Toutes', state['company_branch'])] + [option_html(r['id'], r['sigle'], state['company_branch']) for r in branches])
        client_options = ''.join(
            [option_html('', 'Tous', state['client'])]
            + [
                f'<option value="{h(r["id"])}" title="{h(r["raison_sociale"])}"'
                f'{" selected" if str(r["id"]) == str(state["client"]) else ""}>{h(r["sigle"])}</option>'
                for r in clients
            ]
        )
        selected_client = next((r for r in clients if str(r['id']) == str(state['client'])), None)
        display_client = selected_client or (clients[0] if len(clients) == 1 else None)
        client_direction_label = f"Direction {display_client['sigle']}" if display_client else "Direction client"
        client_direction_column = f"DR {display_client['sigle']}" if display_client else "DR CLIENT"
        client_date_column = f"DATE {display_client['sigle']}" if display_client else "DATE CLIENT"
        company_direction_label = f"Direction {company_name or 'entreprise'}"
        company_direction_column = f"DR {company_name or 'ENTREPRISE'}"
        nature_options = ''.join([
            option_html('', 'Tous', state['nature']),
            *(option_html(value, purchase_order_type_label(value), state['nature']) for value, _ in PURCHASE_ORDER_TYPES),
        ])
        typology_options = ''.join(
            [option_html('', 'Toutes', state['typology'])]
            + [option_html(row['sigle'], row['sigle'], state['typology']) for row in typologies]
        )
        status_options = ''.join(
            [option_html('', 'Tous', state['status'])]
            + [option_html(code, label, state['status']) for code, label in STATUS_LABELS.items()]
        )
        per_page_options = ''.join(option_html(value, value, state['per_page']) for value in (10, 20, 50, 100))
        date_start = state['date_start']
        date_end = state['date_end']
        date_min = date_limits[0] or ''
        date_max = date_limits[1] or ''
        actor = current_actor()
        alert = f'<div class="sapta-alert">{h(state["message"])}</div>' if state["message"] else ''
        summary = f"{total_count} facture{'s' if total_count != 1 else ''} — Total TTC {money(total_ttc)} DA"
        html = render_html_template(
            "table_facturation_new.html",
            USER_INITIALS=h(initials(actor)), USER_NAME=h(actor), ALERT=alert,
            SEARCH=h(state['q']), DIRECTION_OPTIONS=direction_options,
            BRANCH_OPTIONS=branch_options,
            CLIENT_OPTIONS=client_options,
            CLIENT_DIRECTION_LABEL=h(client_direction_label),
            COMPANY_DIRECTION_LABEL=h(company_direction_label),
            CLIENT_DIRECTION_COLUMN=h(client_direction_column),
            COMPANY_DIRECTION_COLUMN=h(company_direction_column),
            CLIENT_DATE_COLUMN=h(client_date_column),
            NATURE_OPTIONS=nature_options, TYPOLOGY_OPTIONS=typology_options,
            STATUS_OPTIONS=status_options,
            SHOW_CANCELLED_CHECKED=' checked' if state['show_cancelled'] else '',
            DATE_START=h(date_start), DATE_END=h(date_end),
            DATE_MIN=h(date_min), DATE_MAX=h(date_max), PER_PAGE=state['per_page'],
            ROWS=''.join(row_html), SUMMARY=h(summary), PAGINATION=pagination,
            PER_PAGE_OPTIONS=per_page_options,
            CSRF_TOKEN=h(session_csrf_token(self.cookie_value(SESSION_COOKIE))),
        )
        self.respond(html)

    def update_table_facturation_new(self):
        try:
            values = self.form()
            invoice_id = int(values.get("invoice_id", "0"))
            user = self.current_user()
            action = values.get("action", "")
            reason = values.get("reason", "")
            if action == "cancel":
                updated = cancel_invoice(invoice_id, user, reason, actor=current_actor())
            elif action == "restore":
                updated = restore_cancelled_invoice(invoice_id, user, reason, actor=current_actor())
            elif "numero_ordre_virement" in values:
                updated = update_payment_reference(
                    invoice_id,
                    user,
                    values.get("numero_ordre_virement", ""),
                    reason=reason,
                    actor=current_actor(),
                )
            elif "remarque" in values:
                can_edit = any(user_has_permission(user, permission) for permission in (
                    "invoice.edit_draft",
                    "invoice.deposit_dtc",
                    "invoice.deposit_mobilis",
                    "invoice.mark_paid",
                ))
                if not can_edit:
                    raise InvoicePermissionError("Permission requise pour modifier la remarque.")
                with db() as con:
                    current = con.execute(
                        "SELECT remarque, cancelled_at FROM invoices WHERE id=? AND deleted_at IS NULL",
                        (invoice_id,),
                    ).fetchone()
                    if not current:
                        raise ValueError("Facture introuvable.")
                    if current["cancelled_at"]:
                        raise InvoiceLifecycleError("La facture annulée est verrouillée.")
                    remark = values.get("remarque", "").strip()
                    con.execute(
                        """
                        UPDATE invoices
                        SET remarque=?, updated_by=?, updated_at=CURRENT_TIMESTAMP
                        WHERE id=?
                        """,
                        (remark, current_actor(), invoice_id),
                    )
                    con.execute(
                        """
                        INSERT INTO audit_log(actor, action, entity_type, entity_id, details)
                        VALUES(?, 'invoice.remark.update', 'invoice', ?, ?)
                        """,
                        (
                            current_actor(),
                            str(invoice_id),
                            json.dumps({"old": current["remarque"], "new": remark}, ensure_ascii=False),
                        ),
                    )
                updated = get_invoice_lifecycle(invoice_id)
            else:
                tracking_fields = [
                    field for field in ("date_depot_dtc", "date_depot_mobilis", "date_ov")
                    if field in values
                ]
                if len(tracking_fields) != 1:
                    raise InvoiceLifecycleError("Une seule date de suivi doit être modifiée à la fois.")
                field = tracking_fields[0]
                updated = update_tracking_dates(
                    invoice_id,
                    user,
                    reason=reason,
                    actor=current_actor(),
                    **{field: values.get(field)},
                )
            payload = {
                "ok": True,
                "status": updated["lifecycle_status"],
                "status_label": STATUS_LABELS.get(updated["lifecycle_status"], updated["lifecycle_status"]),
                "date_depot_dtc": updated["date_depot_dtc"],
                "date_depot_mobilis": updated["date_depot_mobilis"],
                "date_ov": updated["date_ov"],
                "numero_ordre_virement": updated["numero_ordre_virement"],
            }
            self.respond(json.dumps(payload, ensure_ascii=False), content_type="application/json; charset=utf-8")
        except InvoicePermissionError as exc:
            self.respond(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), status=403, content_type="application/json; charset=utf-8")
        except Exception as exc:
            self.respond(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), status=400, content_type="application/json; charset=utf-8")

    def export_table_facturation_new(self):
        state = self.table_facturation_new_state()
        rows, _, _, _, _, _, _, _, _, _ = self.table_facturation_new_rows(state, paginate=False)
        sheet_rows = [[
            styled("DR MOBILIS", 3), styled("N° BC", 3), styled("CODE SITE", 3),
            styled("TYPE BC", 3), styled("OBJET BC", 3), styled("BET", 3),
            styled("ST", 3), styled("TYPOLOGIE SITE", 3), styled("N° FACTURE", 3),
            styled("DATE FACTURE", 3), styled("TTC FACTURE", 3), styled("DATE DÉPÔT DTC", 3),
            styled("DATE DÉPÔT MOBILIS", 3), styled("DATE PAIEMENT", 3),
            styled("N° ORDRE DE VIREMENT", 3), styled("ÉTAT", 3), styled("REMARQUE", 3),
        ]]
        for row in rows:
            is_ndc = row["invoice_type"] == "NDC"
            sheet_rows.append([
                row["direction_sigle"], row["numero_bc"],
                row["ndc_codes"] if is_ndc else row["code_site"],
                purchase_order_type_label(row["effective_type_bc"]) if row["effective_type_bc"] else "",
                row["objet_bc"],
                row["ndc_design_offices"] if is_ndc else row["design_office_name"],
                row["ndc_subcontractors"] if is_ndc else row["subcontractor_name"],
                row["ndc_typologies"] if is_ndc else row["typologie_site"],
                row["invoice_number"] or f"Brouillon #{row['id']}",
                date_fr(row["invoice_date"]), float(row["total_ttc"] or 0),
                date_fr(row["date_depot_dtc"]), date_fr(row["date_depot_mobilis"]),
                date_fr(row["date_ov"]), row["numero_ordre_virement"],
                STATUS_LABELS.get(row["lifecycle_status"], row["lifecycle_status"]),
                row["remarque"],
            ])
        content = make_xlsx([("Table Facturation", sheet_rows)])
        user = self.current_user()
        with db() as con:
            for row in rows:
                record_invoice_export(
                    row["id"],
                    "table_xlsx",
                    user,
                    actor=user["username"],
                    connection=con,
                )
        filename = f"Table_Facturation_{datetime.now().strftime('%Y%m%d')}.xlsx"
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def table_facturation(self):
        return self.redirect("/table-facturation-new")
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
            where.append("po.client_direction_id=?")
            params.append(direction_filter)
        with db() as con:
            regions = [row[0] for row in con.execute(
                "SELECT DISTINCT region FROM sites WHERE deleted_at IS NULL AND region<>'' ORDER BY region"
            ).fetchall()]
            directions = con.execute("SELECT id, name AS direction_regionale FROM client_directions WHERE deleted_at IS NULL ORDER BY name").fetchall()
            rows = con.execute(f"""
                SELECT i.*, po.numero_bc, po.date_bc, po.type_bc, md.name AS direction_regionale,
                       s.code_site, s.region, s.typologie_site, s.bet,
                       (SELECT COUNT(*) FROM invoice_sites xis WHERE xis.invoice_id=i.id) AS ndc_site_count,
                       (SELECT GROUP_CONCAT(xs.code_site, ', ') FROM invoice_sites xis
                        JOIN sites xs ON xs.id=xis.site_id WHERE xis.invoice_id=i.id) AS ndc_codes,
                       (SELECT GROUP_CONCAT(DISTINCT xs.region) FROM invoice_sites xis
                        JOIN sites xs ON xs.id=xis.site_id WHERE xis.invoice_id=i.id) AS ndc_regions
                FROM invoices i
                JOIN purchase_orders po ON po.id=i.purchase_order_id
                JOIN client_directions md ON md.id=po.client_direction_id
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
                <td><span class="action-icons"><button class="icon-button" type="submit" form="{form_id}" title="Enregistrer">✓</button><a class="button-link icon-link" href="/invoices/pdf-ready/facture/{row['id']}" title="Ouvrir la facture">◉</a><a class="button-link icon-link" href="/invoices?edit_id={row['id']}" title="Modifier">✎</a><form method="post" action="/invoices/delete" class="inline-action-form"><input type="hidden" name="id" value="{row['id']}"><button type="submit" class="button-link icon-link danger-link" title="Supprimer" onclick="return confirm('Supprimer cette facture ?')">⌫</button></form></span></td>
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
        raw_filters = {
            key: query.get(key, [""])[0]
            for key in ("date_from", "date_to", "direction", "client", "type_bc", "status", "company_branch")
        }
        scoped_branch = self.visible_company_branch_id()
        if scoped_branch is not None:
            raw_filters["company_branch"] = str(scoped_branch)
        filters, notices = normalize_dashboard_filters(raw_filters)
        with db() as con:
            dashboard = load_dashboard(con, filters)

        def money_kda(value):
            return money(float(value or 0) / 1000)

        def option(value, label, selected, title=""):
            selected_attr = " selected" if str(value) == str(selected) else ""
            title_attr = f' title="{h(title)}"' if title else ""
            return f'<option value="{h(value)}"{selected_attr}{title_attr}>{h(label)}</option>'

        def table_url(status_code):
            values = filters.as_query()
            values["status"] = status_code
            return "/table-facturation-new?" + urlencode(
                {key: value for key, value in values.items() if value}
            )

        def invoice_url(invoice_number):
            return "/table-facturation-new?" + urlencode({"q": invoice_number or ""})

        direction_options = option("", "Toutes", filters.direction) + "".join(
            option(row["id"], row["sigle"], filters.direction, row["direction_regionale"])
            for row in dashboard["directions"]
        )
        client_options = option("", "Tous les clients", filters.client) + "".join(
            option(row["id"], row["sigle"], filters.client, row["raison_sociale"])
            for row in dashboard["clients"]
        )
        type_options = option("", "Tous les types BC", filters.type_bc) + "".join(
            option(value, label, filters.type_bc)
            for value, label in PURCHASE_ORDER_TYPES
        )
        status_options = option("", "Tous les états", filters.status) + "".join(
            option(code, STATUS_LABELS[code], filters.status)
            for code in (*LIFECYCLE_STATUS_ORDER, CANCELLED)
        )
        branch_options = option("", "Toutes", filters.company_branch) + "".join(
            option(row["id"], row["sigle"], filters.company_branch, row["name"])
            for row in dashboard["branches"]
        )
        selected_client = next(
            (row for row in dashboard["clients"] if str(row["id"]) == str(filters.client)),
            None,
        )
        company_direction_label = f'Direction {dashboard["company_name"]}'
        client_direction_label = (
            f'Direction {selected_client["sigle"]}'
            if selected_client else "Direction client"
        )
        notice_html = "".join(
            f'<div class="ops-dashboard-notice">{h(message)}</div>' for message in notices
        )

        kpis = dashboard["kpis"]
        metric_definitions = [
            (
                "Factures émises",
                str(kpis["issued"]["count"]),
                f'{money_kda(kpis["issued"]["amount"])} KDA',
                "file-check-2",
                "issued",
            ),
            (
                "Total TTC",
                f'{money_kda(kpis["total_issued"]["amount"])} KDA',
                f'{kpis["total_issued"]["count"]} facture(s) émise(s)',
                "circle-dollar-sign",
                "total",
            ),
            (
                "Montant payé",
                f'{money_kda(kpis["paid"]["amount"])} KDA',
                f'{kpis["paid"]["count"]} facture(s)',
                "badge-check",
                "paid",
            ),
            (
                "Montant à encaisser",
                f'{money_kda(kpis["outstanding"]["amount"])} KDA',
                f'{kpis["outstanding"]["count"]} facture(s) émise(s)',
                "wallet-cards",
                "outstanding",
            ),
            (
                "Brouillons",
                str(kpis["drafts"]["count"]),
                f'{money_kda(kpis["drafts"]["amount"])} KDA',
                "file-pen-line",
                "drafts",
            ),
        ]
        metric_cards = "".join(
            f'''<article class="ops-kpi ops-kpi-{tone}">
              <span class="ops-kpi-icon"><i data-lucide="{icon}"></i></span>
              <div><span>{h(label)}</span><strong>{h(value)}</strong><small>{h(detail)}</small></div>
            </article>'''
            for label, value, detail, icon, tone in metric_definitions
        )

        status_cards = "".join(
            f'''<a class="ops-stage ops-stage-{code.lower()}{' selected' if filters.status == code else ''}"
                   href="{h(table_url(code))}">
              <span>{h(STATUS_LABELS[code])}</span>
              <strong>{dashboard['status_cards'][code]['count']}</strong>
              <small>{money_kda(dashboard['status_cards'][code]['amount'])} KDA</small>
            </a>'''
            for code in LIFECYCLE_STATUS_ORDER
        )

        max_month = max(
            (float(item["amount"]) for item in dashboard["monthly_ttc"]), default=0
        )
        month_labels = (
            "janv.", "févr.", "mars", "avr.", "mai", "juin",
            "juil.", "août", "sept.", "oct.", "nov.", "déc.",
        )
        monthly_bars = []
        for item in dashboard["monthly_ttc"]:
            year, month_number = item["month"].split("-")
            amount = float(item["amount"])
            height = (amount / max_month * 100) if max_month else 0
            if amount:
                height = max(4, height)
            monthly_bars.append(
                f'''<div class="ops-month" title="{h(month_labels[int(month_number) - 1])} {year}: {money_kda(amount)} KDA">
                  <span>{money_kda(amount) if amount else ''}</span>
                  <div><i style="height:{height:.2f}%"></i></div>
                  <small>{h(month_labels[int(month_number) - 1])}<b>{h(year[-2:])}</b></small>
                </div>'''
            )

        max_status_count = max(
            (item["count"] for item in dashboard["status_distribution"]), default=0
        )
        status_rows = "".join(
            f'''<div class="ops-status-row ops-status-{item['code'].lower()}">
              <div><span>{h(item['label'])}</span><b>{item['count']}</b></div>
              <div class="ops-status-track"><i style="width:{(item['count'] / max_status_count * 100) if max_status_count else 0:.2f}%"></i></div>
              <small>{money_kda(item['amount'])} KDA</small>
            </div>'''
            for item in dashboard["status_distribution"]
        )

        def process_table(title, rows, date_field, date_label, status_code):
            body = []
            for row in rows:
                body.append(f'''<tr>
                  <td><a href="{h(invoice_url(row['invoice_number']))}">{h(row['invoice_number'] or '—')}</a><small>{h(row['numero_bc'] or '—')}</small></td>
                  <td title="{h(row['site_label'])}">{h(row['site_label'])}</td>
                  <td title="{h(row['direction_name'])}">{h(row['direction_name'])}</td>
                  <td>{h(date_fr(row[date_field]))}</td>
                  <td>{money_kda(row['total_ttc'])}</td>
                </tr>''')
            if not body:
                body.append('<tr class="ops-empty-row"><td colspan="5">Aucune facture</td></tr>')
            return f'''<section class="ops-process-panel">
              <header><div><h3>{h(title)}</h3><span>{len(rows)} affichée(s)</span></div><a href="{h(table_url(status_code))}" title="Voir toutes"><i data-lucide="arrow-up-right"></i></a></header>
              <div class="ops-process-scroll"><table>
                <thead><tr><th>Facture / BC</th><th>Site</th><th>Direction</th><th>{h(date_label)}</th><th>TTC KDA</th></tr></thead>
                <tbody>{''.join(body)}</tbody>
              </table></div>
            </section>'''

        process_tables = "".join((
            process_table(
                "Prêtes pour dépôt DTC", dashboard["ready_dtc"],
                "invoice_date", "Date facture", READY_DTC,
            ),
            process_table(
                "À déposer chez Mobilis", dashboard["ready_mobilis"],
                "date_depot_dtc", "Date DTC", DEPOSITED_DTC,
            ),
            process_table(
                "En attente paiement", dashboard["awaiting_payment"],
                "date_depot_mobilis", "Date Mobilis", AWAITING_PAYMENT,
            ),
        ))

        overdue = dashboard["overdue"]
        overdue_items = "".join(
            f'''<a href="{h(invoice_url(row['invoice_number']))}">
              <span><strong>{h(row['invoice_number'] or '—')}</strong><small>{h(row['direction_name'])}</small></span>
              <b>{row['days_overdue']} j</b>
              <em>{money_kda(row['total_ttc'])} KDA</em>
            </a>'''
            for row in dashboard["overdue_rows"]
        ) or '<div class="ops-overdue-empty"><i data-lucide="badge-check"></i><span>Aucun retard supérieur à 60 jours</span></div>'
        max_direction_amount = max(
            (float(item["amount"]) for item in dashboard["overdue_by_direction"]),
            default=0,
        )
        overdue_directions = "".join(
            f'''<div class="ops-direction-row">
              <div><span title="{h(item['direction'])}">{h(item['direction'])}</span><b>{item['count']}</b></div>
              <div><i style="width:{(float(item['amount']) / max_direction_amount * 100) if max_direction_amount else 0:.2f}%"></i></div>
              <small>{money_kda(item['amount'])} KDA</small>
            </div>'''
            for item in dashboard["overdue_by_direction"]
        ) or '<div class="ops-overdue-empty compact"><span>Aucune direction en retard</span></div>'

        date_min, date_max = dashboard["date_bounds"]
        content = f'''
          {notice_html}
          <form class="ops-dashboard-filters" method="get" action="/">
            <label class="ops-filter-company"><span>{h(company_direction_label)}</span><select name="company_branch">{branch_options}</select></label>
            <label class="ops-filter-client"><span>Client</span><select name="client" data-dashboard-client onchange="this.form.querySelector('[name=direction]').value='';this.form.requestSubmit()">{client_options}</select></label>
            <label><span>{h(client_direction_label)}</span><select name="direction">{direction_options}</select></label>
            <label><span>Type BC</span><select name="type_bc">{type_options}</select></label>
            <label class="ops-filter-status"><span>État</span><select name="status">{status_options}</select></label>
            <label><span>Du</span><input type="date" name="date_from" value="{h(filters.date_from)}" min="{h(date_min or '')}" max="{h(date_max or '')}"></label>
            <label><span>Au</span><input type="date" name="date_to" value="{h(filters.date_to)}" min="{h(date_min or '')}" max="{h(date_max or '')}"></label>
            <button class="ops-filter-submit" type="submit"><i data-lucide="list-filter"></i><span>Appliquer</span></button>
            <a class="ops-filter-reset" href="/" title="Réinitialiser les filtres" aria-label="Réinitialiser les filtres"><i data-lucide="rotate-ccw"></i></a>
          </form>

          <section class="ops-kpi-grid" aria-label="Indicateurs généraux">{metric_cards}</section>

          <section class="ops-lifecycle-section">
            <header><div><h2>Cycle de facturation</h2><span>Nombre et montant TTC par état</span></div><strong>{dashboard['visible_count']} facture(s) dans la sélection</strong></header>
            <div class="ops-lifecycle-grid">{status_cards}</div>
          </section>

          <section class="ops-insights-grid">
            <article class="ops-chart-panel">
              <header><div><h2>TTC mensuel des factures émises</h2><span>12 derniers mois selon la date facture</span></div><small>KDA</small></header>
              <div class="ops-monthly-chart">{''.join(monthly_bars)}</div>
            </article>
            <article class="ops-chart-panel">
              <header><div><h2>Répartition des états</h2><span>Même période, direction et type BC</span></div></header>
              <div class="ops-status-chart">{status_rows}</div>
            </article>
          </section>

          <section class="ops-overdue-grid">
            <article class="ops-overdue-panel">
              <header><div><h2>Retards de paiement &gt; 60 jours</h2><span>Depuis la date de dépôt Mobilis</span></div><strong>{overdue['count']}<small>facture(s)</small></strong><b>{money_kda(overdue['amount'])} KDA</b></header>
              <div class="ops-overdue-list">{overdue_items}</div>
            </article>
            <article class="ops-direction-panel">
              <header><div><h2>Montant en retard par direction</h2><span>Factures émises non payées uniquement</span></div></header>
              <div class="ops-direction-list">{overdue_directions}</div>
            </article>
          </section>

          <section class="ops-process-grid">{process_tables}</section>
          <footer class="ops-dashboard-footer"><span>© 2026 SAPTA Facturation — Tous droits réservés.</span><span>Version {h(APP_VERSION)}</span></footer>
        '''
        self.respond(layout("Tableau", content))

    def _legacy_dashboard(self):
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
            where.append("po.client_direction_id=?")
            params.append(direction_filter)
        where_sql = " AND ".join(where)

        with db() as con:
            directions = con.execute("SELECT id, name AS direction_regionale FROM client_directions WHERE deleted_at IS NULL ORDER BY name").fetchall()
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
                SELECT s.code_site, s.nom_site, COALESCE(md.name, s.region) AS direction, po.date_bc
                FROM sites s JOIN purchase_orders po ON po.id=s.purchase_order_id
                LEFT JOIN client_directions md ON md.id=po.client_direction_id
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
          <footer class="dashboard-footer"><span>© 2026 SAPTA Facturation – Tous droits réservés.</span><span>Version {h(APP_VERSION)}</span></footer>
        """
        self.respond(layout("Tableau", content))

    def company(self):
        return self.redirect("/settings?section=enterprise&edit_company=1")
        with db() as con:
            row = con.execute("SELECT * FROM company_settings WHERE id=1").fetchone()
            financial = app_settings(con)

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
            <h3>Paramètres de facturation</h3>
            <div class="setting-row"><span>Retenue de garantie</span><label class="suffix-input"><input name="retention_rate" value="{h(str(fraction_to_percent(financial['retention_rate'])).replace('.', ','))}"><b>%</b></label></div>
            <div class="setting-row"><span>TVA</span><label class="suffix-input"><input name="tax_rate" value="{h(str(fraction_to_percent(financial['tax_rate'])).replace('.', ','))}"><b>%</b></label></div>
            <div class="setting-row"><span>Source pour le montant en lettres</span><select><option>Total TTC</option></select></div>
            <div class="setting-row"><span>Devise</span><select name="currency_code">
              <option value="DZD"{' selected' if financial['currency_code'] == 'DZD' else ''}>DA - Dinar</option>
              <option value="EUR"{' selected' if financial['currency_code'] == 'EUR' else ''}>EUR - Euro</option>
              <option value="USD"{' selected' if financial['currency_code'] == 'USD' else ''}>USD - Dollar</option>
            </select></div>
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
            retention_percent = rate_percent(localized_decimal(values.get("retention_rate", "5")))
            tax_percent = rate_percent(localized_decimal(values.get("tax_rate", "19")))
            retention_rate = percent_to_fraction(retention_percent)
            tax_rate = percent_to_fraction(tax_percent)
            currency_code = values.get("currency_code", "DZD")
            currency_label = {"DZD": "DA", "EUR": "EUR", "USD": "USD"}.get(currency_code, currency_code)
            for key, value in {
                "retention_rate": format(retention_rate, "f"),
                "tax_rate": format(tax_rate, "f"),
                "currency_code": currency_code,
                "currency_label": currency_label,
            }.items():
                con.execute(
                    """
                    INSERT INTO app_settings(key, value, updated_by)
                    VALUES(?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_by=excluded.updated_by, updated_at=CURRENT_TIMESTAMP
                    """,
                    (key, value, current_actor()),
                )
        self.redirect("/settings?section=enterprise&message=Informations entreprise enregistrees")

    def company_branches(self):
        query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        edit_id = query.get("edit_id", [""])[0]
        message = query.get("message", [""])[0]
        with db() as con:
            edit = con.execute("SELECT * FROM company_branches WHERE id=?", (edit_id,)).fetchone() if edit_id else None
            rows = con.execute(
                """
                SELECT cb.*,
                       (SELECT COUNT(*) FROM users u WHERE u.company_branch_id=cb.id) AS user_count,
                       (SELECT COUNT(*) FROM purchase_orders po WHERE po.company_branch_id=cb.id AND po.deleted_at IS NULL) AS bc_count
                FROM company_branches cb ORDER BY cb.name
                """
            ).fetchall()
        listing = table(
            ["Sigle", "Direction", "Adresse", "Utilisateurs", "BC", "État", "Actions"],
            [[
                h(row["sigle"]), h(row["name"]), h(row["address"] or "—"),
                str(row["user_count"]), str(row["bc_count"]),
                "Active" if row["is_active"] else "Inactive",
                f'<a class="button-link icon-link" href="/company-branches?edit_id={row["id"]}" title="Modifier"><i data-lucide="pencil"></i></a>',
            ] for row in rows],
        )
        form = f"""
        <form method="post" action="/company-branches/save" class="panel grid">
          <input type="hidden" name="id" value="{h(edit['id'] if edit else '')}">
          <h2>{'Modifier la direction' if edit else 'Nouvelle direction entreprise'}</h2>
          {field('sigle', 'Sigle', edit['sigle'] if edit else '', required=True)}
          {field('name', 'Nom', edit['name'] if edit else '', required=True)}
          {field('address', 'Adresse', edit['address'] if edit else '')}
          {select_field('is_active', 'État', [('1','Active'),('0','Inactive')], str(edit['is_active']) if edit else '1')}
          <button type="submit">Enregistrer</button>
        </form>"""
        alert = f'<section class="alert">{h(message)}</section>' if message else ""
        self.respond(layout("Directions entreprise", alert + form + f'<section class="panel"><h2>Directions</h2>{listing}</section>'))

    def save_company_branch(self):
        values = self.form()
        try:
            sigle = normalized_sigle(values.get("sigle", ""))
            name = values.get("name", "").strip()
            if not name:
                raise ValueError("Le nom est obligatoire.")
            with db() as con:
                if values.get("id"):
                    if values.get("is_active") != "1":
                        linked = con.execute(
                            "SELECT 1 FROM users WHERE company_branch_id=? AND is_active=1 LIMIT 1",
                            (values["id"],),
                        ).fetchone()
                        if linked:
                            raise ValueError("Désactivez ou transférez d’abord les utilisateurs actifs.")
                    con.execute(
                        "UPDATE company_branches SET code=?,sigle=?,name=?,address=?,is_active=?,updated_by=? WHERE id=?",
                        (sigle, sigle, name, values.get("address", ""), values.get("is_active") == "1", current_actor(), values["id"]),
                    )
                    branch_id = values["id"]
                    action = "company_branch.update"
                else:
                    branch_id = con.execute(
                        "INSERT INTO company_branches(code,sigle,name,address,is_active,created_by,updated_by) VALUES(?,?,?,?,?,?,?)",
                        (sigle, sigle, name, values.get("address", ""), values.get("is_active") == "1", current_actor(), current_actor()),
                    ).lastrowid
                    action = "company_branch.create"
                con.execute(
                    "INSERT INTO audit_log(actor,action,entity_type,entity_id,details,company_branch_id) VALUES(?,?,?,?,?,?)",
                    (current_actor(), action, "company_branch", str(branch_id), name, branch_id),
                )
            self.redirect("/settings?section=enterprise&message=Direction entreprise enregistree")
        except Exception as exc:
            self.redirect(f"/settings?section=enterprise&message={quote('Erreur direction: ' + str(exc))}")

    def save_typology(self):
        values = self.form()
        try:
            sigle, full_label = validate_typology_values(
                values.get("sigle"), values.get("libelle_complet")
            )
            is_active = values.get("is_active") == "1"
            with db() as con:
                typology_id = int(values.get("id") or 0)
                if typology_id:
                    previous = con.execute(
                        "SELECT * FROM typologies WHERE id=?", (typology_id,)
                    ).fetchone()
                    if not previous:
                        raise ValueError("Typologie introuvable.")
                    if not is_active and con.execute(
                        "SELECT 1 FROM sites WHERE typology_id=? AND deleted_at IS NULL LIMIT 1",
                        (typology_id,),
                    ).fetchone():
                        raise ValueError("Cette typologie est utilisée par un site actif.")
                    con.execute(
                        """
                        UPDATE typologies
                        SET sigle=?, libelle_complet=?, is_active=?, updated_by=?
                        WHERE id=?
                        """,
                        (sigle, full_label, is_active, current_actor(), typology_id),
                    )
                    if previous["sigle"] != sigle:
                        con.execute(
                            "UPDATE sites SET typologie_site=?, updated_by=? WHERE typology_id=?",
                            (sigle, current_actor(), typology_id),
                        )
                    action = "typology.update"
                    details = json.dumps(
                        {
                            "old": {
                                "sigle": previous["sigle"],
                                "libelle_complet": previous["libelle_complet"],
                                "is_active": previous["is_active"],
                            },
                            "new": {
                                "sigle": sigle,
                                "libelle_complet": full_label,
                                "is_active": is_active,
                            },
                        },
                        ensure_ascii=False,
                    )
                else:
                    typology_id = con.execute(
                        """
                        INSERT INTO typologies(
                            sigle, libelle_complet, is_active, created_by, updated_by
                        ) VALUES(?,?,?,?,?)
                        """,
                        (sigle, full_label, is_active, current_actor(), current_actor()),
                    ).lastrowid
                    action = "typology.create"
                    details = full_label
                con.execute(
                    "INSERT INTO audit_log(actor,action,entity_type,entity_id,details) VALUES(?,?,?,?,?)",
                    (current_actor(), action, "typology", str(typology_id), details),
                )
            self.redirect("/settings?section=typologies&message=Typologie enregistree")
        except Exception as exc:
            self.redirect(
                f"/settings?section=typologies&message={quote('Erreur typologie: ' + str(exc))}"
            )

    def save_site_partner(self):
        values = self.form()
        kind = values.get("kind")
        if kind not in {"subcontractor", "design_office"}:
            return self.redirect("/settings?message=Type de partenaire invalide")
        table_name = "subcontractors" if kind == "subcontractor" else "design_offices"
        section = "subcontractors" if kind == "subcontractor" else "design-offices"
        try:
            name, contact, phone, email, address = validate_partner_values(
                values.get("raison_sociale"), values.get("contact"),
                values.get("telephone"), values.get("email"), values.get("adresse"),
            )
            is_active = values.get("is_active") == "1"
            partner_id = int(values.get("id") or 0)
            with db() as con:
                duplicate = con.execute(
                    f"SELECT id FROM {table_name} WHERE trim(raison_sociale)=? COLLATE NOCASE AND id<>?",
                    (name, partner_id),
                ).fetchone()
                if duplicate:
                    raise ValueError("Cette raison sociale existe déjà.")
                if partner_id:
                    if not con.execute(f"SELECT 1 FROM {table_name} WHERE id=?", (partner_id,)).fetchone():
                        raise ValueError("Enregistrement introuvable.")
                    con.execute(
                        f"UPDATE {table_name} SET raison_sociale=?,contact=?,telephone=?,email=?,adresse=?,is_active=?,updated_by=? WHERE id=?",
                        (name, contact, phone, email, address, is_active, current_actor(), partner_id),
                    )
                    action = f"{kind}.update"
                else:
                    hidden_sigle = f"REF-{uuid4().hex[:12].upper()}"
                    partner_id = con.execute(
                        f"INSERT INTO {table_name}(raison_sociale,sigle,contact,telephone,email,adresse,is_active,created_by,updated_by) VALUES(?,?,?,?,?,?,?,?,?)",
                        (name, hidden_sigle, contact, phone, email, address, is_active, current_actor(), current_actor()),
                    ).lastrowid
                    action = f"{kind}.create"
                con.execute(
                    "INSERT INTO audit_log(actor,action,entity_type,entity_id,details) VALUES(?,?,?,?,?)",
                    (current_actor(), action, kind, str(partner_id), json.dumps({"raison_sociale": name, "contact": contact, "telephone": phone, "email": email}, ensure_ascii=False)),
                )
            self.redirect(f"/settings?section={section}&message={quote('Enregistrement sauvegardé')}")
        except Exception as exc:
            self.redirect(f"/settings?section={section}&message={quote('Erreur: ' + str(exc))}")

    def send_xlsx(self, content, filename):
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def partner_import_template(self):
        query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        kind = query.get("kind", [""])[0]
        if kind not in {"subcontractors", "design_offices"}:
            return self.respond("Type invalide", status=400, content_type="text/plain")
        filename = "Modele_Sous_traitants.xlsx" if kind == "subcontractors" else "Modele_Bureaux_etudes.xlsx"
        return self.send_xlsx(partner_template(kind), filename)

    def preview_partner_import(self):
        values, files = self.multipart_form()
        kind = values.get("kind")
        section = "subcontractors" if kind == "subcontractors" else "design-offices"
        try:
            if kind not in {"subcontractors", "design_offices"}:
                raise ValueError("Type d'import invalide.")
            uploaded = files.get("excel_file")
            if not uploaded or not uploaded["filename"].lower().endswith(".xlsx"):
                raise ValueError("Sélectionnez un fichier Excel .xlsx.")
            if len(uploaded["content"]) > 10 * 1024 * 1024:
                raise ValueError("Le fichier dépasse la limite de 10 Mo.")
            with db() as con:
                payload = parse_partner_workbook(uploaded["content"], kind, con)
                token = save_import_batch(con, kind, uploaded["filename"], payload, current_actor())
            self.redirect(f"/settings?section={section}&import_token={token}")
        except Exception as exc:
            self.redirect(f"/settings?section={section}&message={quote('Erreur import: ' + str(exc))}")

    def confirm_partner_import(self):
        values = self.form()
        kind = values.get("kind")
        section = "subcontractors" if kind == "subcontractors" else "design-offices"
        try:
            if kind not in {"subcontractors", "design_offices"}:
                raise ValueError("Type d'import invalide.")
            create_backup()
            with db() as con:
                batch, payload = load_import_batch(con, values.get("token"), kind)
                apply_partner_import(con, batch, payload, current_actor())
                con.execute(
                    "INSERT INTO audit_log(actor,action,entity_type,entity_id,details) VALUES(?,?,?,?,?)",
                    (current_actor(), "excel_import.apply", kind, str(batch["id"]), json.dumps({"filename": batch["source_filename"], "rows": len(payload["rows"])}, ensure_ascii=False)),
                )
            self.redirect(f"/settings?section={section}&message={quote('Import Excel terminé avec succès')}")
        except Exception as exc:
            self.redirect(f"/settings?section={section}&message={quote('Erreur import: ' + str(exc))}")

    def import_batch_report(self, expected_type):
        query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        token = query.get("token", [""])[0]
        with db() as con:
            batch = con.execute("SELECT * FROM data_import_batches WHERE token=?", (token,)).fetchone()
        if not batch or (expected_type == "purchase_orders" and batch["import_type"] != "purchase_orders") or (expected_type == "partner" and batch["import_type"] not in {"subcontractors", "design_offices"}):
            return self.respond("Rapport introuvable", status=404, content_type="text/plain")
        content = import_report(json.loads(batch["payload_json"]))
        return self.send_xlsx(content, f"Rapport_import_{batch['import_type']}.xlsx")

    def purchase_order_import_template(self):
        return self.send_xlsx(purchase_order_template(), "Modele_BC_Sites.xlsx")

    def purchase_order_export_excel(self):
        with db() as con:
            content = purchase_order_export(con)
            con.execute(
                "INSERT INTO audit_log(actor,action,entity_type,entity_id,details) VALUES(?,?,?,?,?)",
                (current_actor(), "purchase_orders.excel_export", "purchase_orders", "", "Export BC et sites"),
            )
        return self.send_xlsx(content, "Bons_de_commande_et_sites.xlsx")

    def preview_purchase_order_import(self):
        _, files = self.multipart_form()
        try:
            uploaded = files.get("excel_file")
            if not uploaded or not uploaded["filename"].lower().endswith(".xlsx"):
                raise ValueError("Sélectionnez un fichier Excel .xlsx.")
            if len(uploaded["content"]) > 20 * 1024 * 1024:
                raise ValueError("Le fichier dépasse la limite de 20 Mo.")
            with db() as con:
                payload = parse_purchase_order_workbook(uploaded["content"], con)
                token = save_import_batch(con, "purchase_orders", uploaded["filename"], payload, current_actor())
            self.redirect(f"/purchase-orders?import_token={token}")
        except Exception as exc:
            self.redirect(f"/purchase-orders?message={quote('Erreur import: ' + str(exc))}")

    def confirm_purchase_order_import(self):
        values = self.form()
        try:
            create_backup()
            with db() as con:
                batch, payload = load_import_batch(con, values.get("token"), "purchase_orders")
                apply_purchase_order_import(con, batch, payload, current_actor())
                con.execute(
                    "INSERT INTO audit_log(actor,action,entity_type,entity_id,details) VALUES(?,?,?,?,?)",
                    (current_actor(), "purchase_orders.excel_import", "purchase_orders", str(batch["id"]), json.dumps({"filename": batch["source_filename"], "purchase_orders": len(payload["purchase_orders"]), "sites": len(payload["sites"])}, ensure_ascii=False)),
                )
            self.redirect(f"/purchase-orders?message={quote('Import BC et sites terminé avec succès')}")
        except Exception as exc:
            self.redirect(f"/purchase-orders?message={quote('Erreur import: ' + str(exc))}")

    def clients(self):
        query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        message = query.get("message", [""])[0]
        edit_id = query.get("edit_id", [""])[0]
        direction_edit_id = query.get("direction_edit_id", [""])[0]
        new_direction = query.get("new_direction", [""])[0] == "1"
        with db() as con:
            clients = con.execute(
                """
                SELECT c.*,
                       COUNT(DISTINCT cd.id) AS direction_count,
                       COUNT(DISTINCT po.id) AS bc_count
                FROM clients c
                LEFT JOIN client_directions cd ON cd.client_id=c.id AND cd.deleted_at IS NULL
                LEFT JOIN purchase_orders po ON po.client_direction_id=cd.id AND po.deleted_at IS NULL
                WHERE c.deleted_at IS NULL
                GROUP BY c.id ORDER BY c.raison_sociale
                """
            ).fetchall()
            selected_id = edit_id or query.get("client_id", [""])[0] or (str(clients[0]["id"]) if clients else "")
            selected = con.execute(
                "SELECT * FROM clients WHERE id=? AND deleted_at IS NULL", (selected_id,)
            ).fetchone() if selected_id else None
            directions = con.execute(
                """
                SELECT cd.*, cb.name AS company_branch_name, cb.sigle AS company_branch_sigle,
                       COUNT(DISTINCT po.id) AS bc_count
                FROM client_directions cd
                JOIN company_branches cb ON cb.id=cd.company_branch_id
                LEFT JOIN purchase_orders po ON po.client_direction_id=cd.id AND po.deleted_at IS NULL
                WHERE cd.client_id=? AND cd.deleted_at IS NULL
                GROUP BY cd.id ORDER BY cd.name
                """,
                (selected_id,),
            ).fetchall() if selected_id else []
            direction_edit = con.execute(
                "SELECT * FROM client_directions WHERE id=? AND deleted_at IS NULL",
                (direction_edit_id,),
            ).fetchone() if direction_edit_id else None
            branches = con.execute(
                "SELECT id, name, sigle FROM company_branches WHERE is_active=1 ORDER BY sigle, name"
            ).fetchall()

        client_rows = []
        for row in clients:
            logo = f'<img class="table-logo" src="/{h(row["logo_path"])}" alt="Logo">' if row["logo_path"] else '<span class="client-logo-placeholder"><i data-lucide="building-2"></i></span>'
            client_rows.append([
                logo,
                h(row["sigle"]),
                f'<a class="table-primary-link" href="/clients?client_id={row["id"]}">{h(row["raison_sociale"])}</a>',
                h(row["rgc"] or "—"), h(row["nif"] or "—"),
                h(row["reference_contrat"] or "—"),
                str(row["direction_count"]), str(row["bc_count"]),
                f'<a class="button-link icon-link" href="/clients?edit_id={row["id"]}" title="Modifier"><i data-lucide="pencil"></i></a>',
            ])
        direction_rows = [[
            h(row["sigle"]), h(row["name"]), h(row["address"] or "—"), h(row["company_branch_sigle"]),
            str(row["bc_count"]),
            f'<a class="button-link icon-link" href="/clients?client_id={selected_id}&direction_edit_id={row["id"]}" title="Modifier"><i data-lucide="pencil"></i></a>',
        ] for row in directions]
        alert = f'<section class="alert">{h(message)}</section>' if message else ""
        client_form = f"""
        <aside class="entity-drawer clients-drawer"><header class="drawer-header"><div><small>Client</small><h3>{'Modifier le client' if edit_id else 'Nouveau client'}</h3></div><a class="drawer-close" href="/clients?client_id={h(selected_id)}" aria-label="Fermer">×</a></header>
        <form method="post" action="/clients/save" enctype="multipart/form-data" class="drawer-form grid">
          <input type="hidden" name="id" value="{h(selected['id'] if edit_id and selected else '')}">
          {field('raison_sociale', 'Raison sociale', selected['raison_sociale'] if edit_id and selected else '', required=True)}
          {field('sigle', 'Sigle', selected['sigle'] if edit_id and selected else '', required=True)}
          {field('rgc', 'RGC', selected['rgc'] if edit_id and selected else '')}
          {field('nif', 'NIF', selected['nif'] if edit_id and selected else '')}
          {field('reference_contrat', 'Référence contrat', selected['reference_contrat'] if edit_id and selected else '', required=True)}
          {file_field('logo', 'Logo', '.png,.jpg,.jpeg,.webp')}
          <footer class="drawer-actions"><a class="outline-button" href="/clients?client_id={h(selected_id)}">Annuler</a><button type="submit">Enregistrer</button></footer>
        </form></aside>"""
        direction_form = ""
        if selected:
            direction_form = f"""
            <aside class="entity-drawer clients-drawer"><header class="drawer-header"><div><small>Direction client</small><h3>{'Modifier la direction' if direction_edit else 'Nouvelle direction'}</h3></div><a class="drawer-close" href="/clients?client_id={selected['id']}" aria-label="Fermer">×</a></header>
            <form method="post" action="/clients/directions/save" class="drawer-form grid">
              <input type="hidden" name="id" value="{h(direction_edit['id'] if direction_edit else '')}">
              <input type="hidden" name="client_id" value="{selected['id']}">
              {field('sigle', 'Sigle', direction_edit['sigle'] if direction_edit else '', required=True)}
              {field('name', 'Direction / Agence', direction_edit['name'] if direction_edit else '', required=True)}
              {field('address', 'Adresse', direction_edit['address'] if direction_edit else '')}
              {select_field('company_branch_id', "Direction de l’entreprise", [(row['id'], f"{row['sigle']} — {row['name']}") for row in branches], direction_edit['company_branch_id'] if direction_edit else '')}
              <footer class="drawer-actions"><a class="outline-button" href="/clients?client_id={selected['id']}">Annuler</a><button type="submit">Enregistrer</button></footer>
            </form></aside>"""
        content = f"""
          {alert}
          <div class="entity-page-header"><h2>Clients</h2><a class="button-link primary-blue" href="/clients?new=1"><i data-lucide="plus"></i> Nouveau client</a></div>
          <section class="panel clients-list-panel">{table(['Logo','Sigle','Raison sociale','RGC','NIF','Référence contrat','Directions','BC','Actions'], client_rows)}</section>
          {client_form if query.get('new', [''])[0] == '1' or edit_id else ''}
          {f'<section class="client-summary"><div><small>Client sélectionné</small><h2>{h(selected["sigle"])}</h2><p>{h(selected["raison_sociale"])}</p></div><dl><div><dt>RGC</dt><dd>{h(selected["rgc"] or "—")}</dd></div><div><dt>NIF</dt><dd>{h(selected["nif"] or "—")}</dd></div><div><dt>Référence contrat</dt><dd>{h(selected["reference_contrat"] or "—")}</dd></div></dl></section>' if selected else ''}
          <section class="panel client-directions-panel"><header><h2>Directions de {h(selected['raison_sociale']) if selected else 'client'}</h2>{f'<a class="button-link outline-button" href="/clients?client_id={selected_id}&new_direction=1"><i data-lucide="plus"></i> Nouvelle direction</a>' if selected else ''}</header>{table(['Sigle','Direction / Agence','Adresse',"Direction de l’entreprise",'BC','Actions'], direction_rows)}</section>
          {direction_form if new_direction or direction_edit else ''}
        """
        self.respond(layout("Clients", content))

    def save_client(self):
        values, files = self.multipart_form()
        try:
            logo_path = save_upload(files["logo"], "client_logos", {".png", ".jpg", ".jpeg", ".webp"}) if "logo" in files else ""
            raison_sociale = values.get("raison_sociale", "").strip()
            if not raison_sociale:
                raise ValueError("Raison sociale obligatoire.")
            sigle = normalized_client_sigle(values.get("sigle", ""))
            reference_contrat = values.get("reference_contrat", "").strip()
            if not reference_contrat:
                raise ValueError("Référence contrat obligatoire.")
            with db() as con:
                if values.get("id"):
                    current = con.execute("SELECT * FROM clients WHERE id=? AND deleted_at IS NULL", (values["id"],)).fetchone()
                    if not current:
                        raise ValueError("Client introuvable.")
                    con.execute(
                        """UPDATE clients SET raison_sociale=?, sigle=?, rgc=?, nif=?, reference_contrat=?, logo_path=?, updated_by=?, updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                        (raison_sociale, sigle, values.get("rgc", ""), values.get("nif", ""), reference_contrat, logo_path or current["logo_path"], current_actor(), values["id"]),
                    )
                    client_id = values["id"]
                    action = "client.update"
                else:
                    client_id = con.execute(
                        """INSERT INTO clients(raison_sociale,sigle,rgc,nif,reference_contrat,logo_path,created_by,updated_by) VALUES(?,?,?,?,?,?,?,?)""",
                        (raison_sociale, sigle, values.get("rgc", ""), values.get("nif", ""), reference_contrat, logo_path, current_actor(), current_actor()),
                    ).lastrowid
                    action = "client.create"
                con.execute(
                    "INSERT INTO audit_log(actor,action,entity_type,entity_id,details) VALUES(?,?,?,?,?)",
                    (current_actor(), action, "client", str(client_id), json.dumps({"raison_sociale": raison_sociale, "sigle": sigle}, ensure_ascii=False)),
                )
            self.redirect(f"/clients?client_id={client_id}&message=Client enregistre")
        except Exception as exc:
            self.redirect(f"/clients?message={quote('Erreur client: ' + str(exc))}")

    def save_client_direction(self):
        values = self.form()
        try:
            sigle = normalized_sigle(values.get("sigle", ""))
            name = values.get("name", "").strip()
            if not name:
                raise ValueError("Le nom de la direction est obligatoire.")
            with db() as con:
                branch = con.execute("SELECT id FROM company_branches WHERE id=? AND is_active=1", (values.get("company_branch_id"),)).fetchone()
                if not branch:
                    raise ValueError("Direction de l’entreprise obligatoire.")
                if values.get("id"):
                    con.execute(
                        """UPDATE client_directions SET sigle=?, name=?, address=?, company_branch_id=?, updated_by=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND client_id=?""",
                        (sigle, name, values.get("address", ""), values.get("company_branch_id"), current_actor(), values["id"], values["client_id"]),
                    )
                    direction_id = values["id"]
                    action = "client_direction.update"
                else:
                    direction_id = con.execute(
                        """INSERT INTO client_directions(client_id,company_branch_id,sigle,name,address,created_by,updated_by) VALUES(?,?,?,?,?,?,?)""",
                        (values["client_id"], values["company_branch_id"], sigle, name, values.get("address", ""), current_actor(), current_actor()),
                    ).lastrowid
                    action = "client_direction.create"
                con.execute(
                    "INSERT INTO audit_log(actor,action,entity_type,entity_id,details,company_branch_id) VALUES(?,?,?,?,?,?)",
                    (current_actor(), action, "client_direction", str(direction_id), json.dumps({"sigle": sigle, "name": name}, ensure_ascii=False), values["company_branch_id"]),
                )
            self.redirect(f"/clients?client_id={values['client_id']}&message=Direction client enregistree")
        except Exception as exc:
            self.redirect(f"/clients?client_id={quote(values.get('client_id', ''))}&message={quote('Erreur direction: ' + str(exc))}")

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
                f'<form method="post" action="/mobilis/delete" class="inline-action-form"><input type="hidden" name="id" value="{r["id"]}"><button type="submit" class="button-link icon-link danger-link" title="Supprimer" aria-label="Supprimer" onclick="return confirm(\'Supprimer cette direction ?\')"><i data-lucide="trash-2"></i></button></form>'
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
        self.redirect("/clients")

    def save_contract(self):
        self.redirect("/clients")

    def bpu(self):
        query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        message = query.get("message", [""])[0]
        search = query.get("q", [""])[0].strip()
        category = query.get("category", [""])[0].strip()
        unit = query.get("unit", [""])[0].strip()
        selected_st_version = query.get("st_version", [""])[0].strip()
        active_tab = query.get("tab", ["general"])[0].strip().lower()
        if active_tab not in {"general", "st", "mapping", "history"}:
            active_tab = "general"
        try:
            page = max(1, int(query.get("page", ["1"])[0] or 1))
        except (TypeError, ValueError):
            page = 1
        per_page = 10

        where = ["is_active=1"]
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
            total_items = con.execute("SELECT COUNT(*) FROM bpu_items WHERE is_active=1").fetchone()[0]
            latest = con.execute("SELECT MAX(updated_at) FROM bpu_items WHERE is_active=1").fetchone()[0]
            unit_rows = con.execute("""
                SELECT MIN(unite) AS unite
                FROM bpu_items
                WHERE is_active=1 AND TRIM(COALESCE(unite, '')) <> ''
                GROUP BY LOWER(TRIM(unite))
                ORDER BY LOWER(TRIM(unite))
            """).fetchall()
            unit_options = [row["unite"] for row in unit_rows]
            st_versions = con.execute(
                "SELECT * FROM bpu_st_versions ORDER BY id DESC"
            ).fetchall()
            if not selected_st_version and st_versions:
                selected_st_version = str(st_versions[0]["id"])
            pending_st_items = con.execute(
                """
                SELECT * FROM bpu_st_items
                WHERE version_id=? AND review_status='PENDING'
                ORDER BY st_article_number LIMIT 100
                """,
                (selected_st_version or 0,),
            ).fetchall()

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
        st_version_rows = []
        for version in st_versions:
            coverage = 0 if not version["total_rows"] else round(version["mapped_rows"] * 100 / version["total_rows"], 1)
            action = (
                '<span class="status-pill">Actif</span>' if version["status"] == "ACTIVE" else
                (f'<form method="post" action="/bpu/st/activate" class="inline-form"><input type="hidden" name="version_id" value="{version["id"]}"><button type="submit"{" disabled" if coverage < 100 else ""}>Activer</button></form>')
            )
            st_version_rows.append([
                f'<a href="/bpu?tab=history&st_version={version["id"]}">{h(version["code"])}</a>',
                h(version["source_filename"] or "—"), f'{version["mapped_rows"]}/{version["total_rows"]}',
                f'{coverage:.1f}%', h(version["status"]), action,
            ])
        pending_rows = [[
            str(item["st_article_number"]), h(item["designation"]), f'{float(item["confidence"] or 0) * 100:.1f}%',
            f'''<form method="post" action="/bpu/st/confirm" class="inline-form">
              <input type="hidden" name="version_id" value="{item['version_id']}">
              <input type="hidden" name="st_article_number" value="{item['st_article_number']}">
              <input name="general_article_number" type="number" min="1" value="{h(item['general_article_number'] or '')}" placeholder="Code ENT" required>
              <button type="submit">Confirmer</button>
            </form>''',
        ] for item in pending_st_items]
        selected_version = next((version for version in st_versions if str(version["id"]) == str(selected_st_version)), None)
        selected_coverage = 0 if not selected_version or not selected_version["total_rows"] else round(selected_version["mapped_rows"] * 100 / selected_version["total_rows"], 1)
        st_import_panel = f"""
        <section class="bpu-tab-summary">
          <article><span>Version sélectionnée</span><strong>{h(selected_version['code'] if selected_version else '—')}</strong></article>
          <article><span>Couverture</span><strong>{selected_coverage:.1f}%</strong></article>
          <article><span>État</span><strong>{h(selected_version['status'] if selected_version else '—')}</strong></article>
        </section>
        <section class="panel bpu-st-import-panel">
          <h2>Importer une version BPU ST</h2>
          <form method="post" action="/bpu/st/import" enctype="multipart/form-data" class="grid">
            {field('version_code', 'Code version', required=True)}
            {file_field('bpu_st_file', 'Fichier BPU ST', '.xls,.xlsx')}
            <button type="submit">Importer et analyser</button>
          </form>
        </section>"""
        mapping_panel = f"""<section class="bpu-tab-summary">
          <article><span>Mappings confirmés</span><strong>{selected_version['mapped_rows'] if selected_version else 0}</strong></article>
          <article><span>À vérifier</span><strong>{len(pending_st_items)}</strong></article>
          <article><span>Couverture requise</span><strong>100%</strong></article>
        </section><section class="panel"><div class="panel-title-row"><div><h2>Mapping ST → ENT</h2><small>Une version ne peut être activée qu’après validation complète.</small></div></div>{table(['Code ST','Désignation ST','Confiance','Correspondance ENT'], pending_rows)}</section>"""
        history_panel = f"""<section class="panel"><div class="panel-title-row"><div><h2>Historique des imports</h2><small>Versions, couverture et état d’activation.</small></div></div>{table(['Version','Fichier','Mappings','Couverture','État','Action'], st_version_rows)}</section>"""

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

        tabs = ''.join(
            f'<a class="bpu-tab{" active" if active_tab == key else ""}" href="/bpu?tab={key}">{label}</a>'
            for key, label in (("general", "BPU général"), ("st", "BPU ST"), ("mapping", "Mapping ST → ENT"), ("history", "Historique des imports"))
        )
        tab_content = {
            "general": f'{form_html}<section class="panel bpu-list"><div class="bpu-table-scroll">{listing}</div>{pager}</section>',
            "st": st_import_panel,
            "mapping": mapping_panel,
            "history": history_panel,
        }[active_tab]
        main = (
            f'<section class="bpu-main-card">'
            f'{alert}<nav class="bpu-tabs" aria-label="Sections BPU">{tabs}</nav>{tab_content}'
            f'</section>'
        )
        self.respond(layout("Bordereau des prix unitaires (BPU)", f'<div class="bpu-reference-layout"><div>{main}</div>{validation if active_tab == "general" else ""}</div>'))

    def import_bpu_st_post(self):
        try:
            values, files = self.multipart_form()
            if "bpu_st_file" not in files:
                raise ValueError("Fichier BPU ST obligatoire.")
            stored = save_upload(files["bpu_st_file"], "bpu_st", {".xls", ".xlsx"})
            source = Path(stored)
            if not source.is_absolute():
                source = resolve_stored_upload_path(source)
            with db() as con:
                version_id, total, mapped = import_bpu_st_version(
                    con, source, values.get("version_code"), current_actor()
                )
                con.execute(
                    "INSERT INTO audit_log(actor,action,entity_type,entity_id,details) VALUES(?,?,?,?,?)",
                    (current_actor(), "bpu_st.import", "bpu_st_version", str(version_id), json.dumps({"total": total, "mapped": mapped})),
                )
            self.redirect(f"/bpu?tab=mapping&st_version={version_id}&message={mapped}/{total} mappings confirmes")
        except Exception as exc:
            self.redirect(f"/bpu?message={quote('Erreur BPU ST: ' + str(exc))}")

    def confirm_bpu_st_post(self):
        values = self.form()
        try:
            with db() as con:
                confirm_bpu_st_mapping(
                    con, int(values["version_id"]), int(values["st_article_number"]),
                    int(values["general_article_number"]),
                )
            self.redirect(f"/bpu?tab=mapping&st_version={values['version_id']}&message=Mapping confirme")
        except Exception as exc:
            self.redirect(f"/bpu?tab=mapping&st_version={quote(values.get('version_id', ''))}&message={quote('Erreur mapping: ' + str(exc))}")

    def activate_bpu_st_post(self):
        values = self.form()
        try:
            with db() as con:
                activate_bpu_st_version(con, int(values["version_id"]), current_actor())
                con.execute(
                    "INSERT INTO audit_log(actor,action,entity_type,entity_id,details) VALUES(?,?,?,?,?)",
                    (current_actor(), "bpu_st.activate", "bpu_st_version", values["version_id"], "Activation 100%"),
                )
            self.redirect(f"/bpu?tab=history&st_version={values['version_id']}&message=Version BPU ST activee")
        except Exception as exc:
            self.redirect(f"/bpu?st_version={quote(values.get('version_id', ''))}&message={quote('Activation refusee: ' + str(exc))}")

    def save_bpu(self):
        try:
            _, files = self.multipart_form()
            if "bpu_file" in files:
                bpu_path = save_upload(files["bpu_file"], "bpu", {".xlsx"})
                bpu_source = Path(bpu_path)
                if not bpu_source.is_absolute():
                    bpu_source = resolve_stored_upload_path(bpu_source)
                imported = import_bpu(bpu_source)
                with db() as con:
                    replacement = replace_bpu_catalog(con, imported)
                    seed_bpu_st_mapping(con)
                    con.execute(
                        "INSERT INTO audit_log(actor,action,entity_type,entity_id,details) VALUES(?,?,?,?,?)",
                        (
                            current_actor(), "bpu.general.import", "bpu_catalog", "GENERAL",
                            json.dumps(replacement, ensure_ascii=False),
                        ),
                    )
                message = f"{len(imported)} articles BPU importes"
                if replacement["archived_st_versions"]:
                    message += "; mapping BPU ST archive, revalidation requise"
                self.redirect(f"/bpu?message={quote(message)}")
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
        import_token = query.get("import_token", [""])[0]
        new_mode = query.get("new", [""])[0] == "1"
        can_edit_po = user_is_super_admin(self.current_user())
        can_manage_sites = user_has_permission(self.current_user(), "purchase_order.edit")
        scoped_branch = self.visible_company_branch_id()

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
            "type": lambda r: str(purchase_order_type_label(r["type_bc"])).lower(),
            "amount": lambda r: float(r["montant_ttc"] or 0),
            "sites": lambda r: int(r["site_count"] or 0),
        }
        if sort_key not in sorters:
            sort_key = "date"

        with db() as con:
            edit = con.execute("SELECT * FROM purchase_orders WHERE id=? AND deleted_at IS NULL", (edit_id,)).fetchone() if edit_id else None
            edit_is_locked = bool(con.execute(
                """
                SELECT 1 FROM invoices i
                JOIN invoice_tracking it ON it.invoice_id=i.id
                WHERE i.purchase_order_id=? AND i.deleted_at IS NULL
                  AND i.cancelled_at IS NULL AND it.date_depot_dtc IS NOT NULL
                LIMIT 1
                """,
                (edit_id,),
            ).fetchone()) if edit_id else False
            edit_codes = "\n".join(
                row[0] for row in con.execute(
                    "SELECT code_site FROM sites WHERE purchase_order_id=? AND deleted_at IS NULL ORDER BY id",
                    (edit_id,),
                ).fetchall()
            ) if edit and edit["type_bc"] == "NDC" else ""
            directions = con.execute(
                """SELECT cd.id, cd.name AS direction_regionale, cd.sigle, cd.company_branch_id,
                          c.raison_sociale, c.sigle AS client_sigle
                   FROM client_directions cd JOIN clients c ON c.id=cd.client_id
                   WHERE cd.deleted_at IS NULL AND c.deleted_at IS NULL
                     AND (? IS NULL OR cd.company_branch_id=? )
                   ORDER BY c.raison_sociale, cd.name"""
            , (scoped_branch, scoped_branch)).fetchall()
            typologies = active_typologies(con)
            subcontractors = con.execute(
                "SELECT * FROM subcontractors WHERE is_active=1 ORDER BY raison_sociale COLLATE NOCASE"
            ).fetchall()
            design_offices = con.execute(
                "SELECT * FROM design_offices WHERE is_active=1 ORDER BY raison_sociale COLLATE NOCASE"
            ).fetchall()
            all_rows = con.execute("""
                SELECT po.*, md.name AS direction_regionale,
                       COUNT(DISTINCT s.id) AS site_count,
                       COUNT(DISTINCT CASE WHEN EXISTS (
                           SELECT 1 FROM invoices i WHERE i.deleted_at IS NULL AND i.site_id=s.id
                       ) OR EXISTS (
                           SELECT 1 FROM invoice_sites xis JOIN invoices ni ON ni.id=xis.invoice_id
                           WHERE ni.deleted_at IS NULL AND xis.site_id=s.id
                       ) THEN s.id END) AS invoiced_count
                FROM purchase_orders po
                JOIN client_directions md ON md.id = po.client_direction_id
                LEFT JOIN sites s ON s.purchase_order_id=po.id AND s.deleted_at IS NULL
                WHERE po.deleted_at IS NULL
                GROUP BY po.id
            """).fetchall()

            years = sorted({str(r["date_bc"] or "")[:4] for r in all_rows if r["date_bc"]}, reverse=True)
            rows = list(all_rows)
            if scoped_branch is not None:
                rows = [r for r in rows if int(r["company_branch_id"] or 0) == int(scoped_branch)]
            if search:
                needle = search.lower()
                rows = [r for r in rows if needle in str(r["numero_bc"] or "").lower()
                        or needle in str(r["direction_regionale"] or "").lower()
                        or needle in str(r["objet"] or "").lower()]
            if direction_filter:
                rows = [r for r in rows if str(r["client_direction_id"]) == str(direction_filter)]
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
                SELECT po.*, md.name AS direction_regionale, md.sigle AS direction_sigle,
                       EXISTS(
                         SELECT 1 FROM invoices i
                         JOIN invoice_tracking it ON it.invoice_id=i.id
                         WHERE i.purchase_order_id=po.id AND i.deleted_at IS NULL
                           AND it.date_depot_dtc IS NOT NULL
                       ) AS is_locked
                FROM purchase_orders po
                JOIN client_directions md ON md.id=po.client_direction_id
                WHERE po.id=? AND po.deleted_at IS NULL
                  AND (? IS NULL OR po.company_branch_id=?)
            """, (selected_id, scoped_branch, scoped_branch)).fetchone() if selected_id else None
            site_edit = con.execute(
                """SELECT s.*, t.libelle_complet AS typologie_libelle
                   FROM sites s LEFT JOIN typologies t ON t.id=s.typology_id
                   WHERE s.id=? AND s.deleted_at IS NULL""", (site_edit_id,)
            ).fetchone() if site_edit_id else None
            selected_sites = con.execute("""
                SELECT s.*, sc.raison_sociale AS subcontractor_name,
                       d.raison_sociale AS design_office_name,
                       CASE WHEN EXISTS (SELECT 1 FROM invoices i WHERE i.deleted_at IS NULL AND i.site_id=s.id)
                                  OR EXISTS (SELECT 1 FROM invoice_sites xis JOIN invoices ni ON ni.id=xis.invoice_id WHERE ni.deleted_at IS NULL AND xis.site_id=s.id)
                            THEN 1 ELSE 0 END AS invoiced
                FROM sites s
                LEFT JOIN subcontractors sc ON sc.id=s.subcontractor_id
                LEFT JOIN design_offices d ON d.id=s.design_office_id
                WHERE s.purchase_order_id=? AND s.deleted_at IS NULL ORDER BY s.code_site
            """, (selected_id,)).fetchall() if selected_id else []
            po_import = None
            if import_token:
                try:
                    _, po_import = load_import_batch(con, import_token, "purchase_orders")
                except ValueError:
                    po_import = None

        form_html = f"""
        <section class="master-detail-form">
          <header class="drawer-header"><div><small>Bon de commande</small><h3>{'Modifier le BC' if edit else 'Nouveau bon de commande'}</h3></div><a href="/purchase-orders" class="drawer-close" title="Fermer">×</a></header>
        <form method="post" enctype="multipart/form-data" class="grid po-drawer-form" id="po-form">
          <input type="hidden" name="id" value="{h(edit['id'] if edit else '')}">
          {field('numero_bc', 'N° Bon de commande', edit['numero_bc'] if edit else '', required=True)}
          {field('date_bc', 'Date BC', edit['date_bc'] if edit else '', field_type='date')}
          {select_field('client_direction_id', 'Direction client', [(r["id"], f'{r["client_sigle"]} — {r["sigle"]} — {r["direction_regionale"]}') for r in directions], edit['client_direction_id'] if edit else '')}
          {select_field('type_bc', 'Type BC', PURCHASE_ORDER_TYPES, edit['type_bc'] if edit else '')}
          {field('objet', 'Objet', edit['objet'] if edit else '')}
          {decimal_text_field('montant_ttc', 'Montant TTC', edit['montant_ttc'] if edit else '')}
          {file_field('bc_file', 'Fichier BC PDF/JPG', '.pdf,.jpg,.jpeg,.png')}
          {textarea_field('code_sites', 'Code site - NDC', 'Un code par ligne. Nombre de sites: 0', edit_codes)}
          {textarea_field('override_reason', 'Motif de la modification exceptionnelle', 'Obligatoire pour un BC verrouillé.', '') if edit_is_locked else ''}
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
            <td>{h(purchase_order_type_label(r['type_bc']))}</td>
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
            needs_subcontractor, needs_design_office = site_partner_requirements(selected["type_bc"])
            site_rows_html = []
            for site in selected_sites:
                edit_link = f'/purchase-orders?view_id={selected["id"]}&site_edit_id={site["id"]}'
                open_link = f'/table-facturation-new?q={quote(str(site["code_site"]))}'
                facturation = '<span class="po-status-badge factured">Facturé</span>' if site["invoiced"] else '<span class="po-status-badge to-invoice">À facturer</span>'
                subcontractor_value = h(site["subcontractor_name"] or "À compléter") if needs_subcontractor else "—"
                bet_value = h(site["design_office_name"] or "À compléter") if needs_design_office else "—"
                site_actions = (
                    f'<a href="{open_link}" title="Ouvrir"><i data-lucide="external-link"></i></a>'
                    + (f'<a href="{edit_link}" title="Modifier"><i data-lucide="pencil"></i></a><form method="post" action="/purchase-orders/site/delete" class="inline-action-form"><input type="hidden" name="id" value="{site["id"]}"><input type="hidden" name="po_id" value="{selected["id"]}"><button type="submit" class="danger" title="Supprimer" onclick="return confirm(\'Supprimer ce site ?\')"><i data-lucide="trash-2"></i></button></form>' if can_manage_sites else '')
                )
                site_rows_html.append(f"""<tr>
                  <td>{h(site["code_site"])}</td><td>{h(site["nom_site"])}</td><td>{h(site["typologie_site"])}</td><td>{subcontractor_value}</td><td>{bet_value}</td><td>{facturation}</td>
                  <td><span class="po-site-actions">{site_actions}</span></td>
                </tr>""")

            if selected["attachment_path"]:
                stored_path = resolve_stored_upload_path(selected["attachment_path"])
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
                    <form method="post" action="/purchase-orders/document/delete" class="inline-action-form"><input type="hidden" name="id" value="{selected['id']}"><button type="submit" class="danger" title="Supprimer" onclick="return confirm('Supprimer le document BC ?')"><i data-lucide="trash-2"></i></button></form>
                  </div>
                </div>"""
            else:
                attachment = f"""
                <div class="po-file-card empty-document">
                  <div class="po-pdf-icon muted">PDF</div><div class="po-file-meta"><strong>Aucun document joint</strong><small>Ajoutez un PDF, JPG ou PNG</small></div>
                  <div class="po-file-actions"><form method="post" action="/purchase-orders/document" enctype="multipart/form-data"><input type="hidden" name="po_id" value="{selected['id']}"><label title="Ajouter"><i data-lucide="upload"></i><input type="file" name="bc_file" accept=".pdf,.jpg,.jpeg,.png" onchange="this.form.submit()"></label></form></div>
                </div>"""

            typology_map = {row["sigle"]: row["libelle_complet"] for row in typologies}
            fixed_typology = selected["type_bc"] if selected["type_bc"] in {"MGC", "NDC"} else ""
            current_typology = fixed_typology or (site_edit["typologie_site"] if site_edit else "")
            current_typology_label = typology_map.get(current_typology, site_edit["typologie_libelle"] if site_edit else "")
            typology_list = ''.join(
                f'<option value="{h(sigle)}">{h(label)}</option>' for sigle, label in typology_map.items()
            )
            typology_fields = f"""
              <label><span>Sigle typologie</span><input name="typologie_site" value="{h(current_typology)}" list="typology-options" {'readonly' if fixed_typology else ''} required><datalist id="typology-options">{typology_list}</datalist></label>
              <label><span>Libellé complet</span><input name="typologie_libelle" value="{h(current_typology_label)}" {'readonly' if fixed_typology else ''} required></label>
            """
            subcontractor_field = select_field(
                'subcontractor_id', 'Sous-traitant',
                [('', 'Sélectionner')] + [(row['id'], row['raison_sociale']) for row in subcontractors],
                site_edit['subcontractor_id'] if site_edit else '',
            ) if needs_subcontractor else '<input type="hidden" name="subcontractor_id" value="">'
            design_office_field = select_field(
                'design_office_id', "Bureau d'étude (BET)",
                [('', 'Sélectionner')] + [(row['id'], row['raison_sociale']) for row in design_offices],
                site_edit['design_office_id'] if site_edit else '',
            ) if needs_design_office else '<input type="hidden" name="design_office_id" value="">'
            selected_panel = f"""
            <section class="po-detail">
              <header class="po-detail-header"><div><span>Bon de commande</span><h2>{h(selected['numero_bc'])}</h2></div>
                {'<a class="icon-link po-edit-action" href="/purchase-orders?edit_id=' + str(selected['id']) + '" title="Modifier le bon de commande" aria-label="Modifier le bon de commande"><i data-lucide="pencil"></i></a>' if can_edit_po else ('<span class="po-readonly-badge"><i data-lucide="lock-keyhole"></i> Lecture seule</span>' if selected['is_locked'] else '')}
              </header>
              <div class="po-detail-fields">
                <label><span>N° Bon de commande</span><input value="{h(selected['numero_bc'])}" readonly></label>
                <label class="po-date-field"><span>Date BC</span><input value="{h(date_fr(selected['date_bc']))}" readonly><i data-lucide="calendar-days"></i></label>
                <label><span>Direction client</span><input value="{h(selected['direction_regionale'])}" readonly></label>
                <label><span>Type BC</span><input value="{h(purchase_order_type_label(selected['type_bc']))}" readonly></label>
                <label><span>Montant TTC</span><input value="{money(selected['montant_ttc'])} DA" readonly></label>
                <label class="po-object"><span>Objet</span><textarea readonly>{h(selected['objet'])}</textarea><small>{len(str(selected['objet'] or ''))} / 250</small></label>
              </div>
              <section class="po-document"><h3>Document BC</h3>{attachment}</section>
              <div class="section-header sites-heading"><h3>Sites du bon de commande ({len(selected_sites)})</h3>{'<button type="button" class="outline-button site-toggle"><i data-lucide="plus"></i> Ajouter un site</button>' if can_manage_sites else ''}</div>
              {'<form method="post" action="/sites" class="site-inline-form' + (' editing' if site_edit else '') + '">' if can_manage_sites else '<div hidden>'}
                <input type="hidden" name="id" value="{h(site_edit['id'] if site_edit else '')}">
                <input type="hidden" name="purchase_order_id" value="{selected['id']}">
                {field('code_site', 'Code de site', site_edit['code_site'] if site_edit else '', required=True)}
                {field('nom_site', 'Nom de site', site_edit['nom_site'] if site_edit else '', required=True)}
                {typology_fields}
                {subcontractor_field}{design_office_field}
                <button type="submit">{'Enregistrer' if site_edit else 'Ajouter le site'}</button>
              {'</form>' if can_manage_sites else '</div>'}
              <div class="po-sites-wrap"><table class="po-sites-table"><thead><tr><th>CODE SITE</th><th>NOM DU SITE</th><th>TYPOLOGIE</th><th>SOUS-TRAITANT</th><th>BET</th><th>FACTURATION</th><th>ACTIONS</th></tr></thead><tbody>{''.join(site_rows_html) or '<tr><td colspan="7" class="empty">Aucun site</td></tr>'}</tbody></table></div>
              <aside class="info-banner"><span class="info-icon">i</span><span>Pour le Type NDC, le champ multi-saisie « Codes sites » affiche automatiquement le « Nombre de sites ».</span></aside>
              <script>
                document.querySelector('.site-toggle')?.addEventListener('click',()=>document.querySelector('.site-inline-form')?.classList.toggle('editing'));
                const typologyLabels = {json.dumps(typology_map, ensure_ascii=False)};
                const typologySigle = document.querySelector('.site-inline-form input[name="typologie_site"]');
                const typologyLabel = document.querySelector('.site-inline-form input[name="typologie_libelle"]');
                typologySigle?.addEventListener('input', () => {{
                  const known = typologyLabels[typologySigle.value.trim().toUpperCase()];
                  if (known) typologyLabel.value = known;
                }});
                document.querySelector('.site-inline-form')?.addEventListener('submit', event => {{
                  const known = typologyLabels[typologySigle.value.trim().toUpperCase()];
                  if (known && known !== typologyLabel.value.trim() && !confirm('Ce libellé sera appliqué à tous les sites utilisant ce sigle. Continuer ?')) event.preventDefault();
                }});
              </script>
            </section>"""

        alert = f'<section class="alert">{h(message)}</section>' if message else ""
        direction_options = ''.join(f'<option value="{h(r["id"])}"{" selected" if str(r["id"])==str(direction_filter) else ""}>{h(r["direction_regionale"])}</option>' for r in directions)
        type_options = ''.join(
            f'<option value="{h(value)}"{" selected" if value==type_filter else ""}>{h(label)}</option>'
            for value, label in [('', 'Tous'), *PURCHASE_ORDER_TYPES]
        )
        year_options = ''.join(f'<option value="{h(y)}"{" selected" if y==year_filter else ""}>{h(y)}</option>' for y in years)
        filters = f"""
          <form class="po-filterbar" method="get">
            <label class="search-field"><span class="sr-only">Recherche</span><i data-lucide="search"></i><input name="q" value="{h(search)}" placeholder="Rechercher un BC"></label>
            <label><span>Direction client</span><select name="direction"><option value="">Toutes</option>{direction_options}</select></label>
            <label><span>Type BC</span><select name="type">{type_options}</select></label>
            <label class="po-date-filter"><span>Date</span><div><i data-lucide="calendar-days"></i><select name="year"><option value="">Toutes</option>{year_options}</select></div></label>
            <span class="po-filter-spacer"></span><button class="outline-button po-filter-submit" type="submit"><i data-lucide="list-filter"></i> Filtres</button><a class="outline-button po-reset" href="/purchase-orders" title="Réinitialiser"><i data-lucide="rotate-ccw"></i></a>
          </form>
        """
        import_actions = ""
        if user_is_super_admin(self.current_user()):
            import_actions = '''<a class="outline-button" href="/purchase-orders/export"><i data-lucide="file-down"></i> Exporter Excel</a><a class="outline-button" href="/purchase-orders/import/template"><i data-lucide="download"></i> Modèle Excel</a><form method="post" action="/purchase-orders/import/preview" enctype="multipart/form-data"><label class="outline-button file-action-button"><i data-lucide="upload"></i> Importer Excel<input type="file" name="excel_file" accept=".xlsx" required onchange="this.form.submit()"></label></form>'''
        top = f'<div class="page-action-row"><div class="page-actions">{import_actions}</div><a class="button-link primary-blue" href="/purchase-orders?new=1"><i data-lucide="plus"></i> Nouveau BC</a></div>'
        import_preview = ""
        if po_import:
            po_preview_rows = [[str(row['row']), h(row['numero_bc']), h(row['type_bc']), h(row['status']), h(row['error'] or '—')] for row in po_import['purchase_orders']]
            site_preview_rows = [[str(row['row']), h(row['numero_bc']), h(row['code_site']), h(row['status']), h(row['error'] or '—')] for row in po_import['sites']]
            confirm_button = '' if po_import['error_count'] else f'''<form method="post" action="/purchase-orders/import/confirm"><input type="hidden" name="token" value="{h(import_token)}"><button type="submit"><i data-lucide="check"></i> Confirmer l’import</button></form>'''
            import_preview = f'''<section class="panel import-preview"><header><div><h3>Aperçu BC et sites</h3><small>{len(po_preview_rows)} BC, {len(site_preview_rows)} sites, {po_import['error_count']} erreur(s).</small></div><div class="page-actions"><a class="outline-button" href="/purchase-orders/import/report?token={h(import_token)}"><i data-lucide="file-spreadsheet"></i> Rapport</a>{confirm_button}</div></header><h4>Bons de commande</h4>{table(['Ligne','N° BC','Type','Statut','Erreur'], po_preview_rows)}<h4>Sites</h4>{table(['Ligne','N° BC','Code site','Statut','Erreur'], site_preview_rows)}</section>'''
        right_panel = form_html if (edit or new_mode) else selected_panel
        content = alert + top + import_preview + filters + f'<section class="panel po-master-detail"><div class="po-master-list">{listing}</div><div class="po-detail-pane">{right_panel}</div></section>'
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
        po_id = self.form().get("id", "")
        if po_id:
            with db() as con:
                con.execute("UPDATE purchase_orders SET attachment_path=NULL, updated_by=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND deleted_at IS NULL",
                            (current_actor(), po_id))
        self.redirect(f"/purchase-orders?view_id={quote(po_id)}&message={quote('Document BC supprime')}")

    def save_purchase_order(self):
        try:
            values, files = self.multipart_form()
            valid_types = {value for value, _ in PURCHASE_ORDER_TYPES}
            if values.get("type_bc") not in valid_types:
                raise ValueError("Type de bon de commande invalide.")
            attachment_path = save_upload(files["bc_file"], "purchase_orders", {".pdf", ".jpg", ".jpeg", ".png"}) if "bc_file" in files else ""
            codes = parse_code_sites(values.get("code_sites", ""))
            montant_ttc = parse_amount(values.get("montant_ttc"))
            with db() as con:
                client_direction = con.execute(
                    """
                    SELECT id, company_branch_id, legacy_mobilis_direction_id
                    FROM client_directions WHERE id=? AND deleted_at IS NULL
                    """,
                    (values.get("client_direction_id"),),
                ).fetchone()
                if not client_direction:
                    raise ValueError("Direction client obligatoire.")
                if values.get("id"):
                    if not user_is_super_admin(self.current_user()):
                        raise PermissionError("Seul le Super Admin peut modifier un bon de commande.")
                    po_id = int(values["id"])
                    current = con.execute(
                        "SELECT * FROM purchase_orders WHERE id=? AND deleted_at IS NULL",
                        (po_id,),
                    ).fetchone()
                    if not current:
                        raise ValueError("Bon de commande introuvable.")
                    invoice_state = con.execute(
                        """
                        SELECT COUNT(*) AS invoice_count,
                               SUM(CASE WHEN t.date_depot_dtc IS NOT NULL THEN 1 ELSE 0 END) AS issued_count
                        FROM invoices i
                        LEFT JOIN invoice_tracking t ON t.invoice_id=i.id
                        WHERE i.purchase_order_id=? AND i.deleted_at IS NULL AND i.cancelled_at IS NULL
                        """,
                        (po_id,),
                    ).fetchone()
                    old_values = {
                        "numero_bc": current["numero_bc"],
                        "date_bc": current["date_bc"] or "",
                        "client_direction_id": str(current["client_direction_id"]),
                        "type_bc": current["type_bc"],
                        "objet": current["objet"] or "",
                        "montant_ttc": float(current["montant_ttc"] or 0),
                    }
                    new_values = {
                        "numero_bc": values.get("numero_bc", "").strip(),
                        "date_bc": values.get("date_bc") or "",
                        "client_direction_id": str(values.get("client_direction_id") or ""),
                        "type_bc": values.get("type_bc"),
                        "objet": values.get("objet", ""),
                        "montant_ttc": float(montant_ttc),
                    }
                    locked_override = False
                    override_reason = values.get("override_reason", "").strip()
                    if int(invoice_state["issued_count"] or 0):
                        locked_fields = {
                            "numero_bc", "date_bc", "client_direction_id", "type_bc", "montant_ttc"
                        }
                        changed_locked = [
                            key for key in locked_fields if old_values[key] != new_values[key]
                        ]
                        if changed_locked:
                            if not override_reason:
                                raise ValueError(
                                    "Le motif est obligatoire pour modifier un BC verrouillé."
                                )
                            create_backup()
                            locked_override = True
                    elif old_values["type_bc"] != new_values["type_bc"] and int(invoice_state["invoice_count"] or 0):
                        if "NDC" in {old_values["type_bc"], new_values["type_bc"]}:
                            raise ValueError("Le type NDC ne peut pas être modifié après création d'une facture.")
                        allowed = allowed_bpu_categories(new_values["type_bc"])
                        incompatible = con.execute(
                            """
                            SELECT DISTINCT il.categorie_snapshot
                            FROM invoice_lines il
                            JOIN invoices i ON i.id=il.invoice_id
                            WHERE i.purchase_order_id=? AND i.deleted_at IS NULL
                            """,
                            (po_id,),
                        ).fetchall()
                        if any(row[0] not in allowed for row in incompatible):
                            raise ValueError("Le nouveau type BC est incompatible avec les articles déjà facturés.")
                    con.execute("""
                        UPDATE purchase_orders
                        SET numero_bc=?, date_bc=?, mobilis_direction_id=?, client_direction_id=?, company_branch_id=?,
                            type_bc=?, objet=?, montant_ttc=?, attachment_path=?, updated_by=?, updated_at=CURRENT_TIMESTAMP
                        WHERE id=?
                    """, (new_values["numero_bc"], new_values["date_bc"] or None,
                          client_direction["legacy_mobilis_direction_id"], client_direction["id"],
                          client_direction["company_branch_id"], new_values["type_bc"],
                          new_values["objet"], montant_ttc, attachment_path or current["attachment_path"],
                          current_actor(), po_id))
                    existing_sites = con.execute(
                        "SELECT id, code_site FROM sites WHERE purchase_order_id=? AND deleted_at IS NULL ORDER BY id",
                        (po_id,),
                    ).fetchall()
                    existing_codes = [row["code_site"] for row in existing_sites]
                    if values.get("type_bc") == "NDC":
                        if not codes:
                            raise ValueError("Le bon de commande NDC exige au moins un site.")
                        if int(invoice_state["invoice_count"] or 0) and codes != existing_codes:
                            raise ValueError("Les sites NDC sont verrouillés après création de la facture.")
                        if not int(invoice_state["invoice_count"] or 0):
                            code_to_site = {row["code_site"]: row["id"] for row in existing_sites}
                            for removed_code in set(existing_codes) - set(codes):
                                con.execute(
                                    "UPDATE sites SET deleted_at=CURRENT_TIMESTAMP, deleted_by=? WHERE id=?",
                                    (current_actor(), code_to_site[removed_code]),
                                )
                            for code in codes:
                                if code not in code_to_site:
                                    con.execute(
                                        """
                                        INSERT INTO sites(
                                            purchase_order_id,code_site,nom_site,typologie_site,
                                            typology_id,created_by,updated_by
                                        ) VALUES(?,?,?,'NDC',(SELECT id FROM typologies WHERE sigle='NDC'),?,?)
                                        """,
                                        (po_id, code, code, current_actor(), current_actor()),
                                    )
                            con.execute(
                                """UPDATE sites
                                   SET typologie_site='NDC',
                                       typology_id=(SELECT id FROM typologies WHERE sigle='NDC'),
                                       updated_by=?
                                   WHERE purchase_order_id=? AND deleted_at IS NULL""",
                                (current_actor(), po_id),
                            )
                    con.execute(
                        """
                        INSERT INTO audit_log(actor, action, entity_type, entity_id, details)
                        VALUES(?, 'purchase_order.update', 'purchase_order', ?, ?)
                        """,
                        (
                            current_actor(), str(po_id),
                            json.dumps({
                                "old": old_values,
                                "new": new_values,
                                "locked_override": locked_override,
                                "override_reason": override_reason if locked_override else "",
                            }, ensure_ascii=False),
                        ),
                    )
                else:
                    cursor = con.execute("""
                        INSERT INTO purchase_orders(
                            numero_bc,date_bc,mobilis_direction_id,client_direction_id,company_branch_id,
                            type_bc,objet,montant_ttc,attachment_path,created_by,updated_by
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    """, (values.get("numero_bc"), values.get("date_bc") or None,
                          client_direction["legacy_mobilis_direction_id"], client_direction["id"],
                          client_direction["company_branch_id"], values.get("type_bc"),
                          values.get("objet", ""), montant_ttc, attachment_path,
                          current_actor(), current_actor()))
                    po_id = cursor.lastrowid
                if values.get("type_bc") == "NDC" and not values.get("id"):
                    if not codes:
                        raise ValueError("Le bon de commande NDC exige au moins un site.")
                    con.executemany(
                        """INSERT INTO sites(
                               purchase_order_id,code_site,nom_site,typologie_site,typology_id,created_by,updated_by
                           ) VALUES(?,?,?,'NDC',(SELECT id FROM typologies WHERE sigle='NDC'),?,?)""",
                        [(po_id, code, code, current_actor(), current_actor()) for code in codes],
                    )
            self.redirect(f"/purchase-orders?view_id={po_id}&message=Bon de commande enregistre")
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
        po_id = values.get("purchase_order_id", "")
        try:
            with db() as con:
                purchase_order = con.execute(
                    "SELECT type_bc FROM purchase_orders WHERE id=? AND deleted_at IS NULL",
                    (po_id,),
                ).fetchone()
                if not purchase_order:
                    raise ValueError("Bon de commande introuvable.")
                fixed_typology = purchase_order["type_bc"] in {"NDC", "MGC"}
                typology, full_label = validate_typology_values(
                    purchase_order["type_bc"] if fixed_typology else values.get("typologie_site"),
                    purchase_order["type_bc"] if fixed_typology else values.get("typologie_libelle"),
                )
                if not fixed_typology and typology in {"MGC", "NDC"}:
                    raise ValueError("Cette typologie est réservée au type BC correspondant.")
                typology_record = typology_by_sigle(con, typology, active_only=False)
                if typology_record:
                    if typology_record["libelle_complet"] != full_label:
                        con.execute(
                            "UPDATE typologies SET libelle_complet=?,is_active=1,updated_by=? WHERE id=?",
                            (full_label, current_actor(), typology_record["id"]),
                        )
                        con.execute(
                            "INSERT INTO audit_log(actor,action,entity_type,entity_id,details) VALUES(?,?,?,?,?)",
                            (current_actor(), "typology.update_from_site", "typology", str(typology_record["id"]), full_label),
                        )
                else:
                    typology_id = con.execute(
                        "INSERT INTO typologies(sigle,libelle_complet,created_by,updated_by) VALUES(?,?,?,?)",
                        (typology, full_label, current_actor(), current_actor()),
                    ).lastrowid
                    typology_record = con.execute("SELECT * FROM typologies WHERE id=?", (typology_id,)).fetchone()

                subcontractor_id, design_office_id = validate_site_partners(
                    purchase_order["type_bc"], values.get("subcontractor_id"), values.get("design_office_id"),
                )
                if subcontractor_id and not con.execute(
                    "SELECT 1 FROM subcontractors WHERE id=? AND is_active=1", (subcontractor_id,)
                ).fetchone():
                    raise ValueError("Sous-traitant invalide ou inactif.")
                if design_office_id and not con.execute(
                    "SELECT 1 FROM design_offices WHERE id=? AND is_active=1", (design_office_id,)
                ).fetchone():
                    raise ValueError("BET invalide ou inactif.")

                active_invoice_count = con.execute(
                    "SELECT COUNT(*) FROM invoices WHERE purchase_order_id=? AND deleted_at IS NULL AND cancelled_at IS NULL",
                    (po_id,),
                ).fetchone()[0]
                if purchase_order["type_bc"] == "NDC" and active_invoice_count:
                    raise ValueError("Les sites NDC sont verrouillés après facturation.")
                if purchase_order["type_bc"] != "NDC" and con.execute(
                    "SELECT COUNT(*) FROM sites WHERE purchase_order_id=? AND deleted_at IS NULL AND id<>?",
                    (po_id, int(values.get("id") or 0)),
                ).fetchone()[0]:
                    raise ValueError("Un BC hors NDC ne peut contenir qu’un seul site.")

                if values.get("id"):
                    site_id = int(values["id"])
                    con.execute("""
                        UPDATE sites SET purchase_order_id=?,code_site=?,nom_site=?,region='',
                            typologie_site=?,typology_id=?,bet='',subcontractor_id=?,design_office_id=?,
                            updated_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=?
                    """, (po_id, values.get("code_site"), values.get("nom_site"), typology,
                          typology_record["id"], subcontractor_id, design_office_id,
                          current_actor(), site_id))
                    action = "site.update"
                else:
                    site_id = con.execute("""
                        INSERT INTO sites(purchase_order_id,code_site,nom_site,region,typologie_site,
                            typology_id,bet,subcontractor_id,design_office_id,created_by,updated_by)
                        VALUES(?,?,?,'',?,?,'',?,?,?,?)
                    """, (po_id, values.get("code_site"), values.get("nom_site"), typology,
                          typology_record["id"], subcontractor_id, design_office_id,
                          current_actor(), current_actor())).lastrowid
                    action = "site.create"
                con.execute(
                    "INSERT INTO audit_log(actor,action,entity_type,entity_id,details) VALUES(?,?,?,?,?)",
                    (current_actor(), action, "site", str(site_id), json.dumps({
                        "typologie": typology, "sous_traitant_id": subcontractor_id,
                        "bet_id": design_office_id,
                    }, ensure_ascii=False)),
                )
            self.redirect(f"/purchase-orders?view_id={quote(po_id)}&message=Site enregistre")
        except Exception as exc:
            self.redirect(f"/purchase-orders?view_id={quote(po_id)}&message={quote(str(exc))}")

    def invoices(self):
        message = parse_qs(self.path.split("?", 1)[1]).get("message", [""])[0] if "?" in self.path else ""
        query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        edit_id = query.get("edit_id", [""])[0]
        scoped_branch = self.visible_company_branch_id()
        with db() as con:
            edit = con.execute("SELECT * FROM invoice_lifecycle WHERE id=? AND deleted_at IS NULL", (edit_id,)).fetchone() if edit_id else None
            edit_lines = con.execute(
                """SELECT article_number, quantite, source_reference_type, source_st_number
                   FROM invoice_lines WHERE invoice_id=? ORDER BY id""",
                (edit_id,),
            ).fetchall() if edit_id else []
            edit_ndc_sites = {row[0] for row in con.execute("SELECT site_id FROM invoice_sites WHERE invoice_id=?", (edit_id,)).fetchall()} if edit_id else set()
            purchase_orders = con.execute(
                "SELECT id, numero_bc, type_bc FROM purchase_orders WHERE deleted_at IS NULL AND (? IS NULL OR company_branch_id=?) ORDER BY id DESC",
                (scoped_branch, scoped_branch),
            ).fetchall()
            sites = con.execute("""
                SELECT s.id, s.code_site, s.nom_site, s.purchase_order_id, s.deleted_at,
                       s.typologie_site, po.numero_bc
                FROM sites s
                JOIN purchase_orders po ON po.id = s.purchase_order_id
                WHERE (? IS NULL OR po.company_branch_id=?)
                  AND (
                       s.deleted_at IS NULL
                       OR s.id=?
                       OR s.id IN (
                           SELECT site_id FROM invoice_sites WHERE invoice_id=?
                       )
                  )
                ORDER BY po.numero_bc, s.code_site
            """, (
                scoped_branch,
                scoped_branch,
                edit["site_id"] if edit and edit["site_id"] else 0,
                int(edit_id) if edit_id else 0,
            )).fetchall()
            financial = app_settings(con)
        po_option_html = ['<option value="">Sélectionner un bon de commande</option>']
        for r in purchase_orders:
            selected = " selected" if edit and str(edit["purchase_order_id"]) == str(r["id"]) else ""
            po_option_html.append(f'<option value="{r["id"]}" data-type="{h(r["type_bc"])}"{selected}>{h(r["numero_bc"])} - {h(purchase_order_type_label(r["type_bc"]))}</option>')
        site_option_html = ['<option value="">Sélectionner un site</option>']
        for r in sites:
            selected = " selected" if edit and str(edit["site_id"] or "") == str(r["id"]) else ""
            archived = " [Archivé]" if r["deleted_at"] else ""
            site_option_html.append(
                f'<option value="{r["id"]}" data-po="{r["purchase_order_id"]}" '
                f'data-typology="{h(r["typologie_site"] or "")}"{selected}>'
                f'{h(r["code_site"])} - {h(r["nom_site"])}{archived}</option>'
            )
        ndc_checks = []
        for site in sites:
            archived = " [Archivé]" if site["deleted_at"] else ""
            outside_bc = (
                " [Hors BC historique]"
                if edit and site["id"] in edit_ndc_sites
                and site["purchase_order_id"] != edit["purchase_order_id"]
                else ""
            )
            label = f'{site["numero_bc"]} / {site["code_site"]} - {site["nom_site"]}{archived}{outside_bc}'
            is_existing = str(site["id"] in edit_ndc_sites).lower()
            ndc_checks.append(f'<label class="check-row" data-po="{site["purchase_order_id"]}" data-existing="{is_existing}"><input type="checkbox" value="{site["id"]}" checked disabled><span>{h(label)}</span></label>')
        retention_percent = decimal_value(edit["rg_rate"]) if edit else fraction_to_percent(financial["retention_rate"])
        tax_percent = decimal_value(edit["tva_rate"]) if edit else fraction_to_percent(financial["tax_rate"])
        edit_cancelled = bool(edit and edit["cancelled_at"])
        edit_locked = bool(edit and (edit["date_depot_dtc"] or edit_cancelled))
        invoice_type_value = edit["invoice_type"] if edit else ""
        invoice_typology_value = edit["typologie_snapshot"] if edit else ""
        locked_notice = ""
        if edit_cancelled:
            locked_notice = '<section class="alert" data-inline-alert>Facture annulée : les données financières sont verrouillées. Utilisez la Table Facturation pour la restaurer.</section>'
        elif edit_locked:
            locked_notice = '<section class="alert" data-inline-alert>Facture déposée DTC : les données financières sont verrouillées. Utilisez la Table Facturation pour le suivi.</section>'
        fieldset_disabled = " disabled" if edit_locked else ""
        form_html = f"""
        {locked_notice}
        <form method="post" class="panel invoice-form" id="invoice-form">
          <input type="hidden" name="id" value="{h(edit['id'] if edit else '')}">
          <input type="hidden" name="invoice_type" id="invoice-type" value="{h(invoice_type_value)}">
          <fieldset class="invoice-form-fields"{fieldset_disabled}>
          <div class="invoice-head grid">
            <label><span>Bon de commande</span><select name="purchase_order_id" data-searchable-select data-placeholder="Sélectionner un bon de commande">{''.join(po_option_html)}</select></label>
            <label class="site-head-field"><span>Site</span><input id="invoice-site-display" readonly placeholder="Déterminé par le BC"><select name="site_id" id="invoice-site-select" data-placeholder="Saisir ou rechercher un code site">{''.join(site_option_html)}</select></label>
            {field('invoice_number', 'N° Facture', edit['invoice_number'] if edit else '')}
            {field('invoice_date', 'Date facture', edit['invoice_date'] if edit else '', field_type='date')}
            <label><span>Nature facture</span><input id="invoice-type-display" value="{h(invoice_type_label(invoice_type_value))}" readonly placeholder="Déterminée par le BC"></label>
            <label><span>Typologie</span><input id="invoice-typology-display" value="{h(invoice_typology_value)}" readonly placeholder="Déterminée par le site"></label>
          </div>
          <section class="regular-fields wide">
            <input type="hidden" name="lines" id="invoice-lines">
            <div class="invoice-entry">
              <div class="invoice-lines-scroll" id="invoice-lines-scroll">
              <table class="entry-table">
                <colgroup>
                  <col class="ent-code-column"><col class="st-code-column"><col class="designation-column">
                  <col class="unit-column"><col class="price-column"><col class="quantity-column">
                  <col class="amount-column"><col class="actions-column">
                </colgroup>
                <thead>
                  <tr>
                    <th colspan="2" class="article-group-heading">N° Article</th>
                    <th rowspan="2">Désignation</th>
                    <th rowspan="2">Unité</th>
                    <th rowspan="2">PU/HT</th>
                    <th rowspan="2">Quantité</th>
                    <th rowspan="2">Montant HT</th>
                    <th rowspan="2" title="Actions">⚙</th>
                  </tr>
                  <tr class="article-subheadings">
                    <th>BPU ENT</th>
                    <th>BPU ST</th>
                  </tr>
                </thead>
                <tbody id="invoice-lines-body"></tbody>
              </table>
              </div>
              <div class="invoice-compose-footer">
                <div class="compose-actions"><button type="button" id="add-line" hidden>＋ Ajouter ligne</button>{'' if edit_locked else f'<button type="submit" class="create-invoice-button"><i data-lucide="file-plus-2"></i>{"Enregistrer les modifications" if edit else "Enregistrer"}</button>'}<button type="button" class="outline-button" id="reset-lines" title="Réinitialiser"><i data-lucide="rotate-ccw"></i></button></div>
                <dl class="live-totals">
                  <div><dt>Total HT</dt><dd id="live-total-ht">0,00</dd></div>
                  <div><dt>RG {money(retention_percent)}%</dt><dd id="live-rg">0,00</dd></div>
                  <div class="calculation-stage"><dt>Montant HT après RG</dt><dd id="live-after-rg">0,00</dd></div>
                  <div><dt>TVA {money(tax_percent)}%</dt><dd id="live-tva">0,00</dd></div>
                  <div class="grand-total calculation-stage"><dt>Total TTC</dt><dd id="live-ttc">0,00</dd></div>
                </dl>
              </div>
            </div>
          </section>
          <section class="ndc-fields wide">
            <div class="check-list">{"".join(ndc_checks) if ndc_checks else '<p class="empty">Aucun site disponible</p>'}</div>
            <small id="ndc-count">Nombre de sites: 0</small>
          </section>
          </fieldset>
        </form>
        <script>
          const initialLines = {json.dumps([{"article": r["article_number"], "quantity": r["quantite"], "sourceType": r["source_reference_type"], "sourceSt": r["source_st_number"]} for r in edit_lines], ensure_ascii=False)};
          const invoiceType = document.querySelector('#invoice-type');
          const invoiceTypeDisplay = document.querySelector('#invoice-type-display');
          const invoiceTypologyDisplay = document.querySelector('#invoice-typology-display');
          const invoiceTypeMap = {json.dumps({value: invoice_type_for_purchase_order(value) for value, _ in PURCHASE_ORDER_TYPES}, ensure_ascii=False)};
          const invoiceTypeLabels = {json.dumps({value: invoice_type_label(invoice_type_for_purchase_order(value)) for value, _ in PURCHASE_ORDER_TYPES}, ensure_ascii=False)};
          const existingInvoiceType = {json.dumps(invoice_type_value, ensure_ascii=False)};
          const existingPurchaseOrderId = {json.dumps(str(edit['purchase_order_id']) if edit and edit['purchase_order_id'] else '')};
          const regularFields = document.querySelector('.regular-fields');
          const ndcFields = document.querySelector('.ndc-fields');
          const ndcCount = document.querySelector('#ndc-count');
          function syncInvoiceForm() {{
            const isNdc = invoiceType.value === 'NDC';
            regularFields.style.display = isNdc ? 'none' : 'grid';
            ndcFields.style.display = isNdc ? 'block' : 'none';
            document.querySelector('.site-head-field').style.display = isNdc ? 'none' : 'grid';
            const checked = Array.from(document.querySelectorAll('.check-row[data-po]')).filter(row => row.style.display !== 'none').length;
            ndcCount.textContent = `Nombre de sites: ${{checked}}`;
          }}
          invoiceType.addEventListener('change', syncInvoiceForm);
          syncInvoiceForm();

          const linesBody = document.querySelector('#invoice-lines-body');
          const linesInput = document.querySelector('#invoice-lines');
          const addLineButton = document.querySelector('#add-line');
          const invoiceForm = document.querySelector('#invoice-form');
          const linesScroll = document.querySelector('#invoice-lines-scroll');
          const retentionRatePercent = {json.dumps(format(retention_percent, 'f'))};
          const taxRatePercent = {json.dumps(format(tax_percent, 'f'))};

          function enhanceSearchableSelect(select) {{
            const wrapper = document.createElement('div');
            wrapper.className = 'searchable-combobox';
            const input = document.createElement('input');
            input.type = 'text';
            input.autocomplete = 'off';
            input.placeholder = select.dataset.placeholder || '';
            input.setAttribute('role', 'combobox');
            input.setAttribute('aria-autocomplete', 'list');
            input.setAttribute('aria-expanded', 'false');
            const list = document.createElement('div');
            list.className = 'combobox-options';
            list.setAttribute('role', 'listbox');
            list.hidden = true;
            select.parentNode.insertBefore(wrapper, select);
            wrapper.append(input, list, select);
            select.classList.add('combobox-native-select');

            function availableOptions() {{
              return Array.from(select.options).filter(option => option.value && !option.disabled && !option.hidden);
            }}
            function close() {{
              list.hidden = true;
              input.setAttribute('aria-expanded', 'false');
            }}
            function render() {{
              const query = input.value.trim().toLocaleLowerCase('fr');
              list.innerHTML = '';
              availableOptions().filter(option => option.textContent.toLocaleLowerCase('fr').includes(query)).forEach(option => {{
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'combobox-option';
                button.setAttribute('role', 'option');
                button.textContent = option.textContent;
                button.addEventListener('mousedown', event => event.preventDefault());
                button.addEventListener('click', () => {{
                  select.value = option.value;
                  input.value = option.textContent;
                  close();
                  select.dispatchEvent(new Event('change', {{ bubbles: true }}));
                }});
                list.appendChild(button);
              }});
              list.hidden = list.childElementCount === 0;
              input.setAttribute('aria-expanded', String(!list.hidden));
            }}
            function refresh(clearInvalid = false) {{
              const selected = select.selectedOptions[0];
              if (clearInvalid && selected && selected.value && (selected.disabled || selected.hidden)) select.value = '';
              const current = select.selectedOptions[0];
              input.value = current && current.value && !current.disabled && !current.hidden ? current.textContent : '';
              close();
            }}
            input.addEventListener('focus', render);
            input.addEventListener('input', () => {{
              if (select.value) {{
                select.value = '';
                select.dispatchEvent(new Event('change', {{ bubbles: true }}));
              }}
              render();
            }});
            input.addEventListener('keydown', event => {{
              const options = Array.from(list.querySelectorAll('.combobox-option'));
              if (event.key === 'ArrowDown' && options.length) {{ event.preventDefault(); options[0].focus(); }}
              if (event.key === 'Escape') close();
              if (event.key === 'Enter' && options.length === 1) {{ event.preventDefault(); options[0].click(); }}
            }});
            list.addEventListener('keydown', event => {{
              const options = Array.from(list.querySelectorAll('.combobox-option'));
              const index = options.indexOf(document.activeElement);
              if (event.key === 'ArrowDown') {{ event.preventDefault(); options[Math.min(index + 1, options.length - 1)]?.focus(); }}
              if (event.key === 'ArrowUp') {{ event.preventDefault(); (index <= 0 ? input : options[index - 1]).focus(); }}
              if (event.key === 'Escape') {{ close(); input.focus(); }}
            }});
            document.addEventListener('click', event => {{ if (!wrapper.contains(event.target)) close(); }});
            refresh();
            return {{ refresh, input, wrapper }};
          }}

          const purchaseOrderSelect = document.querySelector('select[name="purchase_order_id"]');
          const siteSelect = document.querySelector('select[name="site_id"]');
          const siteDisplay = document.querySelector('#invoice-site-display');
          const purchaseOrderCombobox = enhanceSearchableSelect(purchaseOrderSelect);
          const siteCombobox = enhanceSearchableSelect(siteSelect);

          function decimalParts(value) {{
            const raw = String(value ?? '0').trim().replace(',', '.');
            const match = raw.match(/^([+-])?(\\d*)(?:\\.(\\d*))?$/);
            if (!match || (!match[2] && !match[3])) return {{ digits: 0n, scale: 0 }};
            const negative = match[1] === '-';
            const fraction = match[3] || '';
            const digits = BigInt((match[2] || '0') + fraction);
            return {{ digits: negative ? -digits : digits, scale: fraction.length }};
          }}

          function pow10(count) {{ return 10n ** BigInt(Math.max(0, count)); }}

          function decimalToCents(value) {{
            const part = decimalParts(value);
            return part.scale > 2
              ? part.digits / pow10(part.scale - 2)
              : part.digits * pow10(2 - part.scale);
          }}

          function multiplyToCents(left, right) {{
            const a = decimalParts(left);
            const b = decimalParts(right);
            const scale = a.scale + b.scale;
            const product = a.digits * b.digits;
            return scale > 2 ? product / pow10(scale - 2) : product * pow10(2 - scale);
          }}

          function rateHundredths(value) {{ return decimalToCents(value); }}
          function applyRate(cents, percentage) {{ return cents * rateHundredths(percentage) / 10000n; }}

          function centsStorage(cents) {{
            const negative = cents < 0n;
            const absolute = negative ? -cents : cents;
            return `${{negative ? '-' : ''}}${{absolute / 100n}}.${{String(absolute % 100n).padStart(2, '0')}}`;
          }}

          function formatCents(cents) {{
            const negative = cents < 0n;
            const absolute = negative ? -cents : cents;
            const whole = (absolute / 100n).toString().replace(/\\B(?=(\\d{{3}})+(?!\\d))/g, ' ');
            return `${{negative ? '-' : ''}}${{whole}},${{String(absolute % 100n).padStart(2, '0')}}`;
          }}

          function formatMoney(value) {{ return formatCents(decimalToCents(value)); }}

          function allowedCategories(type) {{
            if (type === 'ACQUISITION') return ['acquisition'];
            if (type === 'CONSTRUCTION') return ['fourniture', 'prestation'];
            if (type === 'CONST_ACQUIS') return ['acquisition', 'fourniture', 'prestation'];
            if (type === 'NDC') return ['ndc'];
            return [];
          }}

          function syncTypeFromPurchaseOrder() {{
            const poSelect = purchaseOrderSelect;
            const poType = poSelect.options[poSelect.selectedIndex]?.dataset.type || '';
            const previousSiteId = siteSelect.value;
            const preserveLegacyNdc = existingInvoiceType === 'NDC' && poSelect.value === existingPurchaseOrderId;
            invoiceType.value = preserveLegacyNdc ? 'NDC' : (invoiceTypeMap[poType] || '');
            invoiceTypeDisplay.value = preserveLegacyNdc ? 'NDC' : (invoiceTypeLabels[poType] || '');
            syncInvoiceForm();
            const poId = poSelect.value;
            Array.from(siteSelect.options).forEach(option => {{
              const visible = !option.value || !poId || option.dataset.po === poId;
              option.hidden = !visible;
              option.disabled = !visible;
            }});
            const availableSites = Array.from(siteSelect.options).filter(option => option.value && !option.disabled && !option.hidden);
            const currentSite = siteSelect.selectedOptions[0];
            if (poId && (!currentSite?.value || currentSite.disabled || currentSite.hidden)) {{
              siteSelect.value = availableSites.length === 1 ? availableSites[0].value : '';
            }}
            const selectedSite = siteSelect.options[siteSelect.selectedIndex];
            const selectedSiteId = selectedSite?.value || '';
            const isNdc = invoiceType.value === 'NDC';
            if (poId && !isNdc && previousSiteId && selectedSiteId !== previousSiteId) {{
              window.showAppToast(
                'Le site précédent ne correspond pas au BC sélectionné. Il a été remplacé par le site du BC.',
                'warning'
              );
            }}
            siteCombobox.wrapper.hidden = Boolean(poId);
            siteDisplay.hidden = !poId;
            siteDisplay.value = poType === 'NDC'
              ? ''
              : (selectedSite?.value ? selectedSite.textContent : (poId ? 'Aucun site unique disponible' : ''));
            invoiceTypologyDisplay.value = poType === 'NDC'
              ? 'NDC'
              : (poType === 'MGC' ? 'MGC' : (selectedSite?.dataset.typology || '').toUpperCase());
            siteCombobox.refresh();
            document.querySelectorAll('.check-row[data-po]').forEach(row => {{
              const visible = row.dataset.po === poId || row.dataset.existing === 'true';
              row.style.display = visible ? 'flex' : 'none';
            }});
            syncInvoiceForm();
          }}

          function addInvoiceLine(shouldFocus = true) {{
            const tr = document.createElement('tr');
            tr.innerHTML = `
              <td class="article-lookup-cell ent-lookup-cell"><input class="article-input ent-article-input" type="text" inputmode="numeric" autocomplete="off" placeholder="N° ENT" aria-label="Article BPU ENT"><div class="article-results" hidden></div></td>
              <td class="article-lookup-cell st-lookup-cell"><input class="article-input st-article-input" type="text" inputmode="numeric" autocomplete="off" placeholder="N° ST" aria-label="Article BPU ST"><div class="article-results" hidden></div></td>
              <td class="designation-cell"><span class="designation-text">Rechercher un article...</span><small class="article-source-badge"></small></td>
              <td class="unite-cell"><select class="unite-ghost" tabindex="-1" disabled><option></option></select></td>
              <td class="pu-cell" data-value="0">0,00</td>
              <td><input class="quantity-input" type="number" min="0.01" step="0.01"></td>
              <td class="montant-cell" data-value="0">0,00</td>
              <td><button type="button" class="remove-line" title="Supprimer la ligne" aria-label="Supprimer la ligne"><i data-lucide="trash-2"></i></button></td>
            `;
            linesBody.appendChild(tr);
            if (window.lucide) lucide.createIcons({{nodes: [tr]}});
            syncLinesViewport();
            if (shouldFocus) tr.querySelector('.article-input').focus();
          }}

          function rowIsBlank(tr) {{
            return !tr.dataset.articleNumber
              && !tr.querySelector('.ent-article-input').value.trim()
              && !tr.querySelector('.st-article-input').value.trim()
              && !tr.querySelector('.quantity-input').value.trim();
          }}

          function rowIsComplete(tr) {{
            return Boolean(tr.dataset.articleNumber) && Number(tr.querySelector('.quantity-input').value) > 0;
          }}

          function syncLinesViewport() {{
            const completedCount = Array.from(linesBody.querySelectorAll('tr')).filter(rowIsComplete).length;
            linesScroll.classList.toggle('is-scrollable', completedCount >= 10);
          }}

          function ensureTrailingBlankLine() {{
            const rows = Array.from(linesBody.querySelectorAll('tr'));
            const blankRows = rows.filter(rowIsBlank);
            blankRows.slice(0, -1).forEach(row => row.remove());
            const remainingRows = Array.from(linesBody.querySelectorAll('tr'));
            if (!remainingRows.length || remainingRows.every(rowIsComplete)) addInvoiceLine(false);
            syncLinesViewport();
          }}

          function clearSelectedArticle(tr, message = 'Rechercher un article...', activeInput = null) {{
            delete tr.dataset.articleNumber;
            delete tr.dataset.sourceType;
            delete tr.dataset.sourceSt;
            tr.classList.remove('article-source-ent', 'article-source-st');
            tr.querySelectorAll('.article-input').forEach(input => {{
              if (input !== activeInput) input.value = '';
            }});
            tr.querySelector('.designation-text').textContent = message;
            tr.querySelector('.article-source-badge').textContent = '';
            tr.querySelector('.unite-cell').textContent = '';
            tr.querySelector('.pu-cell').textContent = '';
            tr.querySelector('.pu-cell').dataset.value = '0';
            tr.querySelector('.montant-cell').textContent = '';
            tr.querySelector('.montant-cell').dataset.value = '0';
            updateFormTotals();
            ensureTrailingBlankLine();
          }}

          function selectArticle(tr, item) {{
            if (invoiceType.value && !allowedCategories(invoiceType.value).includes(item.categorie)) {{
              clearSelectedArticle(tr, `Article non autorisé pour ${{invoiceType.value}} (${{item.categorie}})`);
              return;
            }}
            tr.dataset.articleNumber = item.article_number;
            tr.dataset.sourceType = item.source_reference_type;
            if (item.st_article_number !== null) tr.dataset.sourceSt = item.st_article_number;
            else delete tr.dataset.sourceSt;
            tr.querySelector('.ent-article-input').value = item.article_number;
            tr.querySelector('.st-article-input').value = item.source_reference_type === 'ST' ? item.st_article_number : '';
            tr.classList.toggle('article-source-st', item.source_reference_type === 'ST');
            tr.classList.toggle('article-source-ent', item.source_reference_type !== 'ST');
            tr.querySelector('.designation-text').textContent = item.designation;
            tr.querySelector('.article-source-badge').textContent = item.source_reference_type === 'ST'
              ? `ST ${{item.st_article_number}} → ENT ${{item.article_number}}`
              : `ENT ${{item.article_number}}`;
            tr.querySelector('.unite-cell').textContent = item.unite;
            tr.querySelector('.pu-cell').dataset.value = item.pu_ht;
            tr.querySelector('.pu-cell').textContent = formatMoney(item.pu_ht);
            tr.querySelectorAll('.article-results').forEach(results => results.hidden = true);
            updateLineAmount(tr);
            ensureTrailingBlankLine();
            tr.querySelector('.quantity-input').focus();
          }}

          function renderArticleResults(tr, input, items) {{
            const results = input.closest('.article-lookup-cell').querySelector('.article-results');
            results.innerHTML = '';
            const bounds = input.getBoundingClientRect();
            results.style.left = `${{Math.max(12, Math.min(bounds.left, window.innerWidth - 632))}}px`;
            results.style.top = `${{Math.min(bounds.bottom + 4, window.innerHeight - 294)}}px`;
            results.dataset.activeIndex = '-1';
            items.forEach(item => {{
              const button = document.createElement('button');
              button.type = 'button';
              button.className = `article-result article-result-${{item.source_reference_type === 'ST' ? 'st' : 'ent'}}`;
              const reference = item.source_reference_type === 'ST'
                ? `ST ${{item.st_article_number}} → ENT ${{item.article_number}}`
                : `ENT ${{item.article_number}}`;
              const strong = document.createElement('strong');
              strong.textContent = reference;
              const designation = document.createElement('span');
              designation.className = 'article-result-designation';
              designation.textContent = item.designation;
              const unit = document.createElement('span');
              unit.className = 'article-result-unit';
              unit.textContent = item.unite || '—';
              const price = document.createElement('span');
              price.className = 'article-result-price';
              price.textContent = `${{formatMoney(item.pu_ht)}} DA`;
              button.append(strong, designation, unit, price);
              button.addEventListener('click', () => selectArticle(tr, item));
              results.appendChild(button);
            }});
            if (!items.length) {{
              const empty = document.createElement('div');
              empty.className = 'article-results-empty';
              empty.textContent = 'Aucun article correspondant';
              results.appendChild(empty);
            }}
            results.hidden = false;
          }}

          async function loadArticle(input, selectSingle = true) {{
            const tr = input.closest('tr');
            const query = input.value.trim();
            if (!query) return clearSelectedArticle(tr);
            const sourceSystem = input.classList.contains('st-article-input') ? 'ST' : 'GENERAL';
            clearSelectedArticle(tr, 'Recherche...', input);
            const response = await fetch(`/bpu/search?q=${{encodeURIComponent(query)}}&numbering_system=${{sourceSystem}}`);
            if (!response.ok) {{ clearSelectedArticle(tr, 'Article introuvable', input); return renderArticleResults(tr, input, []); }}
            const payload = await response.json();
            if (!payload.items.length) {{ clearSelectedArticle(tr, 'Article introuvable', input); return renderArticleResults(tr, input, []); }}
            const exactItem = payload.items.find(item => String(
              sourceSystem === 'ST' ? item.st_article_number : item.article_number
            ) === query);
            if (selectSingle && exactItem) return selectArticle(tr, exactItem);
            if (selectSingle && payload.items.length === 1) return selectArticle(tr, payload.items[0]);
            renderArticleResults(tr, input, payload.items);
            tr.querySelector('.designation-text').textContent = payload.items.length > 1 ? 'Sélectionnez le bon article' : payload.items[0].designation;
          }}

          function updateLineAmount(tr) {{
            const pu = tr.querySelector('.pu-cell').dataset.value || '0';
            const quantity = tr.querySelector('.quantity-input').value || '0';
            const amountCents = multiplyToCents(pu, quantity);
            tr.querySelector('.montant-cell').dataset.value = centsStorage(amountCents);
            tr.querySelector('.montant-cell').textContent = amountCents ? formatCents(amountCents) : '';
            updateFormTotals();
            ensureTrailingBlankLine();
          }}

          function updateFormTotals() {{
            const totalHt = Array.from(linesBody.querySelectorAll('.montant-cell')).reduce((sum, cell) => sum + decimalToCents(cell.dataset.value || '0'), 0n);
            const rg = applyRate(totalHt, retentionRatePercent);
            const afterRg = totalHt - rg;
            const tva = applyRate(afterRg, taxRatePercent);
            const ttc = afterRg + tva;
            document.querySelector('#live-total-ht').textContent = formatCents(totalHt);
            document.querySelector('#live-rg').textContent = formatCents(rg);
            document.querySelector('#live-after-rg').textContent = formatCents(afterRg);
            document.querySelector('#live-tva').textContent = formatCents(tva);
            document.querySelector('#live-ttc').textContent = formatCents(ttc);
          }}

          function serializeLines() {{
            const lines = [];
            linesBody.querySelectorAll('tr').forEach(tr => {{
              const article = tr.dataset.articleNumber;
              const quantity = tr.querySelector('.quantity-input').value.trim();
              if (article && quantity) lines.push({{
                article_number: Number(article),
                quantity,
                source_reference_type: tr.dataset.sourceType || 'GENERAL',
                source_st_number: tr.dataset.sourceSt ? Number(tr.dataset.sourceSt) : null,
              }});
            }});
            linesInput.value = JSON.stringify(lines);
          }}

          async function restoreInitialLine(tr, line) {{
            const query = line.sourceType === 'ST' ? line.sourceSt : line.article;
            const sourceSystem = line.sourceType === 'ST' ? 'ST' : 'GENERAL';
            const response = await fetch(`/bpu/search?q=${{encodeURIComponent(query)}}&numbering_system=${{sourceSystem}}`);
            if (!response.ok) return;
            const payload = await response.json();
            const item = payload.items.find(candidate =>
              Number(candidate.article_number) === Number(line.article) &&
              candidate.source_reference_type === line.sourceType
            );
            if (item) selectArticle(tr, item);
          }}

          linesBody.addEventListener('keydown', event => {{
            if (event.target.classList.contains('article-input')) {{
              const results = event.target.closest('.article-lookup-cell').querySelector('.article-results');
              const options = Array.from(results.querySelectorAll('.article-result'));
              let activeIndex = Number(results.dataset.activeIndex || -1);
              if (event.key === 'ArrowDown' && options.length) {{
                event.preventDefault();
                activeIndex = Math.min(activeIndex + 1, options.length - 1);
                options.forEach((option, index) => option.classList.toggle('active', index === activeIndex));
                options[activeIndex].scrollIntoView({{block:'nearest'}});
                results.dataset.activeIndex = String(activeIndex);
                return;
              }}
              if (event.key === 'ArrowUp' && options.length) {{
                event.preventDefault();
                activeIndex = Math.max(activeIndex - 1, 0);
                options.forEach((option, index) => option.classList.toggle('active', index === activeIndex));
                options[activeIndex].scrollIntoView({{block:'nearest'}});
                results.dataset.activeIndex = String(activeIndex);
                return;
              }}
              if (event.key === 'Escape') {{ results.hidden = true; return; }}
              if (event.key === 'Enter' && activeIndex >= 0 && options[activeIndex]) {{
                event.preventDefault(); options[activeIndex].click(); return;
              }}
            }}
            if (event.target.classList.contains('article-input') && (event.key === 'Enter' || event.key === 'Tab')) {{
              event.preventDefault();
              clearTimeout(event.target._searchTimer);
              loadArticle(event.target);
            }}
            if (event.target.classList.contains('quantity-input') && event.key === 'Enter') {{
              event.preventDefault();
              ensureTrailingBlankLine();
              linesBody.querySelector('tr:last-child .article-input')?.focus();
            }}
          }});
          document.addEventListener('click', event => {{
            if (!event.target.closest('.article-lookup-cell')) document.querySelectorAll('.article-results').forEach(result => result.hidden = true);
          }});
          linesBody.addEventListener('change', event => {{
            if (event.target.classList.contains('article-input')) {{
              clearTimeout(event.target._searchTimer);
              loadArticle(event.target);
            }}
            if (event.target.classList.contains('quantity-input')) updateLineAmount(event.target.closest('tr'));
          }});
          linesBody.addEventListener('input', event => {{
            if (event.target.classList.contains('quantity-input')) updateLineAmount(event.target.closest('tr'));
            if (event.target.classList.contains('article-input')) {{
              const input = event.target;
              clearTimeout(input._searchTimer);
              input._searchTimer = setTimeout(() => loadArticle(input, false), 220);
            }}
          }});
          linesBody.addEventListener('click', event => {{
            const removeButton = event.target.closest('.remove-line');
            if (removeButton) {{
              removeButton.closest('tr').remove();
              updateFormTotals();
              ensureTrailingBlankLine();
            }}
          }});
          addLineButton.addEventListener('click', addInvoiceLine);
          document.querySelector('#reset-lines').addEventListener('click', () => {{ linesBody.innerHTML=''; addInvoiceLine(); updateFormTotals(); }});
          purchaseOrderSelect.addEventListener('change', syncTypeFromPurchaseOrder);
          siteSelect.addEventListener('change', () => {{
            if (!purchaseOrderSelect.value) {{
              const selectedSite = siteSelect.options[siteSelect.selectedIndex];
              invoiceTypologyDisplay.value = (selectedSite?.dataset.typology || '').toUpperCase();
            }}
          }});
          invoiceForm.addEventListener('submit', event => {{
            const unresolved = Array.from(linesBody.querySelectorAll('tr')).some(tr =>
              Array.from(tr.querySelectorAll('.article-input')).some(input => input.value.trim()) &&
              tr.querySelector('.quantity-input').value.trim() &&
              !tr.dataset.articleNumber
            );
            if (unresolved) {{
              event.preventDefault();
              window.showAppToast('Sélectionnez chaque article dans la liste de résultats.', 'warning');
              return;
            }}
            serializeLines();
          }});
          syncTypeFromPurchaseOrder();
          if (initialLines.length) {{
            initialLines.forEach(line => {{
              addInvoiceLine(false);
              const tr = linesBody.querySelector('tr:last-child');
              tr.querySelector(line.sourceType === 'ST' ? '.st-article-input' : '.ent-article-input').value = line.sourceType === 'ST' ? line.sourceSt : line.article;
              tr.querySelector('.quantity-input').value = line.quantity;
              restoreInitialLine(tr, line);
            }});
            addInvoiceLine(false);
          }} else {{
            addInvoiceLine(true);
          }}
        </script>"""
        alert = f'<section class="alert">{h(message)}</section>' if message else ""
        self.respond(layout("Factures", alert + form_html))

    def export_invoice(self):
        query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        invoice_id = int(query.get("id", ["0"])[0])
        invoice_number = invoice_export_data(invoice_id)[0]["invoice_number"]
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            temp_path = Path(tmp.name)
        try:
            from scripts.export_invoice_template import build as build_invoice_template

            build_invoice_template(invoice_id, temp_path)
        except Exception as error:
            temp_path.unlink(missing_ok=True)
            return self.respond(f"Erreur export Excel: {h(str(error))}", status=500)
        content = temp_path.read_bytes()
        temp_path.unlink(missing_ok=True)
        user = self.current_user()
        record_invoice_export(
            invoice_id,
            "invoice_xlsx",
            user,
            actor=user["username"],
        )
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
        session_token = self.cookie_value(SESSION_COOKIE)
        if session_token:
            env["PHOENIX_RENDER_SESSION"] = session_token
            env["PHOENIX_RENDER_SESSION_COOKIE"] = SESSION_COOKIE
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
                    cleanup_old_exports(EXPORT_DIR, stem, suffix)
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
        user = self.current_user()
        record_invoice_export(
            invoice_id,
            f"{document}_pdf_download",
            user,
            actor=user["username"],
        )
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
        user = self.current_user()
        record_invoice_export(
            invoice_id,
            f"{document}_pdf_open",
            user,
            actor=user["username"],
        )
        def open_later():
            open_pdf_with_system_viewer(output_path)
        threading.Timer(0.1, open_later).start()
        cleanup_old_exports(EXPORT_DIR, stem, suffix)
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
        user = self.current_user()
        record_invoice_export(
            invoice_id,
            f"{document}_pdf",
            user,
            actor=user["username"],
        )
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
            total = sum((decimal_value(line["montant_ht"] or 0) for line in rows), decimal_value(0))
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
      Typologie de site: {h(typology_export_label(invoice))}<br>
      Bon de commande: {h(invoice['numero_bc'])}
    </section>
    <table><thead><tr><th>N°</th><th>Désignation</th><th>Unité</th><th>Quantités</th><th>PU/HT</th><th>Montant/HT</th></tr></thead><tbody>{rows_html}</tbody></table>
    <section class="totals">
      <div><span>TOTAL EN H.T</span><span>{money(invoice['total_ht'])}</span></div>
      <div><span>RETENUE DE GARANTIE {h(format_rate(invoice['rg_rate']))}%</span><span>{money(invoice['retenue_garantie'])}</span></div>
      <div><span>MONTANT HT APRES RG</span><span>{money(invoice['montant_ht_apres_rg'])}</span></div>
      <div><span>T V A {h(format_rate(invoice['tva_rate']))}%</span><span>{money(invoice['tva'])}</span></div>
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
      <div>Typologie de site : {h(typology_export_label(invoice))}</div>
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
            total = sum((decimal_value(line["montant_ht"] or 0) for line in rows), decimal_value(0))
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
      <div>Typologie de site : {h(typology_export_label(invoice))}</div>
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
            values = self.form()
            parsed = parse_qs(self._cached_body.decode("utf-8")) if hasattr(self, "_cached_body") else {key: [value] for key, value in values.items()}
            if not values.get("id"):
                require_feature("create_invoice")
            invoice_id = int(values.get("id") or 0)
            purchase_order_id = int(values["purchase_order_id"]) if values.get("purchase_order_id") else None
            requested_site_id = int(values["site_id"]) if values.get("site_id") else None
            invoice_number = values.get("invoice_number", "").strip() or None
            invoice_date = values.get("invoice_date", "").strip() or None
            raw_lines = values.get("lines", "").strip()
            parsed_lines = parse_invoice_line_payload(raw_lines) if raw_lines else []
            if not purchase_order_id and not parsed_lines:
                raise ValueError(
                    "Sélectionnez un bon de commande ou ajoutez au moins un article."
                )
            numbering_system = "GENERAL"
            with db() as con:
                existing = None
                if invoice_id:
                    existing = con.execute(
                        "SELECT * FROM invoice_lifecycle WHERE id=? AND deleted_at IS NULL",
                        (invoice_id,),
                    ).fetchone()
                    if not existing:
                        raise ValueError("Facture introuvable.")
                    if existing["date_depot_dtc"]:
                        raise ValueError("Facture déposée DTC : les données financières sont verrouillées.")

                if existing:
                    invoice_rg_rate = rate_percent(existing["rg_rate"])
                    invoice_tva_rate = rate_percent(existing["tva_rate"])
                else:
                    financial_defaults = app_settings(con)
                    invoice_rg_rate = fraction_to_percent(financial_defaults["retention_rate"])
                    invoice_tva_rate = fraction_to_percent(financial_defaults["tax_rate"])

                purchase_order = None
                invoice_type = None
                purchase_order_type = None
                if purchase_order_id:
                    purchase_order = con.execute(
                        "SELECT * FROM purchase_orders WHERE id=? AND deleted_at IS NULL",
                        (purchase_order_id,),
                    ).fetchone()
                    if not purchase_order:
                        raise ValueError("Bon de commande introuvable.")
                    scoped_branch = self.visible_company_branch_id()
                    if scoped_branch is not None and int(purchase_order["company_branch_id"] or 0) != int(scoped_branch):
                        raise ValueError("Accès refusé à la direction de ce bon de commande.")
                    purchase_order_type = purchase_order["type_bc"]
                    invoice_type = invoice_type_for_purchase_order(purchase_order_type)
                    if not invoice_type:
                        raise ValueError("Type de bon de commande non pris en charge.")
                    if (
                        existing
                        and existing["invoice_type"] == "NDC"
                        and existing["purchase_order_id"] == purchase_order_id
                    ):
                        purchase_order_type = "NDC"
                        invoice_type = "NDC"

                site_ids = []
                typology_record = resolve_invoice_typology(con, purchase_order_type)
                typologie_snapshot = typology_record["sigle"] if typology_record else ""
                typologie_label_snapshot = typology_record["libelle_complet"] if typology_record else ""
                if invoice_type == "NDC":
                    numbering_system = "GENERAL"
                    if purchase_order_type != "NDC" and not (
                        existing and existing["invoice_type"] == "NDC"
                    ):
                        raise ValueError("Une facture NDC exige un bon de commande NDC.")
                    duplicate = con.execute(
                        """
                        SELECT id FROM invoices
                        WHERE purchase_order_id=? AND invoice_type='NDC'
                          AND deleted_at IS NULL AND cancelled_at IS NULL AND id<>?
                        LIMIT 1
                        """,
                        (purchase_order_id, invoice_id),
                    ).fetchone()
                    if duplicate:
                        raise ValueError("Ce bon de commande possède déjà une facture NDC active.")
                    site_ids = [
                        int(row[0]) for row in con.execute(
                            "SELECT id FROM sites WHERE purchase_order_id=? AND deleted_at IS NULL ORDER BY id",
                            (purchase_order_id,),
                        ).fetchall()
                    ]
                    if not site_ids:
                        raise ValueError("Ajoutez au moins un site au bon de commande NDC.")
                    incomplete_bet = con.execute(
                        "SELECT COUNT(*) FROM sites WHERE purchase_order_id=? AND deleted_at IS NULL AND design_office_id IS NULL",
                        (purchase_order_id,),
                    ).fetchone()[0]
                    if incomplete_bet:
                        raise ValueError("Affectez un BET à chaque site NDC avant de créer la facture.")
                    bpu = con.execute(
                        "SELECT * FROM bpu_items WHERE article_number=6 AND is_active=1"
                    ).fetchone()
                    if not bpu:
                        raise ValueError("Article 6 introuvable dans BPU.")
                    quantity = len(site_ids)
                    lines = [{
                        "article_number": 6,
                        "designation": bpu["designation"],
                        "unite": bpu["unite"],
                        "pu_ht": decimal_value(bpu["pu_ht"]),
                        "categorie": "ndc",
                        "quantite": quantity,
                        "montant_ht": line_total(bpu["pu_ht"], quantity),
                        "source_reference_type": "GENERAL",
                        "source_st_number": None,
                    }]
                    site_id = None
                else:
                    site_id = None
                    if purchase_order_id:
                        available_sites = con.execute(
                            "SELECT * FROM sites WHERE purchase_order_id=? AND deleted_at IS NULL ORDER BY id",
                            (purchase_order_id,),
                        ).fetchall()
                        if len(available_sites) != 1:
                            raise ValueError(
                                "Un bon de commande hors NDC doit contenir exactement un site."
                            )
                        site = available_sites[0]
                        validate_site_partners(
                            purchase_order_type, site["subcontractor_id"], site["design_office_id"]
                        )
                        site_id = int(site["id"])
                        typology_record = resolve_invoice_typology(
                            con, purchase_order_type, site["typologie_site"]
                        )
                        if not typology_record:
                            raise ValueError(
                                "Typologie de site invalide ou inactive."
                            )
                        typologie_snapshot = typology_record["sigle"]
                        typologie_label_snapshot = typology_record["libelle_complet"]
                    elif requested_site_id:
                        site = con.execute(
                            """
                            SELECT s.*, po.company_branch_id
                            FROM sites s
                            JOIN purchase_orders po ON po.id=s.purchase_order_id
                            WHERE s.id=? AND s.deleted_at IS NULL AND po.deleted_at IS NULL
                            """,
                            (requested_site_id,),
                        ).fetchone()
                        if not site:
                            raise ValueError("Site introuvable ou archivé.")
                        scoped_branch = self.visible_company_branch_id()
                        if (
                            scoped_branch is not None
                            and int(site["company_branch_id"] or 0) != int(scoped_branch)
                        ):
                            raise ValueError("Accès refusé au site sélectionné.")
                        site_id = int(site["id"])
                        typology_record = resolve_invoice_typology(
                            con, None, site["typologie_site"]
                        )
                        if not typology_record:
                            raise ValueError("Typologie de site invalide ou inactive.")
                        typologie_snapshot = typology_record["sigle"]
                        typologie_label_snapshot = typology_record["libelle_complet"]
                    lines = []
                    allowed = allowed_bpu_categories(purchase_order_type)
                    for line_payload in parsed_lines:
                        line_source = str(line_payload.get("source_reference_type") or "GENERAL").upper()
                        line_numbering = "ST" if line_source == "ST" else "GENERAL"
                        bpu, quantity, source_type, source_st = resolve_invoice_line(
                            con, line_payload, line_numbering
                        )
                        article_number = int(bpu["article_number"])
                        if purchase_order_type and bpu["categorie"] not in allowed:
                            raise ValueError(
                                f"Article {article_number} non autorisé pour {purchase_order_type_label(purchase_order_type)}."
                            )
                        pu_ht = decimal_value(bpu["pu_ht"])
                        lines.append({
                            "article_number": article_number,
                            "designation": bpu["designation"],
                            "unite": bpu["unite"],
                            "pu_ht": pu_ht,
                            "categorie": bpu["categorie"],
                            "quantite": quantity,
                            "montant_ht": line_total(pu_ht, quantity),
                            "source_reference_type": source_type,
                            "source_st_number": source_st,
                        })
                    if any(line["source_reference_type"] == "ST" for line in lines):
                        numbering_system = "ST"

                total_ht, retenue, ht_after_rg, tva, total_ttc = totals_from_lines(
                    lines,
                    retention_rate=invoice_rg_rate,
                    tax_rate=invoice_tva_rate,
                )
                mapping_version = active_mapping_version(con) if numbering_system == "ST" else ""
                if invoice_id:
                    con.execute("""
                        UPDATE invoices
                        SET invoice_number=?, invoice_type=?, purchase_order_id=?, site_id=?, invoice_date=?,
                            total_ht=?, retenue_garantie=?, montant_ht_apres_rg=?, tva=?, total_ttc=?,
                            montant_en_lettres=?, numbering_system=?, typologie_snapshot=?, typologie_label_snapshot=?,
                            updated_by=?, updated_at=CURRENT_TIMESTAMP
                        WHERE id=?
                    """, (
                        invoice_number,
                        invoice_type,
                        purchase_order_id,
                        site_id,
                        invoice_date,
                        money_storage(total_ht),
                        money_storage(retenue),
                        money_storage(ht_after_rg),
                        money_storage(tva),
                        money_storage(total_ttc),
                        amount_words_placeholder(total_ttc) if total_ttc else "",
                        numbering_system,
                        typologie_snapshot,
                        typologie_label_snapshot,
                        current_actor(),
                        invoice_id,
                    ))
                    con.execute("DELETE FROM invoice_lines WHERE invoice_id=?", (invoice_id,))
                    con.execute("DELETE FROM invoice_sites WHERE invoice_id=?", (invoice_id,))
                else:
                    cursor = con.execute("""
                        INSERT INTO invoices(invoice_number,invoice_type,purchase_order_id,site_id,invoice_date,total_ht,retenue_garantie,montant_ht_apres_rg,tva,total_ttc,rg_rate,tva_rate,montant_en_lettres,numbering_system,typologie_snapshot,typologie_label_snapshot,created_by,updated_by)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        invoice_number,
                        invoice_type,
                        purchase_order_id,
                        site_id,
                        invoice_date,
                        money_storage(total_ht),
                        money_storage(retenue),
                        money_storage(ht_after_rg),
                        money_storage(tva),
                        money_storage(total_ttc),
                        money_storage(invoice_rg_rate),
                        money_storage(invoice_tva_rate),
                        amount_words_placeholder(total_ttc) if total_ttc else "",
                        numbering_system,
                        typologie_snapshot,
                        typologie_label_snapshot,
                        current_actor(),
                        current_actor(),
                    ))
                    invoice_id = cursor.lastrowid
                con.executemany("""
                    INSERT INTO invoice_lines(invoice_id,article_number,designation_snapshot,unite_snapshot,pu_ht_snapshot,categorie_snapshot,quantite,montant_ht,source_reference_type,source_st_number,mapping_version)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """, [
                    (invoice_id, line["article_number"], line["designation"], line["unite"], format(decimal_value(line["pu_ht"]), "f"), line["categorie"], format(decimal_value(line["quantite"]), "f"), money_storage(line["montant_ht"]), line["source_reference_type"], line["source_st_number"], mapping_version)
                    for line in lines
                ])
                if invoice_type == "NDC":
                    con.executemany("INSERT INTO invoice_sites(invoice_id,site_id) VALUES(?,?)", [(invoice_id, current_site_id) for current_site_id in site_ids])
                lifecycle = con.execute(
                    "SELECT lifecycle_status FROM invoice_lifecycle WHERE id=?",
                    (invoice_id,),
                ).fetchone()
                action = "invoice.draft.update" if values.get("id") else "invoice.draft.create"
                con.execute(
                    """
                    INSERT INTO invoice_tracking_events(
                        invoice_id, event_type, field_name, new_value, actor, reason
                    ) VALUES(?, 'DRAFT_SAVED', 'lifecycle_status', ?, ?, ?)
                    """,
                    (
                        invoice_id,
                        lifecycle["lifecycle_status"],
                        current_actor(),
                        "Création" if not values.get("id") else "Mise à jour",
                    ),
                )
                con.execute(
                    """
                    INSERT INTO audit_log(actor, action, entity_type, entity_id, details)
                    VALUES(?, ?, 'invoice', ?, ?)
                    """,
                    (
                        current_actor(),
                        action,
                        str(invoice_id),
                        json.dumps({
                            "status": lifecycle["lifecycle_status"],
                            "purchase_order_type": purchase_order_type,
                            "invoice_type": invoice_type,
                            "line_count": len(lines),
                            "numbering_system": numbering_system,
                        }, sort_keys=True),
                    ),
                )
            status_label = STATUS_LABELS.get(lifecycle["lifecycle_status"], lifecycle["lifecycle_status"])
            self.redirect(
                f"/invoices?edit_id={invoice_id}&message={quote('Enregistrement réussi — ' + status_label)}"
            )
        except Exception as exc:
            self.redirect(f"/invoices?message={quote('Erreur creation facture: ' + str(exc))}")

    def style(self):
        self.respond((ROOT_DIR / "static" / "style.css").read_text(encoding="utf-8"), content_type="text/css; charset=utf-8")


class ApplicationHTTPServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        logging.getLogger("phoenix.http").exception(
            "Unhandled HTTP request error from %s:%s",
            *client_address[:2],
            exc_info=sys.exc_info(),
        )


def create_server(host=None, port=None):
    host = host or os.environ.get("PHOENIX_HOST", "127.0.0.1")
    if port is None:
        port = int(os.environ.get("PHOENIX_PORT", "8000"))
    server = ApplicationHTTPServer((host, int(port)), App)
    server.daemon_threads = True
    return server


def main():
    server = create_server()
    host, port = server.server_address[:2]
    print(f"PhoEniX BPU running on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
