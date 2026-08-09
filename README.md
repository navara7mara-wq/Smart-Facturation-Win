# POS AI

Programme de facturation de projets pour Mobilis.

## Phase 1

Cette base contient le schema SQLite approuve pour:

- Parametres de l'entreprise
- Directions regionales Mobilis
- Contrat unique
- BPU unique
- Bons de commande
- Sites
- Factures
- Lignes de facture
- Sites des factures NDC

## Initialiser la base

Installation Windows recommandee:

```powershell
.\setup.cmd
```

Installer d'abord les dependances Python:

```powershell
python -m pip install -r requirements.txt
```

Installer les dependances Node utilisees pour les PDF et les controles visuels:

```powershell
npm install
```

```powershell
python scripts/init_db.py
```

La base est creee dans:

```text
data/pos_ai.sqlite3
```

## Lancer l'application locale

Lancement Windows recommande:

```powershell
.\run.cmd
```

```powershell
python app.py
```

Puis ouvrir:

```text
http://127.0.0.1:8000
```

Pour utiliser un autre port:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_app.ps1 -Port 8001
```

## Controle de conformite visuelle

## Tests automatiques

```powershell
python -m pytest
```

ou:

```powershell
npm test
```

Les huit interfaces principales disposent d'une image de reference fixe. Le test ouvre
chaque page dans Chrome avec la resolution de sa reference, prend une capture et produit
un rapport avec les differences signalees en rouge.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_visual_tests.ps1
```

Pour bloquer automatiquement une livraison lorsqu'une page est sous le seuil defini:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_visual_tests.ps1 -Strict
```

Le rapport est cree dans:

```text
output/visual-regression/report.html
```

Pour une utilisation sans commande, double-cliquer sur `controle-visuel.cmd`. Le test
sera execute puis le rapport s'ouvrira automatiquement dans le navigateur.

Les routes, resolutions et images de reference sont declarees dans
`visual.config.json`. Toute modification volontaire du design doit etre validee avant
de remplacer une image dans `tests/visual/baselines`.

## Regles implementees

- `bpu_items.article_number` est unique.
- `sites.code_site` est unique.
- Les factures normales exigent un seul site.
- Les factures `NDC` n'ont pas de `site_id` direct et utilisent `invoice_sites`.
- Un site ne peut entrer que dans une seule facture `NDC`.
- Une facture `NDC` accepte uniquement l'article `6`.
- Les informations BPU sont sauvegardees dans `invoice_lines` en snapshot.

## Nouvelle interface Table Facturation

Une nouvelle interface independante a ete ajoutee sans supprimer l'ancienne page:

```text
http://127.0.0.1:8000/table-facturation-new
```

Fichiers principaux:

- `templates/table_facturation_new.html`
- `static/sapta-new.css`
- `static/table-facturation-new.js`

La page comprend les filtres SQLite, la pagination, la modification instantanee des remarques et de l'etat de depot, les actions facture et l'export Excel filtre. L'ancienne route `/table-facturation` reste disponible comme sauvegarde.
