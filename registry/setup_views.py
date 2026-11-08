"""The web set-up page's API (used only while the database still needs to be connected)."""
import json
import secrets
import time

from django.http import JsonResponse

from eregistry import envconfig as cfg

from . import setup_state as st
from .static_views import serve_public

_fails = {}
LOCK_KEYS = ['DB_CLIENT', 'DB_HOST', 'DB_PORT', 'DB_NAME', 'DB_USER', 'DB_PASSWORD', 'DATABASE_URL']


def _err(status, message):
    return JsonResponse({'error': message}, status=status)


def handle_setup_request(request):
    """Answers every request while the site is in set-up mode."""
    path = request.path_info
    if not path.startswith('/api/'):
        if request.method not in ('GET', 'HEAD'):
            return _err(405, 'Method not allowed')
        for asset in ('styles.css', 'theme.js', 'setup.js', 'ghs-logo.png'):
            if path.endswith('/' + asset):
                return serve_public(request, asset)
        if '/fonts/' in path and path.endswith('.woff2'):
            return serve_public(request, 'fonts/' + path.rsplit('/', 1)[1])
        return serve_public(request, 'setup.html', page=True)
    if path == '/api/health':
        return JsonResponse({'ok': False, 'setup_required': True, 'version': _version()}, status=503)
    if path == '/api/setup/status' and request.method == 'GET':
        p = cfg.mysql_params()
        return JsonResponse({
            'setup_required': True, 'reason': st.state['reason'] or 'not-configured', 'error': st.state['error'],
            'key_file': str(st.SETUP_KEY_FILE), 'key_source': st.setup_key_source(), 'locked': cfg.locked_keys(LOCK_KEYS),
            'values': {'host': p['host'], 'port': p['port'], 'database': p['database'], 'user': p['user'], 'app_url': cfg.env.get('APP_URL', '')},
        })
    if path in ('/api/setup/test', '/api/setup/save') and request.method == 'POST':
        return _test_or_save(request, save=path.endswith('/save'))
    return JsonResponse({'error': 'The e-Registry needs to be set up first.', 'setup_required': True}, status=503)


def _version():
    from .api import APP_VERSION
    return APP_VERSION


def _test_or_save(request, save):
    if not request.content_type.startswith('application/json'):
        return _err(415, 'Expected JSON')
    try:
        body = json.loads(request.body or b'{}')
    except ValueError:
        return _err(400, 'Invalid JSON')
    ip = request.META.get('REMOTE_ADDR', '?')
    f = _fails.get(ip)
    if f and f['until'] > time.time():
        return _err(429, 'Too many wrong set-up keys. Wait 10 minutes and try again.')
    if not secrets.compare_digest(st.norm_key(body.get('key')).encode(), st.norm_key(st.setup_key()).encode()):
        n = (f or {}).get('count', 0) + 1
        _fails[ip] = {'count': n, 'until': time.time() + 600 if n >= 5 else 0}
        length = len(st.norm_key(st.setup_key()))
        if st.setup_key_source() == 'env':
            return _err(403, f'The set-up key is not correct. Enter the SETUP_KEY you set in cPanel → Setup Python App → Environment variables (it has {length} letters/digits, not counting spaces).')
        return _err(403, f'The set-up key is not correct. Open {st.SETUP_KEY_FILE} in cPanel File Manager and copy the key '
                         f'(it has {length} letters/digits, not counting spaces). Or set your own key with the SETUP_KEY environment variable and restart the app.')
    _fails.pop(ip, None)
    try:
        port = int(body.get('port') or 3306)
    except (TypeError, ValueError):
        port = 3306
    c = {'host': str(body.get('host') or 'localhost').strip()[:200], 'port': port, 'database': str(body.get('database') or '').strip()[:64],
         'user': str(body.get('user') or '').strip()[:80], 'password': str(body.get('password') or '')}
    if not c['database'] or not c['user']:
        return _err(400, 'Enter the database name and the database user.')
    try:
        info = st.test_mysql(c)
    except Exception as e:  # noqa: BLE001
        code = st.mysql_error_code(e)
        return _err(400, st.explain_mysql_error(code, c) or f'Could not connect to MySQL: {e}')
    if not save:
        return JsonResponse({'ok': True, 'message': f'Connected to MySQL {info["version"]}, database "{info["database"]}".'})

    app_url = str(body.get('app_url') or '').strip().rstrip('/')
    values = {'DB_CLIENT': 'mysql', 'DB_HOST': c['host'], 'DB_PORT': c['port'], 'DB_NAME': c['database'], 'DB_USER': c['user'], 'DB_PASSWORD': c['password']}
    if app_url:
        values['APP_URL'] = app_url
    locked = [k for k in cfg.locked_keys(list(values)) if str(cfg.env.get(k)) != str(values[k])]
    if locked:
        return _err(409, f'These settings are fixed in cPanel → Setup Python App → Environment variables (or in the .env file): {", ".join(locked)}. '
                         'Correct or delete them there, then restart the app.')
    cfg.save_settings(values)
    st.apply_db_settings()
    try:
        st.ensure_ready(force=True)
    except Exception as e:  # noqa: BLE001
        return _err(500, st.state.get('error') or f'The database connected, but the tables could not be created: {e}')
    st.request_restart()
    return JsonResponse({'ok': True})


urlpatterns = []   # set-up requests are answered by SetupMiddleware before URL routing
