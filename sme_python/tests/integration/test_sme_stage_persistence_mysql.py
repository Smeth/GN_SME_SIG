from dataclasses import replace
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
from test_restore_mysql import isolated_mysql, _lot7_dossier


def setup_services(isolated_mysql, monkeypatch):
    from sme_gend.services.personnel_service import PersonnelService
    from sme_gend.services import sensitive_data_policy, audit_log
    settings, connect, params, dossier, _ = _lot7_dossier(isolated_mysql, monkeypatch)
    settings = replace(settings, mysql_view_mois='2026-09')
    monkeypatch.setattr('sme_gend.config.get_settings', lambda: settings)
    monkeypatch.setattr(sensitive_data_policy, 'assert_may_edit_personnel_sensitive_fields', lambda *a: None)
    monkeypatch.setattr(audit_log, 'append_personnel_audit', lambda **kw: None)
    for name in ('critical', 'warning', 'information'):
        monkeypatch.setattr(QMessageBox, name, Mock())
    return settings, connect, params, PersonnelService(settings), dossier


@pytest.mark.parametrize('category,stamp,expected', [
    ('MILITAIRE', date(2026,9,20), 'NEW'),
    ('CIVIL', date(2026,9,20), 'OLD MILITARY'),
    ('MILITAIRE', date(2026,10,1), 'OLD MILITARY'),
])
def test_sme_add_diploma_is_persisted_even_when_not_monthly_selection(isolated_mysql, monkeypatch, category, stamp, expected):
    from sme_gend.ui.views.edition_sme_view import EditionSmeView
    from sme_gend.ui import diplome_edit_dialog
    settings, connect, params, personnel, dossier = setup_services(isolated_mysql, monkeypatch)
    row = personnel.get_by_id('1')
    dossier.add_diplome(row, categorie='MILITAIRE', libelle='OLD MILITARY', date_obtention=date(2026,8,1))
    row = personnel.get_by_id('1')
    dialog = Mock()
    dialog.exec.return_value = QDialog.DialogCode.Accepted
    dialog.values.return_value = dict(categorie=category, libelle='NEW', date_obtention=stamp)
    monkeypatch.setattr(diplome_edit_dialog, 'DiplomeEditDialog', lambda *a: dialog)
    monkeypatch.setattr('sme_gend.services.auth_guard.require_permission', lambda *a, **kw: None)
    refreshed = []
    view = SimpleNamespace(_settings=settings, _apply_editing_lock=Mock(), _require_editable=lambda: True,
                           _sync_liste_rows_from_db=lambda ids: refreshed.append(personnel.get_by_id(ids[0])))
    EditionSmeView._add_diplome(view, row)
    assert len(refreshed) == 1 and refreshed[0].diplome == expected
    assert personnel.get_by_id('1').diplome == expected
    assert personnel.get_by_id('1').grade == row.grade
    assert any(d.libelle == 'NEW' and d.date_obtention == stamp for d in dossier.list_diplomes(row))
    QMessageBox.critical.assert_not_called()


def test_bulk_grade_preserves_later_history_and_other_fields_really_persist(isolated_mysql, monkeypatch):
    from sme_gend.ui.views.edition_sme_view import _EditionModel
    settings, connect, params, personnel, _ = setup_services(isolated_mysql, monkeypatch)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO carriere_situation(id,agent_id,sme_periode_id,grade_libelle) VALUES(2,2,1,'MDL')")
            cur.execute("INSERT INTO agent_passation_grade(agent_id,grade_libelle,date_passation) VALUES(1,'MDL','2026-08-01'),(2,'MDL','2026-09-20')")
    app = QApplication.instance() or QApplication([])
    model = _EditionModel()
    model.load([personnel.get_by_id('1'), personnel.get_by_id('2')], None, [], [], [], [], personnel=personnel, settings=settings)
    updated, unchanged, errors = model.bulk_set_grade([0,1], 'ADC', datetime(2026,9,10))
    assert (updated, unchanged, len(errors)) == (2,0,0)
    assert personnel.get_by_id('1').grade == 'ADC'
    assert personnel.get_by_id('2').grade == 'MDL'
    updated, unchanged, errors = model.bulk_set_field([0,1], 'obs', 'SAVED GROUP')
    assert (updated, unchanged, errors) == (2,0,[])
    assert all(personnel.get_by_id(str(i)).obs == 'SAVED GROUP' for i in (1,2))
    app.processEvents()


def test_bulk_structure_does_not_claim_shadowed_change(isolated_mysql, monkeypatch):
    from sme_gend.ui.views.edition_sme_view import _EditionModel
    settings, connect, params, personnel, _ = setup_services(isolated_mysql, monkeypatch)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE carriere_situation SET structure_code='OLD' WHERE id=1")
            cur.execute("INSERT INTO agent_affectation(agent_id,structure_code,date_affectation) VALUES(1,'OLD','2026-09-20')")
    app = QApplication.instance() or QApplication([])
    row = personnel.get_by_id('1')
    model = _EditionModel()
    model.load([row], None, [], [], [], [], personnel=personnel, settings=settings)
    updated, unchanged, errors = model.bulk_set_field([0], 'structure', 'NEW')
    assert updated == 0 and len(errors) == 1
    assert personnel.get_by_id('1').structure == 'OLD'
    assert model._rows[0].structure == row.structure
    app.processEvents()


