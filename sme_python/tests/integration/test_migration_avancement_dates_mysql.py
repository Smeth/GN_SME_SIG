from datetime import date
from test_restore_mysql import isolated_mysql
from test_sme_stage_persistence_mysql import setup_services


def test_advancement_excludes_future_diplomas_and_promotions(isolated_mysql, monkeypatch):
    from sme_gend.repositories.mysql_avancement_repository import MySQLAvancementRepository
    settings, connect, params, personnel, dossier = setup_services(isolated_mysql, monkeypatch)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE carriere_situation SET grade_libelle='MDL',date_grade='2018-01-01',diplome_libelle=NULL WHERE id=1")
            cur.execute("INSERT INTO agent_diplome(agent_id,categorie,libelle_libre,date_obtention) VALUES(1,'MILITAIRE','OPJ','2026-08-20'),(1,'MILITAIRE','FUTURE DIPLOMA','2027-01-01')")
            cur.execute("INSERT INTO agent_passation_grade(agent_id,grade_libelle,date_passation,obs) VALUES(1,'MDL','2027-01-01',''),(1,'SLT','2027-02-01','sortie ecole')")
    repo = MySQLAvancementRepository(settings)
    historical = repo.load_agents(on=date(2026,9,30))[0]
    assert historical.date_grade == date(2018,1,1)
    assert not historical.issu_ecole
    assert any('OPJ' in label for label in historical.diplomes)
    assert not any('FUTURE DIPLOMA' in label for label in historical.diplomes)
    later = repo.load_agents(on=date(2027,2,28))[0]
    assert later.date_grade == date(2027,1,1) and later.issu_ecole
    assert any('FUTURE DIPLOMA' in label for label in later.diplomes)


def test_advancement_does_not_restore_future_legacy_snapshot_diploma(isolated_mysql, monkeypatch):
    from sme_gend.repositories.mysql_avancement_repository import MySQLAvancementRepository
    settings, connect, params, *_ = setup_services(isolated_mysql, monkeypatch)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE carriere_situation SET grade_libelle='MDL',date_grade='2027-01-01',diplome_libelle='FUTURE LEGACY',date_obtention='2027-01-01' WHERE id=1")
    agent = MySQLAvancementRepository(settings).load_agents(on=date(2026,9,30))[0]
    assert agent.diplomes == () and agent.date_grade is None


def test_advancement_date_uses_consulted_month_and_explicit_date_wins(isolated_mysql, monkeypatch):
    from sme_gend.services.avancement_reference_date import avancement_reference_date
    settings, *_ = setup_services(isolated_mysql, monkeypatch)
    assert avancement_reference_date(settings) == date(2026,9,30)
    assert avancement_reference_date(settings, on=date(2026,9,10)) == date(2026,9,10)
