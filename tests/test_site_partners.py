import pytest

from services.site_partners import (
    direction_area_from_sigle,
    site_partner_requirements,
    validate_partner_values,
    validate_site_partners,
)


@pytest.mark.unit
def test_partner_requirements_cover_every_purchase_order_nature():
    assert site_partner_requirements("CONSTRUCTION") == (True, False)
    assert site_partner_requirements("MGC") == (True, False)
    assert site_partner_requirements("ACQUISITION") == (False, True)
    assert site_partner_requirements("NDC") == (False, True)
    assert site_partner_requirements("CONST_ACQUIS") == (True, True)


@pytest.mark.unit
def test_site_partner_validation_clears_forbidden_relations():
    assert validate_site_partners("CONSTRUCTION", 4, 9) == (4, None)
    assert validate_site_partners("ACQUISITION", 4, 9) == (None, 9)
    assert validate_site_partners("CONST_ACQUIS", 4, 9) == (4, 9)
    with pytest.raises(ValueError, match="sous-traitant"):
        validate_site_partners("MGC", None, None)
    with pytest.raises(ValueError, match="BET"):
        validate_site_partners("NDC", None, None)


@pytest.mark.unit
def test_partner_fields_and_direction_area_are_normalized():
    assert validate_partner_values(
        "  Travaux   Ouest ", " Mohamed  Amine ", "0550 00",
        " CONTACT@EXAMPLE.COM ", " Oran ",
    ) == (
        "Travaux Ouest", "Mohamed Amine", "0550 00", "contact@example.com", "Oran"
    )
    with pytest.raises(ValueError, match="e-mail"):
        validate_partner_values("Travaux Ouest", "Contact", "", "invalide", "")
    assert direction_area_from_sigle("DR CHLEF") == "CHLEF"
    assert direction_area_from_sigle(" dr alger est ") == "ALGER EST"
