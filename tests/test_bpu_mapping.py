import sqlite3

import pytest

from services.bpu import replace_bpu_catalog
from services.bpu_mapping import (
    automatic_designation_mapping,
    import_bpu_st_version,
    parse_bpu_st_file,
    resolve_invoice_line,
    search_bpu_items,
    seed_bpu_st_mapping,
)
from services.xlsx import make_xlsx


def mapping_database(tmp_path):
    connection = sqlite3.connect(tmp_path / "mapping.sqlite3")
    connection.row_factory = sqlite3.Row
    connection.execute(
        """CREATE TABLE bpu_items(
            article_number INTEGER PRIMARY KEY,
            designation TEXT NOT NULL,
            unite TEXT NOT NULL,
            pu_ht NUMERIC NOT NULL,
            categorie TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1
        )"""
    )
    connection.execute(
        """CREATE TABLE bpu_st_mapping(
            st_article_number INTEGER PRIMARY KEY,
            general_article_number INTEGER NOT NULL UNIQUE,
            mapping_version TEXT NOT NULL DEFAULT 'BPU-ST-1'
        )"""
    )
    connection.executemany(
        "INSERT INTO bpu_items(article_number,designation,unite,pu_ht,categorie) VALUES(?,?,?,?,?)",
        [
            (482, "Article général sans code ST", "Pièce", 1200, "fourniture"),
            (497, "Article converti depuis ST", "Pièce", 2400, "fourniture"),
        ],
    )
    connection.execute("INSERT INTO bpu_st_mapping VALUES(482,497,'BPU-ST-1')")
    return connection


def test_st_search_returns_only_the_mapped_general_item(tmp_path):
    connection = mapping_database(tmp_path)
    try:
        results = search_bpu_items(connection, "482", "ST")
        assert [(row["source_reference_type"], row["article_number"]) for row in results] == [
            ("ST", 497),
        ]
        assert [row["pu_ht"] for row in results] == [2400]
    finally:
        connection.close()


def test_line_resolution_uses_canonical_general_item(tmp_path):
    connection = mapping_database(tmp_path)
    try:
        item, quantity, source_type, source_st = resolve_invoice_line(
            connection,
            {
                "article_number": 497,
                "quantity": 2,
                "source_reference_type": "ST",
                "source_st_number": 482,
            },
            "ST",
        )
        assert item["article_number"] == 497
        assert item["pu_ht"] == 2400
        assert (quantity, source_type, source_st) == (2, "ST", 482)
    finally:
        connection.close()


def test_general_fallback_is_limited_to_articles_without_st_code(tmp_path):
    connection = mapping_database(tmp_path)
    try:
        item, _, source_type, source_st = resolve_invoice_line(
            connection,
            {
                "article_number": 482,
                "quantity": 1,
                "source_reference_type": "GENERAL_FALLBACK",
            },
            "ST",
        )
        assert item["article_number"] == 482
        assert (source_type, source_st) == ("GENERAL_FALLBACK", None)
        with pytest.raises(ValueError, match="possède une référence ST"):
            resolve_invoice_line(
                connection,
                {
                    "article_number": 497,
                    "quantity": 1,
                    "source_reference_type": "GENERAL_FALLBACK",
                },
                "ST",
            )
    finally:
        connection.close()


def test_official_mapping_resource_loads_all_566_st_relations(tmp_path):
    connection = sqlite3.connect(tmp_path / "official.sqlite3")
    try:
        connection.execute("CREATE TABLE bpu_items(article_number INTEGER PRIMARY KEY, is_active INTEGER NOT NULL DEFAULT 1)")
        connection.execute(
            """CREATE TABLE bpu_st_mapping(
                st_article_number INTEGER PRIMARY KEY,
                general_article_number INTEGER NOT NULL UNIQUE,
                mapping_version TEXT NOT NULL
            )"""
        )
        connection.executemany(
            "INSERT INTO bpu_items(article_number) VALUES(?)",
            ((number,) for number in range(1, 611)),
        )
        assert seed_bpu_st_mapping(connection) == 566
        assert connection.execute("SELECT COUNT(*) FROM bpu_st_mapping").fetchone()[0] == 566
        assert connection.execute(
            "SELECT general_article_number FROM bpu_st_mapping WHERE st_article_number=482"
        ).fetchone()[0] == 497
        assert connection.execute(
            "SELECT COUNT(*) FROM bpu_st_mapping WHERE general_article_number=482"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT general_article_number FROM bpu_st_mapping WHERE st_article_number=322"
        ).fetchone()[0] == 322
    finally:
        connection.close()


