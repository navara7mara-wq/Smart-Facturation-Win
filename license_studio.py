import argparse
import hashlib
import json
import os
import tempfile
import tkinter as tk
from datetime import date
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from services.license_issuer import (
    create_private_key,
    issue_license,
    load_private_key,
    parse_request_bytes,
    public_key_fingerprint,
    public_key_pem,
    save_encrypted_private_key,
    verify_issued_document,
)
from services.machine_identity import activation_request, verify_activation_request


APP_NAME = "PhoEniX License Studio"
APP_VERSION = "1.1.0"


def app_data_dir():
    configured = os.environ.get("PHOENIX_LICENSE_STUDIO_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return base / "SAPTA" / APP_NAME


DATA_DIR = app_data_dir()
VAULT_PATH = DATA_DIR / "publisher-key.encrypted.pem"
HISTORY_PATH = DATA_DIR / "issued-licenses.jsonl"


def write_history(payload, output_path):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "license_id": payload["license_id"],
        "customer": payload["customer"],
        "edition": payload["edition"],
        "expires_at": payload["expires_at"],
        "machine_id": payload["machine_id"],
        "device_name": payload["device_name"],
        "issued_at": payload["issued_at"],
        "max_users": payload["max_users"],
        "max_invoices": payload["max_invoices"],
        "output_file": str(output_path),
    }
    with HISTORY_PATH.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def read_history():
    if not HISTORY_PATH.exists():
        return []
    records = []
    for line in HISTORY_PATH.read_text(encoding="utf-8").splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(records))


