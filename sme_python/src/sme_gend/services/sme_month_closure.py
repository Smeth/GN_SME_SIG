"""Clôture SME : sauvegarde préalable et transition SQL transactionnelle."""

from __future__ import annotations

import re
import shutil
from uuid import uuid4
from sme_gend.services.safe_paths import atomic_destination
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import json

from sme_gend.config.settings import Settings
from sme_gend.config.user_paths import resolved_sme_archives_directory
from sme_gend.services.app_config_store import KEY_LISTE_LAYOUT, load_config_dict
from sme_gend.repositories.sme_repository import SmeRepository
from sme_gend.services.audit_log import append_audit_event


def sme_archives_dir() -> Path:
    d = resolved_sme_archives_directory()
    d.mkdir(parents=True, exist_ok=True)
    return d


def slugify_mois_label(label: str) -> str:
    """Fragment de nom de fichier (sans extension)."""
    s = re.sub(r"[^\w.\-]+", "_", (label or "").strip(), flags=re.UNICODE)
    s = s.strip("._-") or "mois"
    return s[:80]


def extract_yyyy_mm(s: str) -> str | None:
    """Extrait un véritable mois ``YYYY-MM`` présent dans la chaîne."""
    t = (s or "").strip()
    for match in re.finditer(r"(?<![0-9])([0-9]{4})-([0-9]{2})(?![0-9])", t):
        year, month = int(match.group(1)), int(match.group(2))
        if 1 <= year <= 9999 and 1 <= month <= 12:
            return f"{year:04d}-{month:02d}"
    return None


def canonical_sme_mois_yyyy_mm(s: str) -> str | None:
    """
    Normalise un libellé de période SME vers ``YYYY-MM``.

    Gère notamment les formats issus d’Access sans séparateur :

    - ``YYYYMM`` (ex. ``202604`` → avril 2026) ;
    - ``MMYYYY`` (ex. ``042026`` ou ``012026``), courant dans d’anciennes tables sme_periode.
    """
    t = (s or "").strip()
    ex = extract_yyyy_mm(t)
    if ex:
        return ex
    digits = re.sub(r"\D", "", t)
    if len(digits) != 6:
        return None
    yyyy = int(digits[0:4])
    mm = int(digits[4:6])
    if 2000 <= yyyy <= 2100 and 1 <= mm <= 12:
        return f"{yyyy:04d}-{mm:02d}"
    mm2 = int(digits[0:2])
    yyyy2 = int(digits[2:6])
    if 1 <= mm2 <= 12 and 2000 <= yyyy2 <= 2100:
        return f"{yyyy2:04d}-{mm2:02d}"
    return None


def mois_labels_equivalent(a: str, b: str) -> bool:
    """Compare deux libellés de mois (``YYYY-MM``, ``YYYYMM``, ``MMYYYY``, ou égalité textuelle)."""
    aa = canonical_sme_mois_yyyy_mm(a)
    bb = canonical_sme_mois_yyyy_mm(b)
    if aa and bb:
        return aa == bb
    return (a or "").strip().casefold() == (b or "").strip().casefold()


def _archive_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "_" + uuid4().hex[:12]


@dataclass(slots=True)
class SmeMonthClosureResult:
    warnings: list[str]
    data_archive: Path | None
    layout_archive: Path | None
    tabsme_rows_closed: int
    tabsme_rows_opened: int
    tabsme_inserted_suivant: bool
    mysql_archive: Path | None = None
    mysql_bundle_result: dict[str, Any] | None = None


def _snapshot_file(src: Path, dest_dir: Path, base_name: str) -> Path | None:
    if not src.is_file():
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{base_name}_{_archive_stamp()}{src.suffix}"
    with atomic_destination(dest, replace_existing=False) as temporary:
        shutil.copy2(src, temporary)
    return dest