def test_bulk_structure_function_subdivision_and_position_persist(isolated_mysql, monkeypatch):
    from sme_gend.ui.views.edition_sme_view import _EditionModel
    settings, connect, params, personnel, _ = setup_services(isolated_mysql, monkeypatch)
    app = QApplication.instance() or QApplication([])
    for field, value in [('structure', 'NEW'), ('fonction', 'CHEF TEST'),
                         ('subdivision_arme', 'TEST SUBDIVISION'), ('position_admin', 'StageLocal')]:
        model = _EditionModel()
        model.load([personnel.get_by_id('1')], None, [], [], [], [], personnel=personnel, settings=settings)
        updated, unchanged, errors = model.bulk_set_field([0], field, value)
        assert (updated, unchanged, errors) == (1,0,[]), (field, errors)
        assert getattr(personnel.get_by_id('1'), field) == value
        assert personnel.get_by_id('1').grade == 'MDL'
    app.processEvents()


@pytest.mark.parametrize('kind,already_present,fail', [
    ('local', False, False), ('ex', False, False), ('interne', False, False),
    ('local', True, False), ('local', False, True),
])
def test_stage_end_and_diploma_commit_together(isolated_mysql, monkeypatch, kind, already_present, fail, actual_end='2026-09-30'):
    from sme_gend.ui.views import stages_view as module
    from sme_gend.ui.stage_end_diploma_dialog import StageEndDiplomaDialog
    from sme_gend.repositories.mysql_numerique_repository import MySQLNumeriqueRepository
    from sme_gend.models.numerique_satellite import StageDetail
    from sme_gend.services.agent_dossier_service import AgentDossierService
    settings, connect, params, personnel, dossier = setup_services(isolated_mysql, monkeypatch)
    padmin = 'Présent' if already_present else module.padmin_for_stage_kind(kind)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('UPDATE carriere_situation SET position_admin_libelle=%s WHERE id=1', (padmin,))
            cur.execute("INSERT IGNORE INTO ref_type_stage(code,libelle) VALUES('INTERNE','Interne')")
    row = personnel.get_by_id('1')
    sat = MySQLNumeriqueRepository(settings)
    code = module._stage_type_code(kind)
    sat.upsert_stage('42', StageDetail('FORMATION', 'TEST', '01/09/2026', reference_dossier='NOTE STAGE', session='SESSION TEST' if kind == 'interne' else None), type_code=code, date_debut='2026-09-01')
    if already_present:
        # Creating the dated stage now projects its position. Represent the
        # later return explicitly before testing the diploma-only workflow.
        from sme_gend.services.personnel_service import _personnel_write
        from sme_gend.services.temporal_events import record_position
        with _personnel_write(settings, reason='synthetic_stage_return'):
            record_position(settings, 1, 'Présent', date(2026, 9, 15))
        row = personnel.get_by_id('1')
    loader = {'local': sat.load_stages_loc, 'ex': sat.load_stages_ex, 'interne': sat.load_stages_internal}[kind]
    details = loader()
    payload = dict(categorie='MILITAIRE', libelle='STAGE DIPLOMA', date_obtention=date(2026,9,30), _stage_end_date=actual_end)
    monkeypatch.setattr(StageEndDiplomaDialog, 'run', lambda *a, **kw: payload)
    if fail:
        def fail_projection(*a, **kw):
            raise RuntimeError('synthetic diploma failure')
        monkeypatch.setattr(AgentDossierService, '_project_carriere', fail_projection)
    view = SimpleNamespace(_scoped_settings=settings, _sat=sat, _checked={kind: {'42'}},
        _check_can_edit=lambda **kw: True, _keys_for_start_action=lambda _: ['42'],
        _confirm_end_stages=lambda *a: True, _resolve_start_stage_rows=lambda *a: [row],
        _stage_end_reference_date=lambda: '2026-09-30', _prepare_stage_rows=lambda rows: {row.id: row},
        _details_for=lambda _: details, _invalidate_liste_caches=Mock(),
        _view_data=SimpleNamespace(mark_stale=Mock()), _reload_lists_async=Mock())
    module.StagesView._end_checked_stages(view, kind)
    fresh = personnel.get_by_id('1')
    diplomas = dossier.list_diplomes(row)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT date_fin,reference_dossier,session FROM parcours_stage WHERE agent_id=1 AND type_stage_code=%s', (code,))
            end, reference, session = cur.fetchone()
            assert reference == 'NOTE STAGE'
            assert session == ('SESSION TEST' if kind == 'interne' else None)
    if fail:
        assert fresh.position_admin == padmin
        assert not diplomas and end is None
        assert 'synthetic diploma failure' in QMessageBox.information.call_args.args[2]
    else:
        assert fresh.position_admin == 'Présent'
        assert fresh.diplome == 'STAGE DIPLOMA'
        assert fresh.grade == row.grade
        assert len(diplomas) == 1
        assert end == date.fromisoformat(actual_end)
        assert '1 diplôme(s)' in QMessageBox.information.call_args.args[2]
