"""Modèle pour la table Access ``TabGainPerte`` (gains et pertes mensuels)."""

from __future__ import annotations

from dataclasses import dataclass, field


OBSERV_NON_SIGNALE = "Non signalé"


def observ_is_non_signale(observ: str | None) -> bool:
    """Sortie de la liste générale sans perte saisie (placeholder de cohérence)."""
    o = (observ or "").strip().casefold()
    if not o:
        return False
    folded = (
        o.replace("é", "e")
        .replace("è", "e")
        .replace("ê", "e")
    )
    return folded in {
        "non signale",
        "non signaler",
        "non signalee",
        "sortie non signalee",
    }


def observ_is_deces(observ: str | None) -> bool:
    """Décès : motif/observation DECES, DECEDE ou variantes proches."""
    o = (observ or "").strip().upper()
    if not o:
        return False
    if o in ("DECES", "DECEDE", "DÉCÉDÉ", "DÉCÈS", "DECES "):
        return True
    if "DECES" in o or "DECEDE" in o or "DÉCÉD" in o:
        return True
    return False


def cdsme_to_mois_ym(cdsme: str | None) -> str | None:
    """``012026`` → ``2026-01``."""
    if not cdsme:
        return None
    s = cdsme.strip()
    if len(s) == 6 and s.isdigit():
        return f"{s[2:]}-{s[:2]}"
    return None


def periode_ym_for_mouvement(row: "GainPerteRow") -> str | None:
    """Mois SME cible (YYYY-MM) : ``mois_ym`` explicite, sinon dérivé du Cdsme."""
    ym = (row.mois_ym or "").strip()
    if ym and len(ym) >= 7 and ym[4:5] == "-":
        return ym[:7]
    return cdsme_to_mois_ym(row.cdsme)


@dataclass(slots=True)
class GainPerteRow:
    """Une ligne gains & pertes (mouvement_effectif / TabGainPerte)."""

    id: str
    grade: str | None
    nom: str | None
    objet: str | None       # "GAIN " ou "PERTE " (avec espace possible)
    cdsme: str | None       # format MMYYYY ex. "012026"
    observ: str | None      # "DECES", "AJOUT", "ERREUR", …
    mois_ym: str | None = None
    date_reference: str | None = None
    movement_id: int | None = None
    # Ligne calculée (absent de la liste du mois, sans perte saisie) — pas encore en base.
    inferred: bool = False
    structure_code: str | None = None
    # Opaque loaded snapshot; excluded from business equality and diagnostics.
    write_token: tuple[int, str] | None = field(default=None, repr=False, compare=False)

    @property
    def objet_norm(self) -> str:
        return (self.objet or "").strip().upper()

    @property
    def is_gain(self) -> bool:
        return self.objet_norm == "GAIN"

    @property
    def is_perte(self) -> bool:
        return self.objet_norm == "PERTE"

    @property
    def is_deces(self) -> bool:
        return self.is_perte and observ_is_deces(self.observ)

    def effective_mois_ym(self) -> str | None:
        """Mois SME effectif : période liée en priorité, sinon Cdsme."""
        return periode_ym_for_mouvement(self)

    @property
    def observ_display(self) -> str:
        return (self.observ or "").strip() or "—"

    @property
    def objet_display(self) -> str:
        n = self.objet_norm
        if n == "GAIN":
            return "Gain"
        if n == "PERTE":
            return "Perte"
        return (self.objet or "").strip() or "—"

    @property
    def date_display(self) -> str:
        d = (self.date_reference or "").strip()
        if not d:
            return "—"
        if len(d) >= 10 and d[4] == "-":
            return f"{d[8:10]}/{d[5:7]}/{d[:4]}"
        return d