def test_general_catalog_replacement_preserves_referenced_history_and_archives_st_mapping(tmp_path):
    connection = sqlite3.connect(tmp_path / "replacement.sqlite3")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        connection.executescript("""
            CREATE TABLE bpu_items(
                article_number INTEGER PRIMARY KEY,
                designation TEXT NOT NULL,
                unite TEXT NOT NULL,
                pu_ht NUMERIC NOT NULL,
                categorie TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE bpu_st_versions(
                id INTEGER PRIMARY KEY,
                code TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE TABLE bpu_st_items(
                version_id INTEGER NOT NULL,
                st_article_number INTEGER NOT NULL,
                general_article_number INTEGER,
                review_status TEXT NOT NULL,
                FOREIGN KEY(version_id) REFERENCES bpu_st_versions(id) ON DELETE CASCADE,
                FOREIGN KEY(general_article_number) REFERENCES bpu_items(article_number) ON DELETE RESTRICT
            );
        """)
        connection.execute(
            "INSERT INTO bpu_items VALUES(1,'Ancien article','U',10,'fourniture',1,CURRENT_TIMESTAMP)"
        )
        connection.execute("INSERT INTO bpu_st_versions VALUES(7,'ST-OLD','ACTIVE')")
        connection.execute("INSERT INTO bpu_st_items VALUES(7,101,1,'CONFIRMED')")

        result = replace_bpu_catalog(
            connection,
            [(2, "Nouvel article", "m", 25, "prestation")],
        )

        assert result == {"active": 1, "deactivated": 1, "archived_st_versions": [7]}
        assert [tuple(row) for row in connection.execute(
            "SELECT article_number,is_active FROM bpu_items ORDER BY article_number"
        )] == [(1, 0), (2, 1)]
        assert connection.execute(
            "SELECT status FROM bpu_st_versions WHERE id=7"
        ).fetchone()[0] == "ARCHIVED"
        assert search_bpu_items(connection, "1", "GENERAL") == []
        assert [row["article_number"] for row in search_bpu_items(connection, "2", "GENERAL")] == [2]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def test_bpu_st_parser_skips_category_heading_rows(tmp_path):
    workbook = tmp_path / "bpu-st-with-categories.xlsx"
    workbook.write_bytes(make_xlsx([
        ("BPU_ST", [
            ["code ST", "Désignation", "Unité", "Prix Unitaire"],
            ["  ACQUISITION  ", None, None, None],
            [1, "Étude d'acquisition", "Forfait", 100],
            ["Prestation", None, None, None],
            [2, "Prestation technique", "U", 200],
            ["fourniture", None, None, None],
            [3, "Fourniture câble", "ml", 300],
        ])
    ]))

    rows = parse_bpu_st_file(workbook)

    assert [row["st_article_number"] for row in rows] == [1, 2, 3]
    assert [row["designation"] for row in rows] == [
        "Étude d'acquisition",
        "Prestation technique",
        "Fourniture câble",
    ]


def test_automatic_designation_mapping_uses_full_order_to_resolve_ambiguous_labels():
    general_rows = [
        {"article_number": 496, "designation": "Fourniture câble énergie", "unite": "ml"},
        {"article_number": 497, "designation": "F de la crinoline ø 600", "unite": "ml"},
        {"article_number": 498, "designation": "F de la crinoline ø 700", "unite": "ml"},
        {"article_number": 499, "designation": "Fourniture palier intermédiaire", "unite": "U"},
    ]
    st_rows = [
        {"st_article_number": 481, "designation": "Fourniture câble énergie", "unite": "ml"},
        {"st_article_number": 482, "designation": "F de la crinoline ø 600", "unite": "ml"},
        {"st_article_number": 483, "designation": "F de la crinoline", "unite": "ml"},
        {"st_article_number": 484, "designation": "Fourniture palier intermédiaire", "unite": "U"},
    ]

    mapping = automatic_designation_mapping(general_rows, st_rows)

    assert {
        code: row["general_article_number"] for code, row in mapping.items()
    } == {481: 496, 482: 497, 483: 498, 484: 499}
    assert all(row["confirmed"] for row in mapping.values())


def test_st_version_import_persists_complete_automatic_designation_mapping(tmp_path):
    connection = sqlite3.connect(tmp_path / "automatic-import.sqlite3")
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript("""
            CREATE TABLE bpu_items(
                article_number INTEGER PRIMARY KEY,
                designation TEXT NOT NULL,
                unite TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE bpu_st_versions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL UNIQUE,
                source_filename TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                total_rows INTEGER NOT NULL,
                mapped_rows INTEGER NOT NULL,
                imported_by TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE bpu_st_items(
                version_id INTEGER NOT NULL,
                st_article_number INTEGER NOT NULL,
                designation TEXT NOT NULL,
                unite TEXT NOT NULL,
                source_price NUMERIC,
                general_article_number INTEGER,
                confidence NUMERIC NOT NULL,
                review_status TEXT NOT NULL,
                PRIMARY KEY(version_id, st_article_number)
            );
        """)
        connection.executemany(
            "INSERT INTO bpu_items VALUES(?,?,?,1)",
            [
                (496, "Fourniture câble énergie", "ml"),
                (497, "F de la crinoline ø 600", "ml"),
                (498, "F de la crinoline ø 700", "ml"),
                (499, "Fourniture palier intermédiaire", "U"),
            ],
        )
        workbook = tmp_path / "bpu-st-automatic.xlsx"
        workbook.write_bytes(make_xlsx([
            ("BPU_ST", [
                ["code ST", "Désignation", "Unité", "Prix Unitaire"],
                [481, "Fourniture câble énergie", "ml", 10],
                [482, "F de la crinoline ø 600", "ml", 20],
                [483, "F de la crinoline", "ml", 30],
                [484, "Fourniture palier intermédiaire", "U", 40],
            ])
        ]))

        version_id, total, mapped = import_bpu_st_version(
            connection, workbook, "ST-AUTO-1", "qa"
        )

        assert (total, mapped) == (4, 4)
        assert [tuple(row) for row in connection.execute(
            """SELECT st_article_number,general_article_number,review_status
               FROM bpu_st_items WHERE version_id=? ORDER BY st_article_number""",
            (version_id,),
        )] == [
            (481, 496, "CONFIRMED"),
            (482, 497, "CONFIRMED"),
            (483, 498, "CONFIRMED"),
            (484, 499, "CONFIRMED"),
        ]
    finally:
        connection.close()
