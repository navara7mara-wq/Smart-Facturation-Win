# PhoEniX BPU 2.5.2 RC-2

Application Windows locale de facturation et de suivi des factures Mobilis.

## Installation client

Utiliser l'installateur hors ligne:

```text
dist\installer\PhoEniX_BPU_Setup_2.5.2.exe
```

Le poste client n'a pas besoin de Python, Node.js ou d'une connexion Internet. Le programme,
le moteur Excel et le runtime PDF sont inclus. L'installateur cree les raccourcis du menu
Demarrer et, sur demande, du Bureau.

Les donnees client sont separees des fichiers du programme:

```text
%LOCALAPPDATA%\SAPTA\PhoEniX BPU
```

Ce dossier contient la base SQLite, les pieces jointes, les exports, les sauvegardes et les
logs. Il est conserve pendant une mise a jour ou une desinstallation.

## Premier lancement

L'application ouvre une fenetre Windows dediee et utilise un port local aleatoire sur
`127.0.0.1`. Une seule instance peut etre ouverte a la fois.

Compte initial:

```text
Utilisateur: admin
Au premier lancement, l'application exige la creation du mot de passe administrateur. Aucun mot de passe administrateur universel n'est fourni.
```

Le premier lancement impose le remplacement de ce mot de passe. Les formulaires POST sont
proteges par un jeton CSRF lie a la session.

## Fonctionnalites principales

- Cycle facture calcule: Brouillon, Prete DTC, Deposee DTC, En attente paiement, Payee.
- Suivi separe des dates DTC, Mobilis et OV avec validation chronologique.
- Verrouillage financier a partir du depot DTC et audit des operations sensibles.
- Types BC ACQ, CONST, CONST/ACQ, NDC et MGC.
- Factures NDC multi-sites limitees a l'article technique 6.
- Tableau de bord avec montants, encaissements, retards et filtres.
- Exports Excel et PDF, sauvegarde, restauration controlee et gestion des utilisateurs.
- Mode DEMO et activation par fichier licence signe.

## Developpement

Installer les dependances applicatives:

```powershell
python -m pip install -r requirements.txt
npm install
```

Lancer le mode web:

```powershell
python app.py
```

Lancer le mode desktop depuis les sources:

```powershell
python desktop.py
```

## Tests

```powershell
python -m pytest -q -W error
powershell -ExecutionPolicy Bypass -File scripts/run_visual_tests.ps1 -Strict
```

La regression visuelle couvre 16 vues en resolutions desktop et compactes. Le rapport est
genere dans `output\visual-regression\report.html`.

Tester une installation, une mise a jour et une desinstallation isolees:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test_installer.ps1 -Version 2.5.2
```

## Build commercial

Installer les outils de build:

```powershell
python -m pip install -r requirements-build.txt
```

Generer l'application, l'archive Portable, l'Installer et le manifeste SHA-256:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_release.ps1 -Version 2.5.2
```

Le certificat Authenticode commercial n'est pas stocke dans le projet. Une fois le certificat
installe dans le magasin Windows, signer et verifier les binaires avec:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/sign_release.ps1 -CertificateThumbprint THUMBPRINT -Version 2.5.2
```

## Licence produit

Sans activation, l'application fonctionne en mode DEMO. Depuis la page Licence, le client
telecharge une demande d'activation liee a son appareil. L'editeur importe cette demande dans
`PhoEniX License Studio`, definit l'expiration et les limites, puis remet uniquement le fichier
`.license.json` signe au client.

Construire l'outil prive de l'editeur:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_license_studio.ps1
```

L'executable est cree dans `dist/publisher`. Il ne contient jamais la cle privee. Au premier
lancement, importer la cle Ed25519 existante et creer un mot de passe maitre d'au moins 12
caracteres. Le coffre chiffre est conserve dans le profil Windows de l'editeur.

```text
dist\publisher\PhoEniX License Studio.exe
```

La commande de secours exige egalement une demande d'activation:

```powershell
python scripts/issue_license.py --request activation-request.json --customer "Client" --edition standard --expires-at 2027-12-31 --max-users 5 --max-invoices 10000 --output client.license.json
```

Pour une licence perpetuelle avec un nombre de factures illimite:

```powershell
python scripts/issue_license.py --request activation-request.json --customer "Client" --edition enterprise --perpetual --max-users 25 --unlimited-invoices --output client.license.json
```

Ne jamais distribuer `config/license_private_key.pem` ni le coffre chiffre. Sauvegarder le coffre
et son mot de passe dans deux emplacements hors ligne separes.
