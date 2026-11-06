"""Postes de tête selon le type d'unité.

- Direction : DIRECTEUR, par exemple DIRECTEUR DPF.
- Région : COMMANDANT DE REGION (COMREG) et son adjoint CHEF D'ETAT MAJOR (CEMREG).
- Groupement : COMMANDANT DE GROUPEMENT (COMGROUP) et son adjoint
  CHEF D'ETAT MAJOR DU GROUPEMENT (CEMGROUP).
- Division : CHEF DE DIVISION + nom/sigle de la division.
- Section : CHEF DE SECTION + nom/sigle de la section (alias CDS acceptés).
- Secrétariat : CHEF DE SECRETARIAT + nom/sigle (alias CHEFSEC acceptés).
- Cabinet : DIRECTEUR DE CABINET (DIRCAB).
- École : COMMANDANT DE L'ECOLE (COMEC) et son adjoint CHEF D'ETAT MAJOR (CEMEC).
- Centre d'instruction : COMMANDANT DU CENTRE D'INSTRUCTION (COMCI) et son
  adjoint CHEF D'ETAT MAJOR (CEMCI).
- Commandement de la gendarmerie : COMMANDANT DE LA GENDARMERIE (COMGEND)
  et son adjoint COMMANDANT EN SECOND, CHEF D'ETAT MAJOR (C2 CEMGEND).
- État-major de la gendarmerie (EM-GEND) : CHEF D'ETAT MAJOR DE LA
  GENDARMERIE (C2 CEMGEND).
- Centre de renseignements et d'opérations (CRO) : CHEF DE CENTRE.
- Inspection technique : INSPECTEUR IT.
- Office central du renseignement : DIRECTEUR OFFICE CENTRAL DU RENSEIGNEMENT.
- Pôle : CHEF DE POLE, une fois dans chaque pôle.
- Service : CHEF DE SERVICE, une fois dans chaque service.
- Compagnie : COMMANDANT DE COMPAGNIE (CC).
- Brigade : COMMANDANT DE BRIGADE (CB).
- PSIG et peloton : COMMANDANT DE PELOTON (COMPEL).
- Escadron et ESSIG : COMMANDANT D'ESCADRON (COMES).
- Section de recherche judiciaire : COMMANDANT DE LA SECTION DE RECHERCHE
  JUDICIAIRE (COM SRJ).
- Département : CHEF DE DEPARTEMENT (CHEF DEP).
  Le GIGN et le GE-GQG sont des groupements : leur ancienne désignation
  disait « groupe ».

Les postes de direction, région, groupement, école, centre d'instruction,
cabinet, office central et état-major de la gendarmerie sont uniques pour
toute la structure. Ceux de division, section, secrétariat, pôle, service,
compagnie, brigade, peloton, escadron, section de recherche judiciaire et
département sont uniques dans la subdivision.
"""

from __future__ import annotations

from sme_gend.services.fonction_libelle_qualifier import (
    alias_keys_for_qualified_poste,
    fonction_matches_qualified_poste,
    qualified_poste_libelle,
)
from sme_gend.services.fonction_structure_policy import fold_label
from sme_gend.services.fonction_subdivision_kind import subdivision_kind

_DIRECTION_CODES = frozenset(
    {"DDT", "DIRMAT", "DIRTRANS", "DOE", "DPF", "DSF", "DTRJD"}
)
_GROUPEMENT_CODES = frozenset(
    {"GGF", "GGMF", "GGTA", "GSR", "GIGN", "GEGQG"}
)
_ECOLE_CODES = frozenset({"EGN"})
_CENTRE_INSTRUCTION_CODES = frozenset({"CIGN", "CIK", "CIEGN"})
_CABINET_CODES = frozenset({"CABCOMGEND"})
_OFFICE_CODES = frozenset({"OCR"})
_DIRECTEUR_OFFICE = "DIRECTEUR OFFICE CENTRAL DU RENSEIGNEMENT"
_STRUCTURE_FAMILLES = frozenset(
    {
        "DIRECTION",
        "REGION",
        "GROUPEMENT",
        "ECOLE",
        "CENTRE_INSTRUCTION",
        "CABINET",
        "OFFICE",
        "COMMANDEMENT",
        "CRO",
        "INSPECTION",
        "ETAT_MAJOR",
    }
)

_SCOPE_STRUCTURE = "structure"
_SCOPE_SUBDIVISION = "subdivision"