def _snapshot_layout_from_central(settings: Settings, dest_dir: Path, slug: str) -> Path | None:
    """Archive la mise en page depuis MySQL (``app_config``)."""
    payload = load_config_dict(settings, KEY_LISTE_LAYOUT)
    if not isinstance(payload, dict) or not payload:
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"liste_present_layout_{slug}_{_archive_stamp()}.json"
    with atomic_destination(dest, replace_existing=False) as temporary:
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return dest


def _apply_tabsme_closure(
    settings: Settings,
    mois_cloture: str,
    mois_suivant: str,
) -> tuple[list[str], int, int, bool]:
    warnings: list[str] = []
    repo = SmeRepository(settings)
    if not repo.table_exists():
        warnings.append(
            "Table sme_periode absente ou inaccessible : les statuts de période n'ont pas été modifiés."
        )
        return warnings, 0, 0, False

    rows = repo.fetch_all()
    n_closed = 0
    n_open = 0
    inserted = False

    for r in rows:
        if mois_labels_equivalent(r.mois, mois_cloture):
            repo.update(r.codsme, r.mois, "Fermé")
            n_closed += 1

    if not mois_suivant:
        return warnings, n_closed, 0, False

    rows_after = repo.fetch_all()
    for r in rows_after:
        if mois_labels_equivalent(r.mois, mois_suivant):
            repo.update(r.codsme, r.mois, "Ouvert")
            n_open += 1

    if n_closed == 0:
        warnings.append(
            f"Aucune ligne sme_periode dont le mois correspond à « {mois_cloture} » "
            f"(après normalisation YYYY-MM / YYYYMM / MMYYYY). "
            f"Vérifiez qu’une période pour ce mois existe bien dans sme_periode "
            f"(Gestion des mois et archives ou import bundle)."
        )

    if n_open == 0:
        ym = extract_yyyy_mm(mois_suivant)
        if ym:
            code = f"SME-{ym.replace('-', '')}"
            try:
                repo.insert(code, ym, "Ouvert")
                n_open = 1
                inserted = True
            except Exception as e:
                warnings.append(
                    f"Aucune période « mois suivant » dans sme_periode et création automatique échouée "
                    f"({code}) : {e}"
                )
        else:
            warnings.append(
                f"Aucune ligne sme_periode pour le mois suivant « {mois_suivant} » et impossible "
                "d'en créer une automatiquement (indiquez au moins YYYY-MM, ex. 2026-05)."
            )

    return warnings, n_closed, n_open, inserted


