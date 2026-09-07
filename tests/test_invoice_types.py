import pytest

from services.invoice_types import (
    TYPOLOGY_CODES,
    allowed_bpu_categories,
    invoice_type_for_purchase_order,
    invoice_type_label,
    invoice_typology,
    purchase_order_type_label,
)


pytestmark = pytest.mark.unit


def test_purchase_order_and_invoice_type_mapping():
    assert purchase_order_type_label("ACQUISITION") == "ACQ"
    assert purchase_order_type_label("CONSTRUCTION") == "CONST"
    assert purchase_order_type_label("CONST_ACQUIS") == "CONST/ACQ"
    assert purchase_order_type_label("NDC") == "NDC"
    assert purchase_order_type_label("MGC") == "MGC"

    assert invoice_type_for_purchase_order("MGC") == "CONSTRUCTION"
    assert invoice_type_for_purchase_order("NDC") == "NDC"
    assert invoice_type_label("NDC") == "NDC"
    assert invoice_type_label("CONSTRUCTION") == "CONST"


def test_mgc_uses_construction_bpu_categories():
    assert allowed_bpu_categories("MGC") == frozenset(
        {"fourniture", "prestation"}
    )
    assert allowed_bpu_categories("NDC") == frozenset({"ndc"})


def test_invoice_typology_is_derived_from_purchase_order_and_site():
    assert invoice_typology("MGC", "A12") == "MGC"
    assert invoice_typology("NDC", "A12") == "NDC"
    assert invoice_typology("CONSTRUCTION", "a12") == "A12"
    assert invoice_typology("ACQUISITION", "unknown") == ""
    assert set(TYPOLOGY_CODES) == {"A9", "A12", "A15", "PYLONE", "COLLOC", "MGC", "NDC"}
