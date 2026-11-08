"""Database readiness and the web set-up page.

Until MySQL is configured (or when the saved details stop working) the site shows the set-up page.
It asks for the MySQL details, tests them, saves them to settings.env in the data folder, creates
the tables (Django migrations) and the first administrator, and starts the app. A one-time set-up
key stops anyone else from using the page; it comes from the SETUP_KEY environment variable, or the
first line of SETUP-KEY.txt in the data folder, or is generated into that file.
"""
import logging
import re
import secrets
import threading
import time

from django.conf import settings
from django.core.management import call_command
from django.db import connection, connections

from eregistry import envconfig as cfg

log = logging.getLogger('registry')

SETUP_KEY_FILE = cfg.DATA_DIR / 'SETUP-KEY.txt'
MIN_KEY = 8
_lock = threading.Lock()
_ready = False
_last_attempt = 0.0
state = {'reason': None, 'error': None}      # set while the set-up page is needed


class NotConfigured(Exception):
    pass


# ---------------- set-up key ----------------
def norm_key(k):
    """Ignores case, spaces, quotes and invisible characters (e.g. a byte-order mark)."""
    return re.sub(r'[\s​-‍⁠﻿"\'`]', '', str(k or '')).upper()


def _key_from_file(text):
    lines = [l.strip() for l in str(text).lstrip('﻿').splitlines()]
    for line in lines:
        if not line or line.startswith('#'):
            continue
        m = re.search(r'key\s*[:=]\s*(.+)$', line, re.I)   # "set-up key: XXXX", "Key = XXXX"
        candidate = m.group(1).strip() if m else line
        if len(norm_key(candidate)) >= MIN_KEY:
            return candidate
    return None


def setup_key_source():
    if len(norm_key(cfg.env.get('SETUP_KEY'))) >= MIN_KEY:
        return 'env'
    try:
        if _key_from_file(SETUP_KEY_FILE.read_text(encoding='utf-8-sig', errors='replace')):
            return 'file'
    except OSError:
        pass
    return 'generated'


def setup_key():
    if len(norm_key(cfg.env.get('SETUP_KEY'))) >= MIN_KEY:
        return cfg.env['SETUP_KEY'].strip()
    cfg.DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        k = _key_from_file(SETUP_KEY_FILE.read_text(encoding='utf-8-sig', errors='replace'))
        if k:
            return k
    except OSError:
        pass
    abc = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'
    key = '-'.join(''.join(secrets.choice(abc) for _ in range(4)) for _ in range(3))
    text = (f'RHD e-Registry set-up key:  {key}\n\nEnter this key on the set-up page of your e-Registry website '
            'to connect the MySQL database.\nThis file is deleted automatically when set-up is complete.\n')
    try:
        with open(SETUP_KEY_FILE, 'x', encoding='utf-8') as f:
            f.write(text)
    except FileExistsError:
        try:
            k = _key_from_file(SETUP_KEY_FILE.read_text(encoding='utf-8-sig', errors='replace'))
            if k:
                return k
        except OSError:
            pass
        SETUP_KEY_FILE.write_text(text, encoding='utf-8')
    try:
        SETUP_KEY_FILE.chmod(0o600)
    except OSError:
        pass
    log.warning('Set-up required. The set-up key is in %s', SETUP_KEY_FILE)
    return key


# ---------------- MySQL ----------------
def explain_mysql_error(code, c):
    return {
        1045: 'MySQL refused the username or password. In cPanel → MySQL Databases, check the user exists, its password, and that it has been added to the database with ALL PRIVILEGES.',
        1044: f'The user is not allowed to use the database "{c.get("database")}". In cPanel → MySQL Databases → "Add User To Database", add the user with ALL PRIVILEGES.',
        1049: f'The database "{c.get("database")}" does not exist. Check the name; cPanel adds your username in front, e.g. cpaneluser_eregistry.',
        2003: f'Nothing is accepting MySQL connections at {c.get("host")}:{c.get("port")}. On cPanel shared hosting the host is normally "localhost" and the port 3306.',
        2005: f'The database server name "{c.get("host")}" was not found. On cPanel shared hosting use "localhost".',
    }.get(code)


def mysql_error_code(exc):
    for e in (exc, getattr(exc, '__cause__', None)):
        args = getattr(e, 'args', ())
        if args and isinstance(args[0], int):
            return args[0]
    return None


def test_mysql(c):
    import pymysql
    conn = pymysql.connect(host=c['host'], port=int(c['port']), user=c['user'], password=c['password'], database=c['database'],
                           connect_timeout=8, charset='utf8mb4')
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT VERSION(), DATABASE()')
            version, db = cur.fetchone()
        return {'version': version, 'database': db}
    finally:
        conn.close()


# ---------------- applying database settings at run time ----------------
def apply_db_settings():
    """Points Django's default database at the current configuration (after the set-up page saved it)."""
    new = connections.configure_settings({'default': cfg.database_settings()})['default']
    settings.DATABASES['default'] = new
    connections.settings['default'] = new
    try:
        connections['default'].close()
    except Exception:  # noqa: BLE001
        pass
    try:
        del connections['default']
    except (AttributeError, KeyError):
        pass


def _migrate():
    """Runs pending migrations; on MySQL a named lock stops two copies of the app migrating at once."""
    from django.db.migrations.executor import MigrationExecutor
    executor = MigrationExecutor(connection)
    if not executor.migration_plan(executor.loader.graph.leaf_nodes()):
        return
    if connection.vendor == 'mysql':
        with connection.cursor() as cur:
            cur.execute("SELECT GET_LOCK('rhd_eregistry_migrate', 120)")
        try:
            call_command('migrate', interactive=False, verbosity=0)
        finally:
            with connection.cursor() as cur:
                cur.execute("SELECT RELEASE_LOCK('rhd_eregistry_migrate')")
    else:
        call_command('migrate', interactive=False, verbosity=0)


def is_ready():
    return _ready


def ensure_ready(force=False):
    """Connects, migrates and seeds the database once. Raises NotConfigured or a database error."""
    global _ready, _last_attempt
    if _ready:
        return True
    with _lock:
        if _ready:
            return True
        if not force and time.time() - _last_attempt < 10 and state['reason']:
            return False   # failed a moment ago: do not retry on every request
        _last_attempt = time.time()
        if cfg.TESTING:            # the test runner creates and migrates its own database
            _ready = True
            return True
        if connection.vendor == 'dummy' or (not cfg.db_configured() and not cfg.DEV):
            state.update(reason='not-configured', error=None)
            raise NotConfigured()
        try:
            connection.ensure_connection()
            _migrate()
            from .store import seed_first_run
            if seed_first_run():
                log.warning('New database: created the administrator account "admin" with password "admin1234". It must be changed at first sign-in.')
        except Exception as e:  # noqa: BLE001
            code = mysql_error_code(e)
            if connection.vendor == 'mysql' and code:
                state.update(reason='connection-failed', error=explain_mysql_error(code, cfg.mysql_params()) or str(e))
            raise
        state.update(reason=None, error=None)
        _ready = True
        try:
            SETUP_KEY_FILE.unlink()
        except OSError:
            pass
        return True


def request_restart():
    """Asks Passenger (cPanel) to restart every copy of the app so they all read the new settings."""
    tmp = cfg.ROOT / 'tmp'
    try:
        tmp.mkdir(exist_ok=True)
        (tmp / 'restart.txt').write_text(str(time.time()))
    except OSError:
        pass
