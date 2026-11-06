"""Complète un build PyInstaller et prépare un dossier ou une archive vérifiés."""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from zipfile import ZipFile, ZIP_DEFLATED
from contextlib import contextmanager
from uuid import uuid4


ACCESS_SUFFIXES = {".mdb", ".accdb"}


@contextmanager
def selftest_profile(project):
    """Synthetic test profiles contain no credentials and use inherited Windows ACLs."""
    root = (project / 'build').resolve()
    root.mkdir(exist_ok=True)
    profile = root / ('portable_selftest_' + uuid4().hex)
    profile.mkdir(mode=0o777)
    try:
        yield profile
    finally:
        if not profile.is_symlink() and profile.resolve().parent == root:
            shutil.rmtree(profile, ignore_errors=True)


def copy_template_files(source: Path, target: Path) -> None:
    """Never copy a populated Access database into a distributable tree."""
    for path in source.rglob("*"):
        if path.is_file() and path.suffix.lower() not in ACCESS_SUFFIXES:
            dest = target / path.relative_to(source)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)


def empty_access_template(project: Path, source: Path, target: Path) -> str:
    # Windows PowerShell must resolve its own modules, not inherited PS7 modules.
    env = {key: value for key, value in os.environ.items() if key.casefold() != "psmodulepath"}
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "RemoteSigned", "-File",
             str(project / "scripts/New-EmptyAccessTemplate.ps1"),
             "-Source", str(source), "-Destination", str(target)],
            check=True, capture_output=True, text=True, errors="replace", env=env, timeout=90,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Création du gabarit Access trop longue ; archive non publiée. Vérifiez DAO et les autorisations Windows du poste de construction.") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("Création du gabarit Access impossible : " + (exc.stderr or "Erreur PowerShell.")) from exc
    report = json.loads(result.stdout.strip().lstrip("\ufeff"))
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    if not report.get("source_unchanged") or report.get("sha256") != digest or report.get("tables", 0) < 1:
        raise RuntimeError("Gabarit Access non vérifié ; distribution interrompue.")
    return digest


def verify_access_distribution(output: Path, verified: dict[Path, str]) -> None:
    for path in output.rglob("*"):
        if path.is_file() and path.suffix.lower() in ACCESS_SUFFIXES:
            if verified.get(path.resolve()) != hashlib.sha256(path.read_bytes()).hexdigest():
                raise RuntimeError("Fichier Access non vérifié dans le portable ; distribution interrompue.")


