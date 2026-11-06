"""Suggestions de rattachement fonction ↔ structure (postes de tête, contexte)."""

from __future__ import annotations

from dataclasses import dataclass

from sme_gend.models.structure import StructureRow
from sme_gend.services.fonction_postes_tete import poste_index_keys, postes_tete_libelles
from sme_gend.services.fonction_structure_policy import fold_label, structure_fonctions_non_liees
from sme_gend.services.referentiel_service import ReferentielService
from sme_gend.services.structure_label_format import structure_combo_label


@dataclass(frozen=True, slots=True)
class StructureLinkSuggestion:
    code: str
    label: str
    reason: str
    checked: bool


def _expected_poste_for_structure(
    service: ReferentielService,
    st: StructureRow,
    libelle: str,
) -> bool:
    code = (st.code or "").strip()
    if not code:
        return False
    des = st.designation
    fam = st.famille_unite
    fn = (libelle or "").strip()
    if not fn:
        return False
    if poste_index_keys(
        fn,
        structure_code=code,
        designation=des,
        famille=fam,
    ):
        return True
    for poste in postes_tete_libelles(
        structure_code=code,
        designation=des,
        famille=fam,
    ):
        if fold_label(poste) == fold_label(fn):
            return True
    try:
        subs = service.subdivisions_arme_for_structure(code)
    except Exception:
        subs = []
    for sub in subs:
        if poste_index_keys(
            fn,
            structure_code=code,
            designation=des,
            famille=fam,
            subdivision=sub,
        ):
            return True
        for poste in postes_tete_libelles(
            structure_code=code,
            designation=des,
            famille=fam,
            subdivision=sub,
        ):
            if fold_label(poste) == fold_label(fn):
                return True
    return False


def build_structure_suggestions(
    service: ReferentielService,
    libelle: str,
    *,
    anchor_structure: str | None = None,
    structures: list[StructureRow] | None = None,
) -> tuple[str, list[StructureLinkSuggestion]]:
    """
    Retourne (libellé canonique référentiel si trouvé, suggestions).
    """
    raw = (libelle or "").strip()
    canonical = raw
    fonction_id: int | None = None
    for row in service.fonctions_all():
        if row.libelle.casefold() == raw.casefold():
            fonction_id = row.id
            canonical = row.libelle
            break

    linked: set[str] = set()
    if fonction_id is not None:
        linked = {c.casefold() for c in service.fonction_structure_codes(fonction_id)}

    anchor = (anchor_structure or "").strip()
    anchor_key = anchor.casefold()

    if structures is None:
        from sme_gend.repositories.structure_repository import StructureRepository

        structures = StructureRepository(service.settings).fetch_all()

    by_code: dict[str, StructureLinkSuggestion] = {}

    for st in structures:
        code = (st.code or "").strip()
        if not code or structure_fonctions_non_liees(code):
            continue
        reasons: list[str] = []
        checked = False
        if code.casefold() == anchor_key:
            reasons.append("structure courante")
            checked = True
        if code.casefold() in linked:
            reasons.append("déjà rattachée")
            checked = True
        if _expected_poste_for_structure(service, st, canonical or raw):
            reasons.append("poste de tête attendu")
            checked = True
        if not reasons:
            reasons.append("sélection manuelle — aucun rattachement automatique")
        combo = structure_combo_label(code, st.designation)
        by_code[code.casefold()] = StructureLinkSuggestion(
            code=code,
            label=combo,
            reason=", ".join(reasons),
            checked=checked,
        )

    if anchor_key and anchor_key not in by_code:
        st = next((s for s in structures if (s.code or "").strip().casefold() == anchor_key), None)
        if st and not structure_fonctions_non_liees(anchor):
            by_code[anchor_key] = StructureLinkSuggestion(
                code=anchor,
                label=structure_combo_label(anchor, st.designation),
                reason="structure courante",
                checked=True,
            )

    ordered = sorted(by_code.values(), key=lambda s: s.label.casefold())
    return canonical or raw, ordered
