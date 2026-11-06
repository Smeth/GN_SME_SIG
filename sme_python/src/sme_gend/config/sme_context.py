"""Contexte SME : paramètres partagés en MySQL, préférences UI locales par poste."""

from __future__ import annotations

import json
from copy import deepcopy
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

from sme_gend.config.user_paths import user_application_data_dir


def sme_context_file() -> Path:
    """Préférences UI locales uniquement (thème, colonnes)."""
    return user_application_data_dir() / "sme_ui_prefs.json"


def _legacy_context_file() -> Path:
    return user_application_data_dir() / "sme_context.json"


@dataclass
class SmeContext:
    """Mois actif = base MySQL ; workspace partagé ; UI locale."""

    mois_actif: str = ""
    mois_fermes: list[str] = field(default_factory=list)
    jour_cloture: int | None = None
    editeur_defaut: str = ""
    prompt_mois_au_demarrage: bool = True
    theme: str = "system"
    hidden_columns: list[str] = field(default_factory=list)
    extra_columns_shown: list[str] = field(default_factory=list)
    presences_mode: str = "present"
    _workspace_snapshot: object | None = field(default=None, repr=False, compare=False)
    _loaded_mois_actif: str | None = field(default=None, repr=False, compare=False)
    _pending_workspace: object | None = field(default=None, repr=False, compare=False)
    _local_snapshot: object | None = field(default=None, repr=False, compare=False)
    _month_snapshot: object | None = field(default=None, repr=False, compare=False)
    _pending_month_change: object | None = field(default=None, repr=False, compare=False)

    def to_dict(self, *, include_legacy_months: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "mois_actif": self.mois_actif,
            "prompt_mois_au_demarrage": self.prompt_mois_au_demarrage,
            "theme": self.theme,
            "presences_mode": self.presences_mode,
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
        if include_legacy_months and self.mois_fermes:
            out["mois_fermes"] = list(self.mois_fermes)
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
        prompt_mois = True if prompt is None else bool(prompt)
        theme_raw = d.get("theme", "system")
        theme = theme_raw if theme_raw in ("light", "dark", "system") else "system"
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


def load_local_sme_context() -> SmeContext:
    """Load only local display preferences; never read or promote a shared month."""
    local = SmeContext.from_dict(_load_local_ui_prefs())
    ctx = SmeContext(theme=local.theme, hidden_columns=local.hidden_columns,
                     extra_columns_shown=local.extra_columns_shown)
    ctx._local_snapshot = _local_preference_values(ctx)
    return ctx


def load_local_theme_preference() -> str:
    """Thème UI local uniquement (sans MySQL) — à utiliser avant ``get_settings()`` au démarrage."""
    prefs = _load_local_ui_prefs()
    theme_raw = prefs.get("theme", "system")
    return theme_raw if theme_raw in ("light", "dark", "system") else "system"


def _load_local_ui_prefs() -> dict[str, Any]:
    for p in (sme_context_file(), _legacy_context_file()):
        if not p.is_file():
            continue
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
        except (OSError, json.JSONDecodeError):
            continue
    return {}


def _local_preference_values(ctx: SmeContext) -> dict:
    return {"theme": ctx.theme, "hidden_columns": list(ctx.hidden_columns),
            "extra_columns_shown": list(ctx.extra_columns_shown)}


def _save_local_ui_prefs(ctx: SmeContext) -> None:
    p = sme_context_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "theme": ctx.theme,
        "hidden_columns": list(ctx.hidden_columns),
        "extra_columns_shown": list(ctx.extra_columns_shown),
    }
    p.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def peek_editeur_defaut() -> str:
    """Best-effort audit identity; shared settings precede legacy offline fallback."""
    try:
        from sme_gend.config import get_settings
        from sme_gend.services.app_config_store import KEY_SME_WORKSPACE, load_config_dict

        ws = load_config_dict(get_settings(), KEY_SME_WORKSPACE)
        if isinstance(ws, dict):
            # An explicit clear in MySQL must not revive an obsolete profile.
            return str(ws.get("editeur_defaut", "") or "").strip()
    except Exception:
        pass
    for p in (_legacy_context_file(), sme_context_file()):
        if not p.is_file():
            continue
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                ed = str(raw.get("editeur_defaut", "")).strip()
                if ed:
                    return ed
        except (OSError, json.JSONDecodeError):
            continue
    return ""


