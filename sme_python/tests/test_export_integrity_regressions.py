"""Intégrité des exports : périmètre, annexes, mois, chiffres et styles."""
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from openpyxl import load_workbook

from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.numerique_satellite import MissionDetail, StageDetail
from sme_gend.models.agent_evenement import AgentCongeRow
from sme_gend.services.sme_dpf_service import SmeDpfBundle


def scenario(monkeypatch):
    from sme_gend.services import numerique_excel_export as export
    rows = []
    positions = [("Présent", "DPF"), ("Mission", "CS-DPF"), ("Stage interne", "DPF"),
                 ("CongéAdmin", "CS-DPF"), ("Punis", "DPF"), ("Détaché", "DETACHE"),
                 ("StageEx", "CS-DPF"), ("Présent", "RG-BZV")]
    for i, (position, structure) in enumerate(positions, 1):
        rows.append(ListeGeneraleRow(
            id=str(i), noms=f"AGENT TEST {i}", date_naissance=datetime(1985, 3, 12),
            lieu_naissance="Ville", grade="LTN", date_grade=datetime(2025, 1, 1),
            date_service="01/02/2005", ndecision="DEC-GRADE", matricule_militaire=f"MAT-{i}",
            matricule_solde=f"12345{i}", position_admin=position, fonction="Fonction",
            subdivision_arme="Unité", diplome="Diplôme", date_obtention="12/03/2024",
            structure=structure, obs=None, ordre_tri=i, presseance=None,
        ))
    settings = Settings(database_path=Path("unused.mdb"), mysql_view_mois="2026-09")
    detail = {
        "missions": {"2": MissionDetail("Mission test", "Destination", "NDS-42", "2026-09-03")},
        "stages_loc": {}, "stages_ex": {"7": StageDetail("Stage extérieur", "Pays", "01/09/2026 - 30/09/2026")},
        "stages_internal": {"3": StageDetail("Formation interne", "Centre", "01/09/2026 - 30/09/2026")},
        "malades": {}, "deserteurs": {},
        "conges": {"4": AgentCongeRow("ADMIN", "Congé administratif", "2026-09-05",
                                     "2026-09-15", "11", "Autorisation congé", "2026-09", "MANUEL")},
        "positions": {("5", "punis"): ("2026-09-02", "2026-09-07", "Unité punition", "DEC-P", "Motif réel")},
    }
    methods = {"load_missions": "missions", "load_malades": "malades", "load_deserteurs": "deserteurs",
               "load_stages_loc": "stages_loc", "load_stages_ex": "stages_ex", "load_stages_internal": "stages_internal",
               "load_latest_conges_for_period": "conges", "load_position_events_for_period": "positions"}
    class Satellites:
        def __init__(self, st):
            assert st.mysql_view_mois == "2026-09"
    for method, key in methods.items():
        setattr(Satellites, method, lambda self, key=key: detail[key])
    monkeypatch.setattr("sme_gend.services.liste_generale_cache.cached_numerique_liste_rows", lambda st: rows if st.mysql_view_mois == "2026-09" else [])
    monkeypatch.setattr(export, "NumeriqueSatelliteRepository", Satellites)
    monkeypatch.setattr(export, "SubdivisionRepository", lambda st: SimpleNamespace(fetch_flat_hierarchy=lambda: []))
    class EmptyGainPerteRepository:
        def __init__(self, _settings): pass
        def fetch_for_month(self, _month): return []
    monkeypatch.setattr("sme_gend.repositories.gain_perte_repository.GainPerteRepository", EmptyGainPerteRepository)
    monkeypatch.setattr("sme_gend.services.gain_perte_coherence.load_unsignaled_pertes", lambda *a, **kw: [])
    monkeypatch.setattr("sme_gend.repositories.numerique_satellite_repository.NumeriqueSatelliteRepository", Satellites)
    monkeypatch.setattr("sme_gend.repositories.grade_repository.GradeRepository", lambda st: SimpleNamespace(fetch_all=lambda: []))
    monkeypatch.setattr("sme_gend.services.sme_dpf_excel_export.GradeRepository", lambda st: SimpleNamespace(fetch_all=lambda: []))
    monkeypatch.setattr("sme_gend.services.detaches_service.DetachesService", lambda st: SimpleNamespace(fetch_detaches=lambda: [rows[5]]))
    def _detaches_config(_st):
        from sme_gend.models.detaches_file_config import DetachesFileConfig
        return SimpleNamespace(load=lambda: DetachesFileConfig())
    monkeypatch.setattr("sme_gend.services.detaches_config_service.DetachesConfigService", _detaches_config)
    monkeypatch.setattr("sme_gend.services.numerique_recap_baseline.resolve_recap_cd_baseline", lambda *a, **kw: [0] * 10)
    monkeypatch.setattr(export, "add_liste_present_sheet", lambda *a, **kw: None)
    monkeypatch.setattr("sme_gend.services.clean_sme_exports._movements", lambda *a, **kw: [])
    monkeypatch.setattr("sme_gend.services.doc_gen_log.log_doc_generation", lambda **kw: None)
    monkeypatch.setattr("sme_gend.padmin_present.load_padmin_presence_index", lambda st: None)
    monkeypatch.setattr("sme_gend.services.sme_dpf_excel_export.build_export_mois_context", lambda st: SimpleNamespace(export_mois="2026-09"))
    return settings, rows, detail


