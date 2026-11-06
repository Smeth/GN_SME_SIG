import base64
import json
from random import Random
from types import SimpleNamespace
from zipfile import ZipFile

from PySide6.QtGui import QImage


def test_large_photo_is_resized_for_pdf_without_changing_source(tmp_path):
    from sme_gend.services.parcours_pdf_export import _img_data_uri
    data = Random(42).randbytes(1000 * 1000 * 4)
    image = QImage(data, 1000, 1000, 4000, QImage.Format.Format_RGBA8888)
    photo = tmp_path / "large.png"
    assert image.save(str(photo))
    original = photo.read_bytes()
    assert len(original) > 800_000
    uri = _img_data_uri(photo)
    assert uri is not None
    raw = base64.b64decode(uri.partition(",")[2])
    assert len(raw) <= 800_000
    resized = QImage.fromData(raw)
    assert not resized.isNull() and resized.hasAlphaChannel()
    assert resized.width() <= 600 and resized.height() <= 800
    assert photo.read_bytes() == original


def test_libreoffice_pdf_logs_final_document_and_cleans_temporary_files(tmp_path, monkeypatch):
    import shutil
    import subprocess
    from pathlib import Path
    from sme_gend.services.limite_age_service import LimiteAgeService
    service = LimiteAgeService.__new__(LimiteAgeService)
    temporary = []
    def docx(person, config, dest, *args, **kwargs):
        temporary.append(dest)
        dest.write_bytes(b"synthetic docx")
    service.generate_docx = docx
    monkeypatch.setattr(shutil, "which", lambda name: "synthetic-soffice")
    def convert(args, **kwargs):
        Path(args[4]).with_suffix(".pdf").write_bytes(b"synthetic pdf")
    monkeypatch.setattr(subprocess, "run", convert)
    logs = []
    monkeypatch.setattr("sme_gend.services.doc_gen_log.log_doc_generation", lambda **kwargs: logs.append(kwargs))
    target = tmp_path / "final.pdf"
    service.generate_pdf(SimpleNamespace(noms="SYNTHETIC"), object(), target)
    assert target.read_bytes() == b"synthetic pdf"
    assert any(log["format"] == "pdf" and log["dest_path"] == str(target) for log in logs)
    assert all(not path.exists() and not path.with_suffix(".pdf").exists() for path in temporary)


def test_new_backup_redacts_only_mysql_password(tmp_path, monkeypatch):
    from sme_gend.config.settings import Settings
    from sme_gend.services import backup_restore_service as backup
    cfg = tmp_path / "paths.json"
    cfg.write_text(json.dumps({"mysql_password": "SYNTHETIC_ONLY", "mysql_host": "invalid.test",
                               "template_excel_path": "template.xlsx"}), encoding="utf-8")
    monkeypatch.setattr(backup, "_collect_config_paths", lambda: [cfg])
    scope = backup.BackupScope(include_mysql=False, include_app_config=False, include_app_data=False,
                               include_media_photos=False, include_media_grades=False)
    archive = tmp_path / "backup.zip"
    backup.create_backup(Settings(database_path=tmp_path / "unused"), archive, scope=scope)
    with ZipFile(archive) as zf:
        payload = json.loads(zf.read("user_config/paths.json"))
    assert "mysql_password" not in payload
    assert payload["mysql_host"] == "invalid.test"
    assert payload["template_excel_path"] == "template.xlsx"
    assert "mysql_password" in json.loads(cfg.read_text(encoding="utf-8"))


def test_local_override_does_not_replace_shared_root(tmp_path, monkeypatch):
    from sme_gend.config import user_paths
    from sme_gend.services import shared_media
    monkeypatch.setattr(user_paths, "_config_dir", lambda: tmp_path / "profile")
    monkeypatch.setattr(shared_media, "shared_media_root", lambda: tmp_path / "shared")
    user_paths.save_user_paths(None, None, personnel_media_root_path=str(tmp_path / "local"),
                              personnel_media_local_override=True)
    assert user_paths.resolved_personnel_media_root() == tmp_path / "local"
