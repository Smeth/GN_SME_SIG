"""Diff rétroactif entre bases — injection d'entrées d'audit par personne."""

from __future__ import annotations

import calendar
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.audit_log import (
    _all_audit_files,
    append_personnel_audit,
    diff_personnel_rows,
)
from sme_gend.services.personnel_dossier_history import PARCOURS_FIELD_NAMES

_ACCESS_DRIVER = "Microsoft Access Driver (*.mdb, *.accdb)"

_ACCESS_SELECT = """
    SELECT Id, noms, DateN, LieuN, Grade, DateGrade, DateService, Ndecision,
           Matmilitaire, Matsolde, Padmin, Fonction, SubArme, Diplome, DateObtention,
           Structure, Obs, OderTri, presseance
    FROM ListeGenerale
"""


# ------------------------------------------------------------------ helpers

def _last_day_ts(mois: str) -> str:
    """Horodatage ISO UTC du dernier jour du mois (ex. 2026-02-28T23:59:59+00:00)."""
    y, m = int(mois[:4]), int(mois[5:7])
    last = calendar.monthrange(y, m)[1]
    return datetime(y, m, last, 23, 59, 59, tzinfo=timezone.utc).isoformat()


def _read_sqlite_rows(sqlite_path: Path) -> list[ListeGeneraleRow]:
    """Lecture directe de liste_generale depuis un .sqlite sans passer par les settings."""
    rows: list[ListeGeneraleRow] = []
    try:
        with sqlite3.connect(str(sqlite_path)) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("""
                SELECT id, noms, date_naissance, lieu_naissance, grade, date_grade,
                       date_service, ndecision, matricule_militaire, matricule_solde,
                       position_admin, fonction, subdivision_arme, diplome, date_obtention,
                       structure, obs, ordre_tri, presseance
                FROM liste_generale
            """)
            for r in cur.fetchall():
                rows.append(ListeGeneraleRow(
                    id=str(r["id"]),
                    noms=r["noms"],
                    date_naissance=r["date_naissance"],
                    lieu_naissance=r["lieu_naissance"],
                    grade=r["grade"],
                    date_grade=r["date_grade"],
                    date_service=r["date_service"],
                    ndecision=r["ndecision"],
                    matricule_militaire=r["matricule_militaire"],
                    matricule_solde=r["matricule_solde"],
                    position_admin=r["position_admin"],
                    fonction=r["fonction"],
                    subdivision_arme=r["subdivision_arme"],
                    diplome=r["diplome"],
                    date_obtention=r["date_obtention"],
                    structure=r["structure"],
                    obs=r["obs"],
                    ordre_tri=r["ordre_tri"],
                    presseance=r["presseance"],
                ))
    except sqlite3.DatabaseError as e:
        raise ValueError(f"Impossible de lire le fichier SQLite source : {e}") from e
    return rows


def _read_access_rows(mdb_path: Path) -> list[ListeGeneraleRow]:
    """Lecture directe de ListeGenerale depuis un .mdb/.accdb via pyodbc."""
    try:
        import pyodbc
    except ImportError as e:
        raise ValueError(
            "Le module pyodbc est requis pour lire un fichier Access (.mdb/.accdb). "
            "Installez-le ou convertissez d'abord le fichier en SQLite."
        ) from e

    conn_str = f"DRIVER={{{_ACCESS_DRIVER}}};DBQ={mdb_path};"
    try:
        conn = pyodbc.connect(conn_str, autocommit=True)
    except pyodbc.Error as e:
        raise ValueError(
            f"Impossible d'ouvrir le fichier Access « {mdb_path.name} » : {e}\n"
            "Vérifiez que le pilote ODBC Access est installé et que le fichier n'est pas "
            "déjà ouvert dans une autre application."
        ) from e

    try:
        cur = conn.cursor()
        cur.execute(_ACCESS_SELECT)
        return [ListeGeneraleRow.from_sequence(r) for r in cur.fetchall()]
    except pyodbc.Error as e:
        raise ValueError(f"Erreur de lecture Access : {e}") from e
    finally:
        conn.close()


