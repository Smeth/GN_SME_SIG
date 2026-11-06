from datetime import date, datetime
from types import SimpleNamespace
import pytest
from sme_gend.services.import_diploma_date import interpret_diploma_date, source_observation


@pytest.mark.parametrize('raw,expected,place', [
    ('(22/08/2023 CONGO)', date(2023, 8, 22), 'CONGO'),
    ('22/08/2023 Congo', date(2023, 8, 22), 'Congo'),
    ('Le 2/8/2023 à Brazzaville', date(2023, 8, 2), 'Brazzaville'),
    ('2023-08-22', date(2023, 8, 22), None),
    ('2023-08-22T12:30:00', date(2023, 8, 22), None),
    ('08/22/2023', date(2023, 8, 22), None),
    ('22.08.2023', date(2023, 8, 22), None),
    ('22-08-2023', date(2023, 8, 22), None),
    ('29/02/2024', date(2024, 2, 29), None),
    (datetime(2023, 8, 22), date(2023, 8, 22), None),
])
def test_known_dates_and_place_are_separated(raw, expected, place):
    result = interpret_diploma_date(raw)
    assert result.value == expected and result.place == place and result.issue is None


@pytest.mark.parametrize('raw', ['28/07/29 Congo', '(22/08/23 CONGO)', '31/02/2023 Congo',
    '00/00/0000', '2023', 'date inconnue', '2023-08-220', '01/01/2020 au 02/02/2021'])
def test_bad_or_ambiguous_dates_are_not_guessed_and_source_is_preserved(raw):
    result = interpret_diploma_date(raw)
    assert result.value is None and result.issue and result.preserve_source
    assert raw in source_observation('Import archive 2026-11', result)


@pytest.mark.parametrize('value', [None, '', '   '])
def test_missing_date_is_supported(value):
    result = interpret_diploma_date(value)
    assert result.value is None and result.issue is None


@pytest.mark.parametrize('raw', ['(22/08/2023 CONGO)', '28/07/29 Congo', '31/02/2023 Congo'])
def test_diploma_entry_is_kept_and_only_date_issues_are_reported(raw):
    from sme_gend.services.agent_dossier_import_enrichment import _maybe_add_diplome, DossierEnrichmentCounts
    inserted = []
    repo = SimpleNamespace(insert_diplome_if_absent=lambda *a, **kw: inserted.append(kw) or 1)
    quality = DossierEnrichmentCounts()
    assert _maybe_add_diplome(repo, 1, SimpleNamespace(diplome='Brevet', date_obtention=raw),
                             'Import archive 2026-11', 1, date_quality=quality) == 1
    assert inserted[0]['libelle'] == 'Brevet' and inserted[0]['lieu'].casefold() == 'congo'
    assert raw in inserted[0]['obs']
    assert quality.date_issues == (0 if raw.startswith('(22/08/2023') else 1)


def test_import_report_describes_nonblocking_anomalies_and_escapes_text():
    from sme_gend.services.import_report import format_import_result_html
    html = format_import_result_html({'dossier_enrichment': {'date_issues': 1, 'date_warnings': ['<source>']}},
                                     include_alerts=False)
    assert '1 date(s)' in html and "n'est pas bloqué" in html and '&lt;source&gt;' in html


def test_imported_unknown_date_can_be_reviewed_without_inventing_a_date(monkeypatch):
    from PySide6.QtWidgets import QApplication, QDialog
    from sme_gend.ui.diplome_edit_dialog import DiplomeEditDialog
    from sme_gend.models.agent_dossier import AgentDiplomeRow
    app = QApplication.instance() or QApplication([])
    existing = AgentDiplomeRow(1, 1, 'MILITAIRE', 'Brevet', obs='Import archive 2026-11\nDate source : 28/07/29 Congo')
    dialog = DiplomeEditDialog(None, existing=existing)
    dialog._on_ok()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.values()['date_obtention'] is None
    assert '28/07/29 Congo' in dialog.values()['obs']
    dialog.close()


def test_purge_predicate_never_casts_legacy_text_to_a_date():
    from sme_gend.services.month_purge_plan import month_purge_plan
    item = next(p for p in month_purge_plan(1, '2026-11') if p.table == 'agent_diplome')
    assert 'DATE(c.date_obtention)' not in item.predicate
    assert 'REGEXP_LIKE' in item.predicate
