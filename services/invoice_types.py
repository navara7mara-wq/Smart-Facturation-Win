PURCHASE_ORDER_TYPES = (
    ("ACQUISITION", "ACQ"),
    ("CONSTRUCTION", "CONST"),
    ("CONST_ACQUIS", "CONST/ACQ"),
    ("NDC", "NDC"),
    ("MGC", "MGC"),
)

PURCHASE_ORDER_TYPE_LABELS = dict(PURCHASE_ORDER_TYPES)

INVOICE_TYPE_LABELS = {
    "ACQUISITION": "ACQ",
    "CONSTRUCTION": "CONST",
    "CONST_ACQUIS": "CONST/ACQ",
    "NDC": "NDC",
}

TYPOLOGY_CODES = ("A9", "A12", "A15", "PYLONE", "COLLOC", "MGC", "NDC")

INVOICE_TYPE_BY_PURCHASE_ORDER = {
    "ACQUISITION": "ACQUISITION",
    "CONSTRUCTION": "CONSTRUCTION",
    "CONST_ACQUIS": "CONST_ACQUIS",
    "NDC": "NDC",
    "MGC": "CONSTRUCTION",
}

ALLOWED_BPU_CATEGORIES_BY_PURCHASE_ORDER = {
    "ACQUISITION": frozenset({"acquisition"}),
    "CONSTRUCTION": frozenset({"fourniture", "prestation"}),
    "CONST_ACQUIS": frozenset({"acquisition", "fourniture", "prestation"}),
    "NDC": frozenset({"ndc"}),
    "MGC": frozenset({"fourniture", "prestation"}),
}


def purchase_order_type_label(value):
    return PURCHASE_ORDER_TYPE_LABELS.get(str(value or ""), str(value or ""))


def invoice_type_label(value):
    return INVOICE_TYPE_LABELS.get(str(value or ""), str(value or ""))


def invoice_type_for_purchase_order(value):
    return INVOICE_TYPE_BY_PURCHASE_ORDER.get(str(value or ""))


def allowed_bpu_categories(value):
    return ALLOWED_BPU_CATEGORIES_BY_PURCHASE_ORDER.get(
        str(value or ""), frozenset()
    )


def invoice_typology(purchase_order_type, site_typology=""):
    purchase_order_type = str(purchase_order_type or "").upper()
    if purchase_order_type in {"MGC", "NDC"}:
        return purchase_order_type
    candidate = str(site_typology or "").strip().upper()
    return candidate if candidate in TYPOLOGY_CODES else ""
