# References visuelles

Ce dossier contient les images de reference validees pour les huit interfaces du
programme. Elles constituent le contrat visuel du projet.

## Regles

- Une reference ne doit pas etre remplacee pour faire disparaitre un test en echec.
- Toute nouvelle reference doit etre approuvee apres comparaison avec la demande.
- La resolution de chaque reference est conservee dans `visual.config.json`.
- Le rapport compare la reference, la capture actuelle et les ecarts en rouge.
- Le mode `-strict` bloque la validation si une interface est sous le seuil configure.

## Execution

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_visual_tests.ps1
```

Le controle bloquant utilise:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_visual_tests.ps1 -Strict
```
