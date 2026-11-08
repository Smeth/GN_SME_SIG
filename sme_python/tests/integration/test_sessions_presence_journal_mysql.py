"""Corrections verified against disposable MySQL schemas, never production data."""
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import pytest
from test_restore_mysql import isolated_mysql
from test_sensitive_audit_mysql import journal
from sme_gend.repositories.mysql_connection import MySQLConnection


def test_legacy_import_month_and_authoritative_other_columns(journal):
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    settings, insert = journal
    insert(module='system', action='mysql_bundle_import_access', month='2026-11',
           payload={'sme_mois': '2026-09'})
    insert(module='sensitive_data', month='2026-11', payload={'sme_mois': '2026-09'})
    repo = AppConfigRepository(settings)
    rows = repo.search_audit_journal(sme_mois='2026-09')
    assert len(rows) == 1 and rows[0]['action'] == 'mysql_bundle_import_access'
    assert rows[0]['sme_mois_context'] == '2026-09'
    assert len(repo.search_audit_journal(sme_mois='2026-11')) == 1
    assert len(repo.search_audit_journal(sme_mois='2026')) == 2


def test_merged_identity_retains_old_activity(journal):
    from sme_gend.services.activity_audit_log import ActivityAuditQuery, search_activity_audit
    settings, insert = journal
    with MySQLConnection(settings).connect() as conn:
        with conn.cursor() as cur:
            cur.execute('CREATE TABLE agent(id BIGINT PRIMARY KEY,legacy_access_id VARCHAR(64)) ENGINE=InnoDB')
            cur.execute('CREATE TABLE carriere_situation(id BIGINT PRIMARY KEY,agent_id BIGINT) ENGINE=InnoDB')
            cur.execute('CREATE TABLE agent_identity_alias(alias_legacy_id VARCHAR(64) PRIMARY KEY,canonical_agent_id BIGINT) ENGINE=InnoDB')
            cur.execute("INSERT INTO agent VALUES(1,'NEW-KEY')")
            cur.execute('INSERT INTO carriere_situation VALUES(7,1)')
            cur.execute("INSERT INTO agent_identity_alias VALUES('OLD-KEY',1)")
    insert(module='personnel', action='update', pid='OLD-KEY', nom='SYNTHETIC')
    rows = search_activity_audit(ActivityAuditQuery(personnel_id='7'), settings=settings)
    assert len(rows) == 1 and rows[0]['personnel_id'] == 'OLD-KEY'


@pytest.mark.parametrize('mode', ['off', 'single', 'limited'])
def test_revoked_token_is_rejected_in_every_policy(journal, mode):
    from sme_gend.services import auth_session_registry as registry
    from sme_gend.config.auth_config import AuthConfig, AuthUser
    from sme_gend.services.auth_service import AuthService, AuthSession
    settings, _ = journal
    cfg = AuthConfig(enabled=True, concurrent_sessions_mode=mode)
    user = AuthUser('SYNTHETIC', '', '', role='operateur', session_rev=3)
    cfg.users = [user]
    exp = datetime.now(timezone.utc) + timedelta(hours=1)
    token, error = registry.try_register_session(settings, cfg, username=user.username,
        session_rev=3, client_instance_id='CLIENT', machine_label='SYNTHETIC', expires_at=exp)
    assert token and error is None
    auth = AuthService.__new__(AuthService)
    auth._config = cfg; auth._enforced = True; auth.reload_config = lambda: None
    auth._settings = lambda: settings
    auth._session = AuthSession(user.username, user.username, user.role, exp, session_rev=3, session_token=token)
    assert auth.sync_session_from_config() == 'ok'
    registry.unregister_session(settings, token)
    assert auth.sync_session_from_config() == 'invalid'
    assert auth.current_session() is None
    auth._session = AuthSession(user.username, user.username, user.role, exp, session_rev=3, session_token=token)
    with pytest.raises(PermissionError):
        auth.ensure_active_session_registered()
    assert not registry.heartbeat_session(settings, session_token=token, username=user.username, session_rev=3)


def test_authorized_revision_change_keeps_only_current_token(journal):
    from sme_gend.services import auth_session_registry as registry
    from sme_gend.config.auth_config import AuthConfig, AuthUser
    from sme_gend.services.auth_service import AuthService, AuthSession
    settings, _ = journal
    cfg = AuthConfig(enabled=True)
    exp = datetime.now(timezone.utc) + timedelta(hours=1)
    tokens = [registry.try_register_session(settings, cfg, username='SYNTHETIC', session_rev=1,
              client_instance_id=client, machine_label=client, expires_at=exp)[0] for client in ('ONE','TWO')]
    auth = AuthService.__new__(AuthService); auth._settings = lambda: settings
    auth._session = AuthSession('SYNTHETIC', 'SYNTHETIC', 'operateur', exp, session_rev=1, session_token=tokens[0])
    user = AuthUser('SYNTHETIC', '', '', session_rev=1)
    auth._bump_user_session_rev(user)
    assert registry.heartbeat_session(settings, session_token=tokens[0], username='SYNTHETIC', session_rev=2)
    assert not registry.heartbeat_session(settings, session_token=tokens[1], username='SYNTHETIC', session_rev=1)
    registry.unregister_session(settings, tokens[0])
    with pytest.raises(PermissionError):
        registry.advance_session_revision(settings, session_token=tokens[0], username='SYNTHETIC', previous_revision=2, revision=3)


