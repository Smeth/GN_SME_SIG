"""Options d'archivage et activation : états réels, dépendances et validation."""
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QGroupBox, QSpinBox

from sme_gend.ui import import_mois_dialog as dialogs


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("mode", ["import_only", "archive_only", "archive_next"])
def test_import_checkbox_modes(qapp, monkeypatch, mode):
    monkeypatch.setattr(dialogs, "load_sme_context", lambda: SimpleNamespace(mois_actif="2026-09"))
    def execute(dlg):
        archive = dlg.findChild(QCheckBox, "archiveAndClose")
        following = dlg.findChild(QCheckBox, "activateNextMonth")
        group = dlg.findChild(QGroupBox, "nextMonthGroup")
        assert archive.isChecked() and following.isChecked() and group.isEnabled()
        if mode == "import_only":
            archive.click()
            assert not following.isChecked() and not following.isEnabled()
            assert not group.isEnabled()
        elif mode == "archive_only":
            following.click()
            assert not group.isEnabled()
        dlg.findChild(QDialogButtonBox).accepted.emit()
        return dlg.result()
    monkeypatch.setattr(QDialog, "exec", execute)
    result = dialogs.pick_import_options()
    assert result.archive_and_close == (mode != "import_only")
    assert result.mois_suivant == ("2026-10" if mode == "archive_next" else None)


def test_reenable_archive_does_not_silently_reenable_month_switch(qapp, monkeypatch):
    monkeypatch.setattr(dialogs, "load_sme_context", lambda: SimpleNamespace(mois_actif="2026-12"))
    def execute(dlg):
        archive = dlg.findChild(QCheckBox, "archiveAndClose")
        following = dlg.findChild(QCheckBox, "activateNextMonth")
        archive.click()
        archive.click()
        assert following.isEnabled() and not following.isChecked()
        assert not dlg.findChild(QGroupBox, "nextMonthGroup").isEnabled()
        following.click()
        assert dlg.findChild(QGroupBox, "nextMonthGroup").isEnabled()
        assert dlg.findChild(QComboBox, "nextMonth").currentData() == 1
        assert dlg.findChild(QSpinBox, "nextYear").value() == 2027
        dlg.findChild(QComboBox, "importMonth").setCurrentIndex(9)
        assert dlg.findChild(QComboBox, "nextMonth").currentData() == 11
        dlg.findChild(QDialogButtonBox).accepted.emit()
        return dlg.result()
    monkeypatch.setattr(QDialog, "exec", execute)
    assert dialogs.pick_import_options().mois_suivant == "2026-11"


def test_month_switch_rejects_same_or_earlier_month(qapp, monkeypatch):
    warnings = []
    monkeypatch.setattr(dialogs, "load_sme_context", lambda: SimpleNamespace(mois_actif="2026-09"))
    monkeypatch.setattr(dialogs.QMessageBox, "warning", lambda *a: warnings.append(a))
    def execute(dlg):
        dlg.findChild(QComboBox, "nextMonth").setCurrentIndex(8)
        dlg.findChild(QDialogButtonBox).accepted.emit()
        assert dlg.result() != QDialog.DialogCode.Accepted
        dlg.reject()
        return dlg.result()
    monkeypatch.setattr(QDialog, "exec", execute)
    assert dialogs.pick_import_options() is None
    assert len(warnings) == 1


def test_document_cycle_import_cannot_close_or_activate_a_period(qapp, monkeypatch):
    monkeypatch.setattr(dialogs, "load_sme_context", lambda: SimpleNamespace(mois_actif="2026-09"))
    def execute(dlg):
        archive = dlg.findChild(QCheckBox, "archiveAndClose")
        following = dlg.findChild(QCheckBox, "activateNextMonth")
        assert not archive.isChecked() and not archive.isEnabled()
        assert not following.isChecked() and not following.isEnabled()
        assert not dlg.findChild(QGroupBox, "nextMonthGroup").isEnabled()
        dlg.findChild(QDialogButtonBox).accepted.emit()
        return dlg.result()
    monkeypatch.setattr(QDialog, "exec", execute)
    result = dialogs.pick_import_options(allow_legacy_cycle=False)
    assert result.archive_and_close is False and result.mois_suivant is None
