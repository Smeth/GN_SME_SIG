"""Parse `ordre_general.txt` (arborescence resultat.xlsx) → config liste des présents."""

from __future__ import annotations

import re
from pathlib import Path

from sme_gend.models.liste_layout import ListeLayoutConfig, SectionBlock

SECTION_RE = re.compile(r"^(\d+)\.\s+(.+)$")
SUB_RE = re.compile(r"^\s*└──\s+(.+)$")

# Titres exacts (ligne « N. TITRE ») → code structure TabStructure / ListeGenerale.Structure
_TITLE_TO_CODE: dict[str, str] = {
    "COMMANDEMENT DE LA GENDARMERIE": "COMGEND",
    "CAB-COMGEND": "CAB-COMGEND",
    "ETAT-MAJOR DE LA GENDARMERIE NATIONALE": "EM-GEND",
    "INSPECTION TECHNIQUE DE LA GENDARMERIE NATIONALE": "IT",
    "DIRECTION DE L'ORGANISATION ET DE L'EMPLOI": "DOE",
    "DIRECTION DU PERSONNEL ET DE LA FORMATION": "DPF",
    "DIRECTION DES SERVICES FINANCIERS": "DSF",
    "DIRECTION TECHNIQUE DE RENSEIGNEMENTS JUDICIAIRES": "DTRJD",
    "DIRECTION DES TRANSMISSIONS": "DIRTRANS",
    "DIRECTION DES MATERIELS": "DIRMAT",
    "DIRECTION DES DOMAINES ET TRAVAUX": "DDT",
    "CENTRE DE RENSEIGNEMENTS ET D'OPERATIONS": "CRO",
    "OFFICE CENTRAL DU RENSEIGNEMENT": "OCR",
    "ECOLE DE LA GENDARMERIE NATIONALE": "EGN",
    "COMMANDEMENT GIGN": "GIGN",
    "CENTRE D'INSTRUCTION DE KINTELE": "CIK",
    "GROUPEMENT ESCADRONS GRAND QUARTIER GENERAL": "GE-GQG",
    "GROUPEMENT GENDARMERIE FERROVIAIRE": "GGF",
    "GROUPEMENT GENDARMERIE MARITIME ET FLUVIALE": "GGMF",
    "GROUPEMENT GENDARMERIE DES TRANSPORTS AERIENS": "GGTA",
    "GROUPEMENT DE SECURITE ROUTIERE": "GSR",
    "REGION DE GENDARMERIE DE BRAZZAVILLE": "RG-BZV",
    "REGION DE GENDARMERIE DU POOL": "RG-POO",
    "REGION DE GENDARMERIE DE LA BOUENZA": "RG-BZA",
    "REGION DE GENDARMERIE DE LA LEKOUMOU": "RG-LEK",
    "REGION DE GENDARMERIE DU NIARI": "RG-NRI",
    "REGION DE GENDARMERIE DU KOUILOU": "RG-KLU",
    "REGION DE GENDARMERIE DE POINTE-NOIRE": "RG-PNR",
    "REGION DE GENDARMERIE DU DJOUE-LEFINI": "RG-DJL",
    "REGION DE GENDARMERIE DES PLATEAUX": "RG-PLT",
    "REGION DE GENDARMERIE DE LA NKENI-ALIMA": "RG-NKA",
    "REGION DE GENDARMERIE DE LA CUVETTE": "RG-CUV",
    "REGION DE GENDARMERIE DE LA CUVETTE OUEST": "RG-CVO",
    "REGION DE GENDARMERIE DU CONGO-OUBANGUI": "RG-COB",
    "REGION DE GENDARMERIE DE LA SANGHA": "RG-SGH",
    "REGION DE GENDARMERIE DE LA LIKOUALA": "RG-LIK",
}