def _read_file_rows(path: Path) -> list[ListeGeneraleRow]:
    """Lit ListeGenerale depuis n'importe quel fichier supporté (.sqlite, .db, .mdb, .accdb)."""
    suffix = path.suffix.lower()
    if suffix in (".mdb", ".accdb"):
        return _read_access_rows(path)
    if suffix in (".sqlite", ".db"):
        return _read_sqlite_rows(path)
    raise ValueError(
        f"Format non supporté : « {suffix} ». "
        "Utilisez un fichier .sqlite, .db, .mdb ou .accdb."
    )


def _read_current_rows(settings: Settings) -> list[ListeGeneraleRow]:
    from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
    return ListeGeneraleRepository(settings).fetch_all()


def _extract_archive_mois(filename: str) -> str | None:
    """Extrait le premier token YYYY-MM (ou YYYY_MM) d'un nom de fichier d'archive."""
    m = re.search(r"(\d{4})[-_](\d{2})", filename)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return None


def _mois_already_reconstructed(mois: str) -> bool:
    """
    Vérifie si ce mois a déjà été reconstruit via ``run_pairwise_archive_diff``
    (évite de créer des doublons en cas de double exécution).
    """
    for path in _all_audit_files():
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if (
                            entry.get("sme_mois_context") == mois
                            and entry.get("edited_by") == "Reconstruction archives"
                        ):
                            return True
                    except json.JSONDecodeError:
                        pass
        except OSError:
            pass
    return False


# ------------------------------------------------------------------ résultats

@dataclass(slots=True)
class RetroactiveDiffResult:
    persons_source: int
    persons_target: int
    persons_matched: int
    persons_with_changes: int
    audit_entries_created: int
    unmatched_source: list[str]
    warnings: list[str]

    def summary_lines(self, mois: str) -> list[str]:
        parts = [
            f"Mois analysé : {mois}",
            f"Personnes dans la source : {self.persons_source}",
            f"Personnes dans la base courante : {self.persons_target}",
            f"Matchées (par matricule militaire) : {self.persons_matched}",
            f"Avec au moins un changement : {self.persons_with_changes}",
            f"Entrées d'audit créées : {self.audit_entries_created}",
        ]
        if self.unmatched_source:
            n = len(self.unmatched_source)
            sample = ", ".join(self.unmatched_source[:5])
            parts.append(
                f"Non trouvées dans la base courante : {n}"
                + (f" (ex. : {sample}…)" if n > 5 else f" ({sample})")
            )
        if self.warnings:
            parts.append("Avertissements :\n• " + "\n• ".join(self.warnings))
        return parts


@dataclass
class PairwiseDiffResult:
    """Résultat d'un diff entre deux archives consécutives."""
    mois: str
    old_path: Path
    new_path: Path
    persons_matched: int
    persons_with_changes: int
    audit_entries_created: int
    skipped: bool = False
    error: str | None = None


@dataclass
class BulkArchiveHistoryResult:
    """Résultat global de la reconstruction sur toutes les archives."""
    pairs_found: int
    pairs_processed: int
    pairs_skipped: int
    pairs_errored: int
    total_persons_with_changes: int
    total_audit_entries: int
    details: list[PairwiseDiffResult]

    def summary_lines(self) -> list[str]:
        lines = [
            f"Paires d'archives trouvées : {self.pairs_found}",
            f"Mois traités : {self.pairs_processed}",
            f"Mois déjà reconstruits (ignorés) : {self.pairs_skipped}",
        ]
        if self.pairs_errored:
            lines.append(f"Erreurs : {self.pairs_errored}")
        lines += [
            f"Personnes avec changements détectés : {self.total_persons_with_changes}",
            f"Entrées d'audit créées : {self.total_audit_entries}",
        ]
        for d in self.details:
            if d.skipped:
                lines.append(f"  {d.mois} : déjà reconstruit — ignoré")
            elif d.error:
                lines.append(f"  {d.mois} : ERREUR — {d.error}")
            else:
                lines.append(
                    f"  {d.mois} : {d.persons_with_changes} personne(s) "
                    f"→ {d.audit_entries_created} entrée(s) d'audit"
                )
        return lines


# ------------------------------------------------------------------ diff principal (source → DB courante)