def center_window(window, width, height):
    window.update_idletasks()
    x = max(0, (window.winfo_screenwidth() - width) // 2)
    y = max(0, (window.winfo_screenheight() - height) // 2)
    window.geometry(f"{width}x{height}+{x}+{y}")


class LicenseStudio(tk.Tk):
    COLORS = {
        "nav": "#06294b",
        "nav_active": "#0d65b8",
        "background": "#f4f7fa",
        "panel": "#ffffff",
        "border": "#d8e1ea",
        "text": "#10243e",
        "muted": "#64748b",
        "green": "#0b9b57",
        "green_dark": "#087a45",
        "blue_soft": "#e9f3fc",
        "warning": "#a15c00",
    }

    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} {APP_VERSION}")
        self.minsize(1040, 680)
        center_window(self, 1180, 760)
        self.configure(bg=self.COLORS["background"])
        self.request_payload = None
        self.request_path = None
        self._build_styles()
        self._build_shell()
        self.show_issue_page()
        self.after(100, self.ensure_vault)

    def _build_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=self.COLORS["background"])
        style.configure("Panel.TFrame", background=self.COLORS["panel"])
        style.configure("TLabel", background=self.COLORS["panel"], foreground=self.COLORS["text"], font=("Segoe UI", 10))
        style.configure("Muted.TLabel", foreground=self.COLORS["muted"], font=("Segoe UI", 9))
        style.configure("Title.TLabel", background=self.COLORS["background"], foreground=self.COLORS["text"], font=("Segoe UI Semibold", 22))
        style.configure("Section.TLabel", foreground=self.COLORS["text"], font=("Segoe UI Semibold", 12))
        style.configure("Value.TLabel", foreground=self.COLORS["text"], font=("Segoe UI Semibold", 10))
        style.configure("TEntry", padding=(10, 8), fieldbackground="#ffffff", bordercolor=self.COLORS["border"])
        style.configure("TCombobox", padding=(10, 7), fieldbackground="#ffffff", bordercolor=self.COLORS["border"])
        style.configure("Primary.TButton", font=("Segoe UI Semibold", 10), padding=(16, 10), foreground="#ffffff", background=self.COLORS["green"])
        style.map("Primary.TButton", background=[("active", self.COLORS["green_dark"]), ("disabled", "#9db9aa")])
        style.configure("Secondary.TButton", font=("Segoe UI Semibold", 9), padding=(12, 8), foreground=self.COLORS["text"], background="#ffffff", bordercolor=self.COLORS["border"])
        style.map("Secondary.TButton", background=[("active", "#eef4f8")])
        style.configure("Treeview", rowheight=34, font=("Segoe UI", 9), background="#ffffff", fieldbackground="#ffffff", bordercolor=self.COLORS["border"])
        style.configure("Treeview.Heading", font=("Segoe UI Semibold", 9), background="#eef3f7", foreground=self.COLORS["text"], padding=8)

    def _build_shell(self):
        self.nav = tk.Frame(self, width=220, bg=self.COLORS["nav"])
        self.nav.pack(side="left", fill="y")
        self.nav.pack_propagate(False)

        brand = tk.Frame(self.nav, bg=self.COLORS["nav"], padx=22, pady=24)
        brand.pack(fill="x")
        tk.Label(brand, text="PHOENIX", bg=self.COLORS["nav"], fg="#ffffff", font=("Segoe UI Semibold", 17)).pack(anchor="w")
        tk.Label(brand, text="LICENSE STUDIO", bg=self.COLORS["nav"], fg="#9fc5e7", font=("Segoe UI", 9)).pack(anchor="w")

        self.nav_buttons = {}
        for key, label, command in (
            ("issue", "Émettre une licence", self.show_issue_page),
            ("history", "Historique", self.show_history_page),
            ("security", "Sécurité", self.show_security_page),
        ):
            button = tk.Button(
                self.nav,
                text=label,
                command=command,
                anchor="w",
                padx=22,
                pady=13,
                relief="flat",
                borderwidth=0,
                bg=self.COLORS["nav"],
                fg="#dce9f4",
                activebackground=self.COLORS["nav_active"],
                activeforeground="#ffffff",
                font=("Segoe UI Semibold", 10),
                cursor="hand2",
            )
            button.pack(fill="x")
            self.nav_buttons[key] = button

        footer = tk.Frame(self.nav, bg=self.COLORS["nav"], padx=22, pady=18)
        footer.pack(side="bottom", fill="x")
        tk.Label(footer, text=f"Version {APP_VERSION}", bg=self.COLORS["nav"], fg="#9fc5e7", font=("Segoe UI", 8)).pack(anchor="w")
        tk.Label(footer, text="Outil privé de l’éditeur", bg=self.COLORS["nav"], fg="#ffffff", font=("Segoe UI", 9)).pack(anchor="w", pady=(3, 0))

        self.main = tk.Frame(self, bg=self.COLORS["background"], padx=26, pady=22)
        self.main.pack(side="left", fill="both", expand=True)
        self.content = tk.Frame(self.main, bg=self.COLORS["background"])
        self.content.pack(fill="both", expand=True)
        self.status_var = tk.StringVar(value="Prêt")
        tk.Label(self.main, textvariable=self.status_var, bg=self.COLORS["background"], fg=self.COLORS["muted"], font=("Segoe UI", 9), anchor="w").pack(fill="x", pady=(12, 0))

    def clear_page(self, active):
        for child in self.content.winfo_children():
            child.destroy()
        for key, button in self.nav_buttons.items():
            button.configure(bg=self.COLORS["nav_active"] if key == active else self.COLORS["nav"], fg="#ffffff" if key == active else "#dce9f4")

    def page_heading(self, title, subtitle):
        ttk.Label(self.content, text=title, style="Title.TLabel").pack(anchor="w")
        tk.Label(self.content, text=subtitle, bg=self.COLORS["background"], fg=self.COLORS["muted"], font=("Segoe UI", 10)).pack(anchor="w", pady=(3, 18))

    def panel(self, parent, padding=18):
        frame = tk.Frame(parent, bg=self.COLORS["panel"], highlightbackground=self.COLORS["border"], highlightthickness=1, padx=padding, pady=padding)
        return frame

    def show_issue_page(self):
        self.clear_page("issue")
        self.page_heading("Émettre une licence", "Importez la demande du client, définissez les limites puis signez le fichier.")

        request_panel = self.panel(self.content)
        request_panel.pack(fill="x", pady=(0, 14))
        top = tk.Frame(request_panel, bg=self.COLORS["panel"])
        top.pack(fill="x")
        ttk.Label(top, text="1. Appareil client", style="Section.TLabel").pack(side="left")
        ttk.Button(top, text="Importer la demande", style="Secondary.TButton", command=self.import_request).pack(side="right")
        self.request_name_var = tk.StringVar(value="Aucune demande importée")
        self.machine_var = tk.StringVar(value="—")
        self.device_var = tk.StringVar(value="—")
        self.product_var = tk.StringVar(value="—")
        tk.Label(request_panel, textvariable=self.request_name_var, bg=self.COLORS["panel"], fg=self.COLORS["muted"], font=("Segoe UI", 9)).pack(anchor="w", pady=(8, 12))
        details = tk.Frame(request_panel, bg=self.COLORS["blue_soft"], padx=14, pady=11)
        details.pack(fill="x")
        for column, (label, variable) in enumerate((("Identifiant appareil", self.machine_var), ("Nom de l’appareil", self.device_var), ("Version produit", self.product_var))):
            cell = tk.Frame(details, bg=self.COLORS["blue_soft"])
            cell.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 12, 0))
            details.grid_columnconfigure(column, weight=2 if column == 0 else 1)
            tk.Label(cell, text=label, bg=self.COLORS["blue_soft"], fg=self.COLORS["muted"], font=("Segoe UI", 8)).pack(anchor="w")
            tk.Label(cell, textvariable=variable, bg=self.COLORS["blue_soft"], fg=self.COLORS["text"], font=("Consolas" if column == 0 else "Segoe UI Semibold", 9)).pack(anchor="w", pady=(3, 0))

        form_panel = self.panel(self.content)
        form_panel.pack(fill="both", expand=True)
        ttk.Label(form_panel, text="2. Conditions de licence", style="Section.TLabel").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 14))
        for column in range(4):
            form_panel.grid_columnconfigure(column, weight=1)

        self.customer_var = tk.StringVar()
        self.edition_var = tk.StringVar(value="professional")
        self.expiry_var = tk.StringVar(value=date(date.today().year + 1, 12, 31).isoformat())
        self.perpetual_var = tk.BooleanVar(value=False)
        self.users_var = tk.StringVar(value="10")
        self.invoices_var = tk.StringVar(value="100000")
        self.unlimited_invoices_var = tk.BooleanVar(value=False)
        self.reference_var = tk.StringVar()

        self._field(form_panel, "Client", self.customer_var, 1, 0, span=2)
        self._combo(form_panel, "Édition", self.edition_var, ("standard", "professional", "enterprise"), 1, 2, span=2)
        self.expiry_entry = self._field(form_panel, "Expiration (AAAA-MM-JJ)", self.expiry_var, 3, 0)
        self._field(form_panel, "Utilisateurs maximum", self.users_var, 3, 1)
        self.invoices_entry = self._field(form_panel, "Factures maximum", self.invoices_var, 3, 2)
        self._field(form_panel, "Référence commerciale", self.reference_var, 3, 3)
        self._toggle(form_panel, "Licence perpétuelle", self.perpetual_var, 4, 0, self.update_limit_fields)
        self._toggle(form_panel, "Factures illimitées", self.unlimited_invoices_var, 4, 2, self.update_limit_fields)

        actions = tk.Frame(form_panel, bg=self.COLORS["panel"])
        actions.grid(row=5, column=0, columnspan=4, sticky="e", pady=(24, 0))
        ttk.Button(actions, text="Vérifier une licence", style="Secondary.TButton", command=self.verify_license_file).pack(side="left", padx=(0, 10))
        self.issue_button = ttk.Button(actions, text="Créer le fichier licence", style="Primary.TButton", command=self.create_license_file)
        self.issue_button.pack(side="left")

    def _field(self, parent, label, variable, row, column, span=1):
        frame = tk.Frame(parent, bg=self.COLORS["panel"])
        frame.grid(row=row, column=column, columnspan=span, sticky="ew", padx=(0, 12 if column + span < 4 else 0), pady=(0, 14))
        tk.Label(frame, text=label, bg=self.COLORS["panel"], fg=self.COLORS["text"], font=("Segoe UI Semibold", 9)).pack(anchor="w", pady=(0, 5))
        entry = ttk.Entry(frame, textvariable=variable)
        entry.pack(fill="x")
        return entry

    def _toggle(self, parent, label, variable, row, column, command):
        button = tk.Checkbutton(
            parent,
            text=label,
            variable=variable,
            command=command,
            bg=self.COLORS["panel"],
            activebackground=self.COLORS["panel"],
            fg=self.COLORS["text"],
            selectcolor="#ffffff",
            font=("Segoe UI Semibold", 9),
            cursor="hand2",
            padx=0,
        )
        button.grid(row=row, column=column, sticky="w", pady=(0, 4))
        return button

    def update_limit_fields(self):
        self.expiry_entry.configure(state="disabled" if self.perpetual_var.get() else "normal")
        self.invoices_entry.configure(state="disabled" if self.unlimited_invoices_var.get() else "normal")

    def _combo(self, parent, label, variable, values, row, column, span=1):
        frame = tk.Frame(parent, bg=self.COLORS["panel"])
        frame.grid(row=row, column=column, columnspan=span, sticky="ew", padx=(0, 12 if column + span < 4 else 0), pady=(0, 14))
        tk.Label(frame, text=label, bg=self.COLORS["panel"], fg=self.COLORS["text"], font=("Segoe UI Semibold", 9)).pack(anchor="w", pady=(0, 5))
        ttk.Combobox(frame, textvariable=variable, values=values, state="readonly").pack(fill="x")

    def import_request(self):
        path = filedialog.askopenfilename(title="Demande d’activation", filetypes=(("Demande JSON", "*.json"), ("Tous les fichiers", "*.*")))
        if not path:
            return
        try:
            payload = parse_request_bytes(Path(path).read_bytes())
        except Exception as exc:
            messagebox.showerror("Demande invalide", str(exc), parent=self)
            return
        self.request_payload = payload
        self.request_path = Path(path)
        self.request_name_var.set(self.request_path.name)
        self.machine_var.set(payload["machine_id"])
        self.device_var.set(payload.get("device_name", "Windows PC"))
        self.product_var.set(payload.get("product_version", "—"))
        self.status_var.set("Demande d’activation vérifiée.")

    def prompt_password(self, title="Déverrouiller le coffre"):
        dialog = tk.Toplevel(self)
        dialog.title(title)
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)
        dialog.configure(bg=self.COLORS["panel"], padx=22, pady=20)
        tk.Label(dialog, text=title, bg=self.COLORS["panel"], fg=self.COLORS["text"], font=("Segoe UI Semibold", 14)).pack(anchor="w")
        tk.Label(dialog, text="Saisissez le mot de passe maître de la clé de signature.", bg=self.COLORS["panel"], fg=self.COLORS["muted"], font=("Segoe UI", 9)).pack(anchor="w", pady=(4, 14))
        password_var = tk.StringVar()
        entry = ttk.Entry(dialog, textvariable=password_var, show="●", width=42)
        entry.pack(fill="x")
        result = {"value": None}

        def accept():
            result["value"] = password_var.get()
            dialog.destroy()

        actions = tk.Frame(dialog, bg=self.COLORS["panel"])
        actions.pack(fill="x", pady=(18, 0))
        ttk.Button(actions, text="Annuler", style="Secondary.TButton", command=dialog.destroy).pack(side="right")
        ttk.Button(actions, text="Déverrouiller", style="Primary.TButton", command=accept).pack(side="right", padx=(0, 8))
        entry.bind("<Return>", lambda _event: accept())
        entry.focus_set()
        center_window(dialog, 470, 210)
        self.wait_window(dialog)
        return result["value"]

    def unlocked_key(self):
        password = self.prompt_password()
        if password is None:
            return None
        try:
            return load_private_key(VAULT_PATH.read_bytes(), password)
        except Exception:
            messagebox.showerror("Accès refusé", "Mot de passe incorrect ou coffre endommagé.", parent=self)
            return None

    def create_license_file(self):
        if not self.request_payload:
            messagebox.showwarning("Demande requise", "Importez d’abord la demande d’activation du client.", parent=self)
            return
        private_key = self.unlocked_key()
        if not private_key:
            return
        try:
            document = issue_license(
                private_key,
                self.request_payload,
                self.customer_var.get(),
                self.edition_var.get(),
                None if self.perpetual_var.get() else self.expiry_var.get(),
                int(self.users_var.get()),
                None if self.unlimited_invoices_var.get() else int(self.invoices_var.get()),
                self.reference_var.get(),
            )
        except Exception as exc:
            messagebox.showerror("Données incorrectes", str(exc), parent=self)
            return
        safe_customer = "_".join(self.customer_var.get().strip().split())[:40] or "Client"
        default_name = f"PhoEniX_Licence_{safe_customer}_{self.request_payload['machine_id'][-8:]}.license.json"
        output = filedialog.asksaveasfilename(
            title="Enregistrer la licence",
            initialfile=default_name,
            defaultextension=".license.json",
            filetypes=(("Licence PhoEniX", "*.license.json"), ("Fichier JSON", "*.json")),
        )
        if not output:
            return
        target = Path(output)
        target.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        if not verify_issued_document(json.loads(target.read_text(encoding="utf-8")), private_key):
            target.unlink(missing_ok=True)
            messagebox.showerror("Échec de vérification", "Le fichier généré n’a pas passé la vérification cryptographique.", parent=self)
            return
        write_history(document["payload"], target)
        self.status_var.set(f"Licence créée : {target.name}")
        messagebox.showinfo("Licence créée", f"Le fichier a été signé et vérifié.\n\n{target}", parent=self)

    def verify_license_file(self):
        path = filedialog.askopenfilename(title="Vérifier une licence", filetypes=(("Licence PhoEniX", "*.json *.license"), ("Tous les fichiers", "*.*")))
        if not path:
            return
        private_key = self.unlocked_key()
        if not private_key:
            return
        try:
            document = json.loads(Path(path).read_text(encoding="utf-8-sig"))
            valid = verify_issued_document(document, private_key)
            payload = document.get("payload", {})
        except Exception:
            valid, payload = False, {}
        if valid:
            expiration = "À vie" if payload.get("license_term") == "perpetual" else payload.get("expires_at", "—")
            invoices = "Illimitées" if payload.get("max_invoices") is None else payload.get("max_invoices")
            messagebox.showinfo("Licence authentique", f"Client : {payload.get('customer', '—')}\nAppareil : {payload.get('machine_id', '—')}\nExpiration : {expiration}\nFactures : {invoices}", parent=self)
        else:
            messagebox.showerror("Licence invalide", "La signature est absente, altérée ou issue d’une autre clé.", parent=self)

    def show_history_page(self):
        self.clear_page("history")
        self.page_heading("Historique", "Registre local des licences émises par cet outil.")
        panel = self.panel(self.content, padding=12)
        panel.pack(fill="both", expand=True)
        columns = ("customer", "edition", "device", "expiry", "users", "invoices", "issued")
        tree = ttk.Treeview(panel, columns=columns, show="headings")
        headings = {
            "customer": "Client",
            "edition": "Édition",
            "device": "Appareil",
            "expiry": "Expiration",
            "users": "Utilisateurs",
            "invoices": "Factures",
            "issued": "Émise le",
        }
        widths = {"customer": 190, "edition": 95, "device": 145, "expiry": 100, "users": 85, "invoices": 90, "issued": 160}
        for column in columns:
            tree.heading(column, text=headings[column])
            tree.column(column, width=widths[column], minwidth=70, anchor="w")
        for record in read_history():
            expiration = record.get("expires_at") or "À vie"
            invoices = "Illimitées" if record.get("max_invoices") is None else record.get("max_invoices")
            tree.insert("", "end", values=(record.get("customer"), record.get("edition"), record.get("device_name"), expiration, record.get("max_users"), invoices, str(record.get("issued_at", "")).replace("T", " ")[:19]))
        scrollbar = ttk.Scrollbar(panel, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def show_security_page(self):
        self.clear_page("security")
        self.page_heading("Sécurité", "Gestion de la clé privée de signature. Elle ne doit jamais être transmise aux clients.")
        panel = self.panel(self.content)
        panel.pack(fill="x")
        ttk.Label(panel, text="Coffre de signature", style="Section.TLabel").pack(anchor="w")
        status = "Configuré et chiffré" if VAULT_PATH.exists() else "Non configuré"
        tk.Label(panel, text=status, bg=self.COLORS["panel"], fg=self.COLORS["green"] if VAULT_PATH.exists() else self.COLORS["warning"], font=("Segoe UI Semibold", 11)).pack(anchor="w", pady=(8, 4))
        tk.Label(panel, text=str(VAULT_PATH), bg=self.COLORS["panel"], fg=self.COLORS["muted"], font=("Consolas", 9)).pack(anchor="w")
        actions = tk.Frame(panel, bg=self.COLORS["panel"])
        actions.pack(anchor="w", pady=(18, 0))
        ttk.Button(actions, text="Exporter la clé publique", style="Secondary.TButton", command=self.export_public_key).pack(side="left")
        ttk.Button(actions, text="Sauvegarder le coffre chiffré", style="Secondary.TButton", command=self.backup_vault).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Changer le mot de passe", style="Secondary.TButton", command=self.change_password).pack(side="left", padx=(8, 0))

    def export_public_key(self):
        key = self.unlocked_key()
        if not key:
            return
        output = filedialog.asksaveasfilename(title="Exporter la clé publique", initialfile="license_public_key.pem", defaultextension=".pem", filetypes=(("Clé PEM", "*.pem"),))
        if output:
            Path(output).write_bytes(public_key_pem(key))
            messagebox.showinfo("Export terminé", f"Empreinte : {public_key_fingerprint(key)}", parent=self)

    def backup_vault(self):
        if not VAULT_PATH.exists():
            return
        output = filedialog.asksaveasfilename(title="Sauvegarder le coffre", initialfile="PhoEniX_publisher-key.encrypted.pem", defaultextension=".pem", filetypes=(("Coffre chiffré", "*.pem"),))
        if output:
            Path(output).write_bytes(VAULT_PATH.read_bytes())
            messagebox.showinfo("Sauvegarde terminée", "Le coffre reste chiffré. Conservez cette copie hors ligne.", parent=self)

    def change_password(self):
        key = self.unlocked_key()
        if not key:
            return
        self.setup_vault(existing_key=key, title="Nouveau mot de passe maître")

    def ensure_vault(self):
        if not VAULT_PATH.exists():
            self.setup_vault()

    def setup_vault(self, existing_key=None, title="Initialiser le coffre de signature"):
        dialog = tk.Toplevel(self)
        dialog.title(title)
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)
        dialog.configure(bg=self.COLORS["panel"], padx=24, pady=22)
        tk.Label(dialog, text=title, bg=self.COLORS["panel"], fg=self.COLORS["text"], font=("Segoe UI Semibold", 16)).pack(anchor="w")
        help_text = "Choisissez la clé privée utilisée par les versions clients actuelles." if existing_key is None else "Définissez un nouveau mot de passe pour le coffre existant."
        tk.Label(dialog, text=help_text, bg=self.COLORS["panel"], fg=self.COLORS["muted"], font=("Segoe UI", 9), wraplength=510, justify="left").pack(anchor="w", pady=(5, 16))

        source_var = tk.StringVar()
        if existing_key is None:
            source_frame = tk.Frame(dialog, bg=self.COLORS["panel"])
            source_frame.pack(fill="x", pady=(0, 12))
            ttk.Entry(source_frame, textvariable=source_var, width=52).pack(side="left", fill="x", expand=True)
            ttk.Button(source_frame, text="Parcourir", style="Secondary.TButton", command=lambda: source_var.set(filedialog.askopenfilename(parent=dialog, title="Clé privée Ed25519", filetypes=(("Clé PEM", "*.pem"),)) or source_var.get())).pack(side="left", padx=(8, 0))

        password_var = tk.StringVar()
        confirm_var = tk.StringVar()
        for label, variable in (("Mot de passe maître (12 caractères minimum)", password_var), ("Confirmer le mot de passe", confirm_var)):
            tk.Label(dialog, text=label, bg=self.COLORS["panel"], fg=self.COLORS["text"], font=("Segoe UI Semibold", 9)).pack(anchor="w", pady=(4, 5))
            ttk.Entry(dialog, textvariable=variable, show="●", width=62).pack(fill="x", pady=(0, 9))

        def save():
            if password_var.get() != confirm_var.get():
                messagebox.showerror("Confirmation incorrecte", "Les mots de passe ne correspondent pas.", parent=dialog)
                return
            try:
                key = existing_key
                if key is None:
                    if not source_var.get():
                        raise ValueError("Sélectionnez la clé privée existante.")
                    key = load_private_key(Path(source_var.get()).read_bytes())
                save_encrypted_private_key(key, VAULT_PATH, password_var.get())
                verification = load_private_key(VAULT_PATH.read_bytes(), password_var.get())
                if public_key_pem(verification) != public_key_pem(key):
                    raise ValueError("La vérification du coffre a échoué.")
            except Exception as exc:
                messagebox.showerror("Initialisation impossible", str(exc), parent=dialog)
                return
            dialog.destroy()
            self.status_var.set("Coffre de signature initialisé et vérifié.")
            messagebox.showinfo("Coffre sécurisé", f"Clé publique : {public_key_fingerprint(key)}\n\nArchivez ensuite la clé source dans un support hors ligne.", parent=self)

        actions = tk.Frame(dialog, bg=self.COLORS["panel"])
        actions.pack(fill="x", pady=(16, 0))
        ttk.Button(actions, text="Enregistrer le coffre", style="Primary.TButton", command=save).pack(side="right")
        if VAULT_PATH.exists():
            ttk.Button(actions, text="Annuler", style="Secondary.TButton", command=dialog.destroy).pack(side="right", padx=(0, 8))
        center_window(dialog, 610, 390 if existing_key is None else 330)
        self.wait_window(dialog)


