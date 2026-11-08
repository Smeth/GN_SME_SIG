"""Pilotage SME partagé : mois actif et paramètres workspace en base."""

from __future__ import annotations

import logging
from typing import Any

from sme_gend.config.settings import Settings
from sme_gend.repositories.app_config_repository import AppConfigRepository
from sme_gend.repositories.sme_repository import SmeRepository
from copy import deepcopy
from sme_gend.services.app_config_store import KEY_SME_WORKSPACE
from sme_gend.services.shared_config_write import (
    VersionedConfigDict, bind_config_version, load_shared_payload, save_shared_model,
)
from sme_gend.services.auth_context import resolve_audit_actor
from sme_gend.services.mysql_import_period import (
    ensure_sme_periode,
    refresh_est_actuelle_flags,
    refresh_carriere_segment_dates,
)
from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm

_log = logging.getLogger(__name__)

_STATUT_OUVERT = frozenset({"ouvert", "open", "ouverte"})
_STATUT_FERME = frozenset({"fermé", "ferme", "closed", "clos", "fermée", "clôturé", "cloture", "cloturé"})
_STATUT_REOUVERT = frozenset({"réouvert", "reouvert", "re-open", "reopened"})


def _norm_statut(s: str | None) -> str:
    return (s or "").strip().casefold()


def _is_ouvert(statut: str | None) -> bool:
    return _norm_statut(statut) in _STATUT_OUVERT


def _is_reouvert(statut: str | None) -> bool:
    t = _norm_statut(statut)
    return t in _STATUT_REOUVERT or "reouvert" in t or "réouvert" in (statut or "").casefold()


def _is_ferme_strict(statut: str | None) -> bool:
    """Fermé pour navigation historique — pas « Réouvert »."""
    if _is_ouvert(statut) or _is_reouvert(statut):
        return False
    t = _norm_statut(statut)
    return t in _STATUT_FERME or ("ferm" in t and "reouvert" not in t)


def _default_workspace() -> dict[str, Any]:
    return {
        "editeur_defaut": "",
        "jour_cloture": None,
        "prompt_mois_au_demarrage": True,
        "presences_mode": "present",
    }


class WorkspacePreferences(VersionedConfigDict):
    """Visible preferences plus an opaque copy of unrecognized stored fields.

    Legacy month fields stay out of the preference API. They are preserved in
    storage without being reinterpreted as an operational month.
    """

    def __init__(self, raw=None):
        from sme_gend.config.sme_context import SmeContext
        raw = raw if raw is not None else {}
        ctx = SmeContext.from_dict(raw)
        super().__init__({key: getattr(ctx, key) for key in _default_workspace()})
        self._original_payload = deepcopy(raw)

    def to_dict(self):
        return {**self._original_payload, **dict(self)}


def _validate_preferences(payload):
    if set(payload) != set(_default_workspace()):
        raise ValueError("La sauvegarde doit contenir uniquement les préférences partagées du workspace SME.")
    if not isinstance(payload['editeur_defaut'], str):
        raise ValueError("Le signataire doit être un texte.")
    day = payload['jour_cloture']
    if day is not None and (type(day) is not int or not 1 <= day <= 31):
        raise ValueError("Le jour de clôture doit être compris entre 1 et 31, ou non renseigné.")
    if type(payload['prompt_mois_au_demarrage']) is not bool:
        raise ValueError("L'option de dialogue au démarrage doit être cochée ou décochée.")
    if payload['presences_mode'] not in ('present', 'stage_interne', 'les_deux'):
        raise ValueError("Mode de présence invalide.")


