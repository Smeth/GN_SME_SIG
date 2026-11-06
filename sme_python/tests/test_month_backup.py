from datetime import date
from decimal import Decimal
from types import SimpleNamespace
import hashlib,json
from zipfile import ZipFile
from unittest.mock import Mock
import pytest
from sme_gend.services import month_backup as m


def archive(path,payload):
    data=json.dumps(payload).encode()
    with ZipFile(path,'w') as z:
        z.writestr(m.MEMBER,data)
        z.writestr('month_sha256.txt',hashlib.sha256(data).hexdigest())
    return path


def payload():
    return {'format':'sme-month','version':1,'month':'2026-09',
            'tables':{'sme_periode':[{'id':1,'annee_mois':'2026-09'}]}}


def test_typed_values_roundtrip():
    for v in (None,12,'Texte;\nUnicode',b'\x00\xff',Decimal('12.50')):
        assert m.decode(m.encode(v))==v
    assert m.decode(m.encode(date(2026,9,15)))=='2026-09-15'


def test_inspection_rejects_other_month(tmp_path):
    p=payload();p['tables']['sme_periode'][0]['annee_mois']='2026-08'
    with pytest.raises(ValueError,match='correspondent'):
        m.inspect_month_backup(archive(tmp_path/'month.zip',p))


def test_inspection_rejects_account_tables(tmp_path):
    p=payload();p['tables']['app_config']=[]
    with pytest.raises(ValueError,match='interdite'):
        m.inspect_month_backup(archive(tmp_path/'month.zip',p))


def test_inspection_rejects_altered_archive(tmp_path):
    path=tmp_path/'month.zip'
    with ZipFile(path,'w') as z:
        z.writestr(m.MEMBER,json.dumps(payload()))
        z.writestr('month_sha256.txt','wrong')
    with pytest.raises(ValueError,match='Sauvegarde mensuelle'):m.inspect_month_backup(path)


def test_backup_permission_is_checked_before_database(monkeypatch,tmp_path):
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_permission',lambda *a:False)
    with pytest.raises(PermissionError):m.create_month_backup(object(),'2026-09',tmp_path/'x.zip')


def test_restore_permission_is_checked_before_archive(monkeypatch):
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_admin_role',lambda:False)
    with pytest.raises(PermissionError):m.restore_month_backup(object(),'missing.zip',confirmed_month='2026-09')


def test_restore_refuses_wrong_month_before_database(monkeypatch,tmp_path):
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_permission',lambda *a:True)
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_admin_role',lambda:True)
    with pytest.raises(ValueError,match='mois exact'):
        m.restore_month_backup(object(),archive(tmp_path/'x.zip',payload()),confirmed_month='2026-08')


def test_validate_refuses_rows_outside_target():
    p=payload();p['tables']['workflow_task']=[{'id':4,'mois':'2026-08'}]
    schema={t:{'columns':list(rows[0]),'pk':['id'],'fk':[]} for t,rows in p['tables'].items()}
    with pytest.raises(ValueError,match='autre mois'):m.validate_rows(p,schema)
