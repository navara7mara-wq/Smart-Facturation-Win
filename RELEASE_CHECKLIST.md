# PhoEniX BPU Release Checklist

## Verification minimale

- `python -m pytest -q` passe sans erreur.
- `python -m py_compile app.py db.py services/*.py scripts/*.py` passe sans erreur.
- `powershell -ExecutionPolicy Bypass -File scripts/run_visual_tests.ps1 -Strict` passe avant livraison UI.
- L'application demarre via `run.cmd`.
- Le parcours de premiere initialisation exige la definition d'un mot de passe administrateur; aucun identifiant universel reutilisable n'est livre.
- Le premier lancement passe par `/setup`.
- Les formulaires POST contiennent un jeton CSRF valide.
- Un test de connexion/deconnexion est effectue.
- Une sauvegarde et une restauration de test sont effectuees.
- Le test E2E setup/session/CSRF/export Excel/backup/restore passe.
- Le script Inno Setup est disponible pour generer un installateur Windows.
- La page `/status` retourne Database OK.
- Les exports PDF et Excel sont testes sur au moins une facture normale et une facture NDC.

## Installation client

1. Verifier le SHA-256 publie dans `dist\SHA256SUMS.txt`.
2. Lancer `dist\installer\PhoEniX_BPU_Setup_VERSION.exe`.
3. Aucun Python, Node.js ou acces Internet n'est requis chez le client.
4. Lancer PhoEniX BPU depuis le Bureau ou le menu Demarrer.
5. Les donnees sont conservees dans `%LOCALAPPDATA%\SAPTA\PhoEniX BPU` lors des mises a jour et de la desinstallation.

## Points non negociables avant commercialisation large

- Ajouter authentification et roles si plusieurs utilisateurs accedent a l'application.
- Garder la protection CSRF par jeton actif sur les postes clients.
- Garder la sauvegarde/restauration guidee de `data/pos_ai.sqlite3`.
- Valider la conformite legale des factures dans le pays cible.
- Signer et versionner les livraisons client.

## Hors perimetre de cette version locale

- Signature Authenticode tant qu'un certificat commercial n'a pas ete fourni.
- Migration Flask/FastAPI complete.
- Multi-company complet.
- Templates HTML exhaustifs pour toutes les vues.
