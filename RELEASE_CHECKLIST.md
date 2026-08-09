# PhoEniX BPU Release Checklist

## Verification minimale

- `python -m pytest -q` passe sans erreur.
- `python -m py_compile app.py db.py services/*.py scripts/*.py` passe sans erreur.
- `powershell -ExecutionPolicy Bypass -File scripts/run_visual_tests.ps1 -Strict` passe avant livraison UI.
- L'application demarre via `run.cmd`.
- Le compte initial `admin/admin123` est remplace avant livraison.
- Le premier lancement passe par `/setup`.
- Les formulaires POST contiennent un jeton CSRF valide.
- Un test de connexion/deconnexion est effectue.
- Une sauvegarde et une restauration de test sont effectuees.
- Le test E2E setup/session/CSRF/export Excel/backup/restore passe.
- La page `/status` retourne Database OK.
- Les exports PDF et Excel sont testes sur au moins une facture normale et une facture NDC.

## Installation client

1. Installer Python 3.12+.
2. Installer Node.js LTS si les PDF ou les tests visuels sont necessaires.
3. Double-cliquer `setup.cmd`.
4. Double-cliquer `run.cmd`.
5. Ouvrir `http://127.0.0.1:8000`.

## Points non negociables avant commercialisation large

- Ajouter authentification et roles si plusieurs utilisateurs accedent a l'application.
- Garder la protection CSRF par jeton actif sur les postes clients.
- Garder la sauvegarde/restauration guidee de `data/pos_ai.sqlite3`.
- Valider la conformite legale des factures dans le pays cible.
- Signer et versionner les livraisons client.

## Hors perimetre de cette version locale

- Installer MSI/EXE signe.
- Migration Flask/FastAPI complete.
- Multi-company complet.
- Templates HTML exhaustifs pour toutes les vues.
