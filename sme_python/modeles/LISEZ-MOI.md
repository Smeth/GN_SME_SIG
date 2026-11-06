# Dossier `modeles/` — gabarits et fichiers métier

Placez ici **tous les gabarits** utilisés par SME Gendarmerie (Excel, Word).
Les configurations JSON et les schémas SQL restent dans `data/`.

## Fichiers attendus

| Fichier | Usage |
|---------|--------|
| `resultat.xlsx` | Liste des présents (impression / export) |
| `numerique.xlsx` | Situation numérique (aperçu + export Excel) |
| `DETACHES.xlsx` | Liste officielle des détachés |
| `limite_age.docx` | Attestation limite d'âge |
| `soit_transmis.docx` | Attestation soit transmis |
| `smegend.mdb` | *(optionnel)* Archive Access pour import |
| `ordre_general.txt` | *(optionnel)* Texte ordre général |

## Migration depuis l'ancienne arborescence

Si vos fichiers sont encore à la racine du dépôt **Impression/** ou dans
`data/templates/`, l'application les trouve encore — copiez-les ici pour
un déploiement propre :

```text
sme_python/
  modeles/          ← gabarits (ce dossier)
  data/             ← config JSON, schémas SQL, journaux
```

Version portable : le dossier `modeles/` doit se trouver **à côté de**
`SME_Gendarmerie.exe` (copié automatiquement par `scripts/build_portable.cmd`).

Chemins personnalisés : **Fichier → Chemins des fichiers de données…** ou
variables `SME_TEMPLATE_PATH`, `SME_NUMERIQUE_TEMPLATE_PATH`, `SME_DETACHES_PATH`.
