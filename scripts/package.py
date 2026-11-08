"""Builds dist/rhd-e-registry-<version>.zip, ready to upload to cPanel hosting (Setup Python App).

The zip contains the app plus Django and PyMySQL in vendor/ (pure-Python, Linux, Python 3.12+),
so the server needs no "pip install". Local data (data/), secrets (.env) and caches are left out.

    .venv/Scripts/python scripts/package.py      (Windows)
    .venv/bin/python scripts/package.py          (Linux / macOS)
"""
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOP = 'rhd-e-registry'
INCLUDE = ['manage.py', 'passenger_wsgi.py', 'requirements.txt', 'VERSION.json', 'README.md', 'DEPLOY.md', '.env.example',
           'eregistry', 'registry', 'public']
SKIP_PARTS = {'__pycache__', '.pytest_cache'}


def build_vendor():
    vendor = ROOT / 'build' / 'vendor'
    if vendor.exists():
        shutil.rmtree(vendor)
    vendor.mkdir(parents=True)
    # Wheels for the server (Linux, CPython 3.12): pure Python, so they work on any CPython >= 3.12.
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check', '--no-compile',
                    '--target', str(vendor), '--only-binary=:all:', '--platform', 'manylinux2014_x86_64',
                    '--python-version', '3.12', '--implementation', 'cp', '-r', str(ROOT / 'requirements.txt')], check=True)
    for p in list(vendor.rglob('__pycache__')):
        shutil.rmtree(p, ignore_errors=True)
    shutil.rmtree(vendor / 'bin', ignore_errors=True)
    return vendor


def files_under(path):
    if path.is_file():
        yield path
        return
    for p in sorted(path.rglob('*')):
        if p.is_file() and not (SKIP_PARTS & set(p.parts)) and p.suffix != '.pyc':
            yield p


def main():
    version = json.loads((ROOT / 'VERSION.json').read_text())['version']
    vendor = build_vendor()
    out_dir = ROOT / 'dist'
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f'rhd-e-registry-{version}.zip'
    n = 0
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for item in INCLUDE:
            for f in files_under(ROOT / item):
                z.write(f, f'{TOP}/{f.relative_to(ROOT).as_posix()}')
                n += 1
        for f in files_under(vendor):
            z.write(f, f'{TOP}/vendor/{f.relative_to(vendor).as_posix()}')
            n += 1
    names = zipfile.ZipFile(out).namelist()
    for must in ('passenger_wsgi.py', 'manage.py', 'public/index.html', 'public/setup.html', 'registry/migrations/0001_initial.py',
                 'vendor/django/__init__.py', 'vendor/pymysql/__init__.py'):
        if f'{TOP}/{must}' not in names:
            sys.exit(f'Missing {must} in the package')
    print(f'Created {out.relative_to(ROOT)} ({n} files, {out.stat().st_size / 1048576:.1f} MB)')


if __name__ == '__main__':
    main()
