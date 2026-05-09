"""Hiérarchie d'exceptions typées pour l'application SME Gendarmerie."""

from __future__ import annotations


class SmeError(Exception):
    """Exception de base de l'application SME."""


class SmeDataError(SmeError):
    """Erreur de données (validation, cohérence, intégrité)."""


class SmeAccessError(SmeError):
    """Erreur d'accès à la base Access (.mdb / .accdb)."""


class SmeSqliteError(SmeError):
    """Erreur d'accès à la base SQLite."""


class SmeConfigError(SmeError):
    """Configuration manquante ou invalide."""
