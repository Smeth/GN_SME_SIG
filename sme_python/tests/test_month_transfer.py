"""Archives mensuelles et SQL global sur deux mois entièrement synthétiques."""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import json
import re
import zipfile
import pytest
from sme_gend.services import month_transfer as service
from sme_gend.config.settings import Settings
from sme_gend.repositories.mysql_connection import mysql_database_key


def table(name,body,rows):
    _, result = service.schema_from_ddl(f"CREATE TABLE `{name}` ({body}) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")
    result["rows"] = rows
    return result


@pytest.fixture
def source():
    return {
        "sme_periode": table("sme_periode","`id` BIGINT AUTO_INCREMENT, `annee_mois` CHAR(7), `statut` VARCHAR(32), `codsme` VARCHAR(32), PRIMARY KEY (`id`), UNIQUE KEY `uk_month` (`annee_mois`)",
            [dict(id=1,annee_mois="2026-01",statut="Ouvert",codsme="JAN"),dict(id=2,annee_mois="2026-09",statut="Ouvert",codsme="SEP")]),
        "agent": table("agent","`id` BIGINT AUTO_INCREMENT, `legacy_access_id` VARCHAR(64), `noms` VARCHAR(512), `matricule_militaire` VARCHAR(64), PRIMARY KEY (`id`), UNIQUE KEY `uk_legacy` (`legacy_access_id`)",
            [dict(id=5,legacy_access_id="A",noms="AGENT JANVIER",matricule_militaire="JAN"),dict(id=8,legacy_access_id="B",noms="AGENT SEPTEMBRE",matricule_militaire="SEP")]),
        "ref_grade": table("ref_grade","`id` BIGINT AUTO_INCREMENT, `code` VARCHAR(64), `libelle` VARCHAR(64), PRIMARY KEY (`id`), UNIQUE KEY `uk_code` (`code`)",
            [dict(id=3,code="G1",libelle="Grade commun")]),
        "import_lot": table("import_lot","`id` BIGINT AUTO_INCREMENT, `sme_periode_id` BIGINT, `checksum` VARCHAR(64), PRIMARY KEY (`id`), FOREIGN KEY (`sme_periode_id`) REFERENCES `sme_periode` (`id`)",
            [dict(id=20,sme_periode_id=1,checksum="JAN"),dict(id=21,sme_periode_id=2,checksum="SEP")]),
        "carriere_situation": table("carriere_situation","`id` BIGINT AUTO_INCREMENT, `agent_id` BIGINT, `sme_periode_id` BIGINT, `grade_id` BIGINT, `import_lot_id` BIGINT, `est_actuelle` INT, `date_debut` DATE, `date_fin` DATE, `obs` TEXT, PRIMARY KEY (`id`), FOREIGN KEY (`agent_id`) REFERENCES `agent` (`id`), FOREIGN KEY (`sme_periode_id`) REFERENCES `sme_periode` (`id`), FOREIGN KEY (`grade_id`) REFERENCES `ref_grade` (`id`), FOREIGN KEY (`import_lot_id`) REFERENCES `import_lot` (`id`)",
            [dict(id=100,agent_id=5,sme_periode_id=1,grade_id=3,import_lot_id=20,est_actuelle=1,date_debut="2026-01-01",date_fin=None,obs="JAN ORIGINAL"),
             dict(id=101,agent_id=8,sme_periode_id=2,grade_id=3,import_lot_id=21,est_actuelle=1,date_debut="2026-09-01",date_fin=None,obs="SEP ORIGINAL")]),
        "agent_mission": table("agent_mission","`id` BIGINT AUTO_INCREMENT, `agent_id` BIGINT, `sme_periode_id` BIGINT, `libelle` TEXT, PRIMARY KEY (`id`), FOREIGN KEY (`agent_id`) REFERENCES `agent` (`id`), FOREIGN KEY (`sme_periode_id`) REFERENCES `sme_periode` (`id`)",
            [dict(id=40,agent_id=5,sme_periode_id=1,libelle="Mission janvier"),dict(id=41,agent_id=8,sme_periode_id=2,libelle="Mission septembre")]),
        "agent_diplome": table("agent_diplome","`id` BIGINT AUTO_INCREMENT, `agent_id` BIGINT, `import_lot_id` BIGINT, `date_obtention` DATE, `obs` TEXT, PRIMARY KEY (`id`), FOREIGN KEY (`agent_id`) REFERENCES `agent` (`id`), FOREIGN KEY (`import_lot_id`) REFERENCES `import_lot` (`id`)",
            [dict(id=60,agent_id=5,import_lot_id=20,date_obtention="2010-01-01",obs="Import archive 2026-01"),
             dict(id=61,agent_id=8,import_lot_id=21,date_obtention="2026-01-15",obs="Import archive 2026-09")]),
    }


