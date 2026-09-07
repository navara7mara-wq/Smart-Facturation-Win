import re
import unicodedata
import zipfile
import xml.etree.ElementTree as ET


def normalize_header(value):
    text = str(value or "").strip().lower()
    text = "".join(
        char for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    )
    return re.sub(r"\s+", " ", text)


def import_bpu(path):
    worksheet = read_xlsx_sheet(path, "BPU")
    current_category = None
    leading_rows = []
    rows = []
    columns = {"number": 0, "designation": 1, "unite": 2, "price": 3}
    for row in worksheet:
        normalized = [normalize_header(value) for value in row]
        if any("designation" in value or "d e s i g n a t i o n" in value for value in normalized):
            for index, value in enumerate(normalized):
                if "item" in value or value.startswith("n"):
                    columns["number"] = index
                elif "designation" in value or "d e s i g n a t i o n" in value:
                    columns["designation"] = index
                elif "unite" in value:
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
            if leading_rows:
                rows.extend((*pending, current_category) for pending in leading_rows)
                leading_rows.clear()
            continue
        try:
            article_number = int(float(number))
            price = float(pu_ht)
        except (TypeError, ValueError):
            continue
        if designation and unite:
            parsed = (article_number, str(designation).strip(), str(unite).strip(), price)
            if current_category:
                rows.append((*parsed, current_category))
            else:
                leading_rows.append(parsed)
    if leading_rows:
        raise ValueError(
            "Catégorie BPU introuvable pour les premiers articles. "
            "Ajoutez une ligne Acquisition, Prestation ou Fourniture."
        )
    if not rows:
        raise ValueError("Aucun article BPU trouve dans le fichier Excel.")
    return rows


def replace_bpu_catalog(connection, rows):
    """Replace the active general catalogue without deleting referenced history.

    BPU ST versions keep foreign keys to general articles.  Articles omitted from
    a new general catalogue therefore become inactive instead of being deleted.
    Any active ST version that references one of those articles is archived so a
    partially invalid mapping cannot be used for a new invoice.
    """
    rows = list(rows)
    if not rows:
        raise ValueError("Aucun article BPU à importer.")
    article_numbers = [int(row[0]) for row in rows]
    if len(article_numbers) != len(set(article_numbers)):
        raise ValueError("Le fichier BPU contient des numéros d'article dupliqués.")

    connection.executemany("""
        INSERT INTO bpu_items(article_number,designation,unite,pu_ht,categorie,is_active)
        VALUES(?,?,?,?,?,1)
        ON CONFLICT(article_number) DO UPDATE SET
            designation=excluded.designation,
            unite=excluded.unite,
            pu_ht=excluded.pu_ht,
            categorie=excluded.categorie,
            is_active=1,
            updated_at=CURRENT_TIMESTAMP
    """, rows)
    placeholders = ",".join("?" for _ in article_numbers)
    deactivated = connection.execute(
        f"UPDATE bpu_items SET is_active=0 WHERE is_active=1 AND article_number NOT IN ({placeholders})",
        article_numbers,
    ).rowcount

    archived_versions = []
    has_versioned_mapping = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bpu_st_versions'"
    ).fetchone()
    if has_versioned_mapping:
        archived_versions = [
            int(row[0]) for row in connection.execute("""
                SELECT DISTINCT version.id
                FROM bpu_st_versions version
                JOIN bpu_st_items item ON item.version_id=version.id
                JOIN bpu_items general ON general.article_number=item.general_article_number
                WHERE version.status='ACTIVE'
                  AND item.review_status='CONFIRMED'
                  AND general.is_active=0
            """).fetchall()
        ]
        if archived_versions:
            version_placeholders = ",".join("?" for _ in archived_versions)
            connection.execute(
                f"UPDATE bpu_st_versions SET status='ARCHIVED' WHERE id IN ({version_placeholders})",
                archived_versions,
            )
    return {
        "active": len(article_numbers),
        "deactivated": int(deactivated or 0),
        "archived_st_versions": archived_versions,
    }


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
