"""Tests sauvegarde / restauration portable."""

from __future__ import annotations

import json
from pathlib import Path

from sme_gend.services.backup_restore_service import (
    BackupManifest,
    BackupScope,
    RestoreScope,
    _app_config_rows_for_restore,
    _is_audit_mirror_data_file,
    _merge_paths_json,
    _should_run_sql_statement,
    _sql_statement_affects_table,
    format_audit_journal_summary,
)


def test_merge_paths_json_keeps_local_mysql(tmp_path: Path) -> None:
    target = tmp_path / "paths.json"
    target.write_text(
        json.dumps(
            {
                "mysql_host": "192.168.1.99",
                "mysql_port": 3306,
                "mysql_database": "smegend_prod",
                "mysql_user": "sme_local",
                "mysql_password": "secret_local",
                "template_excel_path": "C:\\local\\resultat.xlsx",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    backup = {
        "mysql_host": "10.0.0.5",
        "mysql_port": 3306,
        "mysql_database": "smegend_old",
        "mysql_user": "root",
        "mysql_password": "old_pwd",
        "template_numerique_path": "D:\\share\\numerique.xlsx",
    }
    _merge_paths_json(backup, target)
    merged = json.loads(target.read_text(encoding="utf-8"))
    assert merged["mysql_host"] == "192.168.1.99"
    assert merged["mysql_database"] == "smegend_prod"
    assert merged["mysql_user"] == "sme_local"
    assert merged["mysql_password"] == "secret_local"
    assert merged["template_numerique_path"] == "D:\\share\\numerique.xlsx"
    assert merged["template_excel_path"] == "C:\\local\\resultat.xlsx"


def test_format_audit_journal_summary_by_module() -> None:
    m = BackupManifest(
        format_version=3,
        created_at="2026-01-01",
        mysql_database="smegend",
        mysql_host="127.0.0.1",
        table_counts={"audit_journal": 120},
        config_files=[],
        audit_journal_by_module={
            "sensitive_data": 15,
            "doc_gen": 42,
            "personnel": 50,
            "system": 13,
        },
    )
    lines = format_audit_journal_summary(m)
    assert any("120 entrée" in ln for ln in lines)
    assert any("Actions sensibles : 15" in ln for ln in lines)
    assert any("Générations de documents : 42" in ln for ln in lines)


def test_sql_statement_affects_audit_journal_table() -> None:
    assert _sql_statement_affects_table("DROP TABLE IF EXISTS `audit_journal`;", "audit_journal")
    assert _sql_statement_affects_table(
        "INSERT INTO `audit_journal` (`id`) VALUES (1);", "audit_journal"
    )
    assert not _sql_statement_affects_table("INSERT INTO `agent` (`id`) VALUES (1);", "audit_journal")


def test_backup_scope_media_only_is_partial() -> None:
    sc = BackupScope(
        include_mysql=False,
        include_user_config=False,
        include_app_data=False,
        include_app_config=False,
        include_media_photos=True,
        include_media_grades=False,
    )
    assert not sc.is_full()


def test_should_run_sql_filters_table() -> None:
    stmt = "INSERT INTO `agent` (`id`) VALUES (1);"
    assert _should_run_sql_statement(stmt, allowed_tables={"agent"}, preserve_audit_journal=False)
    assert not _should_run_sql_statement(
        stmt, allowed_tables={"ref_structure"}, preserve_audit_journal=False
    )


def test_restore_keeps_local_accounts_out_of_app_config_import() -> None:
    rows = [
        {"config_key": "auth_config", "payload": {"users": [{"username": "autre"}]}},
        {"config_key": "limite_age_config", "payload": {"grades": []}},
    ]
    kept = _app_config_rows_for_restore(rows, preserve_local_accounts=True)
    assert [r["config_key"] for r in kept] == ["limite_age_config"]
    replaced = _app_config_rows_for_restore(rows, preserve_local_accounts=False)
    assert len(replaced) == 2


def test_is_audit_mirror_data_file() -> None:
    assert _is_audit_mirror_data_file("sensitive_audit_log.jsonl")
    assert _is_audit_mirror_data_file("personnel_audit_2026-01.jsonl")
    assert not _is_audit_mirror_data_file("liste_present_layout.json")
