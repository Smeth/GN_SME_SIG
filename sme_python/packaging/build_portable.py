"""Build a fresh portable with pinned dependencies and runtime verification."""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import importlib.metadata
import os
import struct
import subprocess
import sys
import venv
import hashlib


def source_fingerprint(project):
    digest=hashlib.sha256()
    for path in sorted((project/'src').rglob('*.py')):
        digest.update(path.relative_to(project).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def check_environment():
    if sys.platform != 'win32' or sys.version_info[:2] != (3,13) or struct.calcsize('P') != 8:
        raise RuntimeError('La construction validée exige Windows x64 et Python 3.13.')
    for line in (Path(__file__).parent/'requirements-win-py313.lock').read_text().splitlines():
        if line and not line.startswith('#'):
            name, expected = line.split('==')
            if importlib.metadata.version(name) != expected:
                raise RuntimeError(f'Version différente pour {name}. Relancez sans --use-current-environment pour utiliser un environnement isolé.')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--use-current-environment', action='store_true')
    parser.add_argument('--no-zip', action='store_true', help='Construire le dossier complet sans archive ZIP.')
    args=parser.parse_args()
    if sys.platform != 'win32' or sys.version_info[:2] != (3,13) or struct.calcsize('P') != 8:
        raise RuntimeError('La construction validée exige Windows x64 et Python 3.13.')
    project=Path(__file__).resolve().parents[1]
    if not args.use_current_environment:
        folder=project/'.venv-build'
        if not (folder/'Scripts/python.exe').is_file():
            venv.create(folder,with_pip=True)
        python=folder/'Scripts/python.exe'
        subprocess.run([str(python),'-m','pip','install','-r',str(project/'packaging/requirements-win-py313.lock')],check=True)
        subprocess.run([str(python),'-m','pip','install','--no-deps','--no-build-isolation','-e',str(project)],check=True)
        return subprocess.run([str(python),str(Path(__file__).resolve()),'--use-current-environment']
                              + (['--no-zip'] if args.no_zip else []),check=True).returncode
    check_environment()
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    work=project/'build'/f'portable_{stamp}'
    release=project/'dist'/'releases'/stamp
    work.mkdir(parents=True,exist_ok=False)
    release.mkdir(parents=True,exist_ok=False)
    env={k:v for k,v in os.environ.items() if not k.startswith(('SME_','SMEDB_'))}
    # Keep this verified interpreter's libraries available after isolating APPDATA.
    env['PYTHONPATH']=os.pathsep.join(str(Path(p).resolve()) for p in sys.path if p and Path(p).is_dir())
    for key in ('APPDATA','LOCALAPPDATA','USERPROFILE','TEMP','TMP'):
        env[key]=str(work)
    from PIL import Image
    with Image.open(project/'src/sme_gend/resources/icons/app.png') as icon:
        icon.convert('RGBA').save(project/'build/app.ico',format='ICO',sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])
    before=source_fingerprint(project)
    subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--distpath',str(release),
                    '--workpath',str(work),str(project/'packaging/sme_gendarmerie_portable.spec')],
                   cwd=project,env=env,check=True)
    if before != source_fingerprint(project):
        raise RuntimeError('Les sources ont changé pendant la construction. Relancez pour produire une version cohérente.')
    subprocess.run([sys.executable,str(project/'scripts/package_verified_portable.py'),str(release/'SME_Gendarmerie')]
                   + (['--no-zip'] if args.no_zip else []),cwd=project,env=env,check=True)
    print('Portable vérifié :',release/('SME_Gendarmerie' if args.no_zip else 'SME_Gendarmerie_portable.zip'))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
