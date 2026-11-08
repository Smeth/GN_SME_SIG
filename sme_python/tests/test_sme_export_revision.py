"""Revision files are immutable to the app, typed and independent of live data."""
from dataclasses import fields
from datetime import date, datetime
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.numerique_satellite import StageDetail
from sme_gend.services import sme_export_revision as revisions


def person(name="Agent test"):
    values = {field.name: None for field in fields(ListeGeneraleRow)}
    values.update(id="1", noms=name, grade="CNE", position_admin="Présent", structure="DPF",
                  date_naissance=datetime(1990, 1, 2))
    return ListeGeneraleRow(**values)


def save(root, *, name="Agent test", renderer=None):
    def default_renderer(model, workbook):
        assert model.read_bytes() == b"modele intact"
        workbook.write_bytes(name.encode())
        return ("NUMERIQUE", "Feuil4")
    return revisions.save_export_revision(
        root=root, mois="2026-11", template_bytes=b"modele intact",
        dataset={"rows": [person(name)], "as_of": date(2026, 11, 30),
                 "details": {("1", "stage"): StageDetail("Formation", "Centre", "Novembre",
                                                        write_token=(1, 2, "opaque"), session="2026")}},
        source={"captured_at_utc": "2026-11-06T10:00:00+00:00", "imports": []},
        actor="test", renderer=renderer or default_renderer,
    )


def test_new_export_never_changes_a_previous_revision(tmp_path):
    first = save(tmp_path)
    original = {path.name: path.read_bytes() for path in first.folder.iterdir()}
    second = save(tmp_path, name="Agent modifié")
    assert first.revision_id != second.revision_id
    assert {path.name: path.read_bytes() for path in first.folder.iterdir()} == original
    assert first.manifest["artifacts"]["donnees.json"]["sha256"] != second.manifest["artifacts"]["donnees.json"]["sha256"]
    assert not first.manifest["validation_officielle"]
    assert first.journal_metadata()["template_sha256"] == second.journal_metadata()["template_sha256"]
    data = revisions.read_export_dataset(first.folder)
    assert data["rows"][0].date_naissance == datetime(1990, 1, 2)
    assert data["as_of"] == date(2026, 11, 30)
    assert data["details"][("1", "stage")].session == "2026"
    assert data["details"][("1", "stage")].write_token is None
    assert b"opaque" not in (first.folder / "donnees.json").read_bytes()


@pytest.mark.parametrize("name", revisions.ARTIFACTS)
def test_tampering_is_rejected_before_retrieving_a_version(tmp_path, name):
    revision = save(tmp_path / "history")
    (revision.folder / name).write_bytes(b"changed")
    dest = tmp_path / "result.xlsx"
    dest.write_bytes(b"previous")
    with pytest.raises(ValueError, match="altérée"):
        revisions.retrieve_export_revision(revision.folder, dest)
    assert dest.read_bytes() == b"previous"


def test_exact_retrieval_needs_no_database_and_cannot_overwrite_revision(tmp_path):
    revision = save(tmp_path / "history")
    dest = tmp_path / "copie.xlsx"
    revisions.retrieve_export_revision(revision.folder, dest)
    assert dest.read_bytes() == revision.workbook.read_bytes()
    with pytest.raises(ValueError, match="remplacée"):
        revisions.retrieve_export_revision(revision.folder, revision.workbook)
    other = save(tmp_path / "history", name="Autre révision")
    with pytest.raises(ValueError, match="remplacée"):
        revisions.retrieve_export_revision(revision.folder, other.workbook)
    assert other.workbook.read_bytes() == "Autre révision".encode()


def test_failed_render_publishes_nothing(tmp_path):
    def fail(model, workbook):
        workbook.write_bytes(b"incomplete")
        raise RuntimeError("failed rendering")
    with pytest.raises(RuntimeError, match="failed rendering"):
        save(tmp_path, renderer=fail)
    assert list((tmp_path / "2026-11").iterdir()) == []


def test_unknown_serialized_models_are_not_loaded():
    raw = {"format": revisions.FORMAT, "dataset": {"type": "record", "model": "Path", "fields": {}}}
    with pytest.raises(ValueError, match="inconnu"):
        revisions.decode_export_dataset(json.dumps(raw).encode())
    with pytest.raises(TypeError):
        revisions.encode_export_dataset({"unsupported": SimpleNamespace(secret="never persisted")})


