"""Validation métier des données personnel — retourne des FieldError sans dépendance UI."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

_DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S")

_MAT_RE = re.compile(r"^[A-Za-z0-9\-\s/]{1,30}$")


@dataclass(slots=True)
class FieldError:
    """Erreur de validation sur un champ donné."""

    field: str    # clé machine (correspond aux clés de _field_widgets dans le dialog)
    label: str    # libellé français affiché
    message: str  # description de l'erreur


def _parse_date(s: str) -> datetime | None:
    """None si vide, lève ValueError si format non reconnu."""
    s = (s or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"Format de date non reconnu : {s!r}")


def validate_personnel_fields(
    *,
    noms: str,
    date_naissance_str: str,
    date_grade_str: str,
    date_service_str: str,
    date_obtention_str: str,
    matricule_militaire: str,
    matricule_solde: str,
    ordre_tri_str: str,
    presseance_str: str,
) -> list[FieldError]:
    """Valide les champs bruts du formulaire personnel, retourne la liste des erreurs."""
    errors: list[FieldError] = []
    now = datetime.now()

    # ── Noms ──────────────────────────────────────────────────────────────────
    noms_s = noms.strip()
    if not noms_s:
        errors.append(FieldError("noms", "Noms et prénoms", "Le nom est obligatoire."))
    elif len(noms_s) < 2:
        errors.append(FieldError("noms", "Noms et prénoms", "Minimum 2 caractères requis."))

    # ── Date de naissance ─────────────────────────────────────────────────────
    if date_naissance_str.strip():
        try:
            dn = _parse_date(date_naissance_str)
        except ValueError:
            errors.append(FieldError(
                "date_naissance", "Date de naissance",
                f"Format non reconnu — utilisez AAAA-MM-JJ ou JJ/MM/AAAA.",
            ))
        else:
            if dn and dn > now:
                errors.append(FieldError(
                    "date_naissance", "Date de naissance",
                    "La date de naissance ne peut pas être dans le futur.",
                ))
            elif dn:
                age = (now - dn).days / 365.25
                if age < 16 or age > 90:
                    errors.append(FieldError(
                        "date_naissance", "Date de naissance",
                        f"L'âge calculé ({age:.0f} ans) paraît incohérent (16–90 ans attendu).",
                    ))

    # ── Date de grade ─────────────────────────────────────────────────────────
    if date_grade_str.strip():
        try:
            dg = _parse_date(date_grade_str)
        except ValueError:
            errors.append(FieldError(
                "date_grade", "Date de grade",
                "Format non reconnu — utilisez AAAA-MM-JJ ou JJ/MM/AAAA.",
            ))
        else:
            if dg and dg > now:
                errors.append(FieldError(
                    "date_grade", "Date de grade",
                    "La date de grade ne peut pas être dans le futur.",
                ))

    # ── Date de service (stockée en string) ───────────────────────────────────
    if date_service_str.strip():
        try:
            _parse_date(date_service_str)
        except ValueError:
            errors.append(FieldError(
                "date_service", "Date de service",
                "Format non reconnu — utilisez AAAA-MM-JJ ou JJ/MM/AAAA.",
            ))

    # ── Date d'obtention diplôme (stockée en string) ──────────────────────────
    if date_obtention_str.strip():
        try:
            _parse_date(date_obtention_str)
        except ValueError:
            errors.append(FieldError(
                "date_obtention", "Date d'obtention",
                "Format non reconnu — utilisez AAAA-MM-JJ ou JJ/MM/AAAA.",
            ))

    # ── Matricules ────────────────────────────────────────────────────────────
    if matricule_militaire.strip() and not _MAT_RE.match(matricule_militaire.strip()):
        errors.append(FieldError(
            "matricule_militaire", "Matricule militaire",
            "Format invalide (alphanumérique, tirets/espaces autorisés, max 30 car.).",
        ))

    if matricule_solde.strip() and not _MAT_RE.match(matricule_solde.strip()):
        errors.append(FieldError(
            "matricule_solde", "Matricule solde",
            "Format invalide (alphanumérique, tirets/espaces autorisés, max 30 car.).",
        ))

    # ── Entiers positifs ──────────────────────────────────────────────────────
    if ordre_tri_str.strip():
        try:
            v = int(ordre_tri_str.strip())
            if v < 0:
                errors.append(FieldError("ordre_tri", "Ordre de tri", "Doit être un entier positif ou nul."))
        except ValueError:
            errors.append(FieldError("ordre_tri", "Ordre de tri", "Doit être un entier."))

    if presseance_str.strip():
        try:
            v = int(presseance_str.strip())
            if v < 0:
                errors.append(FieldError("presseance", "Préséance", "Doit être un entier positif ou nul."))
        except ValueError:
            errors.append(FieldError("presseance", "Préséance", "Doit être un entier."))

    return errors
