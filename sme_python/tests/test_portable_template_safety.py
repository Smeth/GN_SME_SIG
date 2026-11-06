import importlib.util
from pathlib import Path

import pytest


def packager():
    path = Path(__file__).resolve().parents[1] / "scripts/package_verified_portable.py"
    spec = importlib.util.spec_from_file_location("portable_packager_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_packaging_never_copies_raw_access_files(tmp_path):
    module = packager()
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    (source / "template.xlsx").write_bytes(b"synthetic workbook")
    (source / "populated.MDB").write_bytes(b"synthetic confidential data")
    module.copy_template_files(source, target)
    assert (target / "template.xlsx").exists()
    assert not (target / "populated.MDB").exists()


def test_packaging_refuses_unverified_internal_database(tmp_path):
    module = packager()
    internal = tmp_path / "_internal/modeles"
    internal.mkdir(parents=True)
    (internal / "legacy.mdb").write_bytes(b"synthetic private data")
    with pytest.raises(RuntimeError, match="Access"):
        module.verify_access_distribution(tmp_path, {})


def test_packaging_checks_template_hash_after_validation(tmp_path):
    import hashlib
    module = packager()
    template = tmp_path / "empty.mdb"
    template.write_bytes(b"synthetic verified template")
    verified = {template.resolve(): hashlib.sha256(template.read_bytes()).hexdigest()}
    module.verify_access_distribution(tmp_path, verified)
    template.write_bytes(b"synthetic replacement")
    with pytest.raises(RuntimeError, match="Access"):
        module.verify_access_distribution(tmp_path, verified)


@pytest.mark.parametrize('create_zip', [True, False])
def test_complete_package_contains_verified_template_only(tmp_path, monkeypatch, create_zip):
    import hashlib
    from zipfile import ZipFile
    module = packager()
    project = tmp_path / "project"
    output = project / "dist/new_portable"
    output.mkdir(parents=True)
    (output / "SME_Gendarmerie.exe").write_bytes(b"synthetic executable")
    for relative in ("packaging/qt_onedir.conf", "scripts/PORTABLE_LISEZMOI.txt",
                     "scripts/lancer_SME_Gendarmerie.cmd", "scripts/paths.json.example"):
        path = project / relative
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_text("synthetic configuration", encoding="utf-8")
    source = project / "modeles/smegend.mdb"
    source.parent.mkdir()
    source.write_bytes(b"SYNTHETIC PRIVATE ROWS")
    monkeypatch.setattr(module, "__file__", str(project / "scripts/package_verified_portable.py"))
    calls = []
    def clean(project, raw, dest):
        calls.append(raw)
        dest.parent.mkdir(exist_ok=True, parents=True)
        dest.write_bytes(b"synthetic empty verified schema")
        return hashlib.sha256(dest.read_bytes()).hexdigest()
    monkeypatch.setattr(module, "empty_access_template", clean)
    monkeypatch.setattr(module, "verify_executable", lambda *args: {"ok": True, "frozen": True, "checks": []})
    archive = module.package(output, create_zip=create_zip)
    assert calls == [source]
    assert source.read_bytes() == b"SYNTHETIC PRIVATE ROWS"
    if not create_zip:
        assert archive == output.resolve()
        assert not list(output.parent.glob('*.zip'))
        assert (output / 'modeles/smegend.mdb').read_bytes() == b'synthetic empty verified schema'
        return
    with ZipFile(archive) as zf:
        names = [name for name in zf.namelist() if name.endswith(".mdb")]
        assert len(names) == 1
        assert zf.read(names[0]) == b"synthetic empty verified schema"


@pytest.mark.parametrize("exit_code,payload,accepted", [
    (1, {"ok": True, "frozen": True, "checks": ["imports", "resources", "users_interface", "login_event_loop", "journal_and_charts", "pdf_output"]}, False),
    (0, {"ok": False, "frozen": True, "checks": []}, False),
    (0, {"ok": True, "frozen": False, "checks": ["imports", "resources", "users_interface", "login_event_loop", "journal_and_charts", "pdf_output"]}, False),
    (0, {"ok": True, "frozen": True, "checks": ["imports"]}, False),
    (0, {"ok": True, "frozen": True, "checks": ["imports", "resources", "users_interface", "login_event_loop", "journal_and_charts", "pdf_output", "date_keyboard_and_nomination_reference", "application_reset_choices", "monthly_backup_codec", "optional_event_fields_and_selection", "structure_effectifs_print", "immediate_position_prompt"]}, True),
])
def test_packager_requires_successful_frozen_runtime_test(tmp_path, monkeypatch, exit_code, payload, accepted):
    import json
    from types import SimpleNamespace
    module = packager()
    project = tmp_path / "project"
    project.mkdir()
    calls = []
    def run(args, **kwargs):
        assert args[1] == "--self-test-report"
        report = Path(args[2])
        assert report.is_relative_to(project / "build")
        assert kwargs["env"]["QT_QPA_PLATFORM"] == "offscreen"
        assert kwargs["env"]["LOCALAPPDATA"] == str(report.parent)
        calls.append(args)
        report.write_text(json.dumps(payload), encoding="utf-8")
        return SimpleNamespace(returncode=exit_code)
    monkeypatch.setattr(module.subprocess, "run", run)
    if accepted:
        assert module.verify_executable(project / "synthetic.exe", project) == payload
    else:
        with pytest.raises(RuntimeError, match="[Aa]utotest"):
            module.verify_executable(project / "synthetic.exe", project)
    assert len(calls) == 1


def test_access_template_generation_does_not_inherit_foreign_powershell_modules(tmp_path, monkeypatch):
    import hashlib
    import json
    from types import SimpleNamespace
    module = packager()
    monkeypatch.setenv("PSMODULEPATH", "SYNTHETIC_POWERSHELL_7_MODULES")
    source, target = tmp_path / "source.mdb", tmp_path / "empty.mdb"
    source.write_bytes(b"synthetic source")
    digest = hashlib.sha256(b"synthetic empty schema").hexdigest()
    def run(args, **kwargs):
        assert not any(key.casefold() == "psmodulepath" for key in kwargs["env"])
        target.write_bytes(b"synthetic empty schema")
        return SimpleNamespace(stdout=json.dumps({"source_unchanged": True, "tables": 1, "sha256": digest}))
    monkeypatch.setattr(module.subprocess, "run", run)
    assert module.empty_access_template(tmp_path, source, target) == digest
    assert source.read_bytes() == b"synthetic source"


def test_access_template_timeout_refuses_publication_and_preserves_source(tmp_path, monkeypatch):
    module = packager()
    source, target = tmp_path / "source.mdb", tmp_path / "empty.mdb"
    source.write_bytes(b"synthetic source")
    def stalled(args, **kwargs):
        assert kwargs["timeout"] == 90
        raise module.subprocess.TimeoutExpired(args, kwargs["timeout"])
    monkeypatch.setattr(module.subprocess, "run", stalled)
    with pytest.raises(RuntimeError, match="archive non publiée"):
        module.empty_access_template(tmp_path, source, target)
    assert source.read_bytes() == b"synthetic source"
    assert not target.exists()