def test_template_and_archive_destinations_are_protected(tmp_path):
    template = tmp_path / "modele.xlsx"
    root = tmp_path / "history"
    with pytest.raises(ValueError, match="modèle"):
        revisions.validate_export_destination(template, template=template, root=root)
    with pytest.raises(ValueError, match="archivée"):
        revisions.validate_export_destination(root / "2026-11" / "old.xlsx", template=template, root=root)


def test_export_refused_before_reading_data_without_permission(monkeypatch, tmp_path):
    from sme_gend.services.numerique_excel_export import export_numerique_workbook
    from sme_gend.config.settings import Settings
    monkeypatch.setattr("sme_gend.services.auth_guard.session_can_export_documents", lambda: False)
    with pytest.raises(PermissionError):
        export_numerique_workbook(Settings(database_path=Path("unused")), tmp_path / "missing.xlsx",
                                  tmp_path / "result.xlsx", export_mois="2026-11")


def test_history_hashes_detect_a_changed_manifest_as_well_as_changed_file(tmp_path):
    revision = save(tmp_path)
    receipt = revision.journal_metadata()
    (revision.folder / "classeur.xlsx").write_bytes(b"changed")
    manifest = revision.manifest
    from hashlib import sha256
    manifest["artifacts"]["classeur.xlsx"] = {"size": 7, "sha256": sha256(b"changed").hexdigest()}
    (revision.folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="journal documentaire"):
        revisions.verify_export_revision(revision.folder, expected=receipt)


def test_retrieval_rechecks_export_permission(tmp_path, monkeypatch):
    revision = save(tmp_path / "history")
    monkeypatch.setattr("sme_gend.services.auth_guard.session_can_export_documents", lambda: False)
    with pytest.raises(PermissionError):
        revisions.retrieve_export_revision(revision.folder, tmp_path / "result.xlsx")
    with pytest.raises(PermissionError):
        revisions.read_export_dataset(revision.folder)


def test_all_database_identities_are_protected_from_export_and_retrieval(tmp_path, monkeypatch):
    from sme_gend.services import sme_publication_backup
    root = tmp_path / "publications_sme"
    monkeypatch.setattr(sme_publication_backup, "storage_root", lambda: root)
    first = save(root / ("a" * 20))
    second = save(root / ("b" * 20), name="Autre identité")
    previous = second.workbook.read_bytes()
    with pytest.raises(ValueError, match="archivée"):
        revisions.validate_export_destination(second.workbook, template=tmp_path / "template.xlsx", root=root / ("a" * 20))
    with pytest.raises(ValueError, match="archivée"):
        revisions.retrieve_export_revision(first.folder, second.workbook)
    assert second.workbook.read_bytes() == previous


@pytest.mark.parametrize("failure", ["unconfirmed", "mirror", "disk"])
def test_saved_file_and_revision_return_a_warning_when_history_fails(tmp_path, monkeypatch, failure):
    monkeypatch.setattr("sme_gend.services.schema_migrations.require_capability", lambda *a: None)
    monkeypatch.setattr("sme_gend.services.sme_publication_registry.register_export",
                        lambda *a: SimpleNamespace(id=1, revision_no=1, status="brouillon", operation_warning=""))
    from openpyxl import Workbook
    from sme_gend.config.settings import Settings
    from sme_gend.services import numerique_excel_export as export
    from sme_gend.services.doc_gen_log import DocumentJournalError
    from sme_gend.repositories.mysql_connection import CommittedMirrorError
    template = tmp_path / "template.xlsx"
    wb = Workbook()
    wb.active.title = "NUMERIQUE"
    wb.save(template)
    wb.close()
    monkeypatch.setattr(export, "_capture_publication_inputs", lambda *a: (
        {"mois": "2026-11", "rows": [], "detaches_config": {}},
        {"captured_at_utc": "2026-11-06T10:00:00+00:00"}))
    monkeypatch.setattr(revisions, "revision_root", lambda settings: tmp_path / "history")
    def render(model, destination, **values):
        destination.write_bytes(b"synthetic workbook")
        return ("NUMERIQUE",)
    monkeypatch.setattr("sme_gend.services.numerique_model_export.build_from_model", render)
    def fail(**kwargs):
        if failure == "mirror":
            raise CommittedMirrorError("mirror not written")
        if failure == "disk":
            raise OSError("mirror disk full")
        raise DocumentJournalError("history not confirmed")
    monkeypatch.setattr("sme_gend.services.doc_gen_log.log_doc_generation", fail)
    dest = tmp_path / "result.xlsx"
    revision = export.export_numerique_publication(Settings(database_path=tmp_path / "unused.mdb"),
                                               template, dest, export_mois="2026-11")
    assert revision.journal_warning
    assert "aucune nouvelle génération" in revision.journal_warning
    assert ("Historique confirmé" in revision.journal_warning) == (failure == "mirror")
    assert dest.read_bytes() == revision.workbook.read_bytes() == b"synthetic workbook"
    assert len(list((tmp_path / "history" / "2026-11").glob("*/manifest.json"))) == 1


