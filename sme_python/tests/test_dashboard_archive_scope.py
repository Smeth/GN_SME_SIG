"""Tableau de bord : cache aligné sur le mois affiché (data_settings)."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.dashboard_service import DashboardService


def _settings(**kw) -> Settings:
    return replace(Settings(database_path=Path(".")), **kw)


def test_dashboard_uses_data_settings_for_liste_cache(monkeypatch) -> None:
    data_st = _settings(mysql_view_mois="2026-04")
    ref_st = _settings(mysql_view_mois=None)
    rows = [
        ListeGeneraleRow(
            id="1",
            noms="Dupont",
            date_naissance=None,
            lieu_naissance=None,
            grade="GND",
            date_grade=None,
            date_service=None,
            ndecision=None,
            matricule_militaire=None,
            matricule_solde=None,
            position_admin="Présent",
            fonction=None,
            subdivision_arme=None,
            diplome=None,
            date_obtention=None,
            structure="U001",
            obs=None,
            ordre_tri=None,
            presseance=None,
        )
    ]
    cache_keys: list[tuple[int, str]] = []

    def _fake_get_liste_rows(settings, loader):
        cache_keys.append(
            (
                id(settings),
                (settings.mysql_view_mois or ""),
            )
        )
        return loader()

    monkeypatch.setattr(
        "sme_gend.services.liste_generale_cache.get_liste_rows",
        _fake_get_liste_rows,
    )
    monkeypatch.setattr(
        "sme_gend.config.liste_generale_scope.build_export_mois_context",
        lambda s, **kwargs: type(
            "C",
            (),
            {
                "export_mois": s.mysql_view_mois or "2026-05",
                "mois_actif": "2026-05",
                "is_archive_view": bool(s.mysql_view_mois),
            },
        )(),
    )
    monkeypatch.setattr(
        "sme_gend.services.dashboard_service.padmin_counts_as_present",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        "sme_gend.services.dashboard_service.build_career_alerts_kpi_card",
        lambda _settings, **_kwargs: None,
    )
    monkeypatch.setattr(
        "sme_gend.services.gain_perte_coherence.load_unsignaled_pertes",
        lambda *args: [],
    )
    monkeypatch.setattr(
        "sme_gend.repositories.subdivision_repository.SubdivisionRepository",
        lambda settings: SimpleNamespace(fetch_flat_hierarchy=lambda: []),
    )

    svc = DashboardService.__new__(DashboardService)
    svc._data_settings = data_st
    svc._settings = ref_st
    svc._snapshot_cache_key = None
    svc._snapshot_cache_val = None
    svc._liste = MagicMock()
    svc._liste.fetch_all = MagicMock(return_value=rows)
    svc._structures = MagicMock()
    svc._structures.fetch_all = MagicMock(return_value=[])
    svc._grades = MagicMock()
    svc._grades.fetch_all = MagicMock(return_value=[])
    svc._sme = MagicMock(table_exists=MagicMock(return_value=False))
    svc._gain_perte = MagicMock()
    svc._gain_perte.fetch_for_month = MagicMock(return_value=[])
    snap = svc._build_snapshot()

    assert snap.total_effectifs == 1
    assert cache_keys[-1] == (id(data_st), "2026-04")
    assert snap.view_mois == "2026-04"
    assert snap.is_archive_view is True
    assert snap.gain_perte_mois == "2026-05"
    svc._gain_perte.fetch_for_month.assert_called_once_with("2026-05")


def test_dashboard_snapshot_cache_tracks_active_month(monkeypatch) -> None:
    data_st = _settings(mysql_view_mois="2026-04")
    svc = DashboardService.__new__(DashboardService)
    svc._data_settings = data_st
    svc._liste = object()
    svc._snapshot_cache_key = None
    svc._snapshot_cache_val = None
    svc._build_snapshot = MagicMock(side_effect=[object(), object(), object()])
    contexts = iter(
        (
            SimpleNamespace(mois_actif="2026-05", presences_mode="present"),
            SimpleNamespace(mois_actif="2026-06", presences_mode="present"),
            SimpleNamespace(mois_actif="2026-06", presences_mode="les_deux"),
        )
    )

    monkeypatch.setattr(
        "sme_gend.config.liste_generale_scope.resolve_liste_view_mois",
        lambda _settings, **kwargs: "2026-04",
    )
    monkeypatch.setattr(
        "sme_gend.config.sme_context.load_sme_context",
        lambda: next(contexts),
    )
    monkeypatch.setattr(
        "sme_gend.services.sme_month_closure.canonical_sme_mois_yyyy_mm",
        lambda month: month,
    )

    first = svc.snapshot()
    second = svc.snapshot()
    third = svc.snapshot()

    assert first is not second
    assert second is not third
    assert svc._build_snapshot.call_count == 3