def smoke_test():
    with tempfile.TemporaryDirectory() as temporary:
        private_key = create_private_key()
        vault = Path(temporary) / "vault.pem"
        save_encrypted_private_key(private_key, vault, "A-strong-test-password")
        unlocked = load_private_key(vault.read_bytes(), "A-strong-test-password")
        request_document = activation_request("2.3.1")
        request_payload = verify_activation_request(request_document)
        license_document = issue_license(unlocked, request_payload, "Smoke Test", "professional", "2999-12-31", 5, 100)
        result = {
            "vault_encrypted": b"ENCRYPTED PRIVATE KEY" in vault.read_bytes(),
            "request_valid": request_payload is not None,
            "license_valid": verify_issued_document(license_document, unlocked),
            "machine_bound": license_document["payload"]["machine_id"] == request_payload["machine_id"],
            "license_sha256": hashlib.sha256(json.dumps(license_document, sort_keys=True).encode("utf-8")).hexdigest(),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if all(result[key] for key in ("vault_encrypted", "request_valid", "license_valid", "machine_bound")) else 1


def main():
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--smoke-test", action="store_true")
    args = parser.parse_args()
    if args.smoke_test:
        raise SystemExit(smoke_test())
    app = LicenseStudio()
    app.mainloop()


if __name__ == "__main__":
    main()