def verify_executable(executable: Path, project: Path) -> dict:
    """Run the app's existing offline mode before publishing a portable ZIP."""
    (project / "build").mkdir(exist_ok=True)
    with selftest_profile(project) as name:
        profile = Path(name).resolve()
        report_path = profile / "self_test.json"
        env = {key: value for key, value in os.environ.items() if not key.startswith(("SME_", "SMEDB_"))}
        env.update({key: str(profile) for key in ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "TEMP", "TMP")})
        env["QT_QPA_PLATFORM"] = "offscreen"
        try:
            result = subprocess.run([str(executable), "--self-test-report", str(report_path)],
                                    env=env, capture_output=True, text=True, timeout=45,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise RuntimeError("Autotest du portable impossible ; archive non publiée.") from exc
        required = {"imports", "resources", "users_interface", "login_event_loop", "journal_and_charts", "pdf_output", "date_keyboard_and_nomination_reference", "application_reset_choices", "monthly_backup_codec", "optional_event_fields_and_selection", "structure_effectifs_print", "immediate_position_prompt"}
        if (result.returncode != 0 or report.get("ok") is not True or report.get("frozen") is not True
                or not required.issubset(report.get("checks") or [])):
            raise RuntimeError("Autotest du portable échoué ; archive non publiée. " + str(report.get("error") or "Contrôles incomplets."))
        return report


def package(output: Path, *, create_zip: bool = True) -> Path:
    project = Path(__file__).resolve().parents[1]
    output = output.resolve()
    if not output.is_relative_to((project / "dist").resolve()):
        raise ValueError("Le dossier de distribution doit être sous sme_python/dist.")
    executable = output / "SME_Gendarmerie.exe"
    if not executable.is_file():
        raise FileNotFoundError(executable)
    for source, target in (
        (project / "packaging/qt_onedir.conf", output / "qt.conf"),
        (project / "scripts/PORTABLE_LISEZMOI.txt", output / "PORTABLE_LISEZMOI.txt"),
        (project / "scripts/lancer_SME_Gendarmerie.cmd", output / "lancer_SME_Gendarmerie.cmd"),
        (project / "scripts/paths.json.example", output / "paths.json.example"),
    ):
        shutil.copy2(source, target)
    runtime_report = verify_executable(executable, project)
    (output / "SELF_TEST.json").write_text(json.dumps(runtime_report, indent=2), encoding="utf-8")
    acceptance = project / "docs/RECETTE_LIVRAISON.md"
    if acceptance.is_file():
        shutil.copy2(acceptance, output / "RECETTE_LIVRAISON.md")
    release_notes = project / "docs/CORRECTION_LENTEURS_CONCURRENCE_V2_2026-11-02.md"
    if release_notes.is_file():
        shutil.copy2(release_notes, output / "NOUVEAUTES_V2_0.1.3.md")
    smb_check = project / "scripts/Test-ImpressionSmb.ps1"
    if smb_check.is_file():
        shutil.copy2(smb_check, output / "Test-ImpressionSmb.ps1")
    for source, target in (
        (project / "data/schema", output / "data/schema"),
        (project / "data/personnel_media/grades", output / "personnel_media/grades"),
    ):
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
    verified_access: dict[Path, str] = {}
    for source, target in ((project / "modeles", output / "modeles"),
                           (project / "data/templates", output / "data/templates")):
        if source.is_dir():
            copy_template_files(source, target)
            for path in source.rglob("*"):
                if path.is_file() and path.suffix.lower() in ACCESS_SUFFIXES:
                    dest = target / path.relative_to(source)
                    verified_access[dest.resolve()] = empty_access_template(project, path, dest)
    # Older repositories may still keep templates at the root or in data/.
    for name in ("limite_age.docx", "soit_transmis.docx", "resultat.xlsx", "DETACHES.xlsx",
                 "numerique.xlsx", "SME_GEND_model.xlsx", "smegend.mdb", "ordre_general.txt", "SME_DPF_MAI_2026.xlsx"):
        candidates = (project / name, project / "data" / name, project.parent / name)
        if name == "SME_GEND_model.xlsx":
            candidates = (project.parent / name, project / name, project / "data" / name)
        source = next((p for p in candidates if p.is_file()), None)
        dest = output / "modeles" / name
        if source is not None and (not dest.exists() or name == "SME_GEND_model.xlsx"):
            dest.parent.mkdir(parents=True, exist_ok=True)
            if source.suffix.lower() in ACCESS_SUFFIXES:
                verified_access[dest.resolve()] = empty_access_template(project, source, dest)
            else:
                shutil.copy2(source, dest)
    for name in ("liste_present_layout.json", "detaches_config.json", "limite_age_config.json", "soit_transmis_config.json", "LISEZ-MOI.txt"):
        source = project / "data" / name
        if source.is_file():
            shutil.copy2(source, output / "data" / name)
    from importlib.metadata import version, PackageNotFoundError
    from sme_gend import __version__ as app_version
    source_hash = hashlib.sha256()
    for path in sorted((project/"src").rglob("*.py")):
        source_hash.update(path.relative_to(project).as_posix().encode())
        source_hash.update(path.read_bytes())
    metadata = {
        "application_version": app_version,
        "source_sha256": source_hash.hexdigest(),
        "dependencies": {name: version(name) for name in ("PyInstaller","PySide6","PyMySQL","pyodbc","openpyxl","python-docx","Pillow")},
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "access_templates_sha256": {str(p.relative_to(output)): digest for p, digest in verified_access.items()},
        "runtime_self_test": runtime_report,
        "changes": ["impression des effectifs par structure : filtre, tri, mois et total", "situation numérique fidèle à SME_GEND_model.xlsx", "référence facultative des désertions", "références des stages et missions", "session texte facultative des stages internes", "sélection et désélection des lignes visibles", "filtre par nature des missions", "restauration des mois sélectionnés avec conservation des notes et sessions"],
    }
    (output / "BUILD_INFO.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    (output / "CORRECTIONS_EXPORTS_ET_RESEAU.txt").write_text(
        "Notes de distribution\n\n"
        "Missions et stages : nature obligatoire signalée par un astérisque ; date de constat obligatoire pour Malade.\n"
        "Édition SME : le choix d'une position dans la liste ouvre immédiatement la fiche complémentaire ; Annuler conserve la position précédente.\n"
        "Effectifs par structure : bouton Imprimer avec aperçu, tableau filtré et trié, mois consulté et total.\n"
        "Situation numérique : modèle SME_GEND_model.xlsx conservé et rempli avec le mois choisi, sans anciennes données.\n"
        "Désertions : Réf/note facultatif dans le module et la fiche personnel.\n"
        "Stages internes : session texte facultative ; références et sessions conservées à la fin du stage.\n"
        "Sélection : Tout cocher / Tout décocher suit les lignes visibles ; filtre de nature dans Missions.\n"
        "Restauration : mois sélectionnés uniquement, sans doublons ; notes et sessions incluses.\n"
        "Fiche individuelle : rubriques renseignées, tableaux continus et en-têtes répétés.\n"
        "Actualisation : détection des imports des autres postes toutes les 10 secondes hors saisie en cours.\n\n"
        "Déployer le dossier portable complet sur chaque poste, avec la même base MySQL.\n"
        "Dans la configuration des chemins, sélectionner puis enregistrer le dossier des médias partagés.\n"
        "Un lecteur réseau connecté est converti en chemin UNC commun (\\\\serveur\\partage).\n"
        "Les lettres des lecteurs peuvent différer entre les postes.\n"
        "Les photos de l'ancien emplacement et du profil local sont copiées sans écraser les fichiers existants.\n"
        "Répéter cet enregistrement sur les postes possédant encore des photos uniquement locales.\n"
        "Chaque utilisateur Windows doit avoir accès au partage ; la configuration de ces accès reste externe à l'application.\n\n"
        "Validation : consulter le rapport de correction fourni avec les sources.\n"
        "Le partage réel entre deux postes reste à vérifier sur place.\n"
        "Les champs absents des registres sources restent vides.\n",
        encoding="utf-8",
    )
    verify_access_distribution(output, verified_access)
    for path in output.rglob("*"):
        parts = [part.casefold() for part in path.relative_to(output).parts]
        if "personnel_media" in parts:
            index = parts.index("personnel_media")
            if path.is_file() and (len(parts)<=index+1 or parts[index+1] != "grades"):
                raise RuntimeError("Média personnel présent dans le portable ; distribution interrompue.")
    if not create_zip:
        return output
    archive = output.parent / f"{output.name}_portable.zip"
    fd, name = tempfile.mkstemp(prefix="portable_", suffix=".zip", dir=output.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        with ZipFile(temporary, "w", ZIP_DEFLATED, compresslevel=6) as zf:
            for path in sorted(output.rglob("*")):
                if path.is_file():
                    zf.write(path, arcname=path.relative_to(output.parent))
        temporary.replace(archive)
    finally:
        temporary.unlink(missing_ok=True)
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--no-zip", action="store_true", help="Préparer le dossier complet vérifié sans archive ZIP.")
    args = parser.parse_args()
    print(package(args.output, create_zip=not args.no_zip))