def test_session_revocation_rolls_back_when_audit_fails(journal):
    from sme_gend.services import auth_session_registry as registry
    from sme_gend.config.auth_config import AuthConfig, AuthUser
    from sme_gend.services.auth_service import AuthService, AuthSession
    settings, _ = journal
    exp = datetime.now(timezone.utc) + timedelta(hours=1)
    cfg = AuthConfig(enabled=True, users=[AuthUser('SYNTHETIC', '', ''),
        AuthUser('ROOT', '', '', role='super_admin')])
    token, _ = registry.try_register_session(settings, cfg, username='SYNTHETIC', session_rev=1,
        client_instance_id='SYNTHETIC', machine_label='SYNTHETIC', expires_at=exp)
    auth = AuthService.__new__(AuthService)
    auth._config=cfg; auth._enforced=True; auth._settings=lambda:settings
    auth._session=AuthSession('ROOT','ROOT','super_admin',exp,session_rev=0,session_token='ACTOR')
    auth._assert_users_admin_perm=lambda permission:None
    def failed(**kw):
        raise RuntimeError('SYNTHETIC AUDIT FAILURE')
    auth._audit=failed
    with pytest.raises(RuntimeError, match='AUDIT FAILURE'):
        auth.revoke_active_session(token)
    assert registry.heartbeat_session(settings,session_token=token,username='SYNTHETIC',session_rev=1)


