"""Clôture mensuelle SME — Piste A : snapshots locaux + mise à jour TabSme + sme_context."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sme_gend.config.settings import Settings, liste_layout_config_path
from sme_gend.config.sme_context import load_sme_context, save_sme_context
from sme_gend.config.user_paths import resolved_sme_archives_directory
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
    """Extrait ``YYYY-MM`` si présent dans la chaîne."""
    t = (s or "").strip()
    m = re.match(r"^(\d{4})-(\d{2})$", t)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m2 = re.search(r"(\d{4})-(\d{2})", t)
    if m2:
        return f"{m2.group(1)}-{m2.group(2)}"
    return None


def canonical_sme_mois_yyyy_mm(s: str) -> str | None:
    """
    Normalise un libellé de période SME vers ``YYYY-MM``.

    Gère notamment les formats issus d’Access sans séparateur :

    - ``YYYYMM`` (ex. ``202604`` → avril 2026) ;
    - ``MMYYYY`` (ex. ``042026`` ou ``012026``), courant dans d’anciennes tables TabSme.
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
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


@dataclass(slots=True)
class SmeMonthClosureResult:
    warnings: list[str]
    sqlite_archive: Path | None
    layout_archive: Path | None
    tabsme_rows_closed: int
    tabsme_rows_opened: int
    tabsme_inserted_suivant: bool


def _snapshot_file(src: Path, dest_dir: Path, base_name: str) -> Path | None:
    if not src.is_file():
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{base_name}_{_archive_stamp()}{src.suffix}"
    shutil.copy2(src, dest)
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
            "Table TabSme absente ou inaccessible : les statuts de période n'ont pas été modifiés."
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

    rows_after = repo.fetch_all()
    for r in rows_after:
        if mois_labels_equivalent(r.mois, mois_suivant):
            repo.update(r.codsme, r.mois, "Ouvert")
            n_open += 1

    if n_closed == 0:
        warnings.append(
            f"Aucune ligne TabSme dont le mois correspond à « {mois_cloture} » "
            f"(après normalisation YYYY-MM / YYYYMM / MMYYYY). "
            f"Vérifiez qu’une période pour ce mois existe bien dans TabSme, ou créez-la dans Access."
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
                    f"Aucune période « mois suivant » dans TabSme et création automatique échouée "
                    f"({code}) : {e}"
                )
        else:
            warnings.append(
                f"Aucune ligne TabSme pour le mois suivant « {mois_suivant} » et impossible "
                "d'en créer une automatiquement (indiquez au moins YYYY-MM, ex. 2026-05)."
            )

    return warnings, n_closed, n_open, inserted


def run_piste_a_month_closure(
    settings: Settings,
    mois_cloture: str,
    mois_suivant: str,
) -> SmeMonthClosureResult:
    """
    Piste A :
    - Copie du fichier SQLite bundle (si présent) vers ``sme_archives/``.
    - Copie de ``liste_present_layout.json`` (si présent).
    - TabSme : statut Fermé pour le mois clôturé, Ouvert pour le mois suivant
      (insertion ``SME-YYYYMM`` + Ouvert si aucune ligne ne correspond au suivant mais YYYY-MM parsable).
    - ``sme_context.json`` : ajout du mois clôturé, nouveau mois actif.
    - Ligne d'audit ``sme_month_closure_piste_a``.
    """
    ym_cloture = extract_yyyy_mm(mois_cloture)
    ym_suivant = extract_yyyy_mm(mois_suivant)
    if not ym_cloture:
        raise ValueError(f"mois_cloture invalide — format YYYY-MM attendu : {mois_cloture!r}")
    if not ym_suivant:
        raise ValueError(f"mois_suivant invalide — format YYYY-MM attendu : {mois_suivant!r}")

    warnings: list[str] = []
    sqlite_archive: Path | None = None
    layout_archive: Path | None = None

    arch = sme_archives_dir()
    slug = slugify_mois_label(mois_cloture)

    sql_path = settings.sqlite_liste_path
    if sql_path is not None:
        p = Path(sql_path).expanduser().resolve()
        if p.is_file():
            try:
                sqlite_archive = _snapshot_file(p, arch, f"sme_bundle_{slug}")
            except OSError as e:
                warnings.append(f"Copie SQLite : {e}")
        elif settings.use_sqlite_liste_generale:
            warnings.append(
                f"Mode SQLite activé mais fichier introuvable : {p} — aucun snapshot bundle."
            )
    elif settings.use_sqlite_liste_generale:
        warnings.append("Chemin SQLite non défini — aucun snapshot bundle.")

    lay = liste_layout_config_path()
    if lay.is_file():
        try:
            layout_archive = _snapshot_file(lay, arch, f"liste_present_layout_{slug}")
        except OSError as e:
            warnings.append(f"Copie mise en page liste : {e}")

    tw, n_closed, n_open, ins = _apply_tabsme_closure(settings, mois_cloture, mois_suivant)
    warnings.extend(tw)

    ctx = load_sme_context()
    if ym_cloture not in ctx.mois_fermes:
        ctx.mois_fermes.append(ym_cloture)
    ctx.mois_actif = ym_suivant
    save_sme_context(ctx)

    append_audit_event(
        action="sme_month_closure_piste_a",
        sme_mois_context=mois_suivant,
        mois_cloture=mois_cloture,
        mois_suivant=mois_suivant,
        sqlite_archive=str(sqlite_archive) if sqlite_archive else None,
        layout_archive=str(layout_archive) if layout_archive else None,
        tabsme_rows_closed=n_closed,
        tabsme_rows_opened=n_open,
        tabsme_auto_insert=ins,
        warnings=warnings,
    )

    return SmeMonthClosureResult(
        warnings=warnings,
        sqlite_archive=sqlite_archive,
        layout_archive=layout_archive,
        tabsme_rows_closed=n_closed,
        tabsme_rows_opened=n_open,
        tabsme_inserted_suivant=ins,
    )


