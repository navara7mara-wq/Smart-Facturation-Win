# PROJECT PROFILE: Smart Facturation (Windows Edition)

## 1. Project Identity & Tech Stack
* **Project Name**: Smart Facturation
* **Target Platform**: Windows Desktop
* **Tech Stack**:
  * **Backend**: Python (Core Logic, Business Engine, File Exports)
  * **Database**: SQLite + Migration Engine
  * **Frontend / UI**: Web-based Desktop UI (HTML5, JS, CSS, Lucide Icons)
  * **Testing**: Pytest (Unit, Integration, E2E, Financial Rules Verification)
  * **Export Engine**: OpenPyXL/Excel Templates (`Facture_Construction.xlsx`) + PDF Engine

## 2. Directory Structure
├── static/                  # Frontend assets (JS, CSS, Icons)
│   ├── app-ui.js            # Main UI interactions
│   ├── table-facturation-new.js # Dynamic invoice table
│   └── style.css / sapta-new.css
├── templates/               # Source templates
│   ├── table_facturation_new.html
│   └── Facture_Construction.xlsx # Construction invoice export template
├── tests/                   # Complete Test Suite
│   ├── test_financial_rules_rc2.py # Financial engine rules
│   ├── test_bpu_mapping.py  # BPU matching with BC/DQ/DE
│   ├── test_invoice_workflow.py # Invoice life-cycle
│   └── test_migrations.py   # Database migration tests
└── database/                # SQLite database and migration scripts

## 3. Financial Engine Core Rules
Do not alter the following mathematical formulas without explicit instructions and verified test cases:
* **Pre-Tax Amount (HT)**: $\text{Montant HT} = \sum (\text{Quantité} \times \text{Prix Unitaire})$
* **Retenue de Garantie (RG)**: Calculated as a contractually defined percentage of total HT.
* **TVA (Value Added Tax)**: $\text{TVA} = (\text{Montant HT} - \text{RG}) \times \text{Taux TVA}$ (or per active public contract rules).
* **Total Amount (TTC)**: $\text{Montant TTC} = \text{Montant HT} + \text{TVA} - \text{RG}$
* **Financial Integrity Verification**: Always validate output against `test_financial_rules_rc2.py`.

## 4. Business Logic & Contracts Rules
* **BPU (Bordereau des Prix Unitaires)**: Primary pricing benchmark. Invoices cannot be issued without valid BPU mapping.
* **BC / DQ / DE**:
  * **BC (Bon de Commande)**: Financial and quantity upper ceiling.
  * **DQ (Devis Quantitatif)**: Actual executed quantities.
  * **DE (Devis Estimatif)**: Initial financial estimates.
* **Mandatory Mapping**: Every invoice line item must hold precise mapping codes for BPU and BC.

## 5. Database & Migration Rules
* Direct manual modifications to the Database Schema are strictly prohibited. Always use dedicated Migration Scripts.
* Every table update requires backwards compatibility verification (`test_migrations.py`).
* Automatic Backup/Restore mechanisms must run prior to any schema migration.