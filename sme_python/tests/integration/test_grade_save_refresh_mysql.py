from dataclasses import replace
from datetime import date, datetime

import pytest
from test_restore_mysql import isolated_mysql, _lot7_dossier


@pytest.mark.parametrize('case', ['valid', 'future', 'older_than_history'])
def test_grade_save_and_reload_are_consistent(isolated_mysql, monkeypatch, case):
    from PySide6.QtWidgets import QApplication
    from sme_gend.services.personnel_service import PersonnelService
    from sme_gend.services import sensitive_data_policy, audit_log
    from sme_gend.ui.views.edition_sme_view import _EditionModel, _COL_IDX
    settings, connect, params, _, _ = _lot7_dossier(isolated_mysql, monkeypatch)
    settings = replace(settings, mysql_view_mois='2026-09')
    monkeypatch.setattr(sensitive_data_policy, 'assert_may_edit_personnel_sensitive_fields', lambda *a: None)
    monkeypatch.setattr(audit_log, 'append_personnel_audit', lambda **kw: None)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO agent_passation_grade(agent_id,grade_libelle,date_passation) VALUES(1,'MDL','2026-08-01')")
    service = PersonnelService(settings)
    row = service.get_by_id('1')
    # Prime the cache before editing, as when several application views are open.
    service.liste_complete()
    app = QApplication.instance() or QApplication([])
    model = _EditionModel()
    model.load([row], None, ['MDL', 'ADC'], [], [], [], personnel=service, settings=settings)
    stamp = {'valid': datetime(2026,9,1), 'future': datetime(2026,10,1),
             'older_than_history': datetime(2026,7,1)}[case]
    model.grade_nomination_prompt = lambda *a: stamp
    errors, successes = [], []
    model.save_err.connect(errors.append)
    model.save_ok.connect(successes.append)
    ok = model.setData(model.index(0, _COL_IDX['grade']), 'ADC')
    fresh = service.get_by_id('1')
    assert ok and not errors and successes
    assert fresh.grade == ('ADC' if case == 'valid' else row.grade)
    assert model._rows[0].grade == fresh.grade
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM agent_passation_grade WHERE agent_id=1 AND grade_libelle='ADC'")
            assert cur.fetchone()[0] == 1
    if case != 'valid':
        assert 'Passation' in successes[0]
        assert model.active_undo_count() == 0
    app.processEvents()
