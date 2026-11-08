import json
from zipfile import ZipFile
from test_restore_mysql import isolated_mysql


def test_sql_json_and_statistics_share_one_snapshot_during_concurrent_commit(isolated_mysql,monkeypatch,tmp_path):
    from sme_gend.services import backup_restore_service as backup
    settings,connect,params=isolated_mysql
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute('CREATE TABLE agent(id INT PRIMARY KEY,noms TEXT) ENGINE=InnoDB')
            cursor.execute('CREATE TABLE app_config(config_key VARCHAR(64) PRIMARY KEY,payload JSON,updated_by VARCHAR(64),updated_at DATETIME(3)) ENGINE=InnoDB')
            cursor.execute('CREATE TABLE audit_journal(id INT PRIMARY KEY,module VARCHAR(64)) ENGINE=InnoDB')
            cursor.execute("INSERT INTO agent VALUES(1,'BEFORE')")
            cursor.execute("INSERT INTO app_config VALUES('test',%s,'SYNTHETIC',NOW(3))",(json.dumps({'point':'BEFORE'}),))
            cursor.execute("INSERT INTO audit_journal VALUES(1,'test')")
    original=backup._capture_backup_sidecars
    def capture(connection,names,result):
        original(connection,names,result)
        with connect(**params) as other:
            with other.cursor() as cursor:
                cursor.execute("UPDATE agent SET noms='AFTER'")
                cursor.execute("UPDATE app_config SET payload=%s",(json.dumps({'point':'AFTER'}),))
                cursor.execute("INSERT INTO audit_journal VALUES(2,'test')")
    monkeypatch.setattr(backup,'_capture_backup_sidecars',capture)
    scope=backup.BackupScope(include_user_config=False,include_app_data=False,include_media_photos=False,
        include_media_grades=False,include_publications=False)
    path=tmp_path/'snapshot.zip'
    manifest=backup.create_backup(settings,path,scope=scope)
    assert manifest.audit_journal_by_module=={'test':1}
    assert manifest.snapshot_consistency['sql_and_shared_config']=='same_transaction'
    with ZipFile(path) as z:
        rows=json.loads(z.read(backup.APP_CONFIG_DUMP_NAME))
        sql=z.read(backup.SQL_DUMP_NAME).decode()
    assert rows[0]['payload']=={'point':'BEFORE'}
    assert '0x'+b'BEFORE'.hex() in sql and '0x'+b'AFTER'.hex() not in sql
    assert manifest.table_counts['audit_journal']==1
    assert backup.inspect_backup(path).file_inventory==manifest.file_inventory