def import_existing_db_for_month(
    settings: Settings,
    source_path: Path,
    mois: str,
    mois_suivant: str | None = None,
) -> SmeMonthClosureResult:
    """
    Importe un fichier de base existant (SQLite ou Access) comme archive d'un mois passé.

    - Copie ``source_path`` vers ``sme_archives/`` avec la convention de nommage standard.
    - TabSme : marque ``mois`` comme Fermé ; ouvre ``mois_suivant`` si fourni.
    - ``sme_context.json`` : ajoute ``mois`` à ``mois_fermes`` ;
      met à jour ``mois_actif`` vers ``mois_suivant`` si fourni.
    - Audit : événement ``sme_month_import_existing_db``.
    """
    warnings: list[str] = []
    sqlite_archive: Path | None = None

    arch = sme_archives_dir()
    slug = slugify_mois_label(mois)

    if not source_path.is_file():
        warnings.append(f"Fichier source introuvable : {source_path}")
    else:
        dest = arch / f"sme_bundle_{slug}_{_archive_stamp()}{source_path.suffix}"
        try:
            shutil.copy2(source_path, dest)
            sqlite_archive = dest
        except OSError as e:
            warnings.append(f"Copie du fichier vers les archives : {e}")

    tw, n_closed, n_open, ins = _apply_tabsme_closure(
        settings, mois, mois_suivant or ""
    )
    warnings.extend(tw)

    ctx = load_sme_context()
    if mois not in ctx.mois_fermes:
        ctx.mois_fermes.append(mois)
    if mois_suivant:
        ctx.mois_actif = mois_suivant
    save_sme_context(ctx)

    append_audit_event(
        action="sme_month_import_existing_db",
        sme_mois_context=mois_suivant or mois,
        mois_importe=mois,
        mois_suivant=mois_suivant or "",
        source_file=str(source_path),
        sqlite_archive=str(sqlite_archive) if sqlite_archive else None,
        tabsme_rows_closed=n_closed,
        tabsme_rows_opened=n_open,
        tabsme_auto_insert=ins,
        warnings=warnings,
    )

    return SmeMonthClosureResult(
        warnings=warnings,
        sqlite_archive=sqlite_archive,
        layout_archive=None,
        tabsme_rows_closed=n_closed,
        tabsme_rows_opened=n_open,
        tabsme_inserted_suivant=ins,
    )


def find_archive_for_month(mois: str) -> Path | None:
    """Retourne l'archive la plus récente pour le mois YYYY-MM dans sme_archives/, ou None."""
    ym = canonical_sme_mois_yyyy_mm(mois) or mois
    slug = slugify_mois_label(ym)
    arch_dir = sme_archives_dir()
    candidates: list[Path] = []
    for ext in ("sqlite", "db", "mdb", "accdb"):
        candidates.extend(arch_dir.glob(f"sme_bundle_{slug}_*.{ext}"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)
