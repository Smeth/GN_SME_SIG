# Impression — S.G.I DPF

**Système de Gestion de l'Information** — Direction du Personnel et de la Formation (Gendarmerie nationale).

Application **Python / MySQL** (PySide6) : effectifs, cycle mensuel SME, dossiers agents, exports.

## Structure

```text
Impression/
├── README.md           ← ce fichier
└── sme_python/         ← application (PySide6 + MySQL)
    ├── modeles/        ← gabarits Excel/Word et .mdb d’import Access
    ├── data/           ← configuration JSON, schémas SQL, journaux
    └── src/
```

## Où placer les fichiers ?

| Type | Emplacement |
|------|-------------|
| Maquettes (`resultat.xlsx`, `numerique.xlsx`, `DETACHES.xlsx`, `.docx`) | `sme_python/modeles/` |
| Config applicative, SQL | `sme_python/data/` |
| Code source | `sme_python/src/` |

Ne pas déposer de gabarits à la racine de **Impression/** : tout doit aller dans **`sme_python/modeles/`** (voir `sme_python/modeles/LISEZ-MOI.md`).

## Lancer l’application

```powershell
cd sme_python
pip install -e .
python -m sme_gend
```

Build portable : `sme_python\scripts\build_portable.cmd`