def test_history_details_expose_revision_receipt_without_expanding_table():
    from sme_gend.ui.views.doc_gen_log_view import _entry_details, _fmt_meta
    meta = {"revision_id": "REVISION TEST", "revision_folder": "dossier archive test",
            "workbook_sha256": "WORKBOOK HASH", "template_sha256": "MODEL HASH", "dataset_sha256": "DATA HASH"}
    details = _entry_details({"doc_type": "numerique", "meta": meta, "count": 6})
    assert all(text in details for text in ("REVISION TEST", "dossier archive test", "WORKBOOK HASH", "MODEL HASH", "DATA HASH"))
    assert _fmt_meta(meta) == "Version SME : REVISION TEST"


def test_history_retrieval_checks_central_receipt_and_copies_exact_file(tmp_path, monkeypatch):
    from sme_gend.ui.views.doc_gen_log_view import DocGenLogView
    revision = save(tmp_path / "history")
    dest = tmp_path / "retrieved.xlsx"
    view = SimpleNamespace(_attestation_history_access=lambda: True)
    monkeypatch.setattr("sme_gend.ui.views.doc_gen_log_view.require_export", lambda parent: True)
    monkeypatch.setattr("PySide6.QtWidgets.QFileDialog.getSaveFileName", lambda *a, **kw: (str(dest), ""))
    monkeypatch.setattr("sme_gend.ui.confirm_dialogs.confirm_overwrite_file", lambda *a: True)
    monkeypatch.setattr("sme_gend.ui.bg_task.run_with_progress", lambda parent, title, label, work: work())
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.information", lambda *a: None)
    DocGenLogView._retrieve_sme_revision(view, {"mois": "2026-11", "meta": revision.journal_metadata()})
    assert dest.read_bytes() == revision.workbook.read_bytes()


@pytest.mark.parametrize("warning", ["", "Historique non confirmé ; fichier conservé."])
def test_export_screen_shows_receipt_and_distinguishes_journal_warning(tmp_path, monkeypatch, warning):
    from PySide6.QtWidgets import QDialog
    from sme_gend.config.settings import Settings
    from sme_gend.services.numerique_excel_export import NumeriqueWorkbookExport
    from sme_gend.ui.views import numerique_combined_view as ui
    result = NumeriqueWorkbookExport(tmp_path / "selected.xlsx", "2026-11", warning)
    template = tmp_path / "template.xlsx"
    template.write_bytes(b"model")
    settings = Settings(database_path=tmp_path / "unused", template_numerique_path=template)
    view = SimpleNamespace(_settings=settings,
        _preview=SimpleNamespace(liste_settings=settings, gain_perte_settings=settings))
    class Dialog:
        DialogCode = QDialog.DialogCode
        def __init__(self, *args): pass
        def exec(self): return self.DialogCode.Accepted
        def chosen_export_mois(self): return "2026-11"
    monkeypatch.setattr(ui, "NumeriqueExportDialog", Dialog)
    monkeypatch.setattr("sme_gend.services.auth_guard.require_permission", lambda *a, **kw: True)
    monkeypatch.setattr(ui, "confirm_overwrite_file", lambda *a: True)
    monkeypatch.setattr(ui.QFileDialog, "getSaveFileName", lambda *a: (str(tmp_path / "selected.xlsx"), ""))
    monkeypatch.setattr(ui, "export_numerique_workbook", lambda *a, **kw: result)
    monkeypatch.setattr(ui, "run_with_progress", lambda parent, title, label, work: work())
    messages = []
    monkeypatch.setattr(ui.QMessageBox, "information", lambda *a: messages.append(("info", a[-1])))
    monkeypatch.setattr(ui.QMessageBox, "warning", lambda *a: messages.append(("warning", a[-1])))
    ui.NumeriqueCombinedView._export(view)
    assert len(messages) == 1
    kind, message = messages[0]
    assert kind == ("warning" if warning else "info")
    assert str(result.path) in message and "novembre 2026" in message.casefold()
    assert "version" not in message.casefold()