@pytest.fixture
def live(source):
    data = deepcopy(source)
    for row in data["sme_periode"]["rows"]:
        row["id"] += 9
        row["statut"] = "Fermé" if row["annee_mois"] == "2026-01" else "Ouvert"
    for name in ("import_lot","carriere_situation","agent_mission"):
        for row in data[name]["rows"]: row["sme_periode_id"] += 9
    data["carriere_situation"]["rows"][0]["obs"] = "JAN A REMPLACER"
    data["carriere_situation"]["rows"][0]["est_actuelle"] = 0
    return data


def test_extracts_one_month_and_only_necessary_identities_and_dependencies(source):
    result = service.extract_month(source,"2026-01")
    assert [r["annee_mois"] for r in result["sme_periode"]["rows"]] == ["2026-01"]
    assert [r["id"] for r in result["agent"]["rows"]] == [5]
    assert [r["id"] for r in result["agent_diplome"]["rows"]] == [60]
    assert result["agent_diplome"]["rows"][0]["date_obtention"] == "2010-01-01"
    assert all(len(t["rows"]) == 1 for t in result.values())
    assert len(source["agent"]["rows"]) == 2


@pytest.mark.parametrize("suffix",[".zip",".sql"])
def test_single_month_export_round_trip_has_no_september_data(source,tmp_path,suffix):
    path = tmp_path/("sme_mois_2026-01"+suffix)
    service.write_month(path,source,"2026-01")
    result = service.load_source(path)
    assert service.months_in(result) == ["2026-01"]
    assert service.dataset_token(result) == service.dataset_token(service.extract_month(source,"2026-01"))
    with pytest.raises(FileExistsError): service.write_month(path,source,"2026-01")


def test_global_internal_sql_can_be_read_then_reduced_to_january(source):
    from sme_gend.services.mysql_backup_service import _literal
    statements = []
    for name,t in source.items():
        statements.append(t["ddl"]+";")
        columns = ",".join(service.quote(c) for c in t["columns"])
        for row in t["rows"]:
            statements.append(f"INSERT INTO `{name}` ({columns}) VALUES ("+",".join(_literal(row[c]) for c in t["columns"])+");")
    parsed = service.read_sql("\n".join(statements))
    assert service.months_in(parsed) == ["2026-01","2026-09"]
    assert service.dataset_token(service.extract_month(parsed,"2026-01")) == service.dataset_token(service.extract_month(source,"2026-01"))


def test_native_directives_are_ignored_only_outside_literals():
    sql = "/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;\n" + \
        "CREATE TABLE `agent` (`id` BIGINT, `noms` TEXT, PRIMARY KEY (`id`)) ENGINE=InnoDB;\n" + \
        "LOCK TABLES `agent` WRITE; INSERT INTO `agent` VALUES (1,'texte /*!50000 DROP TABLE agent */; fin'); UNLOCK TABLES;"
    result = service.read_sql(sql)
    assert result["agent"]["rows"][0]["noms"] == "texte /*!50000 DROP TABLE agent */; fin"
    with pytest.raises(ValueError): service.read_sql("/*!40101 SET SQL_MODE='NO_BACKSLASH_ESCAPES' */;")


@pytest.mark.parametrize("sql",["USE real_database;", "SELECT SLEEP(10);", "LOAD DATA INFILE 'secret';",
    "/*!50000 DROP TABLE agent */;", "CREATE DATABASE pwn;", "DELETE FROM agent;",
    "INSERT INTO agent SELECT * FROM other;", "DROP TABLE agent, sme_periode;"])
def test_unrecognized_or_executable_sql_is_rejected_without_any_database_access(sql):
    with pytest.raises(ValueError): service.read_sql(sql)


def test_schema_parser_supports_project_schema_and_prefix_unique_indexes():
    path = Path(__file__).resolve().parents[1] / "data/schema/mysql_smegend_unified.sql"
    ddl = [s for s in service.split_statements(path.read_text(encoding="utf-8-sig")) if s.lstrip().upper().startswith("CREATE TABLE")]
    result = service.read_sql("\n".join(ddl))
    assert result["ref_fonction"]["unique"] == [["libelle:191"]]


def test_archive_checksum_and_outside_month_rows_are_checked(source,tmp_path):
    path = tmp_path/"tampered.zip"
    selected = service.extract_month(source,"2026-01")
    payload = dict(format=service.FORMAT,month="2026-01",tables=selected,sha256="wrong")
    with zipfile.ZipFile(path,"w") as archive: archive.writestr("month.json",json.dumps(payload))
    with pytest.raises(ValueError,match="Empreinte"): service.load_source(path)
    payload.update(tables=source,sha256=service.dataset_token(source))
    with zipfile.ZipFile(path,"w") as archive: archive.writestr("month.json",json.dumps(payload))
    with pytest.raises(ValueError,match="hors du mois"): service.load_source(path)


