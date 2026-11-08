from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from sme_gend.config.settings import Settings
from sme_gend.services import sme_publication_registry as registry
from sme_gend.services.sme_export_revision import encode_export_dataset, decode_export_dataset


def test_logical_footprint_ignores_mapping_and_query_order_but_not_values_or_model():
    from test_sme_export_revision import person
    rows = [person("B"), replace(person("A"), id="2")]
    first = {"rows": rows, "grades": [], "detaches": [], "movements": [], "details": {"two": {}, "one": {}}}
    second = {"details": {"one": {}, "two": {}}, "movements": [], "detaches": [], "grades": [], "rows": list(reversed(rows))}
    assert registry.dataset_footprint(first, "MODEL-A") == registry.dataset_footprint(second, "MODEL-A")
    assert registry.dataset_footprint(first, "MODEL-A") != registry.dataset_footprint(second, "MODEL-B")
    second["rows"][0] = replace(second["rows"][0], noms="CHANGED")
    assert registry.dataset_footprint(first, "MODEL-A") != registry.dataset_footprint(second, "MODEL-A")
    assert registry.dataset_footprint(first, "MODEL-A") == registry.dataset_footprint(decode_export_dataset(encode_export_dataset(first)), "MODEL-A")


@pytest.mark.parametrize("operation", [
    lambda settings: registry.list_publications(settings),
    lambda settings: registry.get_publication(settings, 1),
    lambda settings: registry.verify_publication(settings, 1),
    lambda settings: registry.download_publication(settings, 1, Path("never-written.xlsx")),
    lambda settings: registry.validate_publication(settings, 1, expected_version=1),
    lambda settings: registry.replace_publication(settings, 1, expected_version=1, expected_current_id=2, reason="TEST"),
    lambda settings: registry.import_legacy_revision(settings, Path("never-opened")),
    lambda settings: registry.require_validated_publication(settings, "2026-11"),
])
def test_permissions_are_enforced_before_any_file_or_database_access(monkeypatch, operation):
    monkeypatch.setattr("sme_gend.services.auth_guard.session_has_permission", lambda permission: False)
    monkeypatch.setattr(registry, "require_capability", lambda *a: pytest.fail("DB touched before permission check"))
    with pytest.raises(PermissionError):
        operation(Settings(database_path=Path("unused")))


def test_confirmed_commit_error_becomes_a_receipt_warning():
    from sme_gend.repositories.mysql_connection import CommittedMirrorError
    publication = registry.Publication(1, "TEST", "2026-11", 1, "valide", 2,
        datetime(2026, 11, 8), "TEST", datetime(2026, 11, 8), "TEST", "", None, "FOOTPRINT", {})
    result = {"publication": publication}
    with registry._confirmed_result(result):
        raise CommittedMirrorError("copy mirror failed")
    assert result["publication"].status == "valide"
    assert "confirmée" in result["publication"].operation_warning
    assert "Ne recommencez" in result["publication"].operation_warning


def test_capability_contract_is_cached_only_for_the_current_transaction(monkeypatch):
    from sme_gend.services import schema_migrations
    from sme_gend.repositories import mysql_connection
    settings = Settings(database_path=Path("unused"))
    calls = []
    monkeypatch.setattr(schema_migrations, "schema_migration_status", lambda st: calls.append(st) or [
        {"capability": "publication_registry", "state": "applied"},
        {"capability": "temporal_projection", "state": "applied"}])
    for _ in range(2):
        token = mysql_connection._transaction_cache.set({})
        try:
            for _ in range(20):
                assert schema_migrations.capability_available(settings, "publication_registry")
                assert schema_migrations.capability_available(settings, "temporal_projection")
        finally:
            mysql_connection._transaction_cache.reset(token)
    assert len(calls) == 2
    schema_migrations.capability_available(settings, "publication_registry")
    schema_migrations.capability_available(settings, "publication_registry")
    assert len(calls) == 4  # Outside a frame, restoration/DDL changes stay visible.


def test_capture_continues_with_a_warning_after_a_confirmed_projection_commit(monkeypatch):
    from contextlib import contextmanager
    from sme_gend.repositories.mysql_connection import CommittedMirrorError
    from sme_gend.services import numerique_excel_export as export
    settings = Settings(database_path=Path("unused"))
    @contextmanager
    def committed(*a, **kw):
        yield
        raise CommittedMirrorError("projection mirror failed after commit")
    monkeypatch.setattr("sme_gend.services.publication_write_barrier.publication_write_barrier", committed)
    monkeypatch.setattr("sme_gend.services.month_projection.refresh_month_projection", lambda *a: None)
    monkeypatch.setattr(export, "_capture_numerique_workbook_data", lambda *a, **kw: ({"rows": []}, {}))
    dataset, source = export._capture_publication_inputs(settings, "2026-11", [], settings)
    assert dataset == {"rows": []} and "confirmée" in source["projection_warning"]


def test_check_contract_normalizes_mysql_metadata_escaping_without_accepting_weaker_rules():
    from sme_gend.services.schema_contracts import _check_expression
    expected = _check_expression("category IN ('GAIN','PERTE')")
    assert _check_expression("(`category` in (_utf8mb4\\'GAIN\\',_utf8mb4\\'PERTE\\'))") == expected
    assert _check_expression("category IN ('GAIN','PERTE') OR 1=1") != expected


def test_restoring_document_tables_alone_cannot_reset_live_central_revisions(monkeypatch):
    from types import SimpleNamespace
    from sme_gend.services import configuration_restore as restore
    from sme_gend.services.schema_migrations import PUBLICATION_TABLES
    monkeypatch.setattr('sme_gend.services.database_maintenance.check_owned_barrier', lambda *a: None)
    def reject_apply(*a):
        pytest.fail('A partial document restore must be rejected before applying shared configuration')
    prepared = SimpleNamespace(settings=Settings(database_path=Path('unused')), conn=object(),
        original=['agent', 'sme_periode', *PUBLICATION_TABLES], apply_config=reject_apply)
    # All four document tables are present; this still cannot replace a live
    # registry while retaining newer workforce data and its operational state.
    document_tables = set(PUBLICATION_TABLES) - {'sme_publication_write_gate'}
    with pytest.raises(restore.IncompatibleRestoreError, match='pas être restaurés séparément'):
        restore.apply_prepared_configuration(prepared, [], None, created_tables=document_tables,
            preserve_audit=True, full_requested=False)
