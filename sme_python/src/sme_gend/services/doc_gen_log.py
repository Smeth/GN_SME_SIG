"""Journalisation des générations de documents (attestations, soit transmis, ZIP, PDF)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _log_path() -> Path:
    from sme_gend.config.settings import project_root
    p = project_root() / "data" / "doc_gen_log.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def log_doc_generation(
    *,
    doc_type: str,
    format: str,
    persons: list[str] | None = None,
    dest_path: str | None = None,
    count: int = 1,
) -> None:
    """Enregistre une génération de document dans le journal JSONL.

    doc_type  : "attestation" | "attestation_batch" | "soit_transmis" | "zip"
    format    : "docx" | "pdf" | "zip"
    persons   : liste de noms (vide → inconnu)
    dest_path : chemin du fichier généré (peut être un temp)
    count     : nombre de personnes traitées
    """
    entry: dict[str, Any] = {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "doc_type": doc_type,
        "format": format,
        "count": count,
    }
    if persons:
        entry["persons"] = persons[:20]  # limiter la taille
    if dest_path:
        entry["dest"] = str(dest_path)
    try:
        p = _log_path()
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def read_doc_log(max_entries: int = 500) -> list[dict]:
    """Lit les dernières entrées du journal (les plus récentes en premier)."""
    p = _log_path()
    if not p.is_file():
        return []
    entries: list[dict] = []
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if len(entries) >= max_entries:
                break
    except OSError:
        pass
    return entries


def clear_doc_log() -> None:
    """Efface le journal."""
    p = _log_path()
    if p.is_file():
        p.write_text("", encoding="utf-8")
