"""Filtres avancés liste générale."""

from datetime import datetime
import pytest

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.ui.personnel_advanced_filter import (
    AdvancedFilterRule,
    rule_from_dict,
    rule_to_dict,
    rules_from_list,
    rules_match,
    rule_matches,
)


def _row(**kwargs) -> ListeGeneraleRow:
    base = dict(
        id="1",
        noms="DUPONT Jean",
        date_naissance=datetime(1985, 3, 15),
        lieu_naissance=None,
        grade="CNE",
        date_grade=None,
        date_service=None,
        ndecision=None,
        matricule_militaire=None,
        matricule_solde=None,
        position_admin=None,
        fonction="Chef",
        subdivision_arme=None,
        diplome=None,
        date_obtention=None,
        structure="01",
        obs=None,
        ordre_tri=None,
        presseance=None,
    )
    base.update(kwargs)
    return ListeGeneraleRow(**base)


def test_sans_matricule_via_empty_rule():
    r = _row(matricule_militaire=None)
    assert rule_matches(r, AdvancedFilterRule("matricule_militaire", "est vide", ""))
    assert not rule_matches(
        r, AdvancedFilterRule("matricule_militaire", "n'est pas vide", "")
    )


def test_date_equals():
    r = _row()
    assert rule_matches(
        r, AdvancedFilterRule("date_naissance", "égal à", "15/03/1985")
    )
    assert not rule_matches(
        r, AdvancedFilterRule("date_naissance", "avant", "01/01/1980")
    )


def test_advanced_and_logic():
    r = _row(grade="CNE", matricule_solde=None)
    rules = [
        AdvancedFilterRule("grade", "égal à", "CNE"),
        AdvancedFilterRule("matricule_solde", "est vide", ""),
    ]
    assert rules_match(r, rules, mode="and")


def test_advanced_or_logic():
    r = _row(grade="CNE", matricule_solde="123")
    rules = [
        AdvancedFilterRule("grade", "égal à", "CNE"),
        AdvancedFilterRule("matricule_solde", "est vide", ""),
    ]
    assert not rules_match(r, rules, mode="and")
    assert rules_match(r, rules, mode="or")


def test_serialize_roundtrip():
    r = AdvancedFilterRule("noms", "contient", "dupont")
    d = rule_to_dict(r)
    assert rule_from_dict(d) == r
    assert rules_from_list([d, {"column": "bad", "operator": "x"}]) == [r]


def test_duration_grade_anniversary_and_grade_combination():
    rules = [AdvancedFilterRule("grade", "égal à", "CNE"),
             AdvancedFilterRule("anciennete_grade", "au moins", "2", "03/11/2026")]
    assert rules_match(_row(date_grade=datetime(2024, 11, 3)), rules)
    assert not rules_match(_row(date_grade=datetime(2024, 11, 4)), rules)
    assert not rules_match(_row(grade="LT", date_grade=datetime(2020, 1, 1)), rules)


@pytest.mark.parametrize("value", [None, "", "inconnue", "2027-01-01"])
def test_service_missing_invalid_or_future_excluded(value):
    assert not rule_matches(_row(date_service=value),
                            AdvancedFilterRule("duree_service", "au moins", "0", "03/11/2026"))


@pytest.mark.parametrize("value", ["03/11/2016", "2016-11-03", "03-11-2016"])
def test_service_dates_and_serialization(value):
    rule = AdvancedFilterRule("duree_service", "au moins", "10 ans", "03/11/2026")
    assert rule_matches(_row(date_service=value), rule)
    assert rule_from_dict(rule_to_dict(rule)) == rule


def test_duration_completed_years_months_and_range():
    row = _row(date_grade=datetime(2024, 5, 3))
    def matches(op, value, reference="03/11/2026"):
        return rule_matches(row, AdvancedFilterRule("anciennete_grade", op, value, reference))
    assert matches("égal à", "2")
    assert not matches("égal à", "2", "03/05/2027")
    assert matches("égal à", "2 ans 6 mois")
    assert not matches("égal à", "2 ans 6 mois", "03/12/2026")
    assert matches("entre", "2 ans ; 2 ans 6 mois")
    assert not matches("entre", "3 ; 2")
    assert not matches("au moins", "incorrect")
    assert matches("moins de", "3")


def test_duration_leap_day_and_month_end():
    assert rule_matches(_row(date_grade=datetime(2024, 2, 29)),
                        AdvancedFilterRule("anciennete_grade", "au moins", "1", "28/02/2025"))
    assert rule_matches(_row(date_grade=datetime(2026, 1, 31)),
                        AdvancedFilterRule("anciennete_grade", "au moins", "1 mois", "28/02/2026"))


