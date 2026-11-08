"""A present-list preview/export must describe one month at one database instant."""
from dataclasses import replace
import json

from openpyxl import load_workbook
import pytest
from test_restore_mysql import isolated_mysql


def seed(isolated_mysql):
    from sme_gend.repositories.mysql_common import ensure_mysql_schema
    from sme_gend.models.liste_layout import ListeLayoutConfig,SectionBlock
    settings,connect,params=isolated_mysql
    settings=replace(settings,mysql_view_mois='2026-11',template_liste_present_path=None)
    ensure_mysql_schema(settings)
    cfg=ListeLayoutConfig(sections=[SectionBlock(id='test',order=0,structure_code='PRESENT_TEST')])
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO sme_periode(id,annee_mois,date_dernier_jour_mois,statut) VALUES"
                "(1,'2026-11','2026-11-30','Ouvert'),(2,'2026-10','2026-10-31','Fermé')")
            cursor.execute("INSERT INTO ref_structure(code,designation) VALUES('PRESENT_TEST','DIRECTION INITIALE')")
            structure_id=cursor.lastrowid
            cursor.execute("INSERT INTO ref_grade(code,libelle) VALUES('TEST_P','GRADE INITIAL')")
            grade_id=cursor.lastrowid
            cursor.execute("INSERT INTO agent(id,legacy_access_id,noms) VALUES(1,'TEST42','AGENT INITIAL')")
            cursor.execute("INSERT INTO carriere_situation(id,agent_id,sme_periode_id,structure_id,structure_code,grade_id,"
                "grade_libelle,position_admin_libelle,date_debut,date_fin,est_actuelle) "
                "VALUES(1,1,1,%s,'PRESENT_TEST',%s,'GRADE INITIAL','Présent','2026-11-01','2026-11-30',1)",
                (structure_id,grade_id))
            cursor.execute("INSERT INTO app_config(config_key,payload) VALUES('liste_present_layout',%s)",
                (json.dumps(cfg.to_dict()),))
    return settings,connect,params,grade_id,structure_id


def test_present_list_captures_personnel_layout_and_references_at_one_instant(isolated_mysql,monkeypatch):
    from sme_gend.services.liste_present_service import ListePresentService
    from sme_gend.services.layout_config_service import LayoutConfigService
    from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
    settings,connect,params,gid,sid=seed(isolated_mysql)
    service=ListePresentService(settings,LayoutConfigService(settings))
    original=ListeGeneraleRepository.fetch_for_presence_mode
    changed=[]
    def concurrent_update(repository,*args,**kwargs):
        values=original(repository,*args,**kwargs)
        if not changed:
            with connect(**params) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("UPDATE agent SET noms='AGENT MODIFIE' WHERE id=1")
                    cursor.execute("UPDATE ref_structure SET designation='DIRECTION MODIFIEE' WHERE id=%s",(sid,))
                    cursor.execute("UPDATE ref_grade SET libelle='GRADE MODIFIE' WHERE id=%s",(gid,))
            changed.append(True)
        return values
    monkeypatch.setattr(ListeGeneraleRepository,'fetch_for_presence_mode',concurrent_update)
    snapshots=[]
    html=service.build_preview_html(on_snapshot=snapshots.append)
    assert changed and 'AGENT INITIAL' in html and 'AGENT MODIFIE' not in html
    assert 'DIRECTION INITIALE' in html and 'DIRECTION MODIFIEE' not in html
    assert any(grade.libelle=='GRADE INITIAL' for grade in snapshots[0].grades)
    assert snapshots[0].month=='2026-11'
    later=service.build_preview_html()
    assert 'AGENT MODIFIE' in later and 'DIRECTION MODIFIEE' in later


def test_first_preview_without_layout_never_writes_shared_configuration(isolated_mysql,monkeypatch):
    from sme_gend.services.liste_present_service import ListePresentService
    from sme_gend.services.layout_config_service import LayoutConfigService
    settings,connect,params,_,_=seed(isolated_mysql)
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM app_config WHERE config_key='liste_present_layout'")
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_permission',lambda key:False)
    html=ListePresentService(settings,LayoutConfigService(settings)).build_preview_html()
    assert 'AGENT INITIAL' in html
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM app_config WHERE config_key='liste_present_layout'")
            assert cursor.fetchone()[0]==0


def test_export_of_preview_keeps_captured_rows_after_later_database_edits(isolated_mysql,monkeypatch,tmp_path):
    from sme_gend.services.liste_present_service import ListePresentService
    from sme_gend.services.layout_config_service import LayoutConfigService
    settings,connect,params,_,_=seed(isolated_mysql)
    service=ListePresentService(settings,LayoutConfigService(settings))
    snapshots=[];service.build_preview_html(on_snapshot=snapshots.append)
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE agent SET noms='APRES APERCU' WHERE id=1")
    monkeypatch.setattr('sme_gend.services.doc_gen_log.log_doc_generation',lambda **kw:None)
    destination=tmp_path/'present.xlsx'
    service.export_excel(destination,snapshot=snapshots[0])
    book=load_workbook(destination,data_only=True)
    try:
        values=[str(value) for row in book.worksheets[0].values for value in row if value is not None]
        assert 'AGENT INITIAL' in values and 'APRES APERCU' not in values
        assert '2026-11' in book.worksheets[0].oddHeader.center.text
    finally:book.close()


def test_module_never_includes_empty_unknown_stage_or_custom_present_flags(isolated_mysql,monkeypatch,tmp_path):
    from sme_gend.services.liste_present_service import ListePresentService
    from sme_gend.services.layout_config_service import LayoutConfigService
    settings,connect,params,_,_=seed(isolated_mysql)
    labels=['Présent au corps',' PRESENT ',None,'','À vérifier','Mission','StageInterne',
        'Malade','CongéAdmin','Détaché','Service spécial','Présent exceptionnel']
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO ref_position_admin(libelle,compte_comme_present) VALUES"
                "('Service spécial',1),('Présent exceptionnel',1)")
            for aid,label in enumerate(labels,2):
                cursor.execute('INSERT INTO agent(id,legacy_access_id,noms) VALUES(%s,%s,%s)',
                    (aid,f'SYNTHETIC_ID_{aid}',f'SYNTHETIC {aid:03}'))
                cursor.execute("INSERT INTO carriere_situation(agent_id,sme_periode_id,structure_code,"
                    "position_admin_libelle,date_debut,date_fin) VALUES(%s,1,'PRESENT_TEST',%s,'2026-11-01','2026-11-30')",(aid,label))
    service=ListePresentService(settings,LayoutConfigService(settings))
    snapshots=[];html=service.build_preview_html(on_snapshot=snapshots.append)
    assert 'AGENT INITIAL' in html and 'SYNTHETIC 002' in html and 'SYNTHETIC 003' in html
    assert all(f'SYNTHETIC {aid:03}' not in html for aid in range(4,len(labels)+2))
    monkeypatch.setattr('sme_gend.services.doc_gen_log.log_doc_generation',lambda **kw:None)
    destination=tmp_path/'strict.xlsx';service.export_excel(destination,snapshot=snapshots[0])
    book=load_workbook(destination,data_only=True)
    try:
        names=[row[2] for row in book.worksheets[0].values if len(row)>2]
        assert 'AGENT INITIAL' in names and 'SYNTHETIC 002' in names and 'SYNTHETIC 003' in names
        assert all(f'SYNTHETIC {aid:03}' not in names for aid in range(4,len(labels)+2))
    finally:book.close()