def structure_kind(
    code: str | None,
    designation: str | None = None,
    famille: str | None = None,
) -> str | None:
    """Type d'unité de la structure, ou None."""
    fam = (famille or "").strip().upper()
    if fam in _STRUCTURE_FAMILLES:
        return fam
    des = _norm(designation)
    if des.startswith("DIRECTION"):
        return "DIRECTION"
    if des.startswith("REGION"):
        return "REGION"
    if "GROUPEMENT" in des.split():
        return "GROUPEMENT"
    if des.startswith("ECOLE"):
        return "ECOLE"
    if des.startswith("CENTRE D INSTRUCTION"):
        return "CENTRE_INSTRUCTION"
    if "CABINET" in des.split():
        return "CABINET"
    if des.startswith("OFFICE CENTRAL"):
        return "OFFICE"
    if des.startswith("COMMANDEMENT DE LA GENDARMERIE"):
        return "COMMANDEMENT"
    if des.startswith("CENTRE DE RENSEIGNEMENT"):
        return "CRO"
    if des.startswith("INSPECTION TECHNIQUE"):
        return "INSPECTION"
    if des.startswith("ETAT MAJOR DE LA GENDARMERIE"):
        return "ETAT_MAJOR"
    return _kind_from_code(code)


def postes_tete_libelles(
    *,
    structure_code: str | None,
    designation: str | None = None,
    famille: str | None = None,
    subdivision: str | None = None,
) -> list[str]:
    """Libellés de tête à proposer pour cette structure et cette subdivision."""
    code = (structure_code or "").strip()
    if not code:
        return []
    kind = structure_kind(code, designation, famille)
    labels: list[str] = []
    if kind == "DIRECTION":
        labels.append(f"DIRECTEUR {code}")
    elif kind == "REGION":
        labels.extend(["COMREG", "CEMREG"])
    elif kind == "GROUPEMENT":
        labels.extend(["COMGROUP", "CEMGROUP"])
    elif kind == "ECOLE":
        labels.extend(["COMEC", "CEMEC"])
    elif kind == "CENTRE_INSTRUCTION":
        labels.extend(["COMCI", "CEMCI"])
    elif kind == "CABINET":
        labels.append("DIRCAB")
    elif kind == "OFFICE":
        labels.append(_DIRECTEUR_OFFICE)
    elif kind == "COMMANDEMENT":
        labels.extend(["COMGEND", "C2 CEMGEND"])
    elif kind == "CRO":
        labels.append("CHEF DE CENTRE")
    elif kind == "INSPECTION":
        labels.append("INSPECTEUR IT")
    elif kind == "ETAT_MAJOR":
        labels.append("C2 CEMGEND")
    sub = subdivision_kind(subdivision)
    qualified = qualified_poste_libelle(subdivision)
    if sub == "DIVISION":
        labels.append(qualified or "CHEF DE DIVISION")
    elif sub == "SECTION":
        labels.append(qualified or "CHEF DE SECTION")
    elif sub == "SECRETARIAT":
        labels.append(qualified or "CHEF DE SECRETARIAT")
        if qualified and qualified != "CHEF DE SECRETARIAT":
            qual = qualified.replace("CHEF DE SECRETARIAT ", "", 1).strip()
            if qual:
                labels.append(f"CHEFSEC {qual}")
    elif sub == "CABINET" and kind != "CABINET":
        labels.append("DIRCAB")
    elif sub == "POLE":
        labels.append(qualified or "CHEF DE POLE")
    elif sub == "SERVICE":
        labels.append(qualified or "CHEF DE SERVICE")
    elif sub == "COMPAGNIE":
        labels.append("CC")
    elif sub == "BRIGADE":
        labels.append("CB")
    elif sub == "SRJ":
        labels.append("COM SRJ")
    elif sub == "DEPARTEMENT":
        labels.append(qualified or "CHEF DE DEPARTEMENT")
        if qualified:
            qual = qualified.replace("CHEF DE DEPARTEMENT ", "", 1)
            if qual:
                labels.append(f"CHEF DEP {qual}")
    elif sub == "PELOTON":
        labels.append("COMPEL")
    elif sub == "ESCADRON":
        labels.append("COMES")
    return labels


