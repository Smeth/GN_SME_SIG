"""Inventaire et harmonisation du référentiel des fonctions (postes qualifiés)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from sme_gend.config.settings import Settings
from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
from sme_gend.repositories.structure_repository import StructureRepository
from sme_gend.services.fonction_libelle_qualifier import qualified_poste_libelle
from sme_gend.services.fonction_postes_tete import poste_index_keys, postes_tete_libelles
from sme_gend.services.fonction_structure_policy import fold_label, structure_fonctions_non_liees
from sme_gend.services.referentiel_service import ReferentielService
from sme_gend.services.structure_label_format import structure_combo_label


@dataclass(frozen=True, slots=True)
class FonctionHarmonizeRow:
    structure_code: str
    structure_label: str
    subdivision: str
    type_unite: str
    libelle_attendu: str
    en_referentiel: bool
    rattachee_structure: bool
    libelles_observes: str
    titulaires: str
    statut: str


@dataclass(slots=True)
class HarmonizeApplyResult:
    created: int = 0
    linked: int = 0
    skipped: int = 0


def _observed_by_scope(
    settings: Settings,
) -> dict[tuple[str, str], set[str]]:
    """(structure_code, subdivision_fold) -> fonctions observées."""
    out: dict[tuple[str, str], set[str]] = {}
    for row in ListeGeneraleRepository(settings).fetch_all():
        sc = (row.structure or "").strip()
        if not sc:
            continue
        sub = fold_label(row.subdivision_arme)
        fn = (row.fonction or "").strip()
        if not fn:
            continue
        out.setdefault((sc.casefold(), sub), set()).add(fn)
    return out


def _titulaires_map(settings: Settings) -> dict[tuple[str, str, str], list[str]]:
    from sme_gend.services.postes_vacants import lister_postes_tete

    structures = StructureRepository(settings).fetch_all()
    people = ListeGeneraleRepository(settings).fetch_all()
    out: dict[tuple[str, str, str], list[str]] = {}
    for poste in lister_postes_tete(structures, [], people):
        key = (
            poste.structure_code.casefold(),
            fold_label(poste.subdivision),
            fold_label(poste.poste),
        )
        out[key] = [holder.label for holder in poste.titulaires_details] if poste.titulaires_details else list(poste.titulaires)
    return out


def collect_harmonize_rows(settings: Settings) -> list[FonctionHarmonizeRow]:
    service = ReferentielService(settings)
    structures = StructureRepository(settings).fetch_all()
    by_label: dict[str, int] = {
        row.libelle.casefold(): row.id for row in service.fonctions_all()
    }
    links = service.fonction_links_summary()
    observed = _observed_by_scope(settings)
    tit_map = _titulaires_map(settings)
    rows: list[FonctionHarmonizeRow] = []
    seen: set[tuple[str, str, str]] = set()

    for st in structures:
        code = (st.code or "").strip()
        if not code or structure_fonctions_non_liees(code):
            continue
        label_st = structure_combo_label(code, st.designation)

        def emit(subdivision: str, poste: str, type_u: str) -> None:
            lib = (poste or "").strip()
            if not lib:
                return
            token = (code.casefold(), fold_label(subdivision), fold_label(lib))
            if token in seen:
                return
            seen.add(token)
            fid = by_label.get(lib.casefold())
            en_ref = fid is not None
            rattachee = en_ref and code.casefold() in {
                c.casefold() for c in links.get(fid or -1, [])
            }
            obs = observed.get((code.casefold(), fold_label(subdivision)), set())
            obs_str = "; ".join(sorted(obs, key=str.casefold)) if obs else ""
            tit = tit_map.get(token, [])
            tit_str = ", ".join(tit) if tit else ""
            if en_ref and rattachee and tit_str:
                statut = "OK"
            elif en_ref and rattachee:
                statut = "Référentiel OK — vacant"
            elif en_ref:
                statut = "Référentiel — non rattaché à la structure"
            else:
                statut = "Manquant au référentiel"
            if obs_str and fold_label(lib) not in {fold_label(x) for x in obs}:
                statut = f"{statut} · écart saisie"
            rows.append(
                FonctionHarmonizeRow(
                    structure_code=code,
                    structure_label=label_st,
                    subdivision=subdivision or "—",
                    type_unite=type_u,
                    libelle_attendu=lib,
                    en_referentiel=en_ref,
                    rattachee_structure=rattachee,
                    libelles_observes=obs_str,
                    titulaires=tit_str,
                    statut=statut,
                )
            )

        for poste in postes_tete_libelles(
            structure_code=code,
            designation=st.designation,
            famille=st.famille_unite,
        ):
            indexed = poste_index_keys(
                poste,
                structure_code=code,
                designation=st.designation,
                famille=st.famille_unite,
            )
            type_u = "Structure" if indexed and indexed[0] == "structure" else "Structure"
            emit("", poste, type_u)

        subs = service.subdivision_arme_choices_for_structure(code)
        for sub in subs:
            for poste in postes_tete_libelles(
                structure_code=code,
                designation=st.designation,
                famille=st.famille_unite,
                subdivision=sub,
            ):
                indexed = poste_index_keys(
                    poste,
                    structure_code=code,
                    designation=st.designation,
                    famille=st.famille_unite,
                    subdivision=sub,
                )
                if indexed is None or indexed[0] != "subdivision":
                    continue
                from sme_gend.services.fonction_postes_tete import subdivision_kind

                kind = subdivision_kind(sub) or "Subdivision"
                emit(sub, poste, kind)

    rows.sort(
        key=lambda r: (
            r.structure_label.casefold(),
            r.subdivision.casefold(),
            r.libelle_attendu.casefold(),
        )
    )
    return rows


def apply_missing_harmonization(settings: Settings) -> HarmonizeApplyResult:
    service = ReferentielService(settings)
    result = HarmonizeApplyResult()
    for row in collect_harmonize_rows(settings):
        if row.en_referentiel and row.rattachee_structure:
            result.skipped += 1
            continue
        lib = row.libelle_attendu
        try:
            service.fonction_ensure_catalog_and_link(row.structure_code, lib)
        except ValueError:
            result.skipped += 1
            continue
        if not row.en_referentiel:
            result.created += 1
        if not row.rattachee_structure:
            result.linked += 1
    return result


def export_harmonize_workbook(path: Path, settings: Settings, *, mois_label: str = "", rows=None) -> int:
    from sme_gend.services.auth_guard import session_can_export_documents
    if not session_can_export_documents():raise PermissionError("Permission effectifs.export requise.")
    rows = collect_harmonize_rows(settings) if rows is None else list(rows)
    wb = Workbook()
    ws = wb.active
    ws.title = "Fonctions"
    title = "Harmonisation des fonctions (postes qualifiés)"
    if mois_label:
        title = f"{title} — {mois_label}"
    ws.merge_cells("A1:J1")
    ws["A1"] = title
    ws["A1"].font = Font(bold=True, size=12)
    headers = (
        "Structure",
        "Code",
        "Subdivision d'arme",
        "Type",
        "Libellé attendu",
        "En référentiel",
        "Rattachée",
        "Titulaire(s) mois",
        "Fonctions observées (effectif)",
        "Statut",
    )
    hr = 3
    for ci, h in enumerate(headers, 1):
        c = ws.cell(hr, ci, h)
        c.font = Font(bold=True)
    widths = (32, 12, 28, 14, 36, 12, 12, 28, 36, 28)
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for ri, row in enumerate(rows, hr + 1):
        ws.cell(ri, 1, row.structure_label)
        ws.cell(ri, 2, row.structure_code)
        ws.cell(ri, 3, row.subdivision)
        ws.cell(ri, 4, row.type_unite)
        ws.cell(ri, 5, row.libelle_attendu)
        ws.cell(ri, 6, "Oui" if row.en_referentiel else "Non")
        ws.cell(ri, 7, "Oui" if row.rattachee_structure else "Non")
        ws.cell(ri, 8, row.titulaires)
        ws.cell(ri, 9, row.libelles_observes)
        ws.cell(ri, 10, row.statut)
    from sme_gend.services.excel_style_helpers import write_literal_cell_value
    for row in ws.iter_rows():
        for cell in row:
            if isinstance(cell.value,str):write_literal_cell_value(ws,cell.row,cell.column,cell.value)
    from sme_gend.services.safe_paths import atomic_destination
    with atomic_destination(path,temporary_suffix='.xlsx') as staged:wb.save(staged)
    from sme_gend.services.doc_gen_log import log_doc_generation
    from sme_gend.config.liste_generale_scope import resolve_liste_view_mois
    log_doc_generation(doc_type='harmonisation_fonctions',format='xlsx',count=len(rows),dest_path=str(path),settings=settings,mois=resolve_liste_view_mois(settings))
    return len(rows)


def suggest_libelle_for_agent(
    *,
    structure_code: str | None,
    designation: str | None,
    famille: str | None,
    subdivision: str | None,
) -> str | None:
    """Premier poste qualifié pertinent pour la saisie agent."""
    if not (subdivision or "").strip():
        return None
    labels = postes_tete_libelles(
        structure_code=structure_code,
        designation=designation,
        famille=famille,
        subdivision=subdivision,
    )
    expected = qualified_poste_libelle(subdivision)
    if expected and expected in labels:
        return expected
    for label in labels:
        indexed = poste_index_keys(
            label,
            structure_code=structure_code,
            designation=designation,
            famille=famille,
            subdivision=subdivision,
        )
        if indexed and indexed[0] == "subdivision":
            return label
    return expected
