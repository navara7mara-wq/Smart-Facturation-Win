import re

PARTNER_RULES = {
    "CONSTRUCTION": (True, False),
    "MGC": (True, False),
    "ACQUISITION": (False, True),
    "NDC": (False, True),
    "CONST_ACQUIS": (True, True),
}


def normalize_partner_sigle(value):
    return " ".join(str(value or "").strip().upper().split())


def validate_partner_values(raison_sociale, contact="", telephone="", email="", adresse=""):
    name = " ".join(str(raison_sociale or "").strip().split())
    contact_name = " ".join(str(contact or "").strip().split())
    phone = " ".join(str(telephone or "").strip().split())
    email_address = str(email or "").strip().lower()
    address = " ".join(str(adresse or "").strip().split())
    if not name or len(name) > 160:
        raise ValueError("La raison sociale est obligatoire (160 caractères maximum).")
    if len(contact_name) > 160:
        raise ValueError("Le contact ne doit pas dépasser 160 caractères.")
    if email_address and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email_address):
        raise ValueError("L'adresse e-mail est invalide.")
    if len(email_address) > 160 or len(phone) > 60 or len(address) > 250:
        raise ValueError("Les coordonnées dépassent la longueur autorisée.")
    return name, contact_name, phone, email_address, address


def site_partner_requirements(purchase_order_type):
    return PARTNER_RULES.get(str(purchase_order_type or "").strip().upper(), (False, False))


def validate_site_partners(purchase_order_type, subcontractor_id, design_office_id):
    needs_subcontractor, needs_design_office = site_partner_requirements(purchase_order_type)
    subcontractor_id = int(subcontractor_id or 0) or None
    design_office_id = int(design_office_id or 0) or None
    if needs_subcontractor and not subcontractor_id:
        raise ValueError("Le sous-traitant est obligatoire pour cette nature de BC.")
    if needs_design_office and not design_office_id:
        raise ValueError("Le BET est obligatoire pour cette nature de BC.")
    return (
        subcontractor_id if needs_subcontractor else None,
        design_office_id if needs_design_office else None,
    )


def direction_area_from_sigle(sigle):
    value = " ".join(str(sigle or "").strip().upper().split())
    if value.startswith("DR ") and len(value) > 3:
        return value[3:].strip()
    return value