class SmeWorkspaceService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._sme = SmeRepository(settings)
        self._app = AppConfigRepository(settings)

    def load_workspace_payload(self) -> WorkspacePreferences:
        raw = load_shared_payload(self._settings, KEY_SME_WORKSPACE)
        return bind_config_version(WorkspacePreferences(raw), KEY_SME_WORKSPACE, raw, self._settings)

    def save_workspace_payload(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, WorkspacePreferences):
            raise ValueError("Version du workspace indisponible. Rechargez les préférences avant d'enregistrer.")
        _validate_preferences(payload)
        save_shared_model(self._settings, KEY_SME_WORKSPACE, payload)

    def list_mysql_months(self) -> list[str]:
        """Mois connus en base (``sme_periode`` / TabSme), sans les archives fichier seules."""
        seen: set[str] = set()
        from sme_gend.repositories.mysql_connection import MySQLConnection

        try:
            mc = MySQLConnection(self._settings)
            with mc.connect(read_only=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT annee_mois FROM sme_periode ORDER BY annee_mois"
                    )
                    for row in cur.fetchall():
                        ym = canonical_sme_mois_yyyy_mm(str(row[0]))
                        if ym:
                            seen.add(ym)
        except Exception:
            pass
        if self._sme.table_exists():
            for r in self._sme.fetch_all():
                ym = canonical_sme_mois_yyyy_mm(r.mois)
                if ym:
                    seen.add(ym)
        return sorted(seen)

    def list_known_months(self) -> list[str]:
        """Tous les mois connus (``YYYY-MM``) : base + archives disque, triés."""
        from sme_gend.services.sme_month_closure import list_months_from_archives

        seen: set[str] = set(self.list_mysql_months())
        seen.update(list_months_from_archives())
        return sorted(seen)

    def list_closed_months(self) -> list[str]:
        fermes: list[str] = []
        if self._sme.table_exists():
            for r in self._sme.fetch_all():
                if _is_ferme_strict(r.statut):
                    ym = canonical_sme_mois_yyyy_mm(r.mois)
                    if ym:
                        fermes.append(ym)
        try:
            from sme_gend.repositories.mysql_connection import MySQLConnection

            mc = MySQLConnection(self._settings)
            with mc.connect(read_only=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT annee_mois, statut FROM sme_periode
                        WHERE statut IS NOT NULL
                        """
                    )
                    for ym_raw, st in cur.fetchall():
                        if _is_ferme_strict(st):
                            ym = canonical_sme_mois_yyyy_mm(str(ym_raw))
                            if ym and ym not in fermes:
                                fermes.append(ym)
        except Exception:
            pass
        return sorted(set(fermes))

    def get_active_mois(self) -> str:
        """Read one authoritative, coherent state; report ambiguity explicitly."""
        return self.load_active_month_snapshot().active_month

    def is_month_closed(self, mois: str) -> bool:
        ym = canonical_sme_mois_yyyy_mm(mois)
        if not ym:
            return False
        return ym in self.list_closed_months()

    def load_active_month_snapshot(self):
        from sme_gend.services.active_month_guard import load_active_month_snapshot
        return load_active_month_snapshot(self._settings)

    def close_month_transition(self, mois_cloture: str, mois_suivant: str) -> str:
        """Internal closure step; its outer transaction has checked the source."""
        from sme_gend.services.action_permissions import assert_any_permission
        assert_any_permission("workflow.close_month")
        from sme_gend.services.active_month_guard import require_period_scope
        require_period_scope(self._settings)
        from sme_gend.services.document_month_cycle import assert_legacy_closure
        assert_legacy_closure(self._settings)
        self._apply_active_month_locked(mois_suivant)
        return mois_suivant

    def set_active_mois(self, mois: str, *, expected=None) -> str:
        """Activate a month only from an explicitly loaded operational snapshot."""
        from sme_gend.services.action_permissions import assert_any_permission
        assert_any_permission("workflow.set_active_month")
        from sme_gend.services.active_month_guard import period_state_write,require_month_snapshot
        from sme_gend.repositories.mysql_connection import MySQLConnection
        from datetime import datetime
        require_month_snapshot(self._settings,expected)
        ym=canonical_sme_mois_yyyy_mm(mois)
        if not ym:
            raise ValueError(f"Mois invalide : {mois!r}")
        try:datetime.strptime(ym+'-01','%Y-%m-%d')
        except ValueError as exc:raise ValueError("Mois invalide dans le calendrier.") from exc
        with period_state_write(self._settings,expected=expected,mois=ym) as change:
            # An ordinary activation must never bypass the exceptional reopening
            # workflow, its specific right, mandatory reason and audit.
            if any(canonical_sme_mois_yyyy_mm(str(row[1])) == ym and _is_ferme_strict(row[2])
                   for row in change.before.rows):
                raise ValueError("Ce mois est clôturé. Utilisez la réouverture exceptionnelle avec un motif avant de l'activer.")
            from sme_gend.services.active_month_guard import load_active_month_snapshot
            current=load_active_month_snapshot(self._settings,current=True)
            if current.active_month==ym and current.rows==change.before.rows:
                return ym
            from sme_gend.services.document_month_cycle import assert_legacy_closure
            assert_legacy_closure(self._settings)
            self._apply_active_month_locked(ym)
        return ym

    def _apply_active_month_locked(self,ym):
        from sme_gend.repositories.mysql_connection import MySQLConnection
        from sme_gend.services.active_month_guard import require_period_scope
        require_period_scope(self._settings)
        with MySQLConnection(self._settings).connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT annee_mois, statut FROM sme_periode ORDER BY id FOR UPDATE")
                preserved={ym}|{str(row[0]) for row in cur.fetchall() if _is_reouvert(row[1])}
                placeholders=", ".join(["%s"]*len(preserved))
                cur.execute(f"UPDATE sme_periode SET statut = 'Fermé' WHERE annee_mois NOT IN ({placeholders})",tuple(preserved))
                ensure_sme_periode(conn,ym,statut="Ouvert")
            refresh_est_actuelle_flags(conn,ym)
            refresh_carriere_segment_dates(conn)
        self._app.append_audit(action="sme_active_month",module="workspace",sme_mois_context=ym,
            payload={"mois":ym},actor=resolve_audit_actor())