def completer_postes_tete(
    libelles: list[str],
    *,
    structure_code: str | None,
    designation: str | None = None,
    famille: str | None = None,
    subdivision: str | None = None,
) -> list[str]:
    """Place les postes de tête en tête de liste, sans doublon."""
    from sme_gend.services.fonction_structure_policy import structure_fonctions_non_liees

    if structure_fonctions_non_liees(structure_code):
        return list(libelles)
    seen = {fold_label(x) for x in libelles if (x or "").strip()}
    head: list[str] = []
    for label in postes_tete_libelles(
        structure_code=structure_code,
        designation=designation,
        famille=famille,
        subdivision=subdivision,
    ):
        key = fold_label(label)
        if key and key not in seen:
            head.append(label)
            seen.add(key)
    return head + [x for x in libelles if (x or "").strip()]


def poste_index_keys(
    fonction: str | None,
    *,
    structure_code: str | None,
    designation: str | None = None,
    famille: str | None = None,
    subdivision: str | None = None,
) -> tuple[str, set[str]] | None:
    """(portée, clés de repli) si la fonction est un poste de tête.

    ``structure`` : un seul titulaire pour toute la structure.
    ``subdivision`` : un seul titulaire dans la subdivision.
    """
    fn = fold_label(fonction)
    if not fn:
        return None
    kind = structure_kind(structure_code, designation, famille)
    code = fold_label(structure_code)
    keys: set[str] = set()
    scope: str | None = None
    if kind == "DIRECTION" and _is_directeur(fn, code):
        scope = _SCOPE_STRUCTURE
        keys.update({fn, "DIRECTEUR", fold_label(f"DIRECTEUR {structure_code}")})
    elif kind == "REGION" and _starts_title(fn, "COMREG", "COMMANDANT DE REGION"):
        scope = _SCOPE_STRUCTURE
        keys.update({fn, "COMREG", "COMMANDANT DE REGION"})
    elif kind == "REGION" and _starts_title(fn, "CEMREG", "CHEF D ETAT MAJOR"):
        scope = _SCOPE_STRUCTURE
        keys.update({fn, "CEMREG", "CHEF D ETAT MAJOR"})
    elif kind == "GROUPEMENT" and _starts_title(
        fn, "COMGROUP", "COMGROUPE", "COMMANDANT DE GROUPEMENT"
    ):
        scope = _SCOPE_STRUCTURE
        keys.update({fn, "COMGROUP", "COMGROUPE", "COMMANDANT DE GROUPEMENT"})
    elif kind == "GROUPEMENT" and _starts_title(
        fn, "CEMGROUP", "CEMGROUPE", "CHEF D ETAT MAJOR DU GROUPEMENT"
    ):
        scope = _SCOPE_STRUCTURE
        keys.update(
            {fn, "CEMGROUP", "CEMGROUPE", "CHEF D ETAT MAJOR DU GROUPEMENT"}
        )
    elif kind == "ECOLE" and _starts_title(fn, "COMEC", "COMMANDANT DE L ECOLE"):
        scope = _SCOPE_STRUCTURE
        keys.update({fn, "COMEC", "COMMANDANT DE L ECOLE", "COMMANDANT DE L'ECOLE"})
    elif kind == "ECOLE" and _starts_title(fn, "CEMEC", "CHEF D ETAT MAJOR"):
        scope = _SCOPE_STRUCTURE
        keys.update({fn, "CEMEC", "CHEF D ETAT MAJOR", "CHEF D'ETAT MAJOR"})
    elif kind == "CENTRE_INSTRUCTION" and _starts_title(
        fn, "COMCI", "COMMANDANT DU CENTRE D INSTRUCTION"
    ):
        scope = _SCOPE_STRUCTURE
        keys.update(
            {
                fn,
                "COMCI",
                "COMMANDANT DU CENTRE D INSTRUCTION",
                "COMMANDANT DU CENTRE D'INSTRUCTION",
            }
        )
    elif kind == "CENTRE_INSTRUCTION" and _starts_title(fn, "CEMCI", "CHEF D ETAT MAJOR"):
        scope = _SCOPE_STRUCTURE
        keys.update({fn, "CEMCI", "CHEF D ETAT MAJOR", "CHEF D'ETAT MAJOR"})
    elif kind == "CABINET" and _starts_title(fn, "DIRCAB", "DIRECTEUR DE CABINET"):
        scope = _SCOPE_STRUCTURE
        keys.update({fn, "DIRCAB", "DIRECTEUR DE CABINET"})
    elif kind == "OFFICE" and _is_directeur_office(fn):
        scope = _SCOPE_STRUCTURE
        keys.update({fn, _norm(_DIRECTEUR_OFFICE), "DIRECTEUR OCR"})
    elif kind == "COMMANDEMENT" and _is_commandant_gendarmerie(fn):
        scope = _SCOPE_STRUCTURE
        keys.update({fn, "COMGEND", "COMMANDANT DE LA GENDARMERIE"})
    elif kind == "COMMANDEMENT" and _is_c2_cemgend(fn):
        scope = _SCOPE_STRUCTURE
        keys.update(
            {
                fn,
                "C2 CEMGEND",
                "CEMGEND",
                "COMMANDANT EN SECOND CHEF D ETAT MAJOR DE LA GENDARMERIE",
            }
        )
    elif kind == "CRO" and _starts_title(fn, "CHEF DE CENTRE"):
        scope = _SCOPE_STRUCTURE
        keys.update({fn, "CHEF DE CENTRE"})
    elif kind == "INSPECTION" and _is_inspecteur_it(fn):
        scope = _SCOPE_STRUCTURE
        keys.update({fn, "INSPECTEUR IT", "INSPECTEUR"})
    elif kind == "ETAT_MAJOR" and _is_chef_em_gend(fn):
        scope = _SCOPE_STRUCTURE
        keys.update(
            {
                fn,
                "C2 CEMGEND",
                "CHEF D ETAT MAJOR DE LA GENDARMERIE",
                "CHEF D'ETAT MAJOR DE LA GENDARMERIE",
            }
        )
    elif subdivision_kind(subdivision) == "DIVISION" and fonction_matches_qualified_poste(
        fonction, subdivision
    ):
        scope = _SCOPE_SUBDIVISION
        keys.update(alias_keys_for_qualified_poste(subdivision))
        keys.add(fn)
        exp = qualified_poste_libelle(subdivision)
        if exp:
            keys.add(fold_label(exp))
    elif subdivision_kind(subdivision) == "SECTION" and fonction_matches_qualified_poste(
        fonction, subdivision
    ):
        scope = _SCOPE_SUBDIVISION
        keys.update(alias_keys_for_qualified_poste(subdivision))
        keys.add(fn)
        exp = qualified_poste_libelle(subdivision)
        if exp:
            keys.add(fold_label(exp))
    elif subdivision_kind(subdivision) == "SECRETARIAT" and fonction_matches_qualified_poste(
        fonction, subdivision
    ):
        scope = _SCOPE_SUBDIVISION
        keys.update(alias_keys_for_qualified_poste(subdivision))
        keys.add(fn)
        exp = qualified_poste_libelle(subdivision)
        if exp:
            keys.add(fold_label(exp))
    elif subdivision_kind(subdivision) == "CABINET" and _starts_title(
        fn, "DIRCAB", "DIRECTEUR DE CABINET"
    ):
        scope = _SCOPE_SUBDIVISION
        keys.update({fn, "DIRCAB", "DIRECTEUR DE CABINET"})
    elif subdivision_kind(subdivision) == "POLE" and fonction_matches_qualified_poste(
        fonction, subdivision
    ):
        scope = _SCOPE_SUBDIVISION
        keys.update(alias_keys_for_qualified_poste(subdivision))
        keys.add(fn)
        exp = qualified_poste_libelle(subdivision)
        if exp:
            keys.add(fold_label(exp))
    elif subdivision_kind(subdivision) == "SERVICE" and fonction_matches_qualified_poste(
        fonction, subdivision
    ):
        scope = _SCOPE_SUBDIVISION
        keys.update(alias_keys_for_qualified_poste(subdivision))
        keys.add(fn)
        exp = qualified_poste_libelle(subdivision)
        if exp:
            keys.add(fold_label(exp))
    elif subdivision_kind(subdivision) == "COMPAGNIE" and _starts_title(
        fn, "CC", "COMMANDANT DE COMPAGNIE"
    ):
        scope = _SCOPE_SUBDIVISION
        keys.update({fn, "CC", "COMMANDANT DE COMPAGNIE"})
    elif subdivision_kind(subdivision) == "BRIGADE" and _starts_title(
        fn, "CB", "COMMANDANT DE BRIGADE"
    ):
        scope = _SCOPE_SUBDIVISION
        keys.update({fn, "CB", "COMMANDANT DE BRIGADE"})
    elif subdivision_kind(subdivision) == "SRJ" and _starts_title(
        fn, "COM SRJ", "COMMANDANT DE LA SECTION DE RECHERCHE JUDICIAIRE"
    ):
        scope = _SCOPE_SUBDIVISION
        keys.update(
            {
                fn,
                "COM SRJ",
                "COMMANDANT DE LA SECTION DE RECHERCHE JUDICIAIRE",
            }
        )
    elif subdivision_kind(subdivision) == "DEPARTEMENT" and fonction_matches_qualified_poste(
        fonction, subdivision
    ):
        scope = _SCOPE_SUBDIVISION
        keys.update(alias_keys_for_qualified_poste(subdivision))
        keys.add(fn)
        exp = qualified_poste_libelle(subdivision)
        if exp:
            keys.add(fold_label(exp))
    elif subdivision_kind(subdivision) == "PELOTON" and _starts_title(
        fn, "COMPEL", "COMMANDANT DE PELOTON", "COMMANDANT DE PELETON"
    ):
        scope = _SCOPE_SUBDIVISION
        keys.update({fn, "COMPEL", "COMMANDANT DE PELOTON"})
    elif subdivision_kind(subdivision) == "ESCADRON" and _starts_title(
        fn, "COMES", "COMMANDANT D ESCADRON"
    ):
        scope = _SCOPE_SUBDIVISION
        keys.update({fn, "COMES", "COMMANDANT D ESCADRON", "COMMANDANT D'ESCADRON"})
    if scope is None:
        return None
    return scope, {k for k in keys if k}


