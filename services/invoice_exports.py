from db import db
from services.typologies import typology_export_label
from services.xlsx import exact_number, make_xlsx, styled
from services.money import format_rate, money_storage


def exact_money(value):
    return exact_number(money_storage(value))


def invoice_export_data(invoice_id):
    with db() as con:
        invoice = con.execute("""
            SELECT i.*, po.numero_bc, po.objet,
                   c.raison_sociale AS doit_nom,
                   md.name AS direction_regionale, md.address AS client_adresse,
                   c.rgc AS client_rgc, c.nif AS client_nif,
                   c.logo_path AS client_logo_path,
                   c.reference_contrat,
                   s.code_site, s.nom_site, s.typologie_site
            FROM invoices i
            JOIN purchase_orders po ON po.id = i.purchase_order_id
            JOIN client_directions md ON md.id = po.client_direction_id
            JOIN clients c ON c.id = md.client_id
            LEFT JOIN sites s ON s.id = i.site_id
            WHERE i.id=?
        """, (invoice_id,)).fetchone()
        if not invoice:
            raise ValueError("Facture introuvable.")
        company = con.execute("SELECT * FROM company_settings WHERE id=1").fetchone()
        contract = {"reference_contrat": invoice["reference_contrat"] or ""}
        lines = con.execute("SELECT * FROM invoice_lines WHERE invoice_id=? ORDER BY id", (invoice_id,)).fetchall()
        ndc_sites = con.execute("""
            SELECT s.code_site, s.nom_site
            FROM invoice_sites invs
            JOIN sites s ON s.id = invs.site_id
            WHERE invs.invoice_id=?
            ORDER BY s.code_site
        """, (invoice_id,)).fetchall()
    return invoice, company, contract, lines, ndc_sites


def invoice_xlsx(invoice_id):
    invoice, company, contract, lines, ndc_sites = invoice_export_data(invoice_id)
    typology_label = typology_export_label(invoice)
    site_label = invoice["code_site"] or ", ".join(site["code_site"] for site in ndc_sites)
    site_name = invoice["nom_site"] or f"{len(ndc_sites)} sites NDC"
    grouped = {
        "ACQUISITION": [line for line in lines if line["categorie_snapshot"] == "acquisition"],
        "FOURNITURES": [line for line in lines if line["categorie_snapshot"] == "fourniture"],
        "PRESTATION": [line for line in lines if line["categorie_snapshot"] == "prestation"],
        "NOTE DE CALCUL": [line for line in lines if line["categorie_snapshot"] == "ndc"],
    }

    header = [
        [styled(company["nom"] or "ENTREPRISE", 2), "", "", "", styled("mobilis", 2), ""],
        [],
        [styled("RGC:", 1), company["rgc"], "", styled("DOIT:", 1), invoice["doit_nom"]],
        [styled("NIF:", 1), company["nif"], "", styled("Direction:", 1), invoice["direction_regionale"]],
        [styled("ART:", 1), company["art"], "", styled("Adresse:", 1), invoice["client_adresse"]],
        [styled("ADRESSE:", 1), company["adresse"], "", styled("RGC N:", 1), invoice["client_rgc"]],
        [styled("N COMPTE:", 1), company["numero_compte"], "", styled("NIF N:", 1), invoice["client_nif"]],
        [],
        ["", styled(f"FACTURE N : {invoice['invoice_number']}", 2), "", "", "", ""],
        [styled("Reference Contrat:", 1), contract["reference_contrat"]],
        [styled("Code de site:", 1), site_label],
        [styled("Nom de site:", 1), site_name],
        [styled("Typologie de site:", 1), typology_label],
        [styled("Bon de commande:", 1), invoice["numero_bc"]],
        [styled("Objet:", 1), invoice["objet"]],
        [],
        [styled("N", 3), styled("Designation", 3), styled("Unite", 3), styled("Quantites", 3), styled("PU/HT", 3), styled("Montant/HT", 3)],
    ]
    line_rows = section_rows(grouped, with_prices=True)
    totals = [
        [],
        ["", "", "", "", styled("TOTAL EN HT", 6), styled(exact_money(invoice["total_ht"]), 5)],
        ["", "", "", "", styled(f"RETENUE DE GARANTIE {format_rate(invoice['rg_rate'])}%", 6), styled(exact_money(invoice["retenue_garantie"]), 5)],
        ["", "", "", "", styled("MONTANT HT APRES RETENUE", 6), styled(exact_money(invoice["montant_ht_apres_rg"]), 5)],
        ["", "", "", "", styled(f"TVA {format_rate(invoice['tva_rate'])} %", 6), styled(exact_money(invoice["tva"]), 5)],
        ["", "", "", "", styled("TOTAL EN TTC", 6), styled(exact_money(invoice["total_ttc"]), 5)],
        [],
        [styled("Arrete la presente Facture a la somme de:", 1), invoice["montant_en_lettres"]],
        [],
        ["", "", "", "", styled(f"L'ENTREPRISE/{company['nom']}", 7), ""],
    ]

    quantitative = [
        [styled("DEVIS QUANTITATIF", 2)],
        [],
        [styled("Code de site:", 1), site_label],
        [styled("Nom de site:", 1), site_name],
        [styled("Typologie de site:", 1), typology_label],
        [],
        [styled("N", 3), styled("Designation", 3), styled("Unite", 3), styled("Quantites", 3)],
    ] + section_rows(grouped, with_prices=False) + [[], [styled("VALIDATION MOBILIS", 1)]]

    estimative = [
        [styled("DEVIS ESTIMATIF", 2)],
        [],
        [styled("Code de site:", 1), site_label],
        [styled("Nom de site:", 1), site_name],
        [styled("Typologie de site:", 1), typology_label],
        [],
        [styled("N", 3), styled("Designation", 3), styled("Unite", 3), styled("Quantites", 3), styled("PU/HT", 3), styled("Montant/HT", 3)],
    ] + section_rows(grouped, with_prices=True) + [
        [],
        ["", "", "", "", styled("TOTAL GENERAL", 6), styled(exact_money(invoice["total_ht"]), 5)],
        [styled("Arrete la presente Facture a la somme de:", 1), invoice["montant_en_lettres"]],
        [],
        ["", "", "", "", styled(f"L'ENTREPRISE/{company['nom']}", 7), ""],
    ]
    return make_xlsx([
        ("Facture", header + line_rows + totals),
        ("Devis Quantitatif", quantitative),
        ("Devis Estimatif", estimative),
    ]), invoice["invoice_number"]


def section_rows(grouped, with_prices):
    rows = []
    for title, items in grouped.items():
        if not items:
            continue
        if with_prices:
            rows.append([styled(title, 4), styled("", 4), styled("", 4), styled("", 4), styled("", 4), styled("", 4)])
        else:
            rows.append([styled(title, 4), styled("", 4), styled("", 4), styled("", 4)])
        for line in items:
            if with_prices:
                rows.append([
                    styled(line["article_number"], 5),
                    styled(line["designation_snapshot"], 5),
                    styled(line["unite_snapshot"], 5),
                    styled(line["quantite"], 5),
                    styled(exact_money(line["pu_ht_snapshot"]), 5),
                    styled(exact_money(line["montant_ht"]), 5),
                ])
            else:
                rows.append([
                    styled(line["article_number"], 5),
                    styled(line["designation_snapshot"], 5),
                    styled(line["unite_snapshot"], 5),
                    styled(line["quantite"], 5),
                ])
    return rows