def test_real_session_revocation_preserves_administrator_hierarchy(journal):
    from sme_gend.services.auth_service import AuthService
    from sme_gend.services import auth_session_registry as registry
    settings, _ = journal
    auth = AuthService()
    auth.ensure_default_admin('synthetic-root-password')
    auth.add_role('delegate', 'Gestionnaire', permissions=['admin.users.accounts', 'admin.users.settings'])
    auth.add_user('manager', 'synthetic-manager-password', role='delegate')
    auth.add_user('agent', 'synthetic-agent-password')
    auth.set_enabled(True)
    root = auth.authenticate('admin', 'synthetic-root-password')
    assert root and root.session_token
    auth.authenticate('manager', 'synthetic-manager-password')
    with pytest.raises(ValueError, match='super administrateur'):
        auth.revoke_active_session(root.session_token)
    with pytest.raises(ValueError, match='super administrateur'):
        auth.revoke_all_sessions_for_user('admin')
    assert registry.heartbeat_session(settings, session_token=root.session_token,
        username='admin', session_rev=0)
    token, error = registry.try_register_session(settings, auth.config, username='agent', session_rev=0,
        client_instance_id='REMOTE', machine_label='Poste synthétique',
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    assert not error and token
    auth.revoke_active_session(token)
    assert not registry.heartbeat_session(settings, session_token=token, username='agent', session_rev=0)


@pytest.mark.parametrize('mode', ['present', 'stage_interne', 'les_deux'])
def test_sql_and_python_presence_rules_agree(journal, mode):
    from sme_gend.repositories.mysql_agent_repository import _padmin_where_clause
    from sme_gend.padmin_present import PadminPresenceIndex, padmin_counts_as_present
    settings, _ = journal
    labels = [None, '', '  ', 'Présent', 'Non présent', 'non-présent', 'nonpresent',
              'Pas présent', 'absent présent', 'représentant', 'Agent présent spécial',
              'StageInterne', ' STAGE   INTERNE ', 'Présent mission', 'Mission', 'Malade']
    flags = {'Présent': False, 'StageInterne': False, 'Mission': True}
    index = PadminPresenceIndex({key.casefold(): val for key, val in flags.items()})
    with MySQLConnection(settings).connect() as conn:
        with conn.cursor() as cur:
            cur.execute('CREATE TABLE ref_position_admin(id INT PRIMARY KEY,libelle VARCHAR(100),compte_comme_present BOOL)')
            cur.execute('CREATE TABLE carriere_situation(id INT PRIMARY KEY,position_admin_id INT,position_admin_libelle VARCHAR(100))')
            for rid, (label, flag) in enumerate(flags.items(), 1):
                cur.execute('INSERT INTO ref_position_admin VALUES(%s,%s,%s)', (rid,label,flag))
            ids = {label: rid for rid,label in enumerate(flags,1)}
            for rid,label in enumerate(labels,1):
                cur.execute('INSERT INTO carriere_situation VALUES(%s,%s,%s)', (rid, ids.get(label), label))
            where, params = _padmin_where_clause(mode)
            cur.execute('SELECT c.id FROM carriere_situation c LEFT JOIN ref_position_admin rpa ON rpa.id=c.position_admin_id WHERE '+where, params)
            actual = {row[0] for row in cur.fetchall()}
    expected = {rid for rid,label in enumerate(labels,1) if padmin_counts_as_present(label, mode, index=index)}
    assert actual == expected


def test_import_preserves_administrator_presence_flag(journal):
    from sme_gend.services.mysql_ref_etl import _upsert_ref_padmin
    settings, _ = journal
    with MySQLConnection(settings).connect() as conn:
        with conn.cursor() as cur:
            cur.execute('CREATE TABLE ref_position_admin(id INT AUTO_INCREMENT PRIMARY KEY,libelle VARCHAR(100) UNIQUE,compte_comme_present BOOL,ordre_tri INT)')
            _upsert_ref_padmin(cur, 'Présent', 0, 1)
            _upsert_ref_padmin(cur, 'Présent', 1, None)
            cur.execute('SELECT compte_comme_present FROM ref_position_admin')
            assert cur.fetchone()[0] == 0


@pytest.mark.parametrize('audit_fails', [False, True])
def test_month_data_and_main_audit_commit_together(journal, tmp_path, monkeypatch, audit_fails):
    from sme_gend.services.empty_sme_month import empty_sme_month
    from sme_gend.services import audit_log
    settings, _ = journal
    tables = ('agent_diplome', 'agent_distinction', 'agent_affectation', 'agent_mission',
              'agent_malade', 'agent_desertion', 'parcours_stage', 'mouvement_effectif',
              'agent_conge', 'carriere_situation')
    with MySQLConnection(settings).connect() as conn:
        with conn.cursor() as cur:
            for name in tables:
                cur.execute(f'CREATE TABLE {name}(import_lot_id BIGINT,sme_periode_id BIGINT) ENGINE=InnoDB')
            cur.execute('CREATE TABLE sme_periode(id BIGINT PRIMARY KEY,annee_mois VARCHAR(7)) ENGINE=InnoDB')
            cur.execute("INSERT INTO sme_periode VALUES(1,'2026-09')")
            cur.execute('CREATE TABLE import_lot(id BIGINT,sme_periode_id BIGINT) ENGINE=InnoDB')
            cur.execute('CREATE TABLE etl_erreur(import_lot_id BIGINT) ENGINE=InnoDB')
            for name in ('workflow_task','career_alert_ack','career_alert_ack_occurrence'):
                cur.execute(f'CREATE TABLE {name}(mois VARCHAR(7)) ENGINE=InnoDB')
            cur.execute('CREATE TABLE month_marker(mois VARCHAR(7)) ENGINE=InnoDB')
            cur.execute("INSERT INTO month_marker VALUES('2026-09'),('2026-08')")
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_admin_role', lambda: True)
    monkeypatch.setattr('sme_gend.repositories.mysql_common.ensure_mysql_schema', lambda s: None)
    monkeypatch.setattr('sme_gend.services.app_config_store.ensure_central_config_ready', lambda s: None)
    monkeypatch.setattr('sme_gend.services.mysql_import_period.ensure_sme_periode', lambda *a: 1)
    monkeypatch.setattr('sme_gend.services.data_sync.publish_data_change', lambda *a, **kw: None)
    monkeypatch.setattr('sme_gend.services.sme_month_closure.sme_archives_dir', lambda: tmp_path)
    backup = tmp_path/'backup.zip'; backup.write_bytes(b'SYNTHETIC')
    archive = tmp_path/'sme_bundle_2026-09_20260930T120000Z.mdb'; archive.write_bytes(b'SYNTHETIC')
    monkeypatch.setattr('sme_gend.services.backup_restore_service.create_backup_to_default_dir', lambda *a, **kw: backup)
    monkeypatch.setattr('sme_gend.services.backup_restore_service.inspect_backup', lambda *a: None)
    def purge(s, month, **kw):
        with MySQLConnection(s).connect() as conn:
            with conn.cursor() as cur:
                cur.execute('DELETE FROM month_marker WHERE mois=%s', (month,))
        return {'marker': 1}
    monkeypatch.setattr('sme_gend.services.empty_sme_month.purge_sme_periode_for_reimport', purge)
    append = audit_log.append_audit_event
    def audited(**kw):
        append(**kw)
        if audit_fails:
            raise RuntimeError('SYNTHETIC AUDIT FAILURE')
    monkeypatch.setattr(audit_log, 'append_audit_event', audited)
    if audit_fails:
        with pytest.raises(RuntimeError, match='AUDIT FAILURE'):
            empty_sme_month(settings, '2026-09')
        assert archive.exists()
    else:
        assert empty_sme_month(settings, '2026-09')['archives_removed'] == 1
        assert not archive.exists()
    with MySQLConnection(settings).connect() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT mois FROM month_marker ORDER BY mois')
            assert [row[0] for row in cur.fetchall()] == (['2026-08', '2026-09'] if audit_fails else ['2026-08'])
            cur.execute("SELECT COUNT(*) FROM audit_journal WHERE action='sme_month_emptied'")
            assert cur.fetchone()[0] == (0 if audit_fails else 1)
