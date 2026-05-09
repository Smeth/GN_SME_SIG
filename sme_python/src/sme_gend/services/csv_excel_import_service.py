"""Import de la liste générale depuis un fichier CSV ou Excel (.xlsx)."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Mapping de colonnes attendues → champ ListeGeneraleRow
# Les en-têtes sont normalisés (minuscule, sans accents ni ponctuation).
# ---------------------------------------------------------------------------

def _normalize_header(h: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFD", str(h).strip().lower())
    s = s.encode("ascii", "ignore").decode("ascii")
    s = s.replace(" ", "_").replace("-", "_")
    # Supprimer les caractères non-alphanumériques restants
    return "".join(c for c in s if c.isalnum() or c == "_")


# Alias de colonnes : nom normalisé possible → champ interne
_COL_ALIASES: dict[str, str] = {
    # noms
    "noms": "noms",
    "nom": "noms",
    "noms_prenoms": "noms",
    "nom_et_prenom": "noms",
    "nom_prenom": "noms",
    # grade
    "grade": "grade",
    "grad": "grade",
    # date de naissance
    "date_naissance": "date_naissance",
    "date_n": "date_naissance",
    "dn": "date_naissance",
    "naissance": "date_naissance",
    # lieu de naissance
    "lieu_naissance": "lieu_naissance",
    "lieu_n": "lieu_naissance",
    "lieu": "lieu_naissance",
    # date d'entrée au service
    "date_service": "date_service",
    "date_entree": "date_service",
    "entree_service": "date_service",
    "entree": "date_service",
    # matricule militaire
    "matricule_militaire": "matricule_militaire",
    "mat_militaire": "matricule_militaire",
    "matmil": "matricule_militaire",
    "matricule_mil": "matricule_militaire",
    # matricule solde
    "matricule_solde": "matricule_solde",
    "mat_solde": "matricule_solde",
    "matsol": "matricule_solde",
    "matricule_sol": "matricule_solde",
    # position administrative
    "position_admin": "position_admin",
    "padmin": "position_admin",
    "position": "position_admin",
    "pos_admin": "position_admin",
    # fonction
    "fonction": "fonction",
    "fonct": "fonction",
    # structure
    "structure": "structure",
    "struct": "structure",
    "code_structure": "structure",
    # subdivision d'arme
    "subdivision_arme": "subdivision_arme",
    "sub_arme": "subdivision_arme",
    "subarme": "subdivision_arme",
    # observations
    "obs": "obs",
    "observation": "obs",
    "observations": "obs",
    "remarques": "obs",
    # ndecision
    "ndecision": "ndecision",
    "n_decision": "ndecision",
    "decision": "ndecision",
    # diplome
    "diplome": "diplome",
    "diplôme": "diplome",
    # date obtention diplôme
    "date_obtention": "date_obtention",
    "date_diplome": "date_obtention",
    # ordre de tri
    "ordre_tri": "ordre_tri",
    "ordre": "ordre_tri",
    # presseance
    "presseance": "presseance",
    "precedence": "presseance",
}

_FIELD_LABELS: dict[str, str] = {
    "noms":                "Noms & prénoms",
    "grade":               "Grade",
    "date_naissance":      "Date de naissance",
    "lieu_naissance":      "Lieu de naissance",
    "date_service":        "Entrée au service",
    "matricule_militaire": "Matricule militaire",
    "matricule_solde":     "Matricule solde",
    "position_admin":      "Position admin.",
    "fonction":            "Fonction",
    "structure":           "Structure",
    "subdivision_arme":    "Subdivision d'arme",
    "obs":                 "Observations",
    "ndecision":           "N° décision",
    "diplome":             "Diplôme",
    "date_obtention":      "Date obtention",
    "ordre_tri":           "Ordre de tri",
    "presseance":          "Préséance",
}

_REQUIRED_FIELDS = {"noms"}


# ---------------------------------------------------------------------------
# Résultat d'import
# ---------------------------------------------------------------------------

@dataclass
class ImportRow:
    """Ligne parsée et mappée, prête pour insertion."""
    noms:                str | None = None
    grade:               str | None = None
    date_naissance:      str | None = None
    lieu_naissance:      str | None = None
    date_service:        str | None = None
    matricule_militaire: str | None = None
    matricule_solde:     str | None = None
    position_admin:      str | None = None
    fonction:            str | None = None
    structure:           str | None = None
    subdivision_arme:    str | None = None
    obs:                 str | None = None
    ndecision:           str | None = None
    diplome:             str | None = None
    date_obtention:      str | None = None
    ordre_tri:           int | None = None
    presseance:          int | None = None

    @property
    def is_valid(self) -> bool:
        return bool((self.noms or "").strip())

    def validation_error(self) -> str | None:
        if not (self.noms or "").strip():
            return "Nom manquant"
        return None


@dataclass
class ImportResult:
    rows: list[ImportRow]
    column_map: dict[str, str]   # colonne fichier → champ interne
    unmapped_columns: list[str]
    errors: list[str]

    @property
    def valid_rows(self) -> list[ImportRow]:
        return [r for r in self.rows if r.is_valid]


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class CsvExcelImportService:

    def parse_file(self, path: Path) -> ImportResult:
        """Lit un fichier CSV ou Excel et retourne les lignes mappées."""
        ext = path.suffix.lower()
        if ext in (".xlsx", ".xls", ".xlsm"):
            headers, raw_rows = self._read_excel(path)
        elif ext in (".csv", ".txt"):
            headers, raw_rows = self._read_csv(path)
        else:
            raise ValueError(f"Format non supporté : {ext!r} (attendu : .xlsx, .csv)")

        col_map, unmapped = self._map_columns(headers)
        rows, errors = self._parse_rows(raw_rows, col_map, headers)
        return ImportResult(
            rows=rows, column_map=col_map,
            unmapped_columns=unmapped, errors=errors,
        )

    def _read_excel(self, path: Path) -> tuple[list[str], list[list[Any]]]:
        import openpyxl
        wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        ws = wb.active
        if ws is None:
            raise ValueError("Le classeur Excel est vide.")
        rows = list(ws.iter_rows(values_only=True))
        wb.close()
        if not rows:
            return [], []
        headers = [str(c) if c is not None else "" for c in rows[0]]
        data = [
            [str(c) if c is not None else "" for c in row]
            for row in rows[1:]
            if any(c is not None and str(c).strip() for c in row)
        ]
        return headers, data

    def _read_csv(self, path: Path) -> tuple[list[str], list[list[Any]]]:
        # Détecter l'encodage et le délimiteur
        for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
            try:
                text = path.read_text(encoding=enc)
                break
            except (UnicodeDecodeError, LookupError):
                continue
        else:
            text = path.read_text(encoding="latin-1", errors="replace")

        sample = text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
        except csv.Error:
            dialect = csv.excel
            dialect.delimiter = ";"

        reader = csv.reader(text.splitlines(), dialect=dialect)
        rows_raw = list(reader)
        if not rows_raw:
            return [], []
        headers = [str(c).strip() for c in rows_raw[0]]
        data = [
            [str(c).strip() for c in row]
            for row in rows_raw[1:]
            if any(str(c).strip() for c in row)
        ]
        return headers, data

    def _map_columns(
        self, headers: list[str]
    ) -> tuple[dict[str, str], list[str]]:
        """Retourne (col_fichier → champ_interne, colonnes_non_mappées)."""
        col_map: dict[str, str] = {}
        unmapped: list[str] = []
        for h in headers:
            if not h:
                continue
            key = _normalize_header(h)
            field = _COL_ALIASES.get(key)
            if field:
                if field not in col_map.values():
                    col_map[h] = field
                # Si le champ est déjà mappé, ignorer la colonne dupliquée
            else:
                unmapped.append(h)
        return col_map, unmapped

    def _parse_rows(
        self,
        raw_rows: list[list[Any]],
        col_map: dict[str, str],
        headers: list[str],
    ) -> tuple[list[ImportRow], list[str]]:
        # Index par colonne-header dans le fichier
        header_idx: dict[str, int] = {h: i for i, h in enumerate(headers)}

        def get(row: list, col_name: str) -> str:
            idx = header_idx.get(col_name)
            if idx is None or idx >= len(row):
                return ""
            return str(row[idx]).strip()

        def field_val(row: list, field: str) -> str | None:
            for col, f in col_map.items():
                if f == field:
                    v = get(row, col)
                    return v if v else None
            return None

        import_rows: list[ImportRow] = []
        errors: list[str] = []

        for i, raw in enumerate(raw_rows, start=2):  # start=2 : ligne 1 = en-têtes
            r = ImportRow(
                noms=field_val(raw, "noms"),
                grade=field_val(raw, "grade"),
                date_naissance=field_val(raw, "date_naissance"),
                lieu_naissance=field_val(raw, "lieu_naissance"),
                date_service=field_val(raw, "date_service"),
                matricule_militaire=field_val(raw, "matricule_militaire"),
                matricule_solde=field_val(raw, "matricule_solde"),
                position_admin=field_val(raw, "position_admin"),
                fonction=field_val(raw, "fonction"),
                structure=field_val(raw, "structure"),
                subdivision_arme=field_val(raw, "subdivision_arme"),
                obs=field_val(raw, "obs"),
                ndecision=field_val(raw, "ndecision"),
                diplome=field_val(raw, "diplome"),
                date_obtention=field_val(raw, "date_obtention"),
            )
            for fld in ("ordre_tri", "presseance"):
                v = field_val(raw, fld)
                if v:
                    try:
                        setattr(r, fld, int(v))
                    except ValueError:
                        errors.append(f"Ligne {i} — {fld} invalide : {v!r}")

            err = r.validation_error()
            if err:
                errors.append(f"Ligne {i} — {err} : {raw[:3]}")

            import_rows.append(r)

        return import_rows, errors

    def import_into_sqlite(
        self,
        rows: list[ImportRow],
        settings,
        *,
        skip_duplicates: bool = True,
    ) -> tuple[int, int, list[str]]:
        """Insère les lignes valides dans la base SQLite.

        Retourne (insérées, ignorées, erreurs).
        """
        from sme_gend.repositories.liste_generale_sqlite_repository import (
            SqliteListeGeneraleRepository,
        )
        from sme_gend.models.liste_generale import ListeGeneraleRow

        repo = SqliteListeGeneraleRepository(settings)
        existing_names: set[str] = set()
        if skip_duplicates:
            existing_names = {
                (r.noms or "").strip().upper()
                for r in repo.fetch_all()
                if r.noms
            }

        inserted = skipped = 0
        errors: list[str] = []

        for r in rows:
            if not r.is_valid:
                continue
            noms_up = (r.noms or "").strip().upper()
            if skip_duplicates and noms_up in existing_names:
                skipped += 1
                continue
            try:
                row = ListeGeneraleRow(
                    id="",
                    noms=r.noms,
                    date_naissance=None,  # stored as string in date_service style
                    lieu_naissance=r.lieu_naissance,
                    grade=r.grade,
                    date_grade=None,
                    date_service=r.date_service,
                    ndecision=r.ndecision,
                    matricule_militaire=r.matricule_militaire,
                    matricule_solde=r.matricule_solde,
                    position_admin=r.position_admin,
                    fonction=r.fonction,
                    subdivision_arme=r.subdivision_arme,
                    diplome=r.diplome,
                    date_obtention=r.date_obtention,
                    structure=r.structure,
                    obs=r.obs,
                    ordre_tri=r.ordre_tri,
                    presseance=r.presseance,
                )
                # date_naissance : stocker la chaîne brute si fournie
                if r.date_naissance:
                    from dataclasses import replace
                    row = replace(row, date_naissance=r.date_naissance)
                repo.insert_row(row)
                existing_names.add(noms_up)
                inserted += 1
            except Exception as e:
                errors.append(f"{r.noms or '?'} : {e}")

        return inserted, skipped, errors
