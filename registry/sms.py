"""SMS alerts.

workflow.notify() queues SMS rows in Outbox. SmsWorker sends them through the configured provider,
retries failures with back-off, and polls for delivery reports.

Provider: Fish Africa (LetsFish), https://docs.letsfish.africa  (OpenAPI: https://api.letsfish.africa/api/docs)
  POST {base}/sms          Authorization: Bearer <app_id>.<app_secret>
       body {sender_id (<= 11 chars), message, recipients: ["233XXXXXXXXX"]}
       202 {success: true, data: [{recipient, reference, status}]}
       error {success: false, error: {status, title, detail}}  (402 = insufficient balance)
  GET  {base}/sms/{reference}  ->  {success: true, data: {status, ...}}
"""
import json
import logging
import os
import re
import threading
import unicodedata
import urllib.error
import urllib.request
from datetime import timedelta

log = logging.getLogger('registry')

LETSFISH_BASE = os.environ.get('LETSFISH_BASE_URL', 'https://api.letsfish.africa/v1')
MAX_ATTEMPTS = 5
MAX_SMS_CHARS = 306                 # two GSM-7 segments
DELIVERY_POLL_MINUTES = 10
DELIVERY_POLL_DAYS = 2
PENDING_DELIVERY = ['queued', 'pending', 'submitted', 'scheduled', 'retry']


# ---------------- formatting ----------------
def normalize_phone(value, country_code='233'):
    """Local or international numbers → 233XXXXXXXXX (no "+"). None if not a plausible mobile number."""
    if not value:
        return None
    s = re.sub(r'[\s\-().]', '', str(value).strip())
    if s.startswith('+'):
        s = s[1:]
    elif s.startswith('00'):
        s = s[2:]
    elif s.startswith('0'):
        s = country_code + s[1:]
    elif len(s) == 9 and not s.startswith(country_code):
        s = country_code + s
    if not re.fullmatch(r'\d{10,15}', s):
        return None
    if country_code == '233' and s.startswith('233') and len(s) != 12:
        return None
    return s


def to_gsm(text):
    """Keeps messages in the cheaper GSM-7 character set: strips accents and replaces typographic symbols."""
    t = str(text)
    for a, b in (('‘', "'"), ('’', "'"), ('“', '"'), ('”', '"'), ('–', '-'), ('—', '-'), ('→', '->'), ('…', '...')):
        t = t.replace(a, b)
    t = ''.join(c for c in unicodedata.normalize('NFD', t) if unicodedata.category(c) != 'Mn')
    return re.sub(r'[^\x20-\x7E\n]', '', t)


def format_sms(settings, message, letter_id):
    prefix = 'e-Registry: '
    base = (settings.get('app_url') or '').rstrip('/')
    link = f' {base}/#/letter/{letter_id}' if base and letter_id else ''
    body = re.sub(r'\s+', ' ', to_gsm(message)).strip()
    room = MAX_SMS_CHARS - len(prefix) - len(link)
    if len(body) > room:
        body = body[:room - 3].rstrip() + '...'
    return prefix + body + link


# ---------------- configuration ----------------
def sms_config():
    from .store import get_settings
    s = get_settings()
    return {
        'enabled': s.get('sms_enabled') == '1',
        'provider': s.get('sms_provider') or 'test',
        'sender_id': s.get('sms_sender_id') or '',
        'app_id': os.environ.get('LETSFISH_APP_ID') or s.get('sms_app_id') or '',
        'app_secret': os.environ.get('LETSFISH_APP_SECRET') or s.get('sms_app_secret') or '',
        'credentials_from_env': bool(os.environ.get('LETSFISH_APP_ID') and os.environ.get('LETSFISH_APP_SECRET')),
        'country_code': s.get('sms_country_code') or '233',
        'settings': s,
    }


def config_problems(cfg):
    if cfg['provider'] == 'test':
        return []
    p = []
    if cfg['provider'] != 'letsfish':
        p.append(f'Unknown SMS provider "{cfg["provider"]}"')
    if not cfg['app_id'] or not cfg['app_secret']:
        p.append('LetsFish App ID and App Secret are not set')
    if not cfg['sender_id']:
        p.append('Sender ID is not set')
    elif len(cfg['sender_id']) > 11:
        p.append('Sender ID must be 11 characters or fewer')
    return p


