# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller — dossier portable (onedir, sans console).

  cd sme_python
  python -m PyInstaller --noconfirm packaging/sme_gendarmerie_portable.spec

Sortie : dist/SME_Gendarmerie/SME_Gendarmerie.exe + _internal/
"""
from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

try:
    _sp = Path(SPECPATH)
except NameError:
    _sp = Path(__file__).resolve()

if _sp.is_file():
    BUILD_DIR = _sp.parent
    PROJECT_ROOT = BUILD_DIR.parent
else:
    BUILD_DIR = _sp
    PROJECT_ROOT = BUILD_DIR.parent

SRC = PROJECT_ROOT / "src"
DATA = PROJECT_ROOT / "data"

datas: list = []
binaries: list = []
hiddenimports = [
    "pyodbc",
    "pymysql",
    "openpyxl",
    "docx",
    "barcode",
    "PIL",
    "PIL.Image",
]

# Package applicatif (styles, icônes)
datas += collect_data_files("sme_gend", includes=["resources/**"])
hiddenimports += collect_submodules("sme_gend")

# Schémas SQL et fichiers data embarqués (configs optionnels)
if (DATA / "schema").is_dir():
    datas.append((str(DATA / "schema"), "data/schema"))
for rel in (
    "LISEZ-MOI.txt",
    "liste_present_layout.json",
    "detaches_config.json",
    "limite_age_config.json",
    "soit_transmis_config.json",
):
    p = DATA / rel
    if p.is_file():
        datas.append((str(p), "data"))

MODELES = PROJECT_ROOT / "modeles"
# Access databases are generated empty and verified by the final packager.
# Never embed the populated sources inside _internal/.
for folder, destination in ((MODELES, "modeles"), (DATA / "templates", "data/templates")):
    if folder.is_dir():
        for path in folder.rglob("*"):
            if path.is_file() and path.suffix.lower() not in {".mdb", ".accdb"}:
                datas.append((str(path), str(Path(destination) / path.relative_to(folder).parent)))

# Insignes de grade (et éventuelles photos embarquées) — dossier à côté de l'exe
PERSONNEL_MEDIA = DATA / "personnel_media"
if (PERSONNEL_MEDIA / "grades").is_dir():
    datas.append((str(PERSONNEL_MEDIA / "grades"), "personnel_media/grades"))
# Rétrocompatibilité si des gabarits restent dans data/templates

# PyInstaller's Qt hooks collect the plugins for the modules actually used.
hiddenimports += ["PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets", "PySide6.QtPrintSupport"]

# Modules Qt lourds non utilisés par l'app
excludes = [
    "pandas", "numpy", "matplotlib", "scipy", "pytest",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineQuick",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtLocation",
    "PySide6.QtMultimedia",
    "PySide6.QtGraphs",
    "PySide6.QtDataVisualization",
]

block_cipher = None

a = Analysis(
    [str(SRC / "sme_gend" / "__main__.py")],
    pathex=[str(SRC)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(BUILD_DIR / "pyi_rth_pyside6_qt.py")],
    excludes=excludes,
    noarchive=False,
    optimize=1,
)
# QtCore uses the native Windows ICU C API. A Poppler ICU DLL from PATH
# exports versioned symbols and must not shadow the Windows implementation.
# The full dependency scan confirms Qt6Core is this build's only ICU consumer.
if sys.platform == "win32":
    native_icu_names = {"icuuc.dll", "icuin.dll", "icu.dll"}
    a.binaries = [entry for entry in a.binaries
                  if Path(entry[0]).name.casefold() not in native_icu_names]

pyz = PYZ(a.pure)

_APP_ICO = PROJECT_ROOT / "build" / "app.ico"
if not _APP_ICO.is_file():
    _APP_ICO = None

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SME_Gendarmerie",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(_APP_ICO) if _APP_ICO is not None else None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="SME_Gendarmerie",
)
