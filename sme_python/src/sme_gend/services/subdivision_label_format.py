"""Libellés courts « subdivision d'arme » pour affichage (liste des présents, exports, etc.)."""

from __future__ import annotations

import re
from collections.abc import Sequence

from sme_gend.repositories.subdivision_repository import SubdivisionFlatRow


def _strip_common_data_errors(s: str) -> str:
    """Corrige des erreurs de saisie récurrentes (ex. « YIE » collé devant le libellé ESR)."""
    t = " ".join(s.split()).strip()
    if re.match(r"YIE\s+ESCADRON", t, re.IGNORECASE):
        t = re.sub(r"^YIE\s+", "", t, count=1, flags=re.IGNORECASE).strip()
    return t


def format_subdivision_arme_display(
    raw: str | None,
    flat: Sequence[SubdivisionFlatRow],
) -> str:
    """
    Ramène un SubArme ListeGenerale vers une forme lisible type « ESR POINTE-NOIRE »
    lorsque le référentiel TabSectrois fournit CodeT + SectDesign.

    Gère notamment les libellés longs ou avec préfixe erroné : si ``SectDesign`` du
    référentiel est contenu dans (ou suffixe de) la valeur brute, on utilise CodeT +
    suffixe court dérivé du design officiel.
    """
    if raw is None:
        return ""
    s = _strip_common_data_errors(" ".join(str(raw).split()))
    if not s or s == "(Sans subdivision)":
        return s
    if not flat:
        fb0 = _fallback_esr_short_from_text(s)
        return fb0 if fb0 else s
    s_cf = s.casefold()

    for r in flat:
        dt = (r.design_t or "").strip()
        if dt and s_cf == dt.casefold():
            return _compose_label(r)

    candidates: list[SubdivisionFlatRow] = []
    for r in flat:
        dt = (r.design_t or "").strip()
        if len(dt) >= 4 and s_cf.endswith(dt.casefold()):
            candidates.append(r)
    if candidates:
        best = max(candidates, key=lambda x: len((x.design_t or "").strip()))
        return _compose_label(best)

    best: SubdivisionFlatRow | None = None
    best_len = 0
    for r in flat:
        dt = (r.design_t or "").strip()
        if len(dt) >= 6 and dt.casefold() in s_cf and len(dt) > best_len:
            best = r
            best_len = len(dt)
    if best:
        return _compose_label(best)
    # Sans ligne référentiel : forme déjà « ESR VILLE » ou dérivée du texte long
    fb = _fallback_esr_short_from_text(s)
    if fb:
        return fb
    return s


def _fallback_esr_short_from_text(s: str) -> str | None:
    """Si ListeGenerale contient « ESR NGO » ou un libellé long ESR sans match TabSectrois."""
    t = _strip_common_data_errors(s)
    m = re.match(r"^ESR\s+(.+)$", t.strip(), re.IGNORECASE)
    if m:
        return f"ESR {' '.join(m.group(1).split())}"
    t_up = t.upper()
    if "ESCADRON" in t_up and "SECURITE" in t_up and "ROUTIERE" in t_up:
        ville = _ville_from_long_escadron_line(t)
        if ville:
            return f"ESR {ville}"
    return None


def _ville_from_long_escadron_line(t: str) -> str | None:
    """Extrait NGO, OUESSO, etc. depuis « … DE BRAZZAVILLE NGO » ou dernier segment court."""
    u = " ".join(t.split())
    u_up = u.upper()
    m = re.search(r"BRAZZAVILLE\s+([A-Z0-9\-]{2,24})\s*$", u_up)
    if m:
        return m.group(1).strip()
    parts = re.split(r"\s+DE\s+", u_up)
    if len(parts) >= 2:
        last = parts[-1].strip()
        if 2 <= len(last) <= 24 and last not in ("SECURITE ROUTIERE",):
            if " " not in last or re.match(r"^POINTE[\s-]+NOIRE$", last):
                return last.replace(" ", "-") if "POINTE" in last else last
    toks = u_up.split()
    if toks:
        tail = toks[-1]
        if 2 <= len(tail) <= 12:
            return tail
    return None


def subdivision_block_heading(
    raw: str | None,
    flat: Sequence[SubdivisionFlatRow],
) -> str:
    """
    Titre de bloc (ligne fusionnée) sous la structure, aligné sur la maquette : libellé seul.

    Cas particulier **ESR** : titre **ESCADRON DE SECURITE ROUTIERE** + **ville**
    (ex. NGO, OUESSO, POINTE-NOIRE). Sinon : même forme courte que ``format_subdivision_arme_display``.
    """
    short = format_subdivision_arme_display(raw, flat)
    ville: str | None = None
    if short:
        m = re.match(r"^ESR\s+(.+)$", short.strip(), re.IGNORECASE)
        if m:
            ville = " ".join(m.group(1).split()).upper().replace(" POINTE NOIRE", " POINTE-NOIRE")
            if "POINTE" in ville and "NOIRE" in ville:
                ville = "POINTE-NOIRE"
    if not ville and raw:
        t = _strip_common_data_errors(str(raw))
        m2 = re.match(r"^ESR\s+(.+)$", t.strip(), re.IGNORECASE)
        if m2:
            ville = m2.group(1).strip().upper()
        else:
            ville = _ville_from_long_escadron_line(t)
    if ville:
        vl = ville.strip().upper().replace(" POINTE NOIRE", " POINTE-NOIRE")
        if re.match(r"^POINTE[\s-]+NOIRE$", vl, re.IGNORECASE):
            vl = "POINTE-NOIRE"
        return f"ESCADRON DE SECURITE ROUTIERE {vl}"

    return short or ""


def _compose_label(r: SubdivisionFlatRow) -> str:
    ct = (r.code_t or "").strip()
    dt = (r.design_t or "").strip()
    if not ct:
        return dt or ""
    if not dt:
        return ct
    dt_u = dt.upper()
    ct_u = ct.upper()
    if dt_u.startswith(ct_u + " "):
        return dt
    if dt_u == ct_u:
        return ct
    if len(dt) <= 36 and "ESCADRON" not in dt_u and "COMMANDEMENT" not in dt_u:
        return f"{ct} {dt}"
    suffix = _suffix_from_long_sect_design(dt)
    if suffix:
        return f"{ct} {suffix}"
    return f"{ct} {dt}"


def _suffix_from_long_sect_design(design_t: str) -> str:
    """Extrait un lieu court depuis un libellé TabSectrois long (ex. … POINTE-NOIRE)."""
    u = " ".join(design_t.split())
    m = re.search(r"(POINTE[\s-]+NOIRE)\s*$", u, re.IGNORECASE)
    if m:
        return "POINTE-NOIRE"
    parts = re.split(r"\s+DE\s+", u, flags=re.IGNORECASE)
    if len(parts) >= 2:
        last = parts[-1].strip()
        m_br = re.match(r"^BRAZZAVILLE\s+(.+)$", last, re.IGNORECASE)
        if m_br:
            tail = m_br.group(1).strip()
            if 2 <= len(tail) <= 24:
                return tail.upper()
        if 4 <= len(last) <= 32:
            return last.upper()
    return ""
