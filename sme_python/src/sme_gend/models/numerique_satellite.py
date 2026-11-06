"""Détails feuilles satellites situation numérique (mission, malade, …)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class MissionDetail:
    nature: str | None
    lieu_mission: str | None
    refnds: str | None
    date_depart: str | None
    write_token: tuple[int, int | None, str] | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class MaladeDetail:
    date_constat: str | None
    pathologie: str | None
    obs: str | None
    write_token: tuple[int, int | None, str] | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class DeserteurDetail:
    dat_desertion: str | None
    write_token: tuple[int, int | None, str] | None = field(default=None, compare=False, repr=False)
    reference_dossier: str | None = None


@dataclass(frozen=True, slots=True)
class StageDetail:
    nature_stage: str | None
    lieu_stage: str | None
    date_debut_fin: str | None
    write_token: tuple[int, int | None, str] | None = field(default=None, compare=False, repr=False)
    reference_dossier: str | None = None
    session: str | None = None