def test_conflicting_period_and_import_provenance_blocks_extraction(source):
    source["carriere_situation"]["rows"][0]["import_lot_id"] = 21
    with pytest.raises(ValueError,match="contradictoires"): service.extract_month(source,"2026-01")


def test_missing_dependency_does_not_silently_export_partial_month(source):
    source["agent"]["rows"] = []
    with pytest.raises(ValueError,match="Dépendance"): service.extract_month(source,"2026-01")


@pytest.mark.parametrize("kind",["identity","reference","different_id","schema"])
def test_preview_reports_blocking_conflicts_without_changing_data(source,live,kind):
    if kind == "identity": live["agent"]["rows"][0]["noms"] = "AUTRE PERSONNE"
    elif kind == "reference": live["ref_grade"]["rows"][0]["libelle"] = "Autre grade"
    elif kind == "different_id": live["agent"]["rows"].append(dict(id=99,legacy_access_id="OTHER",noms="AUTRE",matricule_militaire="JAN"))
    else: live["agent_mission"]["columns"].append("new_column")
    before = deepcopy(live)
    if kind == "schema":
        for r in live["agent_mission"]["rows"]: r["new_column"] = None
        before = deepcopy(live)
    plan = service.build_plan(source,live,"2026-01",("synthetic",))
    assert plan.conflicts
    assert live == before


def test_preview_accepts_different_technical_period_ids_and_preserves_status(source,live):
    plan = service.build_plan(source,live,"2026-01",("synthetic",))
    assert plan.conflicts == ()
    assert dict(plan.existing_counts)["carriere_situation"] == 1
    assert live["sme_periode"]["rows"][0]["statut"] == "Fermé"


def test_merged_legacy_identity_is_not_recreated_by_restore(source,live):
    live["agent_identity_alias"] = table("agent_identity_alias",
        "`alias_legacy_id` VARCHAR(64), `canonical_agent_id` BIGINT, PRIMARY KEY (`alias_legacy_id`), FOREIGN KEY (`canonical_agent_id`) REFERENCES `agent` (`id`)",
        [dict(alias_legacy_id="A",canonical_agent_id=8)])
    plan = service.build_plan(source,live,"2026-01",("test",))
    assert any("rapproché" in conflict for conflict in plan.conflicts)


def test_placeholder_matricules_are_not_reported_as_identity_conflicts(source,live):
    source["agent"]["rows"][0]["matricule_militaire"] = "/"
    for row in live["agent"]["rows"]: row["matricule_militaire"] = "/"
    plan = service.build_plan(source,live,"2026-01",("test",))
    assert not plan.conflicts


