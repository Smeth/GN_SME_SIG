"""Real catalogue conflicts, historical links, audit rollback and media publication."""
import json
from pathlib import Path
from contextlib import nullcontext
from types import SimpleNamespace
import pytest
from test_restore_mysql import isolated_mysql, _lot7_dossier
from sme_gend.services.referentiel_service import ReferentielService
from sme_gend.repositories.reference_write_guard import ReferenceConflictError
from sme_gend.services import grade_media


@pytest.fixture
def catalogues(isolated_mysql, monkeypatch, tmp_path):
    settings, connect, params, _, _ = _lot7_dossier(isolated_mysql, monkeypatch)
    monkeypatch.setattr('sme_gend.config.get_settings', lambda: settings)
    monkeypatch.setattr('sme_gend.services.auth_context.get_app_auth', lambda: None)
    monkeypatch.setattr('sme_gend.services.auth_context.resolve_write_actor', lambda *a: 'SYNTHETIC')
    monkeypatch.setattr('sme_gend.services.auth_context.resolve_audit_actor', lambda *a: 'SYNTHETIC')
    monkeypatch.setattr('sme_gend.services.personnel_media.resolved_personnel_media_root', lambda: tmp_path)
    monkeypatch.setattr('sme_gend.services.personnel_media._media_search_roots', lambda: [tmp_path])
    monkeypatch.setattr('sme_gend.services.shared_media.shared_media_write_target',
        lambda root: nullcontext(SimpleNamespace(root=tmp_path, check=lambda: None)))
    grade_media.invalidate_grade_media_cache()
    service = ReferentielService(settings)
    def sql(query, parameters=()):
        with connect(**params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, parameters)
                return cursor.fetchall() if cursor.description else cursor.rowcount
    return service, sql, tmp_path


def position(service, label):
    return next(r for r in service.padmins() if r.libelle == label)


def grade(service, code='GTEST'):
    return next(r for r in service.grades() if r.code == code)


def test_positions_reject_duplicates_and_stale_update_or_delete(catalogues):
    service, sql, root = catalogues
    service.padmin_insert('Service test', True, 1)
    first = position(service, 'Service test')
    with pytest.raises(ReferenceConflictError):
        service.padmin_insert('Service test', False, 2)
    service.padmin_update('Service test', 'Service test', False, 3, expected=first)
    with pytest.raises(ReferenceConflictError):
        service.padmin_update('Service test', 'Service test', True, 4, expected=first)
    with pytest.raises(ReferenceConflictError):
        service.padmin_delete('Service test', expected=first)
    current = position(service, 'Service test')
    assert not current.compte_comme_present and current.ordre_tri == 3


def test_custom_rename_keeps_id_and_migrates_all_months(catalogues):
    service, sql, root = catalogues
    service.padmin_insert('Ancien service', True, 1)
    before = position(service, 'Ancien service')
    sql('UPDATE carriere_situation SET position_admin_id=%s,position_admin_libelle=%s WHERE id=1',
        (before.ref_id, before.libelle))
    sql("INSERT INTO sme_periode(id,annee_mois,date_dernier_jour_mois) VALUES(2,'2026-08','2026-08-31')")
    sql("INSERT INTO carriere_situation(id,agent_id,sme_periode_id,est_actuelle,position_admin_libelle) VALUES(2,2,2,0,'Ancien   service')")
    service.padmin_update('Ancien service', 'Nouveau service', True, 2, expected=before)
    after = position(service, 'Nouveau service')
    assert after.ref_id == before.ref_id
    assert sql('SELECT position_admin_id,position_admin_libelle FROM carriere_situation ORDER BY id') == (
        (before.ref_id, 'Nouveau service'), (before.ref_id, 'Nouveau service'))
    assert service.padmin_usage_counts()['Nouveau service'] == 2
    with pytest.raises(ValueError, match='archives'):
        service.padmin_delete('Nouveau service', expected=after)
    payload = json.loads(sql("SELECT payload FROM audit_journal WHERE action='position_admin_update' ORDER BY id DESC LIMIT 1")[0][0])
    assert payload['before']['libelle'] == 'Ancien service'
    assert payload['after']['libelle'] == 'Nouveau service'


def test_system_positions_protected_and_custom_delete_audited(catalogues):
    service, sql, root = catalogues
    if not any(r.libelle == 'Mission' for r in service.padmins()):
        service.padmin_insert('Mission', False, 3)
    system = position(service, 'Mission')
    with pytest.raises(ValueError, match='code métier'):
        service.padmin_update('Mission', 'Mission spéciale', False, 1, expected=system)
    with pytest.raises(ValueError, match='système'):
        service.padmin_delete('Mission', expected=system)
    service.padmin_insert('Libre', False, 1)
    service.padmin_delete('Libre', expected=position(service, 'Libre'))
    payload = json.loads(sql("SELECT payload FROM audit_journal WHERE action='position_admin_delete' ORDER BY id DESC LIMIT 1")[0][0])
    assert payload['before']['libelle'] == 'Libre' and payload['after'] is None


