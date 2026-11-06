"""Import Excel / CSV des attributions et référentiels médailles / décorations."""

from __future__ import annotations

import csv
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sme_gend.config.settings import Settings
from sme_gend.models.distinction_ref import (
    DISTINCTION_KIND_DECORATION,
    DISTINCTION_KIND_MEDAILLE,
)
from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
from sme_gend.services.access_date_parse import parse_access_date
from sme_gend.services.agent_dossier_service import AgentDossierService
from sme_gend.services.referentiel_service import ReferentielService


def _normalize_header(h: str) -> str:
    s = unicodedata.normalize("NFD", str(h).strip().lower())
    s = s.encode("ascii", "ignore").decode("ascii")
    s = s.replace(" ", "_").replace("-", "_")
    return "".join(c for c in s if c.isalnum() or c == "_")


_ATTR_ALIASES: dict[str, str] = {
    "id_liste": "legacy_id",
    "id": "legacy_id",
    "legacy_id": "legacy_id",
    "id_liaison": "legacy_id",
    "matricule": "matricule",
    "matricule_militaire": "matricule",
    "matmil": "matricule",
    "noms": "noms",
    "nom": "noms",
    "type": "kind",
    "kind": "kind",
    "code_distinction": "code",
    "code": "code",
    "distinction": "code",
    "date_attribution": "date_attribution",
    "date": "date_attribution",
    "date_publication_jo": "date_jo",
    "date_jo": "date_jo",
    "publication_jo": "date_jo",
    "numero_arrete": "numero_arrete",
    "arrete": "numero_arrete",
    "n_arrete": "numero_arrete",
    "date_arrete": "date_arrete",
    "citation": "citation",
    "mention": "citation",
}

_REF_ALIASES: dict[str, str] = {
    "code": "code",
    "libelle_court": "libelle_court",
    "libelle": "libelle_court",
    "libelle_long": "libelle_long",
    "categorie": "categorie",
    "ordre_preseance": "ordre_preseance",
    "ordre": "ordre_preseance",
    "actif": "actif",
}


@dataclass(slots=True)
class DistinctionImportRow:
    legacy_id: str | None = None
    matricule: str | None = None
    noms: str | None = None
    kind: str = DISTINCTION_KIND_MEDAILLE
    code: str = ""
    date_attribution: Any = None
    date_jo: Any = None
    numero_arrete: str | None = None
    date_arrete: Any = None
    citation: str | None = None
    line_no: int = 0


@dataclass(slots=True)
class DistinctionImportResult:
    refs_medailles_upserted: int = 0
    refs_decorations_upserted: int = 0
    attributions_created: int = 0
    skipped_duplicate: int = 0
    errors: list[str] = field(default_factory=list)


def _parse_kind(raw: str) -> str:
    s = unicodedata.normalize("NFD", (raw or "").strip().lower())
    s = s.encode("ascii", "ignore").decode("ascii")
    if s in ("decoration", "decorations", "ordre", "ordres", "dec"):
        return DISTINCTION_KIND_DECORATION
    if s in ("medaille", "medailles", "med"):
        return DISTINCTION_KIND_MEDAILLE
    if "decor" in s or "ordre" in s:
        return DISTINCTION_KIND_DECORATION
    return DISTINCTION_KIND_MEDAILLE


def _read_sheet_rows(path: Path, sheet_name: str | None = None) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".csv":
        text = path.read_text(encoding="utf-8-sig")
        delim = ";" if text.count(";") > text.count(",") else ","
        reader = csv.DictReader(text.splitlines(), delimiter=delim)
        return [dict(r) for r in reader]
    import openpyxl

    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    if sheet_name and sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
    else:
        ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    try:
        header_row = next(rows_iter)
    except StopIteration:
        return []
    headers = [_normalize_header(h or "") for h in header_row]
    out: list[dict[str, Any]] = []
    for raw in rows_iter:
        if not any(c is not None and str(c).strip() for c in raw):
            continue
        d: dict[str, Any] = {}
        for i, key in enumerate(headers):
            if not key:
                continue
            if i < len(raw):
                d[key] = raw[i]
        out.append(d)
    return out


def _map_row(raw: dict[str, Any], aliases: dict[str, str], line_no: int) -> dict[str, Any]:
    mapped: dict[str, Any] = {}
    for k, v in raw.items():
        nk = _normalize_header(k)
        field_name = aliases.get(nk)
        if field_name:
            mapped[field_name] = v
    for k, v in raw.items():
        nk = _normalize_header(k)
        if nk in aliases:
            mapped[aliases[nk]] = v
    mapped["line_no"] = line_no
    return mapped


def parse_attribution_rows(path: Path) -> list[DistinctionImportRow]:
    raw_rows = _read_sheet_rows(path, "Attributions")
    out: list[DistinctionImportRow] = []
    for i, raw in enumerate(raw_rows, start=2):
        m = _map_row(raw, _ATTR_ALIASES, i)
        code = str(m.get("code") or "").strip()
        if not code and not (m.get("legacy_id") or m.get("matricule") or m.get("noms")):
            continue
        out.append(
            DistinctionImportRow(
                legacy_id=str(m.get("legacy_id") or "").strip() or None,
                matricule=str(m.get("matricule") or "").strip() or None,
                noms=str(m.get("noms") or "").strip() or None,
                kind=_parse_kind(str(m.get("kind") or "")),
                code=code,
                date_attribution=m.get("date_attribution"),
                date_jo=m.get("date_jo"),
                numero_arrete=str(m.get("numero_arrete") or "").strip() or None,
                date_arrete=m.get("date_arrete"),
                citation=str(m.get("citation") or "").strip() or None,
                line_no=i,
            )
        )
    return out


