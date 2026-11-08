from dataclasses import replace
from datetime import datetime
from openpyxl import load_workbook
import pytest
from test_restore_mysql import isolated_mysql
from test_sme_export_snapshot_mysql import seed
from test_publication_registry_mysql import install_registry


@pytest.mark.parametrize('migrated',[False,True])
def test_interval_export_uses_one_headcount_and_deduplicates_observations(isolated_mysql,monkeypatch,tmp_path,migrated):
    from sme_gend.services.period_activity_report import export_period_activity
    settings,connect,params=seed(isolated_mysql)
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE agent SET noms='=SYNTHETIC_FORMULA' WHERE id=1")
            cursor.execute("INSERT INTO agent_passation_grade(agent_id,grade_libelle,date_passation) VALUES(1,'CNE','2026-11-10'),(1,'CNE','2026-11-10'),(1,'ADC','2026-12-05')")
            cursor.execute("INSERT INTO agent_diplome(agent_id,categorie,libelle_libre,date_obtention) VALUES(1,'MILITAIRE','UNDATED',NULL)")
            cursor.execute("INSERT INTO sme_periode(annee_mois,date_dernier_jour_mois,statut) VALUES('2026-10','2026-10-31','Fermé')")
    if migrated:install_registry(settings,tmp_path,monkeypatch)
    path=tmp_path/'period.xlsx'
    data=export_period_activity(settings,'2026-11-01','2026-11-30',path)
    assert data['reference_headcount']==1 and data['reference_date']=='2026-11-30'
    promotions=[row for row in data['events'] if row['kind']=='Passation de grade']
    assert len(promotions)==1 and promotions[0]['observations']==2
    assert not any('ADC' in row['detail'] for row in data['events'])
    assert data['undated_records']['agent_diplome']==1
    book=load_workbook(path)
    assert len(book.sheetnames)==3 and isinstance(book['Synthèse période']['B1'].value,datetime)
    assert all(cell.data_type!='f' for row in book['Événements datés'] for cell in row)
    book.close()
    from sme_gend.services.period_activity_report import capture_period_activity
    partial=capture_period_activity(settings,'2026-11-01','2026-11-15')
    # A closed period label alone is not evidence that its workforce was zero.
    assert partial['reference_date'] is None and partial['reference_headcount'] is None
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO import_lot(sme_periode_id,chemin_fichier) SELECT id,'synthetic-empty-observation.mdb' FROM sme_periode WHERE annee_mois='2026-10'")
    observed_empty=capture_period_activity(settings,'2026-11-01','2026-11-15')
    assert observed_empty['reference_date']=='2026-10-31' and observed_empty['reference_headcount']==0