def test_complete_sme_has_selected_period_details_and_no_template_records(monkeypatch, tmp_path):
    from sme_gend.services.numerique_excel_export import export_numerique_workbook
    settings, rows, detail = scenario(monkeypatch)
    from sme_gend.models.detaches_file_config import DetachesFileConfig
    from sme_gend.services import numerique_excel_export as export
    def capture(st, mois, grade_order, movement_settings, *, include_revision):
        assert include_revision is False
        assert mois == st.mysql_view_mois == "2026-09"
        return {"mois": mois, "rows": rows, "grades": [], "details": detail,
                "detaches": [rows[5]], "previous_counts": [0] * 10, "movements": [],
                "detaches_config": DetachesFileConfig().to_dict()}, {"captured_at_utc": "2026-11-06T10:00:00+00:00"}
    monkeypatch.setattr(export, "_capture_numerique_workbook_data", capture)
    monkeypatch.setattr("sme_gend.services.sme_export_revision.revision_root", lambda st: tmp_path / "revisions")
    target = tmp_path / "SME.xlsx"
    template = Path(__file__).resolve().parents[2] / "SME_GEND_model.xlsx"
    result = export_numerique_workbook(settings, template, target, export_mois="2026-09", mois_actif="2026-02")
    wb = load_workbook(target)
    cached = load_workbook(target, data_only=True)
    original = load_workbook(template)
    try:
        assert sum(1 for r in range(4, wb["LISTE GENERALE"].max_row + 1)
                   if wb["LISTE GENERALE"].cell(r, 2).value) == len(rows)
        assert wb["MISSION"]["E4"].value == "Mission test"
        assert wb["MISSION"]["G4"].value == "NDS-42"
        assert wb["Feuil4"]["E5"].value == "Formation interne"
        assert wb["DETENTION-PRISON"]["E4"].value == "Unité punition"
        assert cached["NUMERIQUE"]["T8"].value == len(rows)
        assert wb.sheetnames == original.sheetnames
        assert wb["NUMERIQUE"]["B8"].data_type == "f"
        assert result.path == target and result.month == "2026-09"
    finally:
        wb.close()
        cached.close()
        original.close()


def test_dpf_includes_all_control_positions_and_excludes_other_structures(monkeypatch, tmp_path):
    from sme_gend.services.sme_dpf_excel_export import export_sme_dpf_workbook
    from sme_gend.services.sme_dpf_scope import filter_dpf_rows
    settings, rows, details = scenario(monkeypatch)
    scoped = filter_dpf_rows(rows)
    target = tmp_path / "DPF.xlsx"
    export_sme_dpf_workbook(settings, target, bundle=SmeDpfBundle(mois="2026-09", liste_gnle=scoped))
    wb = load_workbook(target)
    control = wb["V- CONTROLE SPECIAL DPF"]
    assert control.max_row == 6
    assert {control.cell(r, 10).value for r in range(4, 7)} == {"Mission", "CongéAdmin", "StageEx"}
    assert wb["VI-STAGIAIRES"].max_row == 5
    summary = wb["I- SITUATION NUMERIQUE DES EFF"]
    assert summary.cell(8, summary.max_column).value == 6
    assert sum(summary.cell(r, summary.max_column).value for r in range(9, summary.max_row)) == 6
    assert "Feuil3" not in wb.sheetnames and "UPC9" not in wb.sheetnames
    assert wb["LISTE GNLE"].max_row == 9
    wb.close()
