import re


DEFAULT_TYPOLOGIES = (
    ("A9", "A9"),
    ("A12", "A12 ( MAT 12M + BTS OUTDOOR )"),
    ("A15", "A15"),
    ("PYLONE", "PYLONE"),
    ("COLLOC", "COLLOC"),
    ("MGC", "MGC"),
    ("NDC", "NDC"),
)

_SIGLE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9 /_-]{0,29}$")


def normalize_typology_sigle(value):
    return " ".join(str(value or "").strip().upper().split())


def validate_typology_values(sigle, full_label):
    normalized_sigle = normalize_typology_sigle(sigle)
    normalized_label = " ".join(str(full_label or "").strip().split())
    if not _SIGLE_PATTERN.fullmatch(normalized_sigle):
        raise ValueError(
            "Le sigle doit contenir 1 à 30 caractères : lettres, chiffres, espace, /, _ ou -."
        )
    if not normalized_label:
        raise ValueError("Le libellé complet est obligatoire.")
    if len(normalized_label) > 250:
        raise ValueError("Le libellé complet ne doit pas dépasser 250 caractères.")
    return normalized_sigle, normalized_label


def active_typologies(connection):
    return connection.execute(
        "SELECT * FROM typologies WHERE is_active=1 ORDER BY sigle COLLATE NOCASE"
    ).fetchall()


def typology_by_sigle(connection, sigle, *, active_only=True):
    normalized = normalize_typology_sigle(sigle)
    where_active = " AND is_active=1" if active_only else ""
    return connection.execute(
        f"SELECT * FROM typologies WHERE sigle=? COLLATE NOCASE{where_active}",
        (normalized,),
    ).fetchone()


def resolve_invoice_typology(connection, purchase_order_type, site_typology=""):
    purchase_order_type = normalize_typology_sigle(purchase_order_type)
    sigle = purchase_order_type if purchase_order_type in {"MGC", "NDC"} else site_typology
    return typology_by_sigle(connection, sigle)


def typology_export_label(invoice):
    return (
        invoice["typologie_label_snapshot"]
        or invoice["typologie_snapshot"]
        or invoice["typologie_site"]
        or ""
    )
