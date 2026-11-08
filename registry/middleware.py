"""Middleware: security headers, the set-up page, and starting the background worker."""
import logging
import os

from eregistry import envconfig as cfg

from . import setup_state as st

log = logging.getLogger('registry')

CSP = "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; object-src 'none'"


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        resp = self.get_response(request)
        if not request.path_info.startswith('/django-admin/'):
            resp.setdefault('Content-Security-Policy', CSP)
        resp.setdefault('X-Content-Type-Options', 'nosniff')
        resp.setdefault('Referrer-Policy', 'same-origin')
        if request.path_info.startswith('/api/'):
            resp.setdefault('Cache-Control', 'no-store')
        return resp


class SetupMiddleware:
    """Until the database is connected (and migrated), every request is answered by the set-up page."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not st.is_ready():
            # Another copy of the app may have just saved the database details on the set-up page.
            if cfg.reload_if_changed():
                st.apply_db_settings()
                force = True
            else:
                force = False
            try:
                ready = st.ensure_ready(force=force)
            except st.NotConfigured:
                ready = False
            except Exception as e:  # noqa: BLE001
                ready = False
                if not st.state['reason']:
                    # Not a connection problem: show it rather than the set-up page.
                    log.exception('Could not start')
                    from django.http import HttpResponse
                    return HttpResponse(f'RHD e-Registry could not start: {e}\n\nSee stderr.log in the app folder (cPanel → Setup Python App shows its location), then restart the app.',
                                        status=500, content_type='text/plain; charset=utf-8')
            if not ready:
                if not st.state['reason']:
                    st.state['reason'] = 'not-configured'
                st.setup_key()          # make sure a key exists (file) unless SETUP_KEY is set
                from .setup_views import handle_setup_request
                return handle_setup_request(request)
        return self.get_response(request)


class WorkerMiddleware:
    """Starts the SMS / overdue-reminder worker thread once the app is ready (not during tests)."""
    started = False

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not WorkerMiddleware.started and st.is_ready() and not cfg.TESTING and os.environ.get('SMS_WORKER') != '0':
            WorkerMiddleware.started = True
            from .reminders import run_overdue_reminders
            from .sms import worker
            worker.hooks.append(run_overdue_reminders)
            worker.start()
        return self.get_response(request)