# Codes présents dans l’app mais absents du fichier « ordre général » (conservés en fin de liste).
_TAIL_STRUCTURE_CODES: tuple[str, ...] = ("CS-DPF", "DETACHE", "N_SIGNALE")


def _parse_blocks(text: str) -> list[tuple[int, str, list[str]]]:
    lines = text.splitlines()
    blocks: list[tuple[int, str, list[str]]] = []
    i = 0
    while i < len(lines):
        m = SECTION_RE.match(lines[i])
        if not m:
            i += 1
            continue
        num = int(m.group(1))
        title = m.group(2).strip()
        subs: list[str] = []
        i += 1
        while i < len(lines):
            sm = SUB_RE.match(lines[i])
            if sm:
                subs.append(sm.group(1).strip())
                i += 1
                continue
            if SECTION_RE.match(lines[i]):
                break
            i += 1
        blocks.append((num, title, subs))
    return blocks


def _sans_only(subs: list[str]) -> bool:
    return bool(subs) and all(
        s.replace(" ", "").casefold() in ("(sanssous-section)", "(sanssoussection)")
        for s in subs
    )


def _subdivision_order_from_subs(subs: list[str]) -> list[str] | None:
    if not subs or _sans_only(subs):
        return None
    out: list[str] = []
    seen_line: set[str] = set()
    for s in subs:
        if not s:
            continue
        if s.replace(" ", "").casefold() in ("(sanssous-section)", "(sanssoussection)"):
            continue
        if s in seen_line:
            continue
        seen_line.add(s)
        out.append(s)
    return out or None


def _resolve_structure_code(title: str, subs: list[str]) -> str | None:
    """Retourne le code structure ou None si le bloc doit être ignoré (doublon métier)."""
    if title == "PREMIER GROUPEMENT GENDARMERIE MOBILE":
        first = subs[0] if subs else ""
        if "1ER GGM" in first.upper().replace(" ", "") or "1er GGM" in first:
            return "1er GGM"
        return None
    if title == "DEUXIEME GROUPEMENT GENDARMERIE MOBILE":
        return "2e GGM"
    return _TITLE_TO_CODE.get(title)


def build_layout_config_from_ordre_general_text(text: str) -> ListeLayoutConfig:
    """Construit une `ListeLayoutConfig` : ordre des sections + `subdivision_order` par bloc."""
    blocks = _parse_blocks(text)
    sections: list[SectionBlock] = []
    seen_codes: set[str] = set()
    order_idx = 0

    for _num, title, subs in blocks:
        code = _resolve_structure_code(title, subs)
        if not code or code in seen_codes:
            continue
        seen_codes.add(code)
        sub_order = _subdivision_order_from_subs(subs)
        sections.append(
            SectionBlock(
                id=f"auto-{code}-{order_idx}".replace(" ", "_"),
                order=order_idx,
                structure_code=code,
                title_main_override=None,
                title_sub_override=None,
                group_by_subdivision=True,
                subdivision_order=sub_order,
            )
        )
        order_idx += 1

    for code in _TAIL_STRUCTURE_CODES:
        if code in seen_codes:
            continue
        sections.append(
            SectionBlock(
                id=f"auto-{code}-{order_idx}".replace(" ", "_"),
                order=order_idx,
                structure_code=code,
                title_main_override=None,
                title_sub_override=None,
                group_by_subdivision=True,
                subdivision_order=None,
            )
        )
        order_idx += 1
        seen_codes.add(code)

    return ListeLayoutConfig(sections=sections)


def build_layout_config_from_ordre_general_path(path: Path) -> ListeLayoutConfig:
    return build_layout_config_from_ordre_general_text(path.read_text(encoding="utf-8"))


def default_ordre_general_paths(project_root: Path) -> list[Path]:
    """Candidats : `data/ordre_general.txt` puis `../ordre_general.txt` (dépôt)."""
    return [
        project_root / "data" / "ordre_general.txt",
        project_root.parent / "ordre_general.txt",
    ]
