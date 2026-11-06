from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QApplication, QDialog

from sme_gend.config.settings import Settings
from sme_gend.services.personnel_identity_assessment import assess_identity
from sme_gend.services.career_import_alerts import RelatedCareerAgent, _AgentIdentityRow, _scan_matricule_duplicates


def person(key="A", **changes):
    return replace(RelatedCareerAgent(key, "AGENT ALPHA", date(1970, 1, 1), "MIL123", "SOLD456"), **changes)


@pytest.mark.parametrize("changes,expected", [
    ({"noms": "AGENT BETA"}, "Nom complet"),
    ({"date_naissance": date(1971, 1, 1)}, "Date de naissance"),
    ({"matricule_solde": "OTHER"}, "Matricule solde"),
])
def test_any_explicit_contradiction_blocks_merge(changes, expected):
    result = assess_identity(person(), person("B", **changes))
    assert result.status == "divergent" and not result.eligible
    assert expected in result.contradictions


@pytest.mark.parametrize("changes", [
    {"date_naissance": None, "matricule_solde": None},
    {"noms": None},
    {"matricule_militaire": "/", "matricule_solde": "N/A"},
    {"date_naissance": "0000-00-00", "matricule_solde": "000000"},
])
def test_missing_or_placeholder_data_cannot_provide_identity_evidence(changes):
    result = assess_identity(person(), person("B", **changes))
    assert result.status == "incomplete" and not result.eligible


@pytest.mark.parametrize("changes", [{}, {"matricule_solde": None}, {"date_naissance": None}])
def test_name_plus_two_independent_identifiers_permits_only_a_proposal(changes):
    result = assess_identity(person(), person("B", **changes))
    assert result.eligible
    assert "pas de conclure automatiquement" in result.explanation


def test_only_presentation_normalization_is_permitted():
    assert assess_identity(person(noms="Émile D’ALPHA"), person("B", noms=" emile  d-alpha ")).eligible
    assert not assess_identity(person(noms="ALPHA JEAN"), person("B", noms="JEAN ALPHA")).eligible
    assert not assess_identity(person(noms="ALPHA JEAN"), person("B", noms="ALPHA JEA")).eligible


def group():
    return _scan_matricule_duplicates([
        _AgentIdentityRow("A", "AGENT ALPHA", "MIL123", "SOLD456", date(1970, 1, 1)),
        _AgentIdentityRow("B", "AGENT ALPHA", "MIL123", "SOLD456", date(1970, 1, 1)),
        _AgentIdentityRow("C", "AGENT BETA", "MIL123", "OTHER", date(1970, 12, 9)),
    ])


def test_mixed_group_excludes_unrelated_identity_and_distinguishes_collision():
    alerts = group()
    a = next(item for item in alerts if item.agent_legacy_id == "A")
    c = next(item for item in alerts if item.agent_legacy_id == "C")
    assert [r.legacy_id for r in a.merge_candidates] == ["A", "B"]
    assert "Situation mixte" in a.identity_summary
    assert c.merge_candidates == ()
    assert "identités divergentes" in c.label
    assert "personnes différentes" in c.resolution_guidance
    assert "Ne modifiez pas les noms" in c.resolution_guidance


def test_placeholder_matricules_do_not_generate_duplicate_alerts():
    assert _scan_matricule_duplicates([
        _AgentIdentityRow("A", "AGENT ALPHA", "/", "00000", None),
        _AgentIdentityRow("B", "AGENT BETA", "/", "00000", None),
    ]) == []


def test_acknowledgement_version_tracks_related_identity_changes():
    original = group()[0]
    changed = replace(original, related_agents=tuple(
        replace(row, date_naissance=date(1980, 1, 1)) if row.legacy_id == "B" else row for row in original.related_agents))
    assert changed.fingerprint() != original.fingerprint()


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_collision_has_no_merge_button_and_professional_tab(app, monkeypatch):
    from sme_gend.ui import career_alert_details_dialog as ui
    alert = next(item for item in group() if item.agent_legacy_id == "C")
    widget = ui.CareerAlertDetailsDialog(None, alert,
        Settings(database_path=Path("unused"), mysql_view_mois="2026-09"), MagicMock(), MagicMock())
    assert widget.merge_button.isHidden()
    assert "identités divergentes" in widget.identity_assessment.text()
    assert "Nom complet" in widget.comparison_detail.text()
    assert "Date de naissance" in widget.comparison_detail.text()
    assert "Matricule solde" in widget.comparison_detail.text()
    assert widget.trace_tabs.tabText(1) == "Historique et provenance des dossiers"
    factory = MagicMock()
    monkeypatch.setattr("sme_gend.ui.personnel_merge_dialog.PersonnelMergeDialog", factory)
    widget._merge()
    factory.assert_not_called()
    widget.close()


def test_merge_dialog_receives_only_eligible_group_members(app, monkeypatch):
    from sme_gend.ui import career_alert_details_dialog as ui
    alert = next(item for item in group() if item.agent_legacy_id == "A")
    widget = ui.CareerAlertDetailsDialog(None, alert,
        Settings(database_path=Path("unused"), mysql_view_mois="2026-09"), MagicMock(), MagicMock())
    for method in ("session_has_admin_role", "session_can_modify_personnel", "session_can_delete_personnel"):
        monkeypatch.setattr("sme_gend.services.auth_guard." + method, lambda: True)
    factory = MagicMock()
    factory.return_value.exec.return_value = QDialog.DialogCode.Rejected
    monkeypatch.setattr("sme_gend.ui.personnel_merge_dialog.PersonnelMergeDialog", factory)
    widget._merge()
    assert [item.legacy_id for item in factory.call_args.args[2]] == ["A", "B"]
    factory.reset_mock()
    widget.related_table.selectRow(2)
    assert widget.merge_button.isHidden()
    widget._merge()
    factory.assert_not_called()
    widget.close()


def test_direct_merge_dialog_cannot_preview_divergent_pair(app, monkeypatch):
    from sme_gend.ui import personnel_merge_dialog as ui
    widget = ui.PersonnelMergeDialog(None, Settings(database_path=Path("unused")),
        [person("A"), person("B", noms="AGENT BETA")], "A")
    worker = MagicMock()
    monkeypatch.setattr(ui, "run_in_background", worker)
    assert not widget.preview_button.isEnabled()
    widget._load_preview()
    worker.assert_not_called()
    assert "Divergences" in widget.summary.toPlainText()
    widget.close()


def test_reference_matches_do_not_imply_every_pair_can_be_merged(app, monkeypatch):
    from sme_gend.ui import personnel_merge_dialog as ui
    identities = [person("A", matricule_solde=None), person("B", matricule_solde="SOLDE-B"), person("C", matricule_solde="SOLDE-C")]
    assert assess_identity(identities[0], identities[1]).eligible
    assert assess_identity(identities[0], identities[2]).eligible
    widget = ui.PersonnelMergeDialog(None, Settings(database_path=Path("unused")), identities, "A")
    widget.target.setCurrentIndex(widget.target.findData("C"))
    widget.source.setCurrentIndex(widget.source.findData("B"))
    worker = MagicMock()
    monkeypatch.setattr(ui, "run_in_background", worker)
    widget._load_preview()
    assert not widget.preview_button.isEnabled()
    assert "Matricule solde" in widget.summary.toPlainText()
    worker.assert_not_called()
    widget.close()
