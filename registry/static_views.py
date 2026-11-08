"""Serves the browser app (the public/ folder): index.html for page addresses, plus its assets."""
import mimetypes
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, HttpResponse, HttpResponseRedirect

TYPES = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
         '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon', '.woff2': 'font/woff2'}


def serve_public(request, path='', page=False):
    root = Path(settings.PUBLIC_DIR).resolve()
    # The page uses relative links, so it must be opened with a trailing slash when installed in a
    # sub-folder (e.g. https://example.org/eregistry/).
    if not page and path in ('', '/') and not request.path.endswith('/'):
        return HttpResponseRedirect(request.path + '/' + (('?' + request.GET.urlencode()) if request.GET else ''))
    if not page and path.rstrip('/') in ('setup', 'setup.html'):   # set-up is finished: go to the app
        return HttpResponseRedirect('./' if path.endswith('/') else '.')
    target = None
    if page:
        target = root / path
    else:
        parts = [p for p in path.split('/') if p and p not in ('.', '..')]
        # Try the path as given, then with leading folders removed (handles a sub-folder base path).
        for i in range(len(parts)):
            f = (root / Path(*parts[i:])).resolve()
            if root not in f.parents:
                return HttpResponse('Forbidden', status=403, content_type='text/plain')
            if f.is_file():
                target = f
                break
        if target is None:
            if Path(path).suffix and Path(path).suffix != '.html':
                return HttpResponse('Not found', status=404, content_type='text/plain')
            target = root / 'index.html'
    ext = target.suffix.lower()
    resp = FileResponse(open(target, 'rb'), content_type=TYPES.get(ext) or mimetypes.guess_type(str(target))[0] or 'application/octet-stream')
    resp['Cache-Control'] = 'public, max-age=31536000, immutable' if ext == '.woff2' else 'no-cache'
    return resp
