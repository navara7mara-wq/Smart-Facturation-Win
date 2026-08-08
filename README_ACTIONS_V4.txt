SAPTA Facturation — ACTIONS Document Picker V4

Applied only to Table Facturation:
- MAJ stays as a separate column and contains updated_by + updated_at.
- ACTIONS now shows only: Ouvrir (eye), Modifier (pencil), Supprimer (trash).
- Clicking Ouvrir shows a compact menu: Facture / DQ / DE.
- DQ and DE are shown disabled if the invoice has no lines.
- Added tooltips: Ouvrir / Modifier / Supprimer.
- Fixed table header/body alignment: 13 headers and 13 cells per invoice row.
- Removed FACT/DQ/DE chips from the row itself.
- Sidebar and SAPTA Design Standard were not changed.

Replace:
app.py
static/sapta-new.css
static/table-facturation-new.js
Then restart python app.py and press Ctrl+F5.