def _validated_closure_months(source: str, target: str | None) -> tuple[str, str | None]:
    def validate(value):
        month = canonical_sme_mois_yyyy_mm(value or "")
        if not month:
            raise ValueError("Mois de clôture invalide : format YYYY-MM attendu.")
        try:
            datetime.strptime(month + "-01", "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("Mois de clôture invalide dans le calendrier.") from exc
        return month
    current = validate(source)
    following = validate(target) if target else None
    if following and following <= current:
        raise ValueError("Le mois à activer doit être postérieur au mois clôturé.")
    return current, following


def _commit_closure_transition(settings, source, target, *, action, audit_payload, strict_source=False, expected=None):
    """One owner for statuses, active career, shared revision and audit.

    No local UI preference or legacy context is written to promote a month:
    sme_periode is already the authoritative state for every workstation.
    """
    from sme_gend.services.action_permissions import assert_any_permission
    assert_any_permission("workflow.close_month")
    from sme_gend.repositories.mysql_connection import MySQLConnection, mysql_import_transaction
    from sme_gend.services.active_month_guard import period_state_write,check_month_snapshot
    from sme_gend.repositories.reference_write_guard import ReferenceConflictError
    from sme_gend.services.sme_workspace_service import SmeWorkspaceService, _is_ouvert, _is_reouvert, _is_ferme_strict
    from sme_gend.services.mysql_import_period import refresh_est_actuelle_flags, refresh_carriere_segment_dates
    from sme_gend.services.data_sync import publish_data_change
    from sme_gend.services.liste_generale_cache import invalidate_liste_cache
    from sme_gend.services.sme_editing_policy import invalidate_editing_state_cache

    source, target = _validated_closure_months(source, target)
    try:
        with period_state_write(settings,reason="month_closure",mois=target or source) as change:
            with MySQLConnection(settings).connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT id,annee_mois,statut FROM sme_periode ORDER BY id FOR UPDATE")
                    rows = cur.fetchall()
                matching = [row for row in rows if mois_labels_equivalent(str(row[1]), source)]
                if not matching:
                    raise ValueError("Clôture annulée : la période à clôturer n'existe pas.")
                opened = {canonical_sme_mois_yyyy_mm(str(row[1])) for row in rows if _is_ouvert(row[2])}
                if expected is not None:
                    try:
                        check_month_snapshot(settings,expected,change.before)
                    except ReferenceConflictError:
                        # Concurrent identical closure is idempotent, not a new
                        # transition. A returned-to-source ABA is still refused.
                        if target in opened and len(opened)==1 and all(_is_ferme_strict(row[2]) for row in matching):
                            return ["Cette clôture a déjà été appliquée ; aucune nouvelle bascule effectuée."],0,0,False
                        raise
                if strict_source and opened and source not in opened:
                    if target in opened and len(opened) == 1 and all(_is_ferme_strict(row[2]) for row in matching):
                        return ["Cette clôture a déjà été appliquée ; aucune nouvelle bascule effectuée."], 0, 0, False
                    if not (target in opened and any(_is_reouvert(row[2]) for row in matching)):
                        raise ValueError("Clôture annulée : le mois actif a changé. Rechargez la période.")

                warnings, n_closed, n_open, inserted = _apply_tabsme_closure(settings, source, target or "")
                if n_closed == 0 or (target and n_open == 0):
                    raise RuntimeError("Clôture annulée : tous les statuts n'ont pas pu être appliqués.")
                if target:
                    SmeWorkspaceService(settings).close_month_transition(source, target)
                else:
                    # Archive-and-close without activation must not reopen the
                    # source month via a stale local SmeContext.
                    remaining = sorted(month for month in opened if month and month != source)
                    refresh_est_actuelle_flags(conn, remaining[-1] if remaining else source)
                    refresh_carriere_segment_dates(conn)
                append_audit_event(action=action, sme_mois_context=target or source,
                    **audit_payload, tabsme_rows_closed=n_closed, tabsme_rows_opened=n_open,
                    tabsme_auto_insert=inserted, warnings=warnings)
                from sme_gend.services.shared_reference_write import mark_shared_data_write
                mark_shared_data_write(True)
        return warnings, n_closed, n_open, inserted
    finally:
        invalidate_liste_cache(settings)
        invalidate_editing_state_cache()


def run_piste_a_month_closure(
    settings: Settings,
    mois_cloture: str,
    mois_suivant: str,
    *, expected=None, precheck_report=None,
) -> SmeMonthClosureResult:
    """
    Piste A :
    - Copie du fichier Access (.mdb) si configuré vers ``sme_archives/``.
    - Copie de ``liste_present_layout.json`` (si présent).
    - sme_periode : statut Fermé pour le mois clôturé, Ouvert pour le mois suivant
      (insertion ``SME-YYYYMM`` + Ouvert si aucune ligne ne correspond au suivant mais YYYY-MM parsable).
    - Mois actif et mois fermés relus depuis MySQL, sans promotion via un contexte local.
    - Audit et révision partagée validés dans la même transaction que la clôture.
    """
    from sme_gend.services.action_permissions import assert_any_permission
    assert_any_permission("workflow.close_month")
    ym_cloture, ym_suivant = _validated_closure_months(mois_cloture, mois_suivant)
    if ym_suivant is None:
        raise ValueError("Le mois suivant est requis pour cette clôture.")

    from sme_gend.services.active_month_guard import load_active_month_snapshot
    if expected is None:
        expected = load_active_month_snapshot(settings)
    warnings: list[str] = []
    data_archive: Path | None = None
    layout_archive: Path | None = None

    arch = sme_archives_dir()
    slug = slugify_mois_label(mois_cloture)

    mdb_path = settings.database_path
    if mdb_path is not None:
        p = Path(mdb_path).expanduser().resolve()
        if p.is_file() and p.suffix.lower() in (".mdb", ".accdb"):
            try:
                data_archive = _snapshot_file(p, arch, f"sme_bundle_{slug}")
            except OSError as e:
                warnings.append(f"Copie Access (.mdb) : {e}")
        elif not p.is_file():
            warnings.append(
                f"Fichier Access introuvable pour snapshot : {p}"
            )

    try:
        layout_archive = _snapshot_layout_from_central(settings, arch, slug)
    except OSError as e:
        warnings.append(f"Archive mise en page (MySQL) : {e}")

    mysql_archive: Path | None = None
    try:
        from sme_gend.services.mysql_backup_service import backup_mysql_database

        mysql_archive, mysql_warn = backup_mysql_database(settings, arch, slug)
        warnings.extend(mysql_warn)
        if mysql_archive is None or not mysql_archive.is_file() or mysql_archive.stat().st_size == 0:
            raise RuntimeError("Aucune sauvegarde MySQL valide n'a été créée.")
    except Exception as exc:
        raise RuntimeError(
            "Clôture annulée : la sauvegarde MySQL préalable a échoué. "
            "Vérifiez la connexion, les droits et l'espace du dossier de sauvegarde."
        ) from exc

    tw, n_closed, n_open, ins = _commit_closure_transition(
        settings, ym_cloture, ym_suivant, strict_source=True, expected=expected,
        action="sme_month_closure_piste_a",
        audit_payload={
            "mois_cloture": ym_cloture, "mois_suivant": ym_suivant,
            "data_archive": str(data_archive) if data_archive else None,
            "layout_archive": str(layout_archive) if layout_archive else None,
            "mysql_archive": str(mysql_archive),
            "precheck_report": precheck_report,
        },
    )
    warnings.extend(tw)

    return SmeMonthClosureResult(
        warnings=warnings,
        data_archive=data_archive,
        layout_archive=layout_archive,
        tabsme_rows_closed=n_closed,
        tabsme_rows_opened=n_open,
        tabsme_inserted_suivant=ins,
        mysql_archive=mysql_archive,
    )


def import_existing_db_for_month(
    settings: Settings,
    source_path: Path,
    mois: str,
    mois_suivant: str | None = None,
    *,
    import_to_mysql: bool = True,
    archive_and_close: bool = True,
    progress: Callable[[str], None] | None = None,
    preserve_manual: bool = True,
    replacement_preview=None,
) -> SmeMonthClosureResult:
    """
    Importe un fichier Access (.mdb / .accdb) pour un mois donné.

    Si ``archive_and_close`` (par défaut) :
    - Copie ``source_path`` vers ``sme_archives/`` avec la convention de nommage standard.
    - sme_periode : marque ``mois`` comme Fermé ; ouvre ``mois_suivant`` si fourni.
    - Mois fermés et mois actif déterminés par les statuts MySQL.
    - Sans activation, le mois archivé reste fermé ; aucune préférence locale ne le rouvre.
    Si ``archive_and_close`` est False : import MySQL seul, aucune copie ni clôture
    (équivalent à un import « ponctuel » pour consulter/compléter un mois).

    - Charge liste générale, carrière, référentiels et satellites dans MySQL (ODBC).
    - Audit : événement ``sme_month_import_existing_db`` (toujours émis).
    """
    from sme_gend.services.action_permissions import assert_any_permission
    assert_any_permission("workflow.import_archive")
    if archive_and_close:
        assert_any_permission("workflow.close_month")
    warnings: list[str] = []
    data_archive: Path | None = None
    n_closed = n_open = 0
    ins = False
    mysql_bundle_result: dict[str, Any] | None = None
    from sme_gend.services.active_month_guard import load_active_month_snapshot
    transition_snapshot = load_active_month_snapshot(settings) if archive_and_close else None
    imported_snapshot = []

    if not archive_and_close:
        mois_suivant = None
    else:
        _validated_closure_months(mois, mois_suivant)

    # Import MySQL AVANT la clôture / bascule de mois_actif.
    # Sinon, si « mois suivant » est activé d'abord, l'import du mois choisi
    # est classé « historique » et n'aligne pas est_actuelle — effectifs incohérents.
    if import_to_mysql:
        ym_import = canonical_sme_mois_yyyy_mm(mois) or extract_yyyy_mm(mois)
        if not ym_import:
            warnings.append(f"Mois invalide pour import MySQL : {mois!r}")
        elif not source_path.is_file():
            warnings.append("Import MySQL ignoré : fichier source introuvable.")
        else:
            import dataclasses

            from sme_gend.services.mysql_bundle_import import import_full_mysql_bundle_from_access

            access_st = dataclasses.replace(
                settings,
                database_path=source_path.expanduser().resolve(),
            )
            mysql_bundle_result = import_full_mysql_bundle_from_access(
                access_st,
                settings,
                mois=ym_import,
                progress=progress,
                on_month_committed=imported_snapshot.append,
                **({"preserve_manual": preserve_manual, "replacement_preview": replacement_preview}
                   if replacement_preview is not None or not preserve_manual else {}),
            )
            if imported_snapshot:
                transition_snapshot = imported_snapshot[-1]
            ref_warn = (mysql_bundle_result.get("referentials") or {}).get("warnings") or []
            for w in ref_warn:
                if w and w not in warnings:
                    warnings.append(str(w))

    if archive_and_close:
        if progress:
            progress("Archivage du fichier (sme_archives)…")
        arch = sme_archives_dir()
        slug = slugify_mois_label(mois)

        if not source_path.is_file():
            warnings.append(f"Fichier source introuvable : {source_path}")
        else:
            try:
                data_archive = _snapshot_file(source_path, arch, f"sme_bundle_{slug}")
            except OSError as e:
                warnings.append(f"Copie du fichier vers les archives : {e}")

        if progress:
            progress("Clôture du mois / activation du mois suivant…")
        from sme_gend.repositories.reference_write_guard import ReferenceConflictError
        try:
            tw, n_closed, n_open, ins = _commit_closure_transition(
                settings, mois, mois_suivant, expected=transition_snapshot, action="sme_month_import_existing_db",
                audit_payload={"mois_importe": mois, "mois_suivant": mois_suivant or "",
                    "source_file": str(source_path),
                    "data_archive": str(data_archive) if data_archive else None,
                    "mysql_bundle": mysql_bundle_result},
            )
        except (ReferenceConflictError, PermissionError) as exc:
            if mysql_bundle_result is not None:
                cause = "vos droits ont changé" if isinstance(exc, PermissionError) else "les périodes ont changé"
                raise type(exc)(
                    "L'import MySQL est enregistré, mais la clôture ou l'activation est refusée : "
                    f"{cause}. Rechargez et vérifiez le mois actif avant de clôturer. "
                    "Ne relancez pas automatiquement l'import."
                ) from exc
            raise
        warnings.extend(tw)
    else:
        append_audit_event(action="sme_month_import_existing_db", sme_mois_context=mois,
            mois_importe=mois, mois_suivant="", source_file=str(source_path),
            data_archive=None, tabsme_rows_closed=0, tabsme_rows_opened=0,
            tabsme_auto_insert=False, warnings=warnings, mysql_bundle=mysql_bundle_result)

    return SmeMonthClosureResult(
        warnings=warnings,
        data_archive=data_archive,
        layout_archive=None,
        tabsme_rows_closed=n_closed,
        tabsme_rows_opened=n_open,
        tabsme_inserted_suivant=ins,
        mysql_bundle_result=mysql_bundle_result,
    )


def archive_slug_candidates_for_month(mois: str) -> list[str]:
    """
    Fragments de nom utilisés après ``sme_bundle_`` à la clôture / import.

    Selon la saisie, le mois peut produire un slug ``2026-03`` ou ``202603`` : les variantes
    pertinentes sont essayées pour ``sme_bundle_<slug>_*.mdb``.
    """
    raw = (mois or "").strip()
    if not raw:
        return []

    candidates: set[str] = set()
    canon = canonical_sme_mois_yyyy_mm(raw) or ""
    ex = extract_yyyy_mm(raw) or ""

    for piece in (raw, canon, ex):
        p = (piece or "").strip()
        if not p:
            continue
        candidates.add(slugify_mois_label(p))
        norm = canonical_sme_mois_yyyy_mm(p) or extract_yyyy_mm(p)
        if norm:
            compact = norm.replace("-", "")
            if compact:
                candidates.add(slugify_mois_label(compact))

    return sorted(candidates)


def find_archive_for_month(mois: str) -> Path | None:
    """Retourne l'archive la plus récente pour le mois dans sme_archives/, ou None."""
    arch_dir = sme_archives_dir()
    slugs = archive_slug_candidates_for_month(mois)
    if not slugs:
        return None
    found: set[Path] = set()
    for slug in slugs:
        for ext in ("mdb", "accdb"):
            for p in arch_dir.glob(f"sme_bundle_{slug}_*.{ext}"):
                try:
                    found.add(p.resolve())
                except OSError:
                    found.add(p)
    if not found:
        return None
    return max(found, key=lambda p: p.stat().st_mtime)


_ARCHIVE_STAMP_SUFFIX_RE = re.compile(r"_\d{8}T\d{6}(?:\d{6})?Z(?:_[0-9a-f]{12})?$", re.IGNORECASE)


def extract_mois_from_archive_filename(name: str) -> str | None:
    """Extrait ``YYYY-MM`` du nom ``sme_bundle_<slug>_<stamp>.mdb`` (ou variante)."""
    base = (name or "").strip()
    if not base.lower().startswith("sme_bundle_"):
        return None
    stem = base.rsplit(".", 1)[0]
    rest = stem[len("sme_bundle_") :]
    stamp_m = _ARCHIVE_STAMP_SUFFIX_RE.search(rest)
    slug = rest[: stamp_m.start()] if stamp_m else (rest.rsplit("_", 1)[0] if "_" in rest else rest)
    for candidate in (slug, base):
        ym = canonical_sme_mois_yyyy_mm(candidate) or extract_yyyy_mm(candidate)
        if ym:
            return ym
    m = re.search(r"(\d{4})[-_](\d{2})", base)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return None


def list_months_from_archives() -> list[str]:
    """Mois SME déduits des fichiers ``sme_bundle_*`` dans ``sme_archives/``."""
    arch_dir = sme_archives_dir()
    seen: set[str] = set()
    try:
        if not arch_dir.is_dir():
            return []
        for ext in ("mdb", "accdb"):
            for p in arch_dir.glob(f"sme_bundle_*.{ext}"):
                ym = extract_mois_from_archive_filename(p.name)
                if ym:
                    seen.add(ym)
    except OSError:
        pass
    return sorted(seen)


def liste_generale_settings_for_archive(base: Settings, archive_path: Path) -> Settings:
    """Paramètres pour lecture d’une archive : import Access → MySQL puis base courante."""
    import dataclasses

    archive_path = Path(archive_path).expanduser().resolve()
    ext = archive_path.suffix.lower()
    if ext in (".mdb", ".accdb"):
        return dataclasses.replace(base, database_path=archive_path)
    return base


def shift_sme_month_yyyy_mm(ym_raw: str, *, delta_months: int) -> str | None:
    """Retourne YYYY-MM décalé de ``delta_months`` (ex. -1 = mois précédent), ou None si invalide."""
    ym = canonical_sme_mois_yyyy_mm(ym_raw)
    if not ym:
        return None
    y = int(ym[:4])
    m = int(ym[5:7])
    m += delta_months
    while m < 1:
        m += 12
        y -= 1
    while m > 12:
        m -= 12
        y += 1
    return f"{y:04d}-{m:02d}"