def _kind_from_code(code: str | None) -> str | None:
    folded = fold_label(code)
    compact = folded.replace(" ", "")
    if folded.startswith("RG ") or (compact.startswith("RG") and len(compact) > 2):
        return "REGION"
    if compact in _DIRECTION_CODES or compact.startswith("DIR"):
        return "DIRECTION"
    if compact.endswith("GGM") or compact in _GROUPEMENT_CODES:
        return "GROUPEMENT"
    if compact in _ECOLE_CODES:
        return "ECOLE"
    if compact in _CENTRE_INSTRUCTION_CODES:
        return "CENTRE_INSTRUCTION"
    if compact in _CABINET_CODES or compact.startswith("CAB"):
        return "CABINET"
    if compact in _OFFICE_CODES:
        return "OFFICE"
    if compact == "COMGEND":
        return "COMMANDEMENT"
    if compact == "CRO":
        return "CRO"
    if compact == "IT":
        return "INSPECTION"
    if compact == "EMGEND":
        return "ETAT_MAJOR"
    return None


def _is_commandant_gendarmerie(folded: str) -> bool:
    fn = _norm(folded)
    if fn.startswith("C2 ") or "EN SECOND" in fn:
        return False
    return fn in {"COMGEND", "COMMANDANT DE LA GENDARMERIE"} or fn.startswith(
        "COMMANDANT DE LA GENDARMERIE "
    )