def _migrate_legacy_context_to_db(settings) -> None:
    """Premier lancement : importe sme_context.json vers MySQL."""
    legacy = _legacy_context_file()
    if not legacy.is_file():
        return
    from sme_gend.repositories.reference_collection_guard import fingerprint
    from sme_gend.repositories.reference_write_guard import ReferenceConflictError
    from sme_gend.services.sme_workspace_service import SmeWorkspaceService
    ws = SmeWorkspaceService(settings)
    preferences = ws.load_workspace_payload()
    # A stale profile may initialize an absent workspace, never replace one.
    if preferences.write_token != fingerprint(None):
        return
    try:
        raw = json.loads(legacy.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(raw, dict):
        return
    normalized = SmeContext.from_dict(raw)
    payload = {key: getattr(normalized, key) for key in preferences}
    preferences.update(payload)
    try:
        ws.save_workspace_payload(preferences)
    except ReferenceConflictError:
        return  # Another initializer or editor won; never replay the old month.

    initial_month = ws.load_active_month_snapshot()
    ma = str(raw.get("mois_actif", "")).strip()
    if ma and not initial_month.rows:
        # An existing database (even with no workspace preference yet) must
        # never be promoted back to the operational month of an old profile.
        try:
            ws.set_active_mois(ma,expected=initial_month)
        except ValueError:
            pass


def load_sme_context() -> SmeContext:
    from sme_gend.config import get_settings
    from sme_gend.services.app_config_store import ensure_central_config_ready
    from sme_gend.services.sme_workspace_service import SmeWorkspaceService

    settings = get_settings()
    local = _load_local_ui_prefs()
    ui = SmeContext.from_dict(local)
    ui._local_snapshot = _local_preference_values(ui)

    try:
        ensure_central_config_ready(settings)
        _migrate_legacy_context_to_db(settings)
        ws_svc = SmeWorkspaceService(settings)
        ws = ws_svc.load_workspace_payload()
        month_snapshot = ws_svc.load_active_month_snapshot()
        mois = month_snapshot.active_month
        fermes = ws_svc.list_closed_months()
        shared = SmeContext.from_dict(
            {
                "mois_actif": mois,
                "mois_fermes": fermes,
                "jour_cloture": ws.get("jour_cloture"),
                "editeur_defaut": ws.get("editeur_defaut", ""),
                "prompt_mois_au_demarrage": ws.get("prompt_mois_au_demarrage", True),
                "presences_mode": ws.get("presences_mode", "present"),
            }
        )
        return SmeContext(
            mois_actif=shared.mois_actif,
            mois_fermes=shared.mois_fermes,
            jour_cloture=shared.jour_cloture,
            editeur_defaut=shared.editeur_defaut,
            prompt_mois_au_demarrage=shared.prompt_mois_au_demarrage,
            presences_mode=shared.presences_mode,
            theme=ui.theme,
            hidden_columns=ui.hidden_columns,
            extra_columns_shown=ui.extra_columns_shown,
            _workspace_snapshot=ws,
            _loaded_mois_actif=shared.mois_actif,
            _local_snapshot=deepcopy(ui._local_snapshot),
            _month_snapshot=month_snapshot,
        )
    except Exception as exc:
        _log.warning("Contexte centralisé indisponible (%s) — préférences UI locales seules", exc)
        return ui


def _current_workspace_snapshot(ctx):
    pending = ctx._pending_workspace
    if pending is not None and pending._pending_write is not None:
        from sme_gend.config import get_settings
        from sme_gend.repositories.mysql_connection import MySQLConnection, has_import_transaction
        settings = get_settings()
        if has_import_transaction(settings):
            with MySQLConnection(settings).connect(read_only=True) as connection:
                if pending._pending_write[0]() is connection:
                    return pending
    # A rolled-back transaction cannot lend its pending version to a later save.
    ctx._pending_workspace = None
    return ctx._workspace_snapshot


def save_sme_context(
    ctx: SmeContext,
    *,
    promote_operational_month: bool = True,
    save_shared_preferences: bool = True,
    force_month_change: bool = False,
) -> None:
    """Save an explicit shared edit or local display preferences.

    An unchanged preference snapshot does not write MySQL. A month is promoted
    only when explicitly changed relative to the loaded context. Shared failures
    propagate to the caller; local and shared stores are not one transaction.
    """
    local_values = _local_preference_values(ctx)
    if ctx._local_snapshot is None or local_values != ctx._local_snapshot:
        _save_local_ui_prefs(ctx)
        ctx._local_snapshot = deepcopy(local_values)
    if not save_shared_preferences and not promote_operational_month:
        return

    from sme_gend.config import get_settings
    from sme_gend.services.sme_workspace_service import SmeWorkspaceService, WorkspacePreferences
    from sme_gend.repositories.mysql_connection import after_mysql_commit, mysql_database_key

    snapshot = _current_workspace_snapshot(ctx)
    if not isinstance(snapshot, WorkspacePreferences):
        raise ValueError("Le workspace partagé n'a pas pu être chargé. Rechargez avant d'enregistrer.")
    values = {key: getattr(ctx, key) for key in snapshot}
    preferences_changed = save_shared_preferences and values != dict(snapshot)
    new_month = (ctx.mois_actif or '').strip()
    month_changed = promote_operational_month and new_month and (force_month_change or new_month != ctx._loaded_mois_actif)
    if not preferences_changed and not month_changed:
        return

    settings = get_settings()
    if snapshot._write_database != mysql_database_key(settings):
        raise ValueError("La base de travail a changé. Rechargez le workspace avant d'enregistrer.")
    service = SmeWorkspaceService(settings)
    def save_preferences():
        if not preferences_changed:
            return
        candidate = deepcopy(snapshot)
        candidate.update(values)
        service.save_workspace_payload(candidate)
        ctx._pending_workspace = candidate

        def committed_preferences():
            if ctx._pending_workspace is candidate:
                ctx._workspace_snapshot = deepcopy(candidate)
                ctx._pending_workspace = None

        after_mysql_commit(committed_preferences)

    if not month_changed:
        save_preferences()
        return

    from sme_gend.services.active_month_guard import period_state_write,require_month_snapshot,load_active_month_snapshot
    from sme_gend.repositories.mysql_connection import MySQLConnection,has_import_transaction
    expected = ctx._month_snapshot
    pending = ctx._pending_month_change
    if pending is not None and has_import_transaction(settings):
        with MySQLConnection(settings).connect(read_only=True) as connection:
            if pending.connection() is connection:
                expected = pending.result or load_active_month_snapshot(settings,current=True)
    require_month_snapshot(settings,expected)
    # Check the displayed period before saving preferences. Statuses, careers,
    # workspace, audit and notification either commit together or roll back.
    previous_month_pending = ctx._pending_month_change
    previous_workspace_pending = ctx._pending_workspace
    try:
        with period_state_write(settings,expected=expected,mois=new_month) as change:
            save_preferences()
            service.set_active_mois(new_month,expected=load_active_month_snapshot(settings,current=True))
            ctx._pending_month_change = change

            def committed_month():
                if ctx._pending_month_change is change:
                    ctx._month_snapshot = change.result
                    ctx._loaded_mois_actif = new_month
                    ctx._pending_month_change = None

            after_mysql_commit(committed_month)

    except BaseException:
        candidate = ctx._pending_workspace
        if candidate is not None and candidate is not previous_workspace_pending:
            candidate._pending_write = None
        ctx._pending_workspace = previous_workspace_pending
        ctx._pending_month_change = previous_month_pending
        raise


def is_mois_ferme(mois: str) -> bool:
    """Mois clôturé selon ``sme_periode`` / TabSme (pas le fichier local)."""
    from sme_gend.config import get_settings
    from sme_gend.services.sme_workspace_service import SmeWorkspaceService

    try:
        return SmeWorkspaceService(get_settings()).is_month_closed(mois)
    except Exception:
        return mois in (load_sme_context().mois_fermes or [])


def mois_actif_courant() -> str:
    return load_sme_context().mois_actif