def run_retroactive_diff(
    settings: Settings,
    source_path: Path,
    mois: str,
    *,
    career_only: bool = True,
    dry_run: bool = False,
) -> RetroactiveDiffResult:
    """
    Compare la base SOURCE (backup d'un mois passé) avec la base TARGET (courante).

    Pour chaque personne présente dans les deux bases, matchée par ``matricule_militaire``,
    calcule les différences et crée des entrées d'audit ``action="update"`` datées au
    dernier jour du mois SOURCE — elles apparaîtront dans le Parcours professionnel de chaque personne.

    career_only: si True, ne trace que les champs Parcours (grade, structure, fonction,
                 position_admin, subdivision_arme, date_grade, date_service, ndecision).
    dry_run:     si True, calcule les diffs sans rien écrire.
    """
    warnings: list[str] = []

    if not source_path.is_file():
        raise FileNotFoundError(f"Fichier source introuvable : {source_path}")

    src_rows = _read_file_rows(source_path)
    tgt_rows = _read_current_rows(settings)

    src_by_mat: dict[str, ListeGeneraleRow] = {
        (r.matricule_militaire or "").strip(): r
        for r in src_rows
        if (r.matricule_militaire or "").strip()
    }
    tgt_by_mat: dict[str, ListeGeneraleRow] = {
        (r.matricule_militaire or "").strip(): r
        for r in tgt_rows
        if (r.matricule_militaire or "").strip()
    }

    if not src_by_mat:
        warnings.append(
            "Aucune personne avec matricule_militaire renseigné dans la base source. "
            "Vérifiez que le fichier est bien une base SME valide."
        )

    ts = _last_day_ts(mois)
    motif = f"Diff base {mois} → base courante ({source_path.name})"

    matched = 0
    with_changes = 0
    audit_count = 0
    unmatched: list[str] = []

    for mat, src_row in src_by_mat.items():
        tgt_row = tgt_by_mat.get(mat)
        if tgt_row is None:
            unmatched.append(mat)
            continue
        matched += 1

        changes = diff_personnel_rows(src_row, tgt_row)
        if career_only:
            changes = {k: v for k, v in changes.items() if k in PARCOURS_FIELD_NAMES}

        if not changes:
            continue

        with_changes += 1
        if not dry_run:
            append_personnel_audit(
                action="update",
                personnel_id=tgt_row.id,
                changes=changes,
                sme_mois_context=mois,
                edited_by="Import rétroactif",
                edit_motif=motif,
                ts_utc=ts,
            )
            audit_count += 1

    if len(unmatched) > 20:
        warnings.append(
            f"{len(unmatched)} personne(s) de la source sans correspondance dans la base courante."
        )
    elif unmatched:
        warnings.append(
            f"{len(unmatched)} personne(s) non trouvée(s) dans la base courante : "
            + ", ".join(unmatched)
        )

    return RetroactiveDiffResult(
        persons_source=len(src_rows),
        persons_target=len(tgt_rows),
        persons_matched=matched,
        persons_with_changes=with_changes,
        audit_entries_created=audit_count,
        unmatched_source=unmatched,
        warnings=warnings,
    )


# ------------------------------------------------------------------ diff entre deux archives

