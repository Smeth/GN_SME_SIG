from dataclasses import replace
from datetime import date
from types import SimpleNamespace

from sme_gend.models.agent_dossier import AgentDiplomeRow, AgentAffectationRow
from sme_gend.models.agent_evenement import AgentStageRow
from sme_gend.services.dossier_grouping import (
    group_stages, group_diplomas, group_affectations, function_history,
)


def test_grouped_dialog_can_show_monthly_sources_without_database(monkeypatch, tmp_path):
    consultations = []
    monkeypatch.setattr('sme_gend.services.consultation_audit.queue_consultation', lambda *a, **kw: consultations.append(kw))
    from pathlib import Path
    from unittest.mock import MagicMock
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFontDatabase, QFont
    from test_personnel_dossier_open import _row
    from sme_gend.config.settings import Settings
    from sme_gend.ui import personnel_dossier_dialog as ui
    app = QApplication.instance() or QApplication([])
    font_path = Path('C:/Windows/Fonts/segoeui.ttf')
    if font_path.exists():
        QFontDatabase.addApplicationFont(str(font_path))
        app.setFont(QFont('Segoe UI', 10))
    from sme_gend.ui.theme import apply_app_theme
    apply_app_theme(app, 'light')
    stages = [stage(1, '2025-02', '2025-02-01', '2025-02-28'),
              stage(2, '2025-03', '2025-03-01', '2025-03-31')]
    diploma = AgentDiplomeRow(1, 1, 'MILITAIRE', 'BIA', date(2020, 1, 1))
    first = AgentAffectationRow(1, 1, 'A', date(2025, 2, 1), fonction='Adjoint')
    second = replace(first, id=2, date_affectation=date(2025, 3, 1), fonction='Chef')
    evidence = [SimpleNamespace(annee_mois='2025-02', structure='A', fonction='Adjoint',
                               subdivision_arme=None, diplome='BIA', date_obtention='2020-01-01'),
                SimpleNamespace(annee_mois='2025-03', structure='A', fonction='Chef',
                               subdivision_arme=None, diplome='BIA', date_obtention='2020-01-01')]
    snapshot = {
        'events': {k: stages if k == 'stages' else [] for k in ('missions', 'stages', 'malades', 'desertions', 'conges')},
        'dossier': {'list_diplomes': [diploma, replace(diploma, id=2)], 'list_affectations': [second, first],
                    'list_distinctions': [], 'list_passations': []},
        'career_snapshots': evidence, 'grades': [], 'structures': [], 'positions': [],
        'timeline': [], 'situation': None, 'alerts': [],
        'media': {'root': Path('unused'), 'photo': None, 'grade': None},
    }
    from sme_gend.repositories.mysql_connection import MySQLConnection
    forbidden = MagicMock(side_effect=AssertionError('Unexpected database access'))
    monkeypatch.setattr(MySQLConnection, 'connect', forbidden)
    settings = Settings(database_path=Path('unused'), mysql_view_mois='2026-09')
    widget = ui.PersonnelDossierDialog(None, _row(), MagicMock(), MagicMock(settings=settings),
                                      allow_edit=False, readonly_snapshot=snapshot)
    assert widget._tbl_stages.rowCount() == 1
    assert widget._tbl_diplomes.rowCount() == 1
    assert widget._tbl_affectations.rowCount() == 1
    assert widget._tbl_functions.rowCount() == 2
    assert '2025-02' in widget._tbl_diplomes.item(0, 0).toolTip()
    monkeypatch.setattr(ui.QInputDialog, 'getItem', lambda *a: (a[3][1], True))
    assert widget._pick_group_source(widget._stage_groups[0]) is stages[1]
    widget._group_history.setChecked(False)
    assert widget._tbl_stages.rowCount() == 2
    assert widget._tbl_diplomes.rowCount() == 2
    assert widget._tbl_affectations.rowCount() == 2
    widget._group_history.setChecked(True)
    widget._tabs.setCurrentIndex(1)
    widget.resize(1280, 900)
    widget.prepare_for_exec()
    widget.show()
    widget.resize(1280, 900)
    app.processEvents()
    assert widget.grab().save(str(tmp_path / 'dossier.png'))
    print('Dossier render:', tmp_path / 'dossier.png')
    forbidden.assert_not_called()
    assert consultations and consultations[-1]['action'] == 'dossier_tab_consulted'
    widget.close()


def stage(i, month, start, end, **kwargs):
    return AgentStageRow('EXTERIEUR', start, end, 'CDS', 'Turquie',
                         kwargs.get('obs', '2023-2026'), month, i)


def test_continuous_stage_keeps_original_rows_and_does_not_invent_real_dates():
    rows = [stage(1, '2025-02', '2025-02-01', '2025-02-28'),
            stage(2, '2025-03', '2025-03-01', '2025-03-31')]
    groups = group_stages(rows)
    assert len(groups) == 1
    assert groups[0].months == ('2025-02', '2025-03')
    assert groups[0].row.date_debut == '2025-02-01'
    assert groups[0].row.date_fin == '2025-03-31'
    assert groups[0].members == rows
    assert rows[0].date_fin == '2025-02-28'


def test_stages_separated_by_a_gap_or_unknown_month_remain_separate():
    rows = [stage(1, '2025-02', '2025-02-01', '2025-02-28'),
            stage(2, '2025-04', '2025-04-01', '2025-04-30'),
            stage(3, None, '2025-05-01', '2025-05-31')]
    assert len(group_stages(rows)) == 3


def test_diploma_unknown_date_merges_only_when_unambiguous():
    unknown = AgentDiplomeRow(1, 1, 'MILITAIRE', 'BIA')
    known = replace(unknown, id=2, date_obtention=date(2020, 1, 1))
    evidence = SimpleNamespace(diplome=' bia ', date_obtention='2020-01-01', annee_mois='2026-01')
    groups = group_diplomas([unknown, known], [evidence])
    assert len(groups) == 1 and groups[0].row is known
    assert groups[0].months == ('2026-01',)
    another = replace(known, id=3, date_obtention=date(2021, 1, 1))
    assert len(group_diplomas([unknown, known, another])) == 3


def test_return_to_structure_is_separate_but_function_change_is_grouped():
    first = AgentAffectationRow(1, 1, 'A', date(2025, 1, 1), fonction='Adjoint')
    second = replace(first, id=2, date_affectation=date(2025, 2, 1), fonction='Chef')
    third = replace(first, id=3, date_affectation=date(2025, 3, 1), structure_code='B')
    fourth = replace(first, id=4, date_affectation=date(2025, 4, 1))
    groups = group_affectations([fourth, third, second, first])
    assert [len(g.members) for g in groups] == [1, 1, 2]
    snapshots = [SimpleNamespace(annee_mois='2025-01', structure='A', fonction='Chef', subdivision_arme='X'),
                 SimpleNamespace(annee_mois='2025-02', structure='A', fonction=' chef ', subdivision_arme='X'),
                 SimpleNamespace(annee_mois='2025-03', structure='A', fonction='Adjoint', subdivision_arme='X')]
    assert len(function_history(snapshots)) == 2
