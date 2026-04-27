"""Contexte SME mensuel stocké localement (sans modifier la base Access)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "mois_actif": self.mois_actif,
            "mois_fermes": list(self.mois_fermes),
        }
        if self.jour_cloture is not None:
            out["jour_cloture"] = self.jour_cloture
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
        return cls(
            mois_actif=str(ma).strip() if ma else "",
            mois_fermes=fermes,
            jour_cloture=jour,
        )


def load_sme_context() -> SmeContext:
    p = sme_context_file()
    if not p.is_file():
        return SmeContext()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return SmeContext()
    if not isinstance(raw, dict):
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