# ---------------- HTTP ----------------
def http_fetch(method, url, headers=None, body=None, timeout=20):
    """Returns (status, headers, text). Raises OSError if the server cannot be reached."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read().decode('utf-8', 'replace')


# ---------------- providers ----------------
# send() returns {ok, ref, status, error, http_status, retry_after, permanent, provider_down}
class TestProvider:
    """Test mode: nothing leaves the server. Messages are logged and marked sent."""
    def send(self, cfg, to, message, fetch):
        log.info('[SMS test mode] to %s: %s', to, message)
        import time
        return {'ok': True, 'ref': f'test-{int(time.time() * 1000):x}', 'status': 'test'}

    def status(self, cfg, ref, fetch):
        return None


class LetsFishProvider:
    def _auth(self, cfg):
        return {'Authorization': f'Bearer {cfg["app_id"]}.{cfg["app_secret"]}', 'Accept': 'application/json'}

    def send(self, cfg, to, message, fetch):
        payload = json.dumps({'sender_id': cfg['sender_id'], 'message': message, 'recipients': [to]}).encode()
        try:
            status, headers, text = fetch('POST', f'{LETSFISH_BASE}/sms', {**self._auth(cfg), 'Content-Type': 'application/json'}, payload)
        except OSError as e:
            return {'ok': False, 'error': f'Could not reach LetsFish: {e}'}
        try:
            body = json.loads(text) if text else None
        except ValueError:
            body = None
        if 200 <= status < 300 and body and body.get('success') is not False:
            data = body.get('data')
            item = None
            if isinstance(data, list) and data:
                item = next((d for d in data if d.get('recipient') == to), data[0])
            if item and item.get('status') == 'failed':
                return {'ok': False, 'error': 'LetsFish rejected the recipient', 'permanent': True, 'ref': item.get('reference')}
            return {'ok': True, 'ref': (item or {}).get('reference'), 'status': (item or {}).get('status') or 'queued'}
        err = (body or {}).get('error') or {}
        detail = ': '.join(x for x in (err.get('title'), err.get('detail')) if x) or f'HTTP {status}'
        out = {'ok': False, 'http_status': status, 'error': f'LetsFish {status}: {detail}'}
        if status == 429:
            try:
                out['retry_after'] = int({k.lower(): v for k, v in headers.items()}.get('retry-after') or 60)
            except ValueError:
                out['retry_after'] = 60
        if status in (401, 402, 403):
            out['provider_down'] = True     # bad credentials, no balance, forbidden sender ID: pause the queue
        elif status in (400, 404, 422):
            out['permanent'] = True         # a malformed request will not succeed on retry
        return out

    def status(self, cfg, ref, fetch):
        try:
            status, _, text = fetch('GET', f'{LETSFISH_BASE}/sms/{urllib.request.quote(ref)}', self._auth(cfg), None, 15)
            if not 200 <= status < 300:
                return None
            return ((json.loads(text) or {}).get('data') or {}).get('status')
        except (OSError, ValueError):
            return None


PROVIDERS = {'test': TestProvider(), 'letsfish': LetsFishProvider()}


# ---------------- worker ----------------
class SmsWorker:
    def __init__(self, fetch=None, interval=15):
        self.fetch = fetch or http_fetch
        self.interval = interval
        self.lock = threading.Lock()
        self.paused_until = None
        self.last_provider_error = None
        self.hooks = []          # extra periodic jobs, e.g. overdue reminders
        self._thread = None

    def start(self):
        """Background thread: tick every `interval` seconds (as long as the process is alive)."""
        if self._thread:
            return
        stop = threading.Event()

        def loop():
            from django.db import close_old_connections
            stop.wait(2)
            while not stop.is_set():
                try:
                    close_old_connections()
                    self.tick()
                except Exception:   # noqa: BLE001 - keep the worker alive
                    log.exception('SMS worker error')
                finally:
                    close_old_connections()
                stop.wait(self.interval)
        self._thread = threading.Thread(target=loop, name='sms-worker', daemon=True)
        self._thread.start()

    def tick(self):
        if not self.lock.acquire(blocking=False):
            return
        try:
            for h in self.hooks:
                h()
            self.send_due()
            self.poll_delivery()
        finally:
            self.lock.release()

    def _now(self):
        from .store import utcnow
        return utcnow()

    def claim(self, row):
        """Marks a row as being sent, so a second copy of the app (some hosts run several) skips it."""
        from .models import Outbox
        return Outbox.objects.filter(pk=row.pk, status__in=['pending', 'retry']).update(status='sending', next_attempt_at=self._now()) == 1

    def deliver(self, row, cfg):
        from .models import Outbox
        result = PROVIDERS[cfg['provider']].send(cfg, row.recipient, row.message, self.fetch)
        attempts = row.attempts + 1
        if result.get('ok'):
            Outbox.objects.filter(pk=row.pk).update(status='sent', sent_at=self._now(), attempts=attempts, provider=cfg['provider'],
                                                    provider_ref=result.get('ref'), delivery_status=result.get('status'), last_error=None)
            self.last_provider_error = None
            return result
        status = 'retry'
        delay = min(2 ** attempts, 60)
        if result.get('retry_after'):
            delay = max(1, -(-result['retry_after'] // 60))
        if result.get('provider_down'):
            delay = 30   # an account-wide problem: pause, without using up this message's attempts
            self.paused_until = self._now() + timedelta(minutes=30)
            self.last_provider_error = {'at': self._now().isoformat(), 'error': result.get('error')}
        if result.get('permanent') or (not result.get('provider_down') and attempts >= MAX_ATTEMPTS):
            status = 'failed'
        Outbox.objects.filter(pk=row.pk).update(
            status=status, attempts=row.attempts if result.get('provider_down') else attempts,
            next_attempt_at=self._now() + timedelta(minutes=delay), last_error=result.get('error'), provider=cfg['provider'])
        return result

    def send_due(self):
        from django.db.models import Q
        from .models import Outbox
        cfg = sms_config()
        if not cfg['enabled'] or config_problems(cfg) or (self.paused_until and self._now() < self.paused_until):
            return 0
        # Rows left "sending" by a process that stopped mid-send are retried after 10 minutes.
        Outbox.objects.filter(channel='sms', status='sending', next_attempt_at__lt=self._now() - timedelta(minutes=10)).update(status='retry')
        rows = list(Outbox.objects.filter(channel='sms', status__in=['pending', 'retry'])
                    .filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=self._now())).order_by('id')[:20])
        sent = 0
        for row in rows:
            if not self.claim(row):
                continue
            r = self.deliver(row, cfg)
            sent += 1 if r.get('ok') else 0
            if r.get('provider_down'):
                break
        return sent

    def poll_delivery(self):
        from django.db.models import Q
        from .models import Outbox
        cfg = sms_config()
        if cfg['provider'] == 'test' or config_problems(cfg):
            return
        now = self._now()
        checked_before = now - timedelta(minutes=DELIVERY_POLL_MINUTES)
        rows = (Outbox.objects.filter(channel='sms', status='sent', provider=cfg['provider'], provider_ref__isnull=False,
                                      delivery_status__in=PENDING_DELIVERY, sent_at__gt=now - timedelta(days=DELIVERY_POLL_DAYS))
                .filter(Q(delivery_checked_at__lt=checked_before) | Q(delivery_checked_at__isnull=True, sent_at__lt=checked_before))
                .order_by('id')[:20])
        for row in rows:
            st = PROVIDERS[cfg['provider']].status(cfg, row.provider_ref, self.fetch)
            Outbox.objects.filter(pk=row.pk).update(delivery_status=st or row.delivery_status, delivery_checked_at=now)

    def send_now(self, phone, message):
        """Sends a message immediately (the administrator's "Send test SMS" button)."""
        from .models import Outbox
        cfg = sms_config()
        problems = config_problems(cfg)
        if problems:
            return {'ok': False, 'error': '; '.join(problems)}
        to = normalize_phone(phone, cfg['country_code'])
        if not to:
            return {'ok': False, 'error': 'That is not a valid mobile number'}
        row = Outbox.objects.create(channel='sms', recipient=to, message=format_sms(cfg['settings'], message, None),
                                    status='sending', next_attempt_at=self._now())
        r = self.deliver(row, cfg)
        return {**r, 'to': to, 'id': row.pk}


worker = SmsWorker()