def test_duration_panel_applies_and_restores_rule():
    from PySide6.QtCore import QDate
    from PySide6.QtWidgets import QApplication
    from sme_gend.ui.personnel_advanced_filter_panel import PersonnelAdvancedFilterPanel
    app = QApplication.instance() or QApplication([])
    panel = PersonnelAdvancedFilterPanel()
    panel._col.setCurrentIndex(panel._col.findData("duree_service"))
    panel._value.setText("10 ans")
    panel._today.setChecked(False)
    panel._reference.setDate(QDate(2026, 11, 3))
    panel._on_add_rule()
    rule = panel.rules()[0]
    assert rule.reference_date == "03/11/2026"
    restored = PersonnelAdvancedFilterPanel()
    restored.load_state(panel.export_state())
    assert restored.rules() == [rule]
    assert "03/11/2026" in restored._summary.toolTip()
    panel.close()
    restored.close()


@pytest.mark.parametrize("width,height", [(640, 480), (1024, 768), (1366, 768)])
def test_advanced_panel_keeps_table_visible_with_many_long_rules(width, height):
    from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QTableWidget
    from sme_gend.ui.personnel_advanced_filter_panel import PersonnelAdvancedFilterPanel
    app = QApplication.instance() or QApplication([])
    host = QWidget()
    layout = QVBoxLayout(host)
    panel = PersonnelAdvancedFilterPanel()
    table = QTableWidget(30, 3)
    layout.addWidget(panel)
    layout.addWidget(table, 1)
    host.resize(width, height)
    host.show()
    app.processEvents()
    assert not panel._body.isVisible()
    collapsed_height = panel.height()
    panel._col.setCurrentIndex(panel._col.findData("duree_service"))
    panel.load_state({"expanded": True, "rules": [
        rule_to_dict(AdvancedFilterRule(key, "contient", "Valeur très longue " * 30))
        for key in ("noms", "grade", "fonction", "structure", "obs")
    ]})
    app.processEvents()
    expanded_height = panel.height()
    assert host.width() == width
    assert expanded_height < 210
    assert table.height() > height / 2
    assert panel._rules_line.height() <= panel.fontMetrics().height() + 8
    assert len(panel._rules_line.toolTip()) > len(panel._rules_line.text())
    panel._on_toggle_body()
    app.processEvents()
    app.processEvents()  # Propager le recalcul du panneau au layout parent.
    assert panel.height() == collapsed_height
    assert len(panel.rules()) == 5  # Replier conserve les critères appliqués.
    host.close()


def test_invalid_duration_does_not_change_applied_rules(monkeypatch):
    from PySide6.QtWidgets import QApplication, QMessageBox
    from sme_gend.ui.personnel_advanced_filter_panel import PersonnelAdvancedFilterPanel
    app = QApplication.instance() or QApplication([])
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    panel = PersonnelAdvancedFilterPanel()
    panel._col.setCurrentIndex(panel._col.findData("duree_service"))
    panel._value.setText("10 ans")
    panel._on_add_rule()
    original = panel.rules()
    for value in ("", "abc", "-2", "2 ; abc"):
        panel._value.setText(value)
        panel._on_add_rule()
        assert panel.rules() == original
    assert len(warnings) == 4
    panel.close()


def test_duration_rules_filter_actual_table_and_reset():
    from PySide6.QtWidgets import QApplication
    from sme_gend.ui.personnel_filter_proxy import PersonnelFilterProxy
    from sme_gend.ui.personnel_table_model import PersonnelTableModel
    app = QApplication.instance() or QApplication([])
    rows = [_row(id="ok", grade="Capitaine", date_grade=datetime(2024, 11, 3),
                 date_service="03/11/2016"),
            _row(id="recent", grade="Capitaine", date_grade=datetime(2024, 11, 4),
                 date_service="03/11/2016"),
            _row(id="missing", grade="Capitaine", date_grade=datetime(2020, 1, 1)),
            _row(id="other", grade="Lieutenant", date_grade=datetime(2020, 1, 1),
                 date_service="03/11/2016")]
    model = PersonnelTableModel(rows)
    proxy = PersonnelFilterProxy()
    proxy.setSourceModel(model)
    criteria = dict(only_empty_structure=False, padmin=None, grade=None,
                    structure=None, subarme=None, needle="")
    proxy.set_filter_criteria(**criteria, advanced_rules=[
        AdvancedFilterRule("grade", "égal à", "Capitaine"),
        AdvancedFilterRule("anciennete_grade", "au moins", "2", "03/11/2026"),
        AdvancedFilterRule("duree_service", "au moins", "10", "03/11/2026")])
    assert proxy.rowCount() == 1
    assert model.row_at(proxy.mapToSource(proxy.index(0, 0)).row()).id == "ok"
    proxy.set_filter_criteria(**criteria)
    assert proxy.rowCount() == 4
