"""Sexe / genre du personnel (M = masculin, F = féminin)."""

from __future__ import annotations

SEXE_M = "M"
SEXE_F = "F"

SEXE_LABELS: dict[str, str] = {
    SEXE_M: "Masculin",
    SEXE_F: "Féminin",
}

SEXE_COMBO_ITEMS: tuple[tuple[str, str], ...] = (
    ("", "— Non renseigné —"),
    (SEXE_M, "Masculin (M)"),
    (SEXE_F, "Féminin (F)"),
)


def normalize_sexe(raw: str | None) -> str | None:
    """Normalise vers M, F ou None."""
    if raw is None:
        return None
    s = str(raw).strip().upper()
    if not s:
        return None
    if s in (SEXE_M, "M.", "H", "HOMME", "MASCULIN"):
        return SEXE_M
    if s in (SEXE_F, "F.", "FEMME", "FEMININ", "FÉMININ"):
        return SEXE_F
    if s.startswith("M"):
        return SEXE_M
    if s.startswith("F"):
        return SEXE_F
    return None


def sexe_display(code: str | None) -> str:
    """Libellé court pour affichage tableau."""
    c = normalize_sexe(code)
    if c == SEXE_M:
        return "M"
    if c == SEXE_F:
        return "F"
    return ""


def sexe_display_long(code: str | None) -> str:
    c = normalize_sexe(code)
    if c is None:
        return "—"
    return SEXE_LABELS.get(c, c)