def run_pairwise_archive_diff(
    old_path: Path,
    new_path: Path,
    mois: str,
    *,
    career_only: bool = True,
    dry_run: bool = False,
    skip_if_exists: bool = True,
) -> PairwiseDiffResult:
    """
    Compare deux archives consécutives et génère les entrées d'audit pour ``mois``.

    old_path : archive du mois précédent (état « avant »)
    new_path : archive du mois cible (état « après »)
    mois     : YYYY-MM de new_path (les entrées sont datées à la fin de ce mois)

    Les entrées sont taggées ``edited_by="Reconstruction archives"`` pour permettre
    la détection de doublons lors d'une ré-exécution (``skip_if_exists=True``).
    """
    if skip_if_exists and not dry_run and _mois_already_reconstructed(mois):
        return PairwiseDiffResult(
            mois=mois, old_path=old_path, new_path=new_path,
            persons_matched=0, persons_with_changes=0, audit_entries_created=0,
            skipped=True,
        )

    try:
        old_rows = _read_file_rows(old_path)
    except Exception as e:
        return PairwiseDiffResult(
            mois=mois, old_path=old_path, new_path=new_path,
            persons_matched=0, persons_with_changes=0, audit_entries_created=0,
            error=f"Lecture archive précédente : {e}",
        )

    try:
        new_rows = _read_file_rows(new_path)
    except Exception as e:
        return PairwiseDiffResult(
            mois=mois, old_path=old_path, new_path=new_path,
            persons_matched=0, persons_with_changes=0, audit_entries_created=0,
            error=f"Lecture archive courante : {e}",
        )

    old_by_mat: dict[str, ListeGeneraleRow] = {
        (r.matricule_militaire or "").strip(): r
        for r in old_rows
        if (r.matricule_militaire or "").strip()
    }
    new_by_mat: dict[str, ListeGeneraleRow] = {
        (r.matricule_militaire or "").strip(): r
        for r in new_rows
        if (r.matricule_militaire or "").strip()
    }

    try:
        ts = _last_day_ts(mois)
    except (ValueError, IndexError):
        ts = datetime.now(timezone.utc).isoformat()

    motif = f"Reconstruction archives {mois} ({old_path.name} → {new_path.name})"
    matched = 0
    with_changes = 0
    audit_count = 0

    for mat, new_row in new_by_mat.items():
        old_row = old_by_mat.get(mat)
        if old_row is None:
            continue
        matched += 1
        changes = diff_personnel_rows(old_row, new_row)
        if career_only:
            changes = {k: v for k, v in changes.items() if k in PARCOURS_FIELD_NAMES}
        if not changes:
            continue
        with_changes += 1
        if not dry_run:
            append_personnel_audit(
                action="update",
                personnel_id=new_row.id,
                changes=changes,
                sme_mois_context=mois,
                edited_by="Reconstruction archives",
                edit_motif=motif,
                ts_utc=ts,
            )
            audit_count += 1

    return PairwiseDiffResult(
        mois=mois, old_path=old_path, new_path=new_path,
        persons_matched=matched, persons_with_changes=with_changes,
        audit_entries_created=audit_count,
    )


# ------------------------------------------------------------------ reconstruction en masse

def run_bulk_archive_history(
    archives_dir: Path,
    *,
    career_only: bool = True,
    dry_run: bool = False,
    skip_existing: bool = True,
    progress: Any = None,
) -> BulkArchiveHistoryResult:
    """
    Scanne ``archives_dir``, trouve toutes les archives avec YYYY-MM dans leur nom,
    les trie chronologiquement, puis pour chaque paire consécutive (N-1, N) génère
    l'historique parcours de toutes les personnes dont les données ont changé.

    progress : callable(str) optionnel pour rapporter l'avancement.
    skip_existing : si True, ignore les mois déjà reconstruits (défaut : True).
    """
    SUPPORTED = {".sqlite", ".db", ".mdb", ".accdb"}

    # Collecte et déduplication : un seul fichier par YYYY-MM (le dernier modifié)
    candidates: dict[str, tuple[Path, float]] = {}
    try:
        for p in archives_dir.iterdir():
            if not p.is_file() or p.suffix.lower() not in SUPPORTED:
                continue
            mois = _extract_archive_mois(p.name)
            if not mois:
                continue
            try:
                mtime = p.stat().st_mtime
            except OSError:
                mtime = 0.0
            if mois not in candidates or mtime > candidates[mois][1]:
                candidates[mois] = (p, mtime)
    except OSError:
        return BulkArchiveHistoryResult(0, 0, 0, 0, 0, 0, [])

    sorted_archives = sorted(candidates.items())  # [(YYYY-MM, (Path, mtime)), ...]
    pairs_found = max(0, len(sorted_archives) - 1)

    details: list[PairwiseDiffResult] = []
    processed = skipped = errored = 0

    for i in range(1, len(sorted_archives)):
        old_mois, (old_path, _) = sorted_archives[i - 1]
        new_mois, (new_path, _) = sorted_archives[i]

        if progress:
            progress(f"Analyse {old_mois} → {new_mois}…")

        result = run_pairwise_archive_diff(
            old_path, new_path, new_mois,
            career_only=career_only,
            dry_run=dry_run,
            skip_if_exists=skip_existing,
        )
        details.append(result)
        if result.skipped:
            skipped += 1
        elif result.error:
            errored += 1
        else:
            processed += 1

    return BulkArchiveHistoryResult(
        pairs_found=pairs_found,
        pairs_processed=processed,
        pairs_skipped=skipped,
        pairs_errored=errored,
        total_persons_with_changes=sum(d.persons_with_changes for d in details if not d.skipped),
        total_audit_entries=sum(d.audit_entries_created for d in details),
        details=details,
    )
