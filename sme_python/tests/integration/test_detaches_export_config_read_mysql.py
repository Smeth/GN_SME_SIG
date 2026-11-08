"""Optional export layout works on a pre-migration database without writes."""
import json

import pytest
from test_restore_mysql import isolated_mysql

from sme_gend.models.detaches_file_config import DetachesFileConfig
from sme_gend.repositories.mysql_connection import mysql_read_snapshot
from sme_gend.services.detaches_config_service import DetachesConfigService


@pytest.mark.parametrize('state',['absent_table','absent_key','existing','invalid'])
def test_export_layout_read_preserves_legacy_database_and_missing_config(isolated_mysql,monkeypatch,state):
    settings,connect,params=isolated_mysql
    payload={'options':{'titre_ligne_feuille':'SYNTHETIC EXPORT','unknown':'PRESERVE'},
             'structures':[{'sigle':'SYN','ordre':3,'titres_lignes':['SYNTHETIC UNIT']}]}
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            if state!='absent_table':
                cursor.execute('CREATE TABLE app_config(config_key VARCHAR(64) PRIMARY KEY,payload JSON) ENGINE=InnoDB')
                if state in ('existing','invalid'):
                    cursor.execute('INSERT INTO app_config VALUES(%s,%s)',
                                   ('detaches_config',json.dumps(payload if state=='existing' else ['invalid'])))
    def read_state():
        with connect(**params) as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() ORDER BY TABLE_NAME')
                tables=cursor.fetchall()
                rows=()
                if state!='absent_table':
                    cursor.execute('SELECT config_key,payload FROM app_config ORDER BY config_key')
                    rows=cursor.fetchall()
                return tables,rows
    before=read_state()
    def forbidden(*args,**kwargs):raise AssertionError('The direct export may not initialize/edit a shared layout')
    monkeypatch.setattr('sme_gend.services.shared_config_write.save_shared_model',forbidden)
    monkeypatch.setattr('sme_gend.services.shared_config_write.may_write_config',forbidden)
    with mysql_read_snapshot(settings):
        if state=='invalid':
            with pytest.raises(RuntimeError,match='configuration partagée'):
                DetachesConfigService(settings).load_for_export()
        else:
            config=DetachesConfigService(settings).load_for_export()
            expected=DetachesFileConfig.from_dict(payload) if state=='existing' else DetachesFileConfig()
            assert config.to_dict()==expected.to_dict()
            assert config.write_token is None
    assert read_state()==before
    assert not any(name[0]=='_sme_schema_migration' for name in before[0])