def test_actual_grade_service_delete_has_business_trace(catalogues):
    service, sql, root = catalogues
    service.grade_insert('GTEST', 'Grade libre', 1, 10)
    service.grade_delete('GTEST', expected=grade(service))
    assert not sql("SELECT id FROM ref_grade WHERE code='GTEST'")
    payload = json.loads(sql("SELECT payload FROM audit_journal WHERE action='grade_delete' ORDER BY id DESC LIMIT 1")[0][0])
    assert payload['before']['code'] == 'GTEST' and payload['after'] is None


def image(root, name, color):
    from PySide6.QtGui import QImage, QColor
    value = QImage(32, 32, QImage.Format.Format_ARGB32)
    value.fill(QColor(color))
    path = root / name
    assert value.save(str(path))
    return path


def test_media_association_survives_rename_and_prevents_stale_overwrite(catalogues):
    from sme_gend.services.personnel_media import grade_insignia_path
    service, sql, root = catalogues
    source = image(root, 'blue.png', 'blue')
    service.save_grade('GTEST', 'Ancien grade', 1, 10, insignia_source=source, create=True)
    first = grade(service)
    old_path = grade_insignia_path('Ancien grade', [first])
    assert old_path and old_path.is_file()
    service.grade_update('GTEST', 'Nouveau grade', 1, 10, expected=first)
    fresh = grade(service)
    assert grade_insignia_path('Nouveau grade', [fresh]) == old_path
    with pytest.raises(ReferenceConflictError):
        service.save_grade('GTEST', 'Ancien grade', 1, 10, expected=first,
                           insignia_source=image(root, 'red.png', 'red'))
    assert grade_insignia_path('GTEST', [fresh]) == old_path


def test_failed_image_audit_rolls_back_grade_and_association(catalogues, monkeypatch):
    from sme_gend.services.personnel_media import grade_insignia_path
    from sme_gend.services import business_audit
    service, sql, root = catalogues
    service.save_grade('GTEST', 'Grade initial', 1, 10, insignia_source=image(root, 'blue.png', 'blue'), create=True)
    before = grade(service)
    old_path = grade_insignia_path('GTEST', [before])
    old_mapping = sql('SELECT payload FROM app_config WHERE config_key=%s', (grade_media.KEY,))[0][0]
    real = business_audit.record_business_action
    def fail(settings, **kw):
        if kw['action'] == 'grade_insignia_change':
            raise RuntimeError('SYNTHETIC IMAGE AUDIT FAILURE')
        return real(settings, **kw)
    monkeypatch.setattr(business_audit, 'record_business_action', fail)
    with pytest.raises(RuntimeError, match='IMAGE AUDIT'):
        service.save_grade('GTEST', 'Échec', 2, 20, expected=before,
                           insignia_source=image(root, 'red.png', 'red'))
    assert grade(service).libelle == 'Grade initial'
    assert sql('SELECT payload FROM app_config WHERE config_key=%s', (grade_media.KEY,))[0][0] == old_mapping
    grade_media.invalidate_grade_media_cache()
    assert grade_insignia_path('GTEST', [grade(service)]) == old_path


def test_image_only_change_invalidates_grade_snapshot(catalogues):
    service, sql, root = catalogues
    service.save_grade('GTEST', 'Grade', 1, 10, insignia_source=image(root, 'blue.png', 'blue'), create=True)
    old = grade(service)
    service.save_grade('GTEST', 'Grade', 1, 10, expected=old, insignia_source=image(root, 'red.png', 'red'))
    assert grade(service).write_token != old.write_token
    with pytest.raises(ReferenceConflictError):
        service.grade_update('GTEST', 'Grade écrasé', 2, 20, expected=old)


def test_legacy_insignia_migrates_on_rename(catalogues):
    from sme_gend.services.personnel_media import grade_insignia_path
    service, sql, root = catalogues
    service.grade_insert('GTEST', 'Ancien grade', 1, 10)
    (root / 'grades').mkdir()
    image(root / 'grades', 'ancien_grade.png', 'blue')
    service.grade_update('GTEST', 'Nouveau grade', 1, 10, expected=grade(service))
    assert grade_insignia_path('Nouveau grade', [grade(service)]) is not None


@pytest.mark.parametrize('linked', [False, True])
def test_presence_sql_and_python_exports_agree_for_custom_position(catalogues, linked):
    from sme_gend.padmin_present import invalidate_padmin_presence_cache
    from sme_gend.services.numerique_padmin import is_present_strict
    from sme_gend.repositories.mysql_agent_repository import _padmin_where_clause
    service, sql, root = catalogues
    service.padmin_insert('Service spécial', True, 2)
    entry = position(service, 'Service spécial')
    label = entry.libelle if linked else '  Service   special  '
    sql('UPDATE carriere_situation SET position_admin_id=%s,position_admin_libelle=%s WHERE id=1', (entry.ref_id if linked else None, label))
    clause, params = _padmin_where_clause('present')
    selected = sql('SELECT c.id FROM carriere_situation c LEFT JOIN ref_position_admin rpa ON '
                   'rpa.id=c.position_admin_id WHERE ' + clause, params)
    invalidate_padmin_presence_cache()
    assert selected == ((1,),)
    assert is_present_strict(SimpleNamespace(position_admin=label))
