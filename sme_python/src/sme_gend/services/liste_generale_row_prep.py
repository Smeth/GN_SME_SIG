"""Préparation des lignes Access avant upsert MySQL (Id manquants, doublons Id)."""

from __future__ import annotations

import hashlib
from dataclasses import replace

from sme_gend.models.liste_generale import ListeGeneraleRow


def _content_fingerprint(r: ListeGeneraleRow) -> str:
    """Empreinte stable (indépendante du rang de ligne et du lot d'import)."""
    parts = [
        str(r.noms or "").strip().upper(),
        str(r.matricule_militaire or "").strip(),
        str(r.matricule_solde or "").strip(),
        str(r.date_naissance or ""),
        str(r.lieu_naissance or "").strip().upper(),
    ]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]


def prepare_liste_generale_rows_for_import(
    rows: list[ListeGeneraleRow],
    *,
    import_lot_id: int,
) -> tuple[list[ListeGeneraleRow], int, int, int]:
    """
    Ne supprime aucune ligne.

    - Id Access vide → ``GEN-{empreinte}`` (stable entre réimports ; le lot
      n'entre pas dans la clé). Collisions de contenu → ``GEN-{fp}#1``, ``#2``…
    - Id Access dupliqué dans le même fichier → suffixe ``#1``, ``#2``… (rang
      d'occurrence dans le groupe, pas l'index fichier).

    Retourne ``(lignes, ids_vides_source, ids_auto_generes, ids_access_dupliques_resolus)``.
    """
    out: list[ListeGeneraleRow] = []
    ids_empty = 0
    ids_auto = 0
    dup_ids = 0
    seen_raw: dict[str, int] = {}
    seen_fp: dict[str, int] = {}
    _ = import_lot_id  # conservé pour compatibilité d'appel / logs futurs

    for r in rows:
        raw = str(r.id or "").strip()
        leg = raw
        if not leg:
            ids_empty += 1
            fp = _content_fingerprint(r)
            n = seen_fp.get(fp, 0)
            seen_fp[fp] = n + 1
            leg = f"GEN-{fp}" if n == 0 else f"GEN-{fp}#{n}"
            ids_auto += 1
        else:
            n = seen_raw.get(raw, 0)
            seen_raw[raw] = n + 1
            if n > 0:
                dup_ids += 1
                leg = f"{raw}#{n}"
        if leg != raw or not raw:
            r = replace(r, id=leg, access_join_id=leg)
        out.append(r)

    return out, ids_empty, ids_auto, dup_ids