def _parse_bool(v: Any) -> bool:
    if v is None:
        return True
    s = str(v).strip().lower()
    return s in ("1", "true", "oui", "yes", "o", "y")


def _upsert_refs_from_workbook(path: Path, service: ReferentielService, result: DistinctionImportResult) -> None:
    if path.suffix.lower() == ".csv":
        return
    import openpyxl

    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    for sheet, kind in (("Medailles_ref", "medaille"), ("Decorations_ref", "decoration")):
        if sheet not in wb.sheetnames:
            continue
        raw_rows = _read_sheet_rows(path, sheet)
        existing_med = {r.code.upper(): r for r in service.medailles()}
        existing_dec = {r.code.upper(): r for r in service.decorations()}
        for i, raw in enumerate(raw_rows, start=2):
            m = _map_row(raw, _REF_ALIASES, i)
            code = str(m.get("code") or "").strip()
            if not code:
                continue
            try:
                ordre = m.get("ordre_preseance")
                ordre_i = int(ordre) if ordre is not None and str(ordre).strip() else None
            except ValueError:
                ordre_i = None
            payload = {
                "code": code,
                "libelle_court": str(m.get("libelle_court") or "").strip() or None,
                "libelle_long": str(m.get("libelle_long") or "").strip() or None,
                "categorie": str(m.get("categorie") or "").strip() or None,
                "ordre_preseance": ordre_i,
                "actif": _parse_bool(m.get("actif")),
            }
            try:
                if kind == "medaille":
                    ex = existing_med.get(code.upper())
                    if ex:
                        service.medaille_update(ex.id, **payload)
                    else:
                        service.medaille_insert(**payload)
                    result.refs_medailles_upserted += 1
                else:
                    ex = existing_dec.get(code.upper())
                    if ex:
                        service.decoration_update(ex.id, **payload)
                    else:
                        service.decoration_insert(**payload)
                    result.refs_decorations_upserted += 1
            except Exception as exc:
                result.errors.append(f"{sheet} ligne {i}: {exc}")


def _resolve_liste_row(
    rows: list,
    *,
    legacy_id: str | None,
    matricule: str | None,
    noms: str | None,
):
    if legacy_id:
        leg = legacy_id.strip()
        for r in rows:
            if str(r.satellite_join_key() or r.id or "").strip() == leg:
                return r
    if matricule:
        mat = matricule.strip().casefold()
        for r in rows:
            if (r.matricule_militaire or "").strip().casefold() == mat:
                return r
    if noms:
        nm = noms.strip().casefold()
        for r in rows:
            if (r.noms or "").strip().casefold() == nm:
                return r
    return None


def _duplicate(existing, kind: str, ref_med_id: int | None, ref_dec_id: int | None, d_attr) -> bool:
    for d in existing:
        if kind == DISTINCTION_KIND_MEDAILLE:
            if d.ref_medaille_id == ref_med_id and d.date_attribution == d_attr:
                return True
        elif d.ref_decoration_id == ref_dec_id and d.date_attribution == d_attr:
            return True
    return False


def apply_distinction_import(
    settings: Settings,
    path: Path,
    *,
    upsert_referentials: bool = True,
) -> DistinctionImportResult:
    result = DistinctionImportResult()
    ref_svc = ReferentielService(settings)
    if not ref_svc.distinctions_tables_ready():
        result.errors.append("Tables médailles / décorations absentes (migrations MySQL).")
        return result
    if upsert_referentials and path.suffix.lower() != ".csv":
        _upsert_refs_from_workbook(path, ref_svc, result)

    import_rows = parse_attribution_rows(path)
    if not import_rows:
        result.errors.append("Aucune ligne d'attribution lisible dans le fichier.")
        return result

    liste = ListeGeneraleRepository(settings).fetch_all()
    dossier = AgentDossierService(settings)
    med_by_code = {m.code.upper(): m for m in ref_svc.medailles()}
    dec_by_code = {d.code.upper(): d for d in ref_svc.decorations()}

    for row in import_rows:
        if not row.code:
            result.errors.append(f"Ligne {row.line_no}: code_distinction manquant.")
            continue
        lg = _resolve_liste_row(
            liste,
            legacy_id=row.legacy_id,
            matricule=row.matricule,
            noms=row.noms,
        )
        if lg is None:
            result.errors.append(
                f"Ligne {row.line_no}: agent introuvable (id_liste / matricule / noms)."
            )
            continue
        code_u = row.code.upper()
        ref_med_id: int | None = None
        ref_dec_id: int | None = None
        if row.kind == DISTINCTION_KIND_MEDAILLE:
            med = med_by_code.get(code_u)
            if not med:
                result.errors.append(f"Ligne {row.line_no}: code médaille inconnu « {row.code} ».")
                continue
            ref_med_id = med.id
        else:
            dec = dec_by_code.get(code_u)
            if not dec:
                result.errors.append(
                    f"Ligne {row.line_no}: code décoration inconnu « {row.code} »."
                )
                continue
            ref_dec_id = dec.id

        d_attr = parse_access_date(row.date_attribution)
        try:
            existing = dossier.list_distinctions(lg)
            if _duplicate(existing, row.kind, ref_med_id, ref_dec_id, d_attr):
                result.skipped_duplicate += 1
                continue
            dossier.add_distinction(
                lg,
                kind=row.kind,
                ref_medaille_id=ref_med_id,
                ref_decoration_id=ref_dec_id,
                date_attribution=d_attr,
                date_publication_journal=parse_access_date(row.date_jo),
                numero_arrete=row.numero_arrete,
                date_arrete=parse_access_date(row.date_arrete),
                citation=row.citation,
                source="import_excel",
            )
            result.attributions_created += 1
        except Exception as exc:
            result.errors.append(f"Ligne {row.line_no}: {exc}")

    return result
