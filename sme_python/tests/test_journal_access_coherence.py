from unittest.mock import Mock
import pytest


def test_sensitive_export_keeps_its_module_and_filters():
    from sme_gend.services.sensitive_audit_log import SensitiveAuditQuery
    from sme_gend.services.activity_audit_log import sensitive_export_query
    query = sensitive_export_query(SensitiveAuditQuery(actor='admin', sme_mois='2026-09', limit=50))
    assert query.module == 'sensitive_data'
    assert query.actor == 'admin' and query.sme_mois == '2026-09' and query.limit == 50


@pytest.mark.parametrize('consult,export', [(False, False), (True, False), (False, True)])
def test_export_needs_both_rights_before_creating_file(monkeypatch, tmp_path, consult, export):
    from sme_gend.services.activity_audit_log import export_activity_csv, ActivityAuditQuery
    monkeypatch.setattr('sme_gend.services.auth_guard.session_can_view_sensitive_audit', lambda: consult)
    monkeypatch.setattr('sme_gend.services.auth_guard.session_can_export_documents', lambda: export)
    destination = tmp_path / 'journal.csv'
    with pytest.raises(PermissionError):
        export_activity_csv(ActivityAuditQuery(), destination, settings=None)
    assert not list(tmp_path.iterdir())


def test_local_history_purge_checks_permission_before_file_access(monkeypatch):
    from sme_gend.services import doc_gen_log
    monkeypatch.setattr('sme_gend.services.auth_guard.session_can_purge_doc_log', lambda: False)
    path = Mock()
    monkeypatch.setattr(doc_gen_log, '_log_path', path)
    with pytest.raises(PermissionError):
        doc_gen_log.clear_doc_log()
    path.assert_not_called()


def test_limited_document_history_cannot_query_other_document_types(monkeypatch):
    from sme_gend.services.doc_gen_log import _allowed_doc_types
    monkeypatch.setattr('sme_gend.services.auth_guard.session_can_view_attestation_history', lambda: True)
    monkeypatch.setattr('sme_gend.services.auth_guard.session_can_view_doc_log', lambda: False)
    assert 'soit_transmis' in _allowed_doc_types()
    assert _allowed_doc_types('liste_personnel') == []
    assert _allowed_doc_types('dossier_pdf') == []
