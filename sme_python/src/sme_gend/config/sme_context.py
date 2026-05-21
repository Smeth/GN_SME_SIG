"""Contexte SME mensuel stocké localement (sans modifier la base Access)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

from sme_gend.config.user_paths import user_application_data_dir


def sme_context_file() -> Path:
    return user_application_data_dir() / "sme_context.json"


@dataclass
class SmeContext:
    """Mois de travail courant et historique des clôtures (fichier JSON utilisateur)."""

    mois_actif: str = ""
    mois_fermes: list[str] = field(default_factory=list)
    """Jour de clôture administrative (1–31), optionnel — traçabilité locale uniquement."""
    jour_cloture: int | None = None
    """Nom, initiales ou poste enregistré(e) dans l’audit des fiches (optionnel)."""
    editeur_defaut: str = ""
    """Si True, afficher au démarrage le dialogue « ouvrir une période SME » (TabSme + mois actif)."""
    prompt_mois_au_demarrage: bool = True
    """Thème visuel : « light » (défaut) ou « dark »."""
    theme: str = "light"
    """Colonnes masquées dans la liste du personnel (liste de clés de champ)."""
    hidden_columns: list[str] = field(default_factory=list)
    """Colonnes supplémentaires (masquées par défaut) explicitement affichées par l'utilisateur."""
    extra_columns_shown: list[str] = field(default_factory=list)
    """Filtre d'affichage de la liste des présents : 'present' (défaut), 'stage_interne', ou 'les_deux'."""
    presences_mode: str = "present"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "mois_actif": self.mois_actif,
            "mois_fermes": list(self.mois_fermes),
            "prompt_mois_au_demarrage": self.prompt_mois_au_demarrage,
            "theme": self.theme,
        }
        if self.jour_cloture is not None:
            out["jour_cloture"] = self.jour_cloture
        ed = (self.editeur_defaut or "").strip()
        if ed:
            out["editeur_defaut"] = ed
        if self.hidden_columns:
            out["hidden_columns"] = list(self.hidden_columns)
        if self.extra_columns_shown:
            out["extra_columns_shown"] = list(self.extra_columns_shown)
        out["presences_mode"] = self.presences_mode
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SmeContext:
        ma = d.get("mois_actif")
        mf = d.get("mois_fermes")
        fermes: list[str] = []
        if isinstance(mf, list):
            fermes = [str(x).strip() for x in mf if str(x).strip()]
        jc_raw = d.get("jour_cloture")
        jour: int | None = None
        if jc_raw is not None and str(jc_raw).strip() != "":
            try:
                v = int(jc_raw)
                if 1 <= v <= 31:
                    jour = v
            except (TypeError, ValueError):
                jour = None
        ed_raw = d.get("editeur_defaut")
        editeur = str(ed_raw).strip() if ed_raw is not None else ""
        prompt = d.get("prompt_mois_au_demarrage")
        if prompt is None:
            prompt_mois = True
        else:
            prompt_mois = bool(prompt)
        theme_raw = d.get("theme", "light")
        theme = theme_raw if theme_raw in ("light", "dark") else "light"
        hc_raw = d.get("hidden_columns")
        hidden: list[str] = [str(x) for x in hc_raw] if isinstance(hc_raw, list) else []
        ecs_raw = d.get("extra_columns_shown")
        extra_shown: list[str] = [str(x) for x in ecs_raw] if isinstance(ecs_raw, list) else []
        _valid_modes = ("present", "stage_interne", "les_deux")
        mode_raw = d.get("presences_mode", "present")
        presences_mode = mode_raw if mode_raw in _valid_modes else "present"
        return cls(
            mois_actif=str(ma).strip() if ma else "",
            mois_fermes=fermes,
            jour_cloture=jour,
            editeur_defaut=editeur,
            prompt_mois_au_demarrage=prompt_mois,
            theme=theme,
            hidden_columns=hidden,
            extra_columns_shown=extra_shown,
            presences_mode=presences_mode,
        )


def load_sme_context() -> SmeContext:
    p = sme_context_file()
    if not p.is_file():
        return SmeContext()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        _log.warning("Fichier contexte SME illisible — réinitialisation par défaut (%s) : %s", p, exc)
        return SmeContext()
    if not isinstance(raw, dict):
        _log.warning("Fichier contexte SME : format inattendu (%s) — réinitialisation", type(raw).__name__)
        return SmeContext()
    return SmeContext.from_dict(raw)


def save_sme_context(ctx: SmeContext) -> None:
    p = sme_context_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(ctx.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def mois_actif_courant() -> str:
    """Libellé du mois SME en cours (vide si non défini)."""
    return load_sme_context().mois_actif
