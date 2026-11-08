"""Configuration for RHD e-Registry, read before Django starts.

Sources, highest priority first:
  1. environment variables (e.g. cPanel → Setup Python App → Environment variables)
  2. ".env" in the project folder (optional, edited by hand)
  3. "settings.env" in the data folder, written by the web set-up page (/setup) when the
     database details are entered in the browser.
None of these files are ever served to browsers.
"""
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
env = os.environ
_from_settings_file = set()   # keys whose value came from settings.env (may be replaced later)


def parse_env(text):
    out = {}
    for line in str(text).splitlines():
        if line.strip().startswith('#'):
            continue
        m = re.match(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$', line)
        if not m:
            continue
        v = m.group(2)
        if len(v) >= 2 and v[0] == v[-1] and v[0] in '"\'':
            v = v[1:-1].replace('\\"', '"').replace('\\\\', '\\')
        else:
            v = re.sub(r'\s+#.*$', '', v)
        out[m.group(1)] = v
    return out


def _load(path, from_settings=False):
    try:
        text = Path(path).read_text(encoding='utf-8-sig')
    except OSError:
        return False
    for k, v in parse_env(text).items():
        if k not in env or (from_settings and k in _from_settings_file):
            env[k] = v
            if from_settings:
                _from_settings_file.add(k)
    return True


_load(env.get('ENV_FILE') or ROOT / '.env')

# Development: "manage.py runserver" / "manage.py test", or ERG_DEV=1. Anything else (including cPanel
# hosting through passenger_wsgi.py) is a real installation, which never silently uses a local
# SQLite file: until MySQL is configured it shows the set-up page.
_cmd = sys.argv[1] if len(sys.argv) > 1 else ''
DEV = env.get('ERG_DEV') == '1' or (Path(sys.argv[0]).name == 'manage.py' and _cmd in ('runserver', 'test'))
TESTING = _cmd == 'test'

if TESTING and not env.get('DATA_DIR'):
    import tempfile
    DATA_DIR = Path(tempfile.mkdtemp(prefix='ereg-test-'))   # tests never touch the real data folder
else:
    DATA_DIR = Path(env.get('DATA_DIR') or (ROOT / 'data' if DEV else Path.home() / 'rhd-e-registry-data'))
UPLOAD_DIR = DATA_DIR / 'uploads'
SETTINGS_FILE = DATA_DIR / 'settings.env'
_load(SETTINGS_FILE, from_settings=True)


def _stamp():
    try:
        s = SETTINGS_FILE.stat()
        return f'{s.st_mtime_ns}:{s.st_size}'
    except OSError:
        return None


_settings_stamp = _stamp()


def db_configured():
    return bool(env.get('DB_CLIENT') or env.get('DATABASE_URL'))


def mysql_params():
    url = env.get('DATABASE_URL')
    if url and url.startswith('mysql'):
        from urllib.parse import urlparse, unquote
        u = urlparse(url)
        return {'host': u.hostname or 'localhost', 'port': u.port or 3306, 'user': unquote(u.username or ''),
                'password': unquote(u.password or ''), 'database': unquote(u.path.lstrip('/'))}
    return {'host': env.get('DB_HOST') or 'localhost', 'port': int(env.get('DB_PORT') or 3306),
            'user': env.get('DB_USER') or '', 'password': env.get('DB_PASSWORD') or '', 'database': env.get('DB_NAME') or ''}


def db_client():
    if env.get('DATABASE_URL', '').startswith('mysql'):
        return 'mysql'
    return (env.get('DB_CLIENT') or ('sqlite' if DEV else '')).lower()


def database_settings():
    """The Django DATABASES['default'] entry for the current configuration."""
    client = db_client()
    if client == 'mysql':
        p = mysql_params()
        return {
            'ENGINE': 'django.db.backends.mysql', 'NAME': p['database'], 'USER': p['user'], 'PASSWORD': p['password'],
            'HOST': p['host'], 'PORT': str(p['port']), 'CONN_MAX_AGE': 60, 'CONN_HEALTH_CHECKS': True,
            'OPTIONS': {'charset': 'utf8mb4', 'init_command': "SET sql_mode='STRICT_TRANS_TABLES'"},
        }
    if client == 'sqlite':
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        return {'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(env.get('SQLITE_FILE') or DATA_DIR / 'eregistry.sqlite3'),
                'OPTIONS': {'timeout': 20}}
    # Not configured: no database until the set-up page has been completed.
    return {'ENGINE': 'django.db.backends.dummy'}


def locked_keys(keys):
    """Keys fixed by environment variables or .env, which the set-up page cannot change."""
    return [k for k in keys if k in env and k not in _from_settings_file]


def save_settings(values):
    """Saves values to settings.env (readable only by the hosting account) and applies them."""
    global _settings_stamp
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        current = parse_env(SETTINGS_FILE.read_text(encoding='utf-8-sig'))
    except OSError:
        current = {}
    merged = {**current, **{k: str(v) for k, v in values.items()}}

    def q(v):
        return '"' + str(v).replace('\\', '\\\\').replace('"', '\\"').replace('\r', '').replace('\n', '') + '"'

    text = (f'# RHD e-Registry settings, written by the set-up page on {time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}.\n'
            "# Values set in cPanel environment variables or in the project's .env file take priority.\n"
            + '\n'.join(f'{k}={q(v)}' for k, v in merged.items()) + '\n')
    tmp = SETTINGS_FILE.with_name(f'settings.env.{os.getpid()}.tmp')
    tmp.write_text(text, encoding='utf-8')
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, SETTINGS_FILE)
    for k, v in values.items():
        if k not in env or k in _from_settings_file:
            env[k] = str(v)
            _from_settings_file.add(k)
    _settings_stamp = _stamp()


def reload_if_changed():
    """Re-reads settings.env if another process changed it. Returns True if reloaded."""
    global _settings_stamp
    stamp = _stamp()
    if stamp == _settings_stamp:
        return False
    _load(SETTINGS_FILE, from_settings=True)
    _settings_stamp = stamp
    return True
