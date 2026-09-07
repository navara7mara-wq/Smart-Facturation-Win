from datetime import date, timedelta
from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from db import db
from services.auth import ensure_default_admin, hash_password


VISUAL_PASSWORD = "Visual123"


def main():
    ensure_default_admin()
    today = date.today()
    invoice_date = today - timedelta(days=12)
    with db() as con:
        con.execute(
            """
            UPDATE users
            SET password_hash=?, must_change_password=0, failed_attempts=0,
                locked_until=NULL, last_login_at=CURRENT_TIMESTAMP
            WHERE username='admin'
            """,
            (hash_password(VISUAL_PASSWORD),),
        )
        con.execute(
            """
            UPDATE company_settings
            SET nom='SAPTA', rgc='16B0000000', nif='000016000000000',
                art='16000000000', adresse='Alger, Algerie',
                numero_compte='001 00000 0000000000 00'
            WHERE id=1
            """
        )
        con.execute(
            "UPDATE contract_settings SET reference_contrat='CTR-MOBILIS-2026' WHERE id=1"
        )
        con.execute(
            """
            UPDATE mobilis_client_settings
            SET doit='ATM Mobilis', nif='000016099166279', nis='000016099166279',
                is_configured=1
            WHERE id=1
            """
        )
        con.execute(
            """
            INSERT OR REPLACE INTO app_settings(key, value, updated_by)
            VALUES('license_demo_started_at', ?, 'visual-test')
            """,
            (today.isoformat(),),
        )
        directions = []
        for name, address in (
            ("Direction regionale Alger", "Alger Centre"),
            ("Direction regionale Oran", "Bir El Djir, Oran"),
            ("Direction regionale Chlef", "Centre-ville, Chlef"),
        ):
            directions.append(
                con.execute(
                    """
                    INSERT INTO mobilis_directions(
                        doit_nom, direction_regionale, adresse, rgc, nif,
                        created_by, updated_by
                    ) VALUES('ATM Mobilis', ?, ?, '16B0991662', '000716099166279',
                             'visual-test', 'visual-test')
                    """,
                    (name, address),
                ).lastrowid
            )
        con.executemany(
            """
            INSERT INTO bpu_items(article_number, designation, unite, pu_ht, categorie)
            VALUES(?, ?, ?, ?, ?)
            """,
            (
                (1, "Routeur d'acces", "U", 150000, "acquisition"),
                (2, "Cable a fibre optique", "m", 850, "fourniture"),
                (3, "Pose et raccordement", "U", 45000, "prestation"),
                (6, "Forfait NDC par site", "Site", 50000, "prestation"),
            ),
        )
        purchase_orders = {}
        for number, direction_id, type_bc, amount in (
            ("BC-ACQ-2026", directions[0], "ACQUISITION", 535500),
            ("BC-MGC-2026", directions[0], "MGC", 2261000),
            ("BC-MIX-2026", directions[1], "CONST_ACQUIS", 1785000),
            ("BC-NDC-2026", directions[2], "NDC", 119000),
        ):
            purchase_orders[type_bc] = con.execute(
                """
                INSERT INTO purchase_orders(
                    numero_bc, date_bc, mobilis_direction_id, type_bc, objet,
                    montant_ttc, created_by, updated_by
                ) VALUES(?, ?, ?, ?, 'Projet de demonstration visuelle', ?,
                         'visual-test', 'visual-test')
                """,
                (number, (today - timedelta(days=20)).isoformat(), direction_id, type_bc, amount),
            ).lastrowid
        sites = {}
        for code, name, po_type, region in (
            ("ALG-1001", "Site Alger Centre", "ACQUISITION", "ALGER"),
            ("ALG-2001", "Site Maintenance MGC", "MGC", "ALGER"),
            ("ORN-3001", "Site Mixte Oran", "CONST_ACQUIS", "ORAN"),
            ("CHL-4001", "Site NDC Chlef 1", "NDC", "CHLEF"),
            ("CHL-4002", "Site NDC Chlef 2", "NDC", "CHLEF"),
        ):
            sites[code] = con.execute(
                """
                INSERT INTO sites(
                    purchase_order_id, code_site, nom_site, region,
                    typologie_site, bet, created_by, updated_by
                ) VALUES(?, ?, ?, ?, 'A12', 'BET Demo', 'visual-test', 'visual-test')
                """,
                (purchase_orders[po_type], code, name, region),
            ).lastrowid

        def add_invoice(number, invoice_type, po_type, site_code, total_ht, status):
            retention = round(total_ht * 0.05, 2)
            after_retention = round(total_ht - retention, 2)
            tax = round(after_retention * 0.19, 2)
            total_ttc = round(after_retention + tax, 2)
            invoice_id = con.execute(
                """
                INSERT INTO invoices(
                    invoice_number, invoice_type, purchase_order_id, site_id,
                    invoice_date, total_ht, retenue_garantie,
                    montant_ht_apres_rg, tva, total_ttc, remarque,
                    created_by, updated_by
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Donnees de demonstration',
                         'visual-test', 'visual-test')
                """,
                (
                    number, invoice_type, purchase_orders[po_type], sites.get(site_code),
                    invoice_date.isoformat(), total_ht, retention, after_retention, tax, total_ttc,
                ),
            ).lastrowid
            if invoice_type == "NDC":
                con.executemany(
                    "INSERT INTO invoice_sites(invoice_id, site_id) VALUES(?, ?)",
                    ((invoice_id, sites["CHL-4001"]), (invoice_id, sites["CHL-4002"])),
                )
                article, designation, unit, price, category, quantity = (
                    6, "Forfait NDC par site", "Site", 50000, "ndc", 2
                )
            else:
                article, designation, unit, price, category, quantity = (
                    3, "Pose et raccordement", "U", total_ht, "prestation", 1
                )
            con.execute(
                """
                INSERT INTO invoice_lines(
                    invoice_id, article_number, designation_snapshot,
                    unite_snapshot, pu_ht_snapshot, categorie_snapshot,
                    quantite, montant_ht
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (invoice_id, article, designation, unit, price, category, quantity, total_ht),
            )
            dates = {
                "DEPOSITED_DTC": ((today - timedelta(days=8)).isoformat(), None, None),
                "AWAITING_PAYMENT": (
                    (today - timedelta(days=9)).isoformat(),
                    (today - timedelta(days=7)).isoformat(), None,
                ),
                "PAID": (
                    (today - timedelta(days=10)).isoformat(),
                    (today - timedelta(days=8)).isoformat(),
                    (today - timedelta(days=2)).isoformat(),
                ),
            }
            if status in dates:
                con.execute(
                    """
                    UPDATE invoice_tracking
                    SET date_depot_dtc=?, date_depot_mobilis=?, date_ov=?,
                        updated_by='visual-test'
                    WHERE invoice_id=?
                    """,
                    (*dates[status], invoice_id),
                )
            return invoice_id

        add_invoice("FAC-READY-2026", "NDC", "NDC", None, 100000, "READY_DTC")
        add_invoice("FAC-DTC-2026", "CONSTRUCTION", "MGC", "ALG-2001", 2000000, "DEPOSITED_DTC")
        add_invoice("FAC-WAIT-2026", "CONST_ACQUIS", "CONST_ACQUIS", "ORN-3001", 1500000, "AWAITING_PAYMENT")
        add_invoice("FAC-PAID-2026", "ACQUISITION", "ACQUISITION", "ALG-1001", 450000, "PAID")
    print("Visual database seeded")


if __name__ == "__main__":
    main()
