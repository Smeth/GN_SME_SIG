"""Ordre prioritaire des fonctions — configuration MySQL + ref_fonction."""

from __future__ import annotations

from sme_gend.config.settings import Settings
from sme_gend.models.fonction_ref import FonctionRefRow
from sme_gend.models.fonction_priority_config import FonctionPriorityConfig
from sme_gend.repositories.fonctions_repository import FonctionsRepository
from sme_gend.services.app_config_store import KEY_FONCTION_PRIORITY

# Libellés historiques (bootstrap si ref_fonction contient les entrées correspondantes).
LEGACY_DEFAULT_LABELS: tuple[str, ...] = (
    "COMGEND",
    "C2 CEMGEND",
    "INSPECTEUR IT",
    "DIRECTEUR DOE",
    "DIRECTEUR DPF",
    "DIRECTEUR DSF",
    "DIRECTEUR DIRTRANS",
    "DIRECTEUR DTRJD",
    "DIRECTEUR DIRMAT",
    "DIRECTEUR DDT",
)

# Anciens libellés carrière → libellé ref_fonction cible (normalisation).
LEGACY_LABEL_ALIASES: dict[str, str] = {
    "directeur it": "INSPECTEUR IT",
    "directeur deo": "DIRECTEUR DOE",
}


def _norm_fonction(s: str | None) -> str:
    return " ".join((s or "").strip().split()).casefold()


def _fonctions_by_id(settings: Settings) -> dict[int, FonctionRefRow]:
    return {f.id: f for f in FonctionsRepository(settings).fetch_all()}


def _index_by_label(fonctions: dict[int, FonctionRefRow]) -> dict[str, int]:
    out: dict[str, int] = {}
    for fid, row in fonctions.items():
        key = _norm_fonction(row.libelle)
        if key and key not in out:
            out[key] = fid
    return out


def load_fonction_priority_config(settings: Settings) -> FonctionPriorityConfig:
    from sme_gend.services.shared_config_write import load_shared_model
    return load_shared_model(settings,KEY_FONCTION_PRIORITY,FonctionPriorityConfig.from_dict,initialize=False)


def save_fonction_priority_config(
    settings: Settings,
    cfg: FonctionPriorityConfig,
    *,
    updated_by: str | None = None,
) -> None:
    from sme_gend.services.shared_config_write import save_shared_model
    ids=[int(value) for value in cfg.fonction_ids]
    if len(ids)!=len(set(ids)) or any(value<=0 for value in ids):
        raise ValueError("Chaque fonction prioritaire doit avoir un identifiant positif et unique.")
    def validate(cursor):
        if not ids:return
        cursor.execute("SELECT id FROM ref_fonction WHERE id IN ("+','.join(['%s']*len(ids))+") FOR SHARE",tuple(ids))
        if {int(row[0]) for row in cursor.fetchall()}!=set(ids):
            raise ValueError("Une fonction choisie a été supprimée. Rechargez le catalogue avant d'enregistrer.")
    save_shared_model(settings,KEY_FONCTION_PRIORITY,cfg,updated_by=updated_by,validate=validate)


def bootstrap_default_config(settings: Settings) -> FonctionPriorityConfig:
    """Construit la config initiale en résolvant les libellés legacy vers ref_fonction."""
    by_label = _index_by_label(_fonctions_by_id(settings))
    ids: list[int] = []
    seen: set[int] = set()
    for label in LEGACY_DEFAULT_LABELS:
        fid = by_label.get(_norm_fonction(label))
        if fid is not None and fid not in seen:
            ids.append(fid)
            seen.add(fid)
    return FonctionPriorityConfig(fonction_ids=ids)


def ensure_fonction_priority_config(settings: Settings) -> FonctionPriorityConfig:
    """Charge ou crée la configuration (bootstrap legacy → ref_fonction)."""
    from sme_gend.repositories.reference_collection_guard import fingerprint
    from sme_gend.repositories.reference_write_guard import ReferenceConflictError
    from sme_gend.services.shared_config_write import copy_config_version
    cfg = load_fonction_priority_config(settings)
    # An explicitly saved empty list is a real preference, not a missing key.
    if cfg.write_token!=fingerprint(None):return cfg
    initial=copy_config_version(cfg,bootstrap_default_config(settings))
    from sme_gend.services.auth_guard import session_has_permission
    if initial.fonction_ids and session_has_permission("referentiels.fonctions"):
        try:save_fonction_priority_config(settings,initial,updated_by="bootstrap")
        except ReferenceConflictError:return load_fonction_priority_config(settings)
    return initial


def prune_fonction_from_priority(settings: Settings, fonction_id: int) -> None:
    from sme_gend.services.shared_config_write import save_shared_model
    from sme_gend.services.shared_reference_write import shared_reference_write
    with shared_reference_write(settings):
        cfg = load_fonction_priority_config(settings)
        fid = int(fonction_id)
        if fid not in cfg.fonction_ids:return
        cfg.fonction_ids = [x for x in cfg.fonction_ids if x != fid]
        # Removal cannot introduce another orphan; retain the historical policy
        # of ignoring other old orphan ids at display rather than rewriting them.
        save_shared_model(settings,KEY_FONCTION_PRIORITY,cfg)


def resolve_ordered_labels(
    settings: Settings,
    cfg: FonctionPriorityConfig | None = None,
) -> list[str]:
    """Libellés ref_fonction dans l'ordre configuré (ignore les id orphelins)."""
    if cfg is None:
        cfg = ensure_fonction_priority_config(settings)
    by_id = _fonctions_by_id(settings)
    out: list[str] = []
    for fid in cfg.fonction_ids:
        row = by_id.get(int(fid))
        if row and row.libelle:
            out.append(row.libelle.strip())
    return out


def build_priority_index(settings: Settings) -> dict[str, int]:
    """
    Mappe un libellé carrière normalisé → rang prioritaire (0 = tête de liste).

    Utilise ref_fonction via les id configurés + alias legacy.
    """
    cfg = ensure_fonction_priority_config(settings)
    by_id = _fonctions_by_id(settings)
    index: dict[str, int] = {}

    for rank, fid in enumerate(cfg.fonction_ids):
        row = by_id.get(int(fid))
        if not row:
            continue
        key = _norm_fonction(row.libelle)
        if key:
            index[key] = rank

    for alias, target in LEGACY_LABEL_ALIASES.items():
        target_id = None
        for fid in cfg.fonction_ids:
            row = by_id.get(int(fid))
            if row and _norm_fonction(row.libelle) == _norm_fonction(target):
                target_id = int(fid)
                break
        if target_id is None:
            continue
        try:
            rank = cfg.fonction_ids.index(target_id)
        except ValueError:
            continue
        index[_norm_fonction(alias)] = rank

    return index


def footer_hint(settings: Settings) -> str:
    labels = resolve_ordered_labels(settings)
    if not labels:
        return (
            "Ordre par défaut : aucune fonction prioritaire configurée "
            "(Référentiels → Fonctions → Ordre liste générale). "
            "Tri par grade (PrefOrd), puis nom. "
            "Cliquez sur un en-tête pour trier — réactualisez la liste pour retrouver l'ordre métier."
        )
    if len(labels) <= 8:
        chain = " → ".join(labels)
    else:
        chain = " → ".join(labels[:6]) + f" → … (+{len(labels) - 6})"
    return (
        f"Ordre par défaut : {chain}, puis toutes les autres fonctions par grade (PrefOrd). "
        "Cliquez sur un en-tête pour trier — réactualisez la liste pour retrouver l'ordre métier "
        "(Référentiels → Fonctions → Ordre liste générale)."
    )