def _is_c2_cemgend(folded: str) -> bool:
    fn = _norm(folded)
    return (
        fn in {"C2 CEMGEND", "CEMGEND"}
        or fn.startswith("C2 CEMGEND ")
        or fn.startswith("COMMANDANT EN SECOND")
    )


def _is_chef_em_gend(folded: str) -> bool:
    fn = _norm(folded)
    if fn in {"C2 CEMGEND", "CEMGEND"} or fn.startswith("C2 CEMGEND "):
        return True
    return _starts_title(fn, "CHEF D ETAT MAJOR DE LA GENDARMERIE", "CHEF D ETAT MAJOR")


def _is_inspecteur_it(folded: str) -> bool:
    fn = _norm(folded)
    return fn in {"INSPECTEUR IT", "INSPECTEUR"} or fn.startswith("INSPECTEUR IT ")


def _is_directeur_office(folded: str) -> bool:
    fn = _norm(folded)
    if fn in {_norm(_DIRECTEUR_OFFICE), "DIRECTEUR OCR"}:
        return True
    return fn.startswith("DIRECTEUR OFFICE CENTRAL")


def _is_directeur(folded: str, structure_folded: str) -> bool:
    if folded == "DIRECTEUR":
        return True
    if not folded.startswith("DIRECTEUR "):
        return False
    if not structure_folded:
        return True
    return structure_folded in folded


def _norm(value: str | None) -> str:
    return " ".join(
        fold_label(value).replace("'", " ").replace("’", " ").split()
    )


def _starts_title(folded: str, *titles: str) -> bool:
    fn = _norm(folded)
    if fn.startswith(("CHAUFFEUR ", "SEC ", "GDC ")):
        return False
    for title in titles:
        token = _norm(title)
        if fn == token or fn.startswith(token + " "):
            return True
    return False