def test_month_from_a_global_backup_zip_is_read_without_extracting_files(source,tmp_path):
    from sme_gend.services.backup_restore_service import SQL_DUMP_NAME
    sql_path = tmp_path/"one.sql"
    service.write_month(sql_path,source,"2026-01")
    path = tmp_path/"full-backup.zip"
    with zipfile.ZipFile(path,"w") as archive:
        archive.writestr("manifest.json",json.dumps(dict(format_version=4,created_at="2026-01-31",mysql_database="test",mysql_host="localhost",table_counts={},config_files=[])))
        archive.writestr(SQL_DUMP_NAME,sql_path.read_bytes())
    loaded = service.load_source(path)
    assert service.months_in(loaded) == ["2026-01"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["full-backup.zip","one.sql"]


def test_invalid_calendar_month_is_rejected(source):
    source["sme_periode"]["rows"][0]["annee_mois"] = "2026-13"
    with pytest.raises(ValueError): service.extract_month(source,"2026-13")


class MemoryCursor:
    def __init__(self,store,fail=None): self.store,self.fail,self.lastrowid = store,fail,0
    def __enter__(self): return self
    def __exit__(self,*a): pass
    def execute(self,sql,params):
        match = re.match(r"DELETE FROM `([^`]+)`",sql)
        if match:
            name = match[1]; table = self.store[name]
            table["rows"][:] = [r for r in table["rows"] if tuple(r[c] for c in table["primary"]) != params]
            return
        match = re.match(r"INSERT INTO `([^`]+)` \(([^)]+)\) VALUES",sql)
        assert match, sql
        name = match[1]
        if name == self.fail: raise RuntimeError("synthetic insert failure")
        table = self.store[name]
        row = dict(zip([c.strip('`') for c in match[2].split(',')],params))
        if table["auto"] and table["auto"] not in row:
            row[table["auto"]] = max([r[table["auto"]] for r in table["rows"]] or [0])+1
        self.lastrowid = row.get(table["auto"],0)
        for cols,parent,pcols in table["foreign"]:
            key = tuple(row[c] for c in cols)
            assert any(v is None for v in key) or any(tuple(p[c] for c in pcols) == key for p in self.store[parent]["rows"])
        table["rows"].append(row)


@pytest.fixture
def storage(live,monkeypatch,tmp_path):
    from sme_gend.services import active_month_guard  # charger avant de remplacer le connecteur
    settings = Settings(database_path=Path("unused"))
    connection = SimpleNamespace(cursor=lambda:MemoryCursor(live))
    @contextmanager
    def connect(**kw): yield connection
    @contextmanager
    def write(*a,**kw):
        before = deepcopy(live)
        try: yield SimpleNamespace(before=SimpleNamespace(active_month="2026-09"))
        except BaseException:
            live.clear(); live.update(before); raise
    monkeypatch.setattr(service,"_authorize",lambda **kw:None)
    monkeypatch.setattr(service,"_read_connection",lambda *a,**kw:deepcopy(live))
    monkeypatch.setattr("sme_gend.repositories.mysql_connection.MySQLConnection",lambda settings:SimpleNamespace(connect=connect))
    monkeypatch.setattr("sme_gend.services.active_month_guard.period_state_write",write)
    monkeypatch.setattr("sme_gend.services.shared_reference_write.mark_shared_data_write",lambda *a:None)
    backup = MagicMock(return_value=tmp_path/"synthetic-prior-backup.zip")
    monkeypatch.setattr("sme_gend.services.backup_restore_service.ensure_recent_backup",backup)
    audit = MagicMock()
    monkeypatch.setattr("sme_gend.services.business_audit.record_business_action",audit)
    monkeypatch.setattr("sme_gend.services.liste_generale_cache.invalidate_liste_cache",lambda *a:None)
    return settings,live,connection,backup,audit


def test_restore_changes_only_january_and_remaps_period_lot_and_row_ids(source,storage):
    settings,live,connection,backup,audit = storage
    before = deepcopy(live)
    plan = service.build_plan(source,live,"2026-01",mysql_database_key(settings))
    result = service.apply_month(settings,plan)
    assert result["month"] == "2026-01"
    assert live["sme_periode"] == before["sme_periode"]
    assert live["agent"] == before["agent"]
    assert live["ref_grade"] == before["ref_grade"]
    january = [r for r in live["carriere_situation"]["rows"] if r["sme_periode_id"] == 10]
    assert len(january) == 1 and january[0]["obs"] == "JAN ORIGINAL"
    assert january[0]["est_actuelle"] == 0 and january[0]["date_fin"] == "2026-01-31"
    assert [r for r in live["carriere_situation"]["rows"] if r["sme_periode_id"] == 11] == [before["carriere_situation"]["rows"][1]]
    assert live["agent_mission"]["rows"][0] == before["agent_mission"]["rows"][1]
    assert any(r["date_obtention"] == "2010-01-01" for r in live["agent_diplome"]["rows"])
    backup.assert_called_once_with(settings,max_age_days=0.0)
    assert audit.call_args.kwargs["action"] == "sme_month_restore"


def test_stale_preview_and_backup_failure_cannot_replace_month(source,storage):
    settings,live,connection,backup,audit = storage
    plan = service.build_plan(source,live,"2026-01",mysql_database_key(settings))
    live["agent"]["rows"][0]["noms"] = "CHANGED AFTER PREVIEW"
    before = deepcopy(live)
    with pytest.raises(Exception,match="changé"): service.apply_month(settings,plan)
    assert live == before
    audit.assert_not_called()
    backup.return_value = None
    with pytest.raises(RuntimeError,match="sauvegarde"): service.apply_month(settings,plan)
    assert live == before


def test_mid_restore_failure_rolls_back_every_month_and_audit(source,storage):
    settings,live,connection,backup,audit = storage
    before = deepcopy(live)
    connection.cursor = lambda:MemoryCursor(live,fail="carriere_situation")
    plan = service.build_plan(source,live,"2026-01",mysql_database_key(settings))
    with pytest.raises(RuntimeError,match="synthetic"): service.apply_month(settings,plan)
    assert live == before
    audit.assert_not_called()


def test_mutated_source_or_other_database_plan_rejected_before_backup(source,storage):
    settings,live,connection,backup,audit = storage
    plan = service.build_plan(source,live,"2026-01",mysql_database_key(settings))
    plan.source["agent"]["rows"][0]["noms"] = "MODIFIED"
    with pytest.raises(ValueError): service.apply_month(settings,plan)
    backup.assert_not_called()
