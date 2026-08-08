from services.invoice_exports import section_rows


def test_section_rows_includes_category_and_prices():
    rows = section_rows({
        "FOURNITURES": [{
            "article_number": 1,
            "designation_snapshot": "Cable",
            "unite_snapshot": "m",
            "quantite": 12,
            "pu_ht_snapshot": 100,
            "montant_ht": 1200,
        }]
    }, with_prices=True)

    assert rows == [
        [("FOURNITURES", 4), ("", 4), ("", 4), ("", 4), ("", 4), ("", 4)],
        [(1, 5), ("Cable", 5), ("m", 5), (12, 5), (100, 5), (1200, 5)],
    ]


def test_section_rows_skips_empty_categories_without_prices():
    rows = section_rows({
        "ACQUISITION": [],
        "PRESTATION": [{
            "article_number": 6,
            "designation_snapshot": "Installation",
            "unite_snapshot": "Forfait",
            "quantite": 1,
            "pu_ht_snapshot": 500,
            "montant_ht": 500,
        }],
    }, with_prices=False)

    assert rows == [
        [("PRESTATION", 4), ("", 4), ("", 4), ("", 4)],
        [(6, 5), ("Installation", 5), ("Forfait", 5), (1, 5)],
    ]
