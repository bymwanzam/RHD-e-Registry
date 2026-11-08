"""JSON API used by the browser app (public/app.js). Paths and response shapes match the app exactly."""
import base64
import csv
import hashlib
import io
import json
import re
import secrets
import time
from datetime import datetime, timedelta, timezone as dt_timezone
from functools import wraps
from pathlib import Path

from django.conf import settings as django_settings
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.db import transaction
from django.db.models import BooleanField, Case, Count, IntegerField, Min, Q, Value, When
from django.http import FileResponse, HttpResponse, JsonResponse
from django.urls import path, re_path
from django.views.decorators.csrf import csrf_exempt

from eregistry import envconfig as cfg

from . import workflow as wf
from .models import Attachment, Facility, Letter, LetterEvent, Notification, Outbox, User
from .roles import CATEGORIES, LEVELS, PRIORITIES, ROLES, STATUSES
from .sms import config_problems, normalize_phone, sms_config, worker as sms_worker
from .store import get_settings, iso, public_settings, set_setting, utcnow

HttpError, fail = wf.HttpError, wf.fail
MAX_FILE = 10 * 1024 * 1024
ALLOWED_MIME = {
    'application/pdf': '.pdf', 'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp',
    'application/msword': '.doc',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
    'application/vnd.ms-excel': '.xls',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': '.xlsx',
}
_failed_logins = {}


def _app_version():
    try:
        return json.loads((cfg.ROOT / 'VERSION.json').read_text())['version']
    except (OSError, ValueError, KeyError):
        return '2.0.0'


APP_VERSION = _app_version()


# ---------------- request handling ----------------
def api(methods=('GET',), public=False, admin=False):
    """Decorator: JSON body parsing, sign-in / admin checks, and {error} responses."""
    def deco(fn):
        @csrf_exempt
        @wraps(fn)
        def view(request, *args, **kwargs):
            try:
                if request.method not in methods:
                    fail(405, 'Method not allowed')
                body = {}
                if request.method in ('POST', 'PUT'):
                    # Requiring JSON blocks simple cross-site form posts (CSRF), alongside the SameSite cookie.
                    if not request.content_type.startswith('application/json'):
                        fail(415, 'Expected JSON')
                    try:
                        body = json.loads(request.body or b'{}') or {}
                    except ValueError:
                        fail(400, 'Invalid JSON')
                    if not isinstance(body, dict):
                        fail(400, 'Invalid JSON')
                user = None
                if not public:
                    user = request.user if request.user.is_authenticated and request.user.is_active else None
                    if not user:
                        fail(401, 'Please sign in')
                    if admin and not user.is_admin:
                        fail(403, 'Administrators only')
                return fn(request, user, body, *args, **kwargs)
            except HttpError as e:
                return JsonResponse({'error': e.message}, status=e.status)
        return view
    return deco


def ok(data, status=200):
    return JsonResponse(data, status=status, safe=False, json_dumps_params={'ensure_ascii': False})


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


# ---------------- serialisers ----------------
def public_user(u, facility=None):
    r = ROLES.get(u.role, {})
    f = facility or u.facility
    return {
        'id': u.id, 'name': u.name, 'username': u.username, 'role': u.role, 'role_label': r.get('label'), 'role_short': r.get('short'),
        'facility_id': u.facility_id, 'facility_name': f.name if f else None, 'facility_level': f.level if f else None,
        'phone': u.phone, 'email': u.email, 'is_admin': u.is_admin, 'active': u.is_active, 'must_change_password': u.must_change_password,
        'can_record_incoming': bool(r.get('secretary') or r.get('registry') or r.get('receiver') or u.is_admin),
        'can_create': not r.get('system'),
    }


def letters_query(u):
    return (wf.visible_letters(u)
            .select_related('origin_facility', 'dest_facility', 'holder__facility', 'created_by')
            .annotate(overdue_flag=Case(When(wf.overdue_q(), then=Value(True)), default=Value(False), output_field=BooleanField()),
                      attachment_n=Count('attachments', distinct=True)))


def letter_json(l):
    h, c, fo, fd = l.holder, l.created_by, l.origin_facility, l.dest_facility
    return {
        'id': l.id, 'ref_no': l.ref_no, 'subject': l.subject, 'summary': l.summary, 'category': l.category, 'priority': l.priority,
        'origin_facility_id': l.origin_facility_id, 'external_sender': l.external_sender, 'external_ref': l.external_ref,
        'letter_date': iso(l.letter_date), 'dest_facility_id': l.dest_facility_id, 'created_by': l.created_by_id,
        'holder_id': l.holder_id, 'desk_since': iso(l.desk_since), 'status': l.status, 'due_date': iso(l.due_date),
        'parent_id': l.parent_id, 'created_at': iso(l.created_at), 'dispatched_at': iso(l.dispatched_at),
        'received_at': iso(l.received_at), 'closed_at': iso(l.closed_at), 'updated_at': iso(l.updated_at),
        'origin_name': fo.name if fo else None, 'origin_code': fo.code if fo else None,
        'dest_name': fd.name, 'dest_code': fd.code,
        'holder_name': h.name if h else None, 'holder_role': h.role if h else None, 'holder_facility': h.facility.name if h else None,
        'holder_role_label': ROLES.get(h.role, {}).get('short') if h else None,
        'creator_name': c.name, 'creator_role': c.role, 'creator_role_label': ROLES.get(c.role, {}).get('short'),
        'from_name': fo.name if fo else l.external_sender,
        'overdue': bool(getattr(l, 'overdue_flag', False)), 'attachment_count': getattr(l, 'attachment_n', 0),
    }


def _day_start(s):
    return datetime.fromisoformat(s).replace(tzinfo=dt_timezone.utc)


def search_letters(u, q):
    qs = letters_query(u)
    box = q.get('box')
    if box == 'desk':
        qs = qs.filter(holder=u)
    elif box == 'sent':
        qs = qs.filter(created_by=u)
    elif box == 'handled':
        qs = qs.filter(id__in=LetterEvent.objects.filter(actor=u).values('letter_id'))
    if q.get('status') in STATUSES:
        qs = qs.filter(status=q['status'])
    if q.get('open') == '1':
        qs = qs.exclude(status='Closed')
    if q.get('category') in CATEGORIES:
        qs = qs.filter(category=q['category'])
    if q.get('priority') in PRIORITIES:
        qs = qs.filter(priority=q['priority'])
    if q.get('facility_id'):
        fid = _int(q['facility_id'])
        qs = qs.filter(Q(origin_facility_id=fid) | Q(dest_facility_id=fid))
    if q.get('holder_facility_id'):
        qs = qs.filter(holder__facility_id=_int(q['holder_facility_id']))
    if q.get('holder_id'):
        qs = qs.filter(holder_id=_int(q['holder_id']))
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', q.get('from') or ''):
        qs = qs.filter(created_at__gte=_day_start(q['from']))
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', q.get('to') or ''):
        qs = qs.filter(created_at__lt=_day_start(q['to']) + timedelta(days=1))
    if q.get('overdue') == '1':
        qs = qs.filter(wf.overdue_q())
    if q.get('q'):
        t = str(q['q']).strip()[:100]
        qs = qs.filter(Q(ref_no__icontains=t) | Q(subject__icontains=t) | Q(summary__icontains=t) | Q(external_sender__icontains=t)
                       | Q(external_ref__icontains=t) | Q(origin_facility__name__icontains=t) | Q(created_by__name__icontains=t))
    limit = min(_int(q.get('limit')) or 100, 1000)
    offset = max(_int(q.get('offset')), 0)
    total = qs.count()
    rows = [letter_json(l) for l in qs.order_by('-updated_at', '-id')[offset:offset + limit]]
    return {'rows': rows, 'total': total}


def officer_list(facility_id, exclude_id=None):
    qs = User.objects.filter(facility_id=facility_id, is_active=True).exclude(role='SYSADMIN').order_by('name')
    if exclude_id:
        qs = qs.exclude(pk=exclude_id)
    return [{'id': x.id, 'name': x.name, 'role': x.role, 'role_label': ROLES.get(x.role, {}).get('label')} for x in qs]


def letter_full(u, letter_id):
    l = letters_query(u).filter(pk=letter_id).first()
    if not l:
        if Letter.objects.filter(pk=letter_id).exists():
            fail(403, 'You do not have access to this letter')
        fail(404, 'Letter not found')
    events = [{
        'id': e.id, 'letter_id': e.letter_id, 'at': iso(e.at), 'actor_id': e.actor_id, 'action': e.action, 'to_user_id': e.to_user_id,
        'note': e.note, 'status': e.status, 'actor_name': e.actor.name, 'actor_role': e.actor.role, 'actor_facility': e.actor.facility.name,
        'to_name': e.to_user.name if e.to_user else None, 'to_role': e.to_user.role if e.to_user else None,
        'actor_role_label': ROLES.get(e.actor.role, {}).get('short'),
        'to_role_label': ROLES.get(e.to_user.role, {}).get('short') if e.to_user else None,
    } for e in l.events.select_related('actor__facility', 'to_user').order_by('id')]
    attachments = [{'id': a.id, 'filename': a.filename, 'mime': a.mime, 'size': a.size, 'at': iso(a.at), 'uploaded_by_name': a.uploaded_by.name}
                   for a in l.attachments.select_related('uploaded_by').order_by('id')]
    actions = wf.available_actions(u, l)
    parent = None
    if l.parent_id:
        p = Letter.objects.filter(pk=l.parent_id).values('id', 'ref_no', 'subject', 'status').first()
        parent = p or None
    replies = list(Letter.objects.filter(parent_id=l.id).order_by('id').values('id', 'ref_no', 'subject', 'status'))
    return {
        'letter': letter_json(l), 'events': events, 'attachments': attachments, 'actions': actions,
        'suggestion': wf.suggestion(u, l),
        'colleagues': officer_list(u.facility_id, u.id) if actions else [],
        'destOfficers': officer_list(l.dest_facility_id) if 'dispatch' in actions else [],
        'parent': parent, 'replies': replies,
        'can_attach': l.status != 'Closed' and (l.holder_id == u.id or l.created_by_id == u.id),
    }


# ---------------- auth & basics ----------------
@api(('POST',), public=True)
def login_view(request, user, body):
    username = str(body.get('username') or '').strip().lower()[:60]
    lock = _failed_logins.get(username)
    if lock and lock['until'] > time.time():
        fail(429, 'Too many failed attempts. Try again in a few minutes.')
    u = authenticate(request, username=username, password=str(body.get('password') or ''))
    if not u or not u.is_active:
        n = (lock or {}).get('count', 0) + 1
        _failed_logins[username] = {'count': n, 'until': time.time() + 300 if n >= 5 else 0}
        fail(401, 'Wrong username or password')
    _failed_logins.pop(username, None)
    login(request, u)
    return ok({'user': public_user(u)})


@api(('POST',), public=True)
def logout_view(request, user, body):
    logout(request)
    return ok({'ok': True})


@api(public=True)
def public_info(request, user, body):
    return ok({'region_name': get_settings().get('region_name')})


@api(public=True)
def health(request, user, body):
    from django.db import connection
    connection.ensure_connection()
    return ok({'ok': True, 'version': APP_VERSION, 'database': connection.vendor, 'python': __import__('platform').python_version()})


@api(public=True)
def cron(request, user, body):
    """Wake-up link for a cPanel cron job: runs SMS sending and reminders. Protected by a secret key."""
    expected = (get_settings().get('cron_key') or '').encode()
    given = str(request.GET.get('key') or '').encode()
    if not expected or not secrets.compare_digest(given, expected):
        fail(403, 'Invalid key')
    before = Outbox.objects.filter(channel='sms', status='sent').count()
    sms_worker.tick()
    return ok({'ok': True, 'sms_sent': Outbox.objects.filter(channel='sms', status='sent').count() - before, 'at': iso(utcnow())})


@api()
def me(request, user, body):
    unread = Notification.objects.filter(user=user, is_read=False).count()
    desk = Letter.objects.filter(holder=user).exclude(status='Closed').count()
    return ok({'user': public_user(user), 'unread': unread, 'desk': desk})


@api(('POST',))
def me_password(request, user, body):
    if not user.check_password(str(body.get('current') or '')):
        fail(400, 'Current password is wrong')
    pw = str(body.get('password') or '')
    if len(pw) < 8:
        fail(400, 'New password must be at least 8 characters')
    if len(pw) > 200:
        fail(400, 'Password is too long')
    user.set_password(pw)
    user.must_change_password = False
    user.save(update_fields=['password', 'must_change_password'])
    update_session_auth_hash(request, user)
    return ok({'ok': True})


def clean_phone(v):
    raw = str(v or '').strip()
    if not raw:
        return None
    if len(raw) > 40 or not normalize_phone(raw, get_settings().get('sms_country_code') or '233'):
        fail(400, f'"{raw[:40]}" is not a valid mobile number. Use e.g. 0244123456 or +233244123456')
    return raw


def clean_email(v):
    e = str(v or '').strip()
    if not e:
        return None
    if len(e) > 190 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', e):
        fail(400, 'Enter a valid email address')
    return e


@api(('POST',))
def me_profile(request, user, body):
    user.phone = clean_phone(body.get('phone'))
    user.email = clean_email(body.get('email'))
    user.save(update_fields=['phone', 'email'])
    return ok({'ok': True})


@api()
def meta(request, user, body):
    order = {'REGION': 0, 'DHD': 1, 'HOSPITAL': 2}
    facilities = sorted(Facility.objects.values('id', 'name', 'code', 'level', 'parent_id', 'active'), key=lambda f: (order.get(f['level'], 3), f['name']))
    return ok({'roles': ROLES, 'levels': LEVELS, 'statuses': STATUSES, 'categories': CATEGORIES, 'priorities': PRIORITIES,
               'facilities': facilities, 'settings': public_settings()})


# ---------------- letters ----------------
@api(('GET', 'POST'))
def letters(request, user, body):
    if request.method == 'POST':
        return ok({'id': wf.create_letter(user, body)}, status=201)
    return ok(search_letters(user, request.GET))


@api()
def letters_csv(request, user, body):
    rows = search_letters(user, {**request.GET.dict(), 'limit': 1000})['rows']
    cols = [('ref_no', 'Reference'), ('subject', 'Subject'), ('category', 'Category'), ('priority', 'Priority'), ('from_name', 'From'),
            ('dest_name', 'To'), ('status', 'Status'), ('holder_name', 'Current desk'), ('desk_since', 'On desk since'), ('overdue', 'Overdue'),
            ('created_at', 'Created'), ('dispatched_at', 'Dispatched'), ('received_at', 'Received'), ('closed_at', 'Closed')]

    def cell(v):
        s = '' if v is None else str(v)
        return "'" + s if re.match(r'^[=+\-@]', s) else s   # prevent spreadsheet formula injection

    buf = io.StringIO()
    w = csv.writer(buf, lineterminator='\r\n')
    w.writerow([c[1] for c in cols])
    for r in rows:
        w.writerow([cell(('Yes' if r['overdue'] else '') if k == 'overdue' else r.get(k)) for k, _ in cols])
    resp = HttpResponse('﻿' + buf.getvalue(), content_type='text/csv; charset=utf-8')
    resp['Content-Disposition'] = f'attachment; filename="e-registry-letters-{utcnow().date().isoformat()}.csv"'
    return resp


@api()
def letter_detail(request, user, body, letter_id):
    return ok(letter_full(user, letter_id))


@api(('POST',))
def letter_action(request, user, body, letter_id):
    wf.perform_action(user, letter_id, body)
    return ok(letter_full(user, letter_id))


@api(('POST',))
def letter_attach(request, user, body, letter_id):
    l = Letter.objects.filter(pk=letter_id).first()
    if not l or not wf.can_view(user, l):
        fail(404, 'Letter not found')
    if l.status == 'Closed' or not (l.holder_id == user.id or l.created_by_id == user.id):
        fail(403, 'Only the officer holding the letter, or its author, can attach files')
    mime = str(body.get('mime') or '')
    ext = ALLOWED_MIME.get(mime)
    if not ext:
        fail(400, 'Attach a PDF, image, Word or Excel file')
    try:
        data = base64.b64decode(str(body.get('data') or ''), validate=False)
    except ValueError:
        fail(400, 'The file could not be read')
    if not data:
        fail(400, 'The file is empty')
    if len(data) > MAX_FILE:
        fail(413, 'Files must be 10 MB or smaller')
    filename = re.sub(r'[^\w.\- ()]', '_', Path(str(body.get('filename') or f'attachment{ext}')).name)[:150]
    stored = secrets.token_hex(16) + ext
    cfg.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    (cfg.UPLOAD_DIR / stored).write_bytes(data)
    with transaction.atomic():
        Attachment.objects.create(letter=l, filename=filename, stored_name=stored, mime=mime, size=len(data),
                                  sha256=hashlib.sha256(data).hexdigest(), uploaded_by=user)
        wf.add_event(l, user, 'Attached file', None, filename, l.status)
        Letter.objects.filter(pk=l.pk).update(updated_at=utcnow())
    return ok(letter_full(user, l.id))


@api()
def attachment_download(request, user, body, attachment_id):
    a = Attachment.objects.filter(pk=attachment_id).select_related('letter').first()
    if not a:
        fail(404, 'Not found')
    if not wf.can_view(user, a.letter):
        fail(403, 'You do not have access to this file')
    file = cfg.UPLOAD_DIR / Path(a.stored_name).name
    if not file.exists():
        fail(404, 'File missing on server')
    inline = a.mime == 'application/pdf' or a.mime.startswith('image/')
    resp = FileResponse(open(file, 'rb'), content_type=a.mime, as_attachment=not inline, filename=a.filename)
    resp['Cache-Control'] = 'private, no-store'
    return resp


# ---------------- dashboard ----------------
@api()
def dashboard(request, user, body):
    vis = wf.visible_letters(user)
    od = wf.overdue_q()
    since30 = utcnow() - timedelta(days=30)
    kpi = {
        'my_desk': Letter.objects.filter(holder=user).exclude(status='Closed').count(),
        'my_overdue': Letter.objects.filter(holder=user).filter(od).count(),
        'open': vis.exclude(status='Closed').count(),
        'overdue': vis.filter(od).count(),
        'closed_30': vis.filter(status='Closed', closed_at__gte=since30).count(),
        'created_30': vis.filter(created_at__gte=since30).count(),
    }
    counts = dict(vis.order_by().values_list('status').annotate(n=Count('id')))
    by_status = [{'status': s, 'n': counts.get(s, 0)} for s in STATUSES]
    overdue_count = Count(Case(When(od, then=Value(1)), output_field=IntegerField()))
    by_facility = [{'id': r['holder__facility_id'], 'name': r['holder__facility__name'], 'level': r['holder__facility__level'],
                    'open': r['open_n'], 'overdue': r['overdue_n']}
                   for r in vis.filter(holder__isnull=False).order_by()
                   .values('holder__facility_id', 'holder__facility__name', 'holder__facility__level')
                   .annotate(open_n=Count(Case(When(~Q(status='Closed'), then=Value(1)), output_field=IntegerField())), overdue_n=overdue_count)
                   .order_by('-overdue_n', '-open_n')]
    by_officer = [{'id': r['holder_id'], 'name': r['holder__name'], 'role': r['holder__role'], 'facility': r['holder__facility__name'],
                   'open': r['open_n'], 'overdue': r['overdue_n'], 'oldest': iso(r['oldest']), 'role_label': ROLES.get(r['holder__role'], {}).get('short')}
                  for r in vis.filter(holder__isnull=False).exclude(status='Closed').order_by()
                  .values('holder_id', 'holder__name', 'holder__role', 'holder__facility__name')
                  .annotate(open_n=Count('id'), overdue_n=overdue_count, oldest=Min('desk_since'))
                  .order_by('-overdue_n', '-open_n')[:15]]
    recent = [{'id': e.id, 'at': iso(e.at), 'action': e.action, 'note': e.note, 'letter_id': e.letter_id, 'ref_no': e.letter.ref_no,
               'subject': e.letter.subject, 'actor_name': e.actor.name, 'actor_role': e.actor.role,
               'to_name': e.to_user.name if e.to_user else None, 'actor_role_label': ROLES.get(e.actor.role, {}).get('short')}
              for e in LetterEvent.objects.filter(letter__in=vis.values('id')).select_related('letter', 'actor', 'to_user').order_by('-id')[:12]]
    try:
        sla = int(get_settings().get('sla_days') or 3)
    except ValueError:
        sla = 3
    return ok({'kpi': kpi, 'by_status': by_status, 'by_facility': by_facility, 'by_officer': by_officer, 'recent': recent, 'sla_days': sla})


# ---------------- notifications ----------------
@api()
def notifications(request, user, body):
    rows = [{'id': n.id, 'user_id': n.user_id, 'letter_id': n.letter_id, 'message': n.message, 'is_read': int(n.is_read), 'at': iso(n.at),
             'ref_no': n.letter.ref_no if n.letter else None, 'subject': n.letter.subject if n.letter else None}
            for n in Notification.objects.filter(user=user).select_related('letter').order_by('-id')[:100]]
    return ok({'rows': rows})


@api(('POST',))
def notifications_read(request, user, body):
    qs = Notification.objects.filter(user=user)
    if body.get('id'):
        qs = qs.filter(pk=_int(body['id']))
    qs.update(is_read=True)
    return ok({'ok': True})


# ---------------- administration ----------------
@api(('GET', 'POST'), admin=True)
def admin_users(request, user, body):
    if request.method == 'POST':
        return admin_user_create(request, user, body)
    users = sorted(User.objects.select_related('facility'), key=lambda u: (u.facility.level, u.facility.name, u.name))
    return ok({'rows': [{**public_user(u), 'facility_name': u.facility.name} for u in users]})


def validate_user_body(body, is_new):
    name = str(body.get('name') or '').strip()
    username = str(body.get('username') or '').strip().lower()
    if not name:
        fail(400, 'Name is required')
    if len(name) > 150:
        fail(400, 'Name is too long')
    if is_new and not re.fullmatch(r'[a-z0-9._-]{3,40}', username):
        fail(400, 'Username: 3–40 characters, letters, numbers, dots, dashes')
    role = body.get('role')
    if role not in ROLES:
        fail(400, 'Choose a role')
    f = Facility.objects.filter(pk=_int(body.get('facility_id'))).first()
    if not f:
        fail(400, 'Choose a directorate or hospital')
    lvl = ROLES[role]['level']
    if lvl != 'ANY' and lvl != f.level:
        fail(400, f'The role "{ROLES[role]["label"]}" belongs at a {LEVELS[lvl]}, not a {LEVELS[f.level]}')
    return {'name': name, 'username': username, 'role': role, 'facility': f, 'phone': clean_phone(body.get('phone')),
            'email': clean_email(body.get('email')), 'is_admin': bool(body.get('is_admin'))}


def admin_user_create(request, user, body):
    v = validate_user_body(body, True)
    pw = str(body.get('password') or '')
    if len(pw) < 8:
        fail(400, 'Initial password must be at least 8 characters')
    if User.objects.filter(username__iexact=v['username']).exists():
        fail(400, 'That username is taken')
    User.objects.create_user(v.pop('username'), pw, must_change_password=True, **v)
    return ok({'ok': True}, status=201)


@api(('PUT',), admin=True)
def admin_user_update(request, user, body, user_id):
    v = validate_user_body(body, False)
    active = bool(body.get('active'))
    if user_id == user.id and (not active or not v['is_admin']):
        fail(400, 'You cannot deactivate or remove admin rights from your own account')
    v.pop('username')
    User.objects.filter(pk=user_id).update(is_active=active, **v)
    return ok({'ok': True})


@api(('POST',), admin=True)
def admin_user_password(request, user, body, user_id):
    pw = str(body.get('password') or '')
    if len(pw) < 8:
        fail(400, 'Password must be at least 8 characters')
    target = User.objects.filter(pk=user_id).first()
    if not target:
        fail(404, 'Not found')
    target.set_password(pw)      # also signs the officer out everywhere
    target.must_change_password = True
    target.save(update_fields=['password', 'must_change_password'])
    return ok({'ok': True})


@api(('POST',), admin=True)
def admin_facility_create(request, user, body):
    name = str(body.get('name') or '').strip()
    code = str(body.get('code') or '').strip().upper()
    if not name:
        fail(400, 'Name is required')
    if len(name) > 200:
        fail(400, 'Name is too long')
    if not re.fullmatch(r'[A-Z0-9-]{2,12}', code):
        fail(400, 'Code: 2–12 capital letters or numbers (used in reference numbers)')
    if body.get('level') not in LEVELS:
        fail(400, 'Choose a level')
    if Facility.objects.filter(code=code).exists():
        fail(400, 'That code is already used')
    Facility.objects.create(name=name, code=code, level=body['level'], parent_id=_int(body.get('parent_id')) or None)
    return ok({'ok': True}, status=201)


@api(('PUT',), admin=True)
def admin_facility_update(request, user, body, facility_id):
    name = str(body.get('name') or '').strip()
    if not name:
        fail(400, 'Name is required')
    if len(name) > 200:
        fail(400, 'Name is too long')
    Facility.objects.filter(pk=facility_id).update(name=name, parent_id=_int(body.get('parent_id')) or None, active=bool(body.get('active')))
    return ok({'ok': True})


@api(('PUT',), admin=True)
def admin_settings(request, user, body):
    try:
        sla = round(float(body.get('sla_days')))
    except (TypeError, ValueError):
        sla = 0
    if not 1 <= sla <= 60:
        fail(400, 'Days on one desk must be between 1 and 60')
    set_setting('sla_days', sla)
    set_setting('region_name', (str(body.get('region_name') or '').strip() or 'Regional Health Directorate')[:150])
    set_setting('email_enabled', '1' if body.get('email_enabled') else '0')
    return ok({'ok': True})


# ----- SMS -----
def sms_status():
    c = sms_config()
    s = c['settings']
    counts = dict(Outbox.objects.filter(channel='sms').order_by().values_list('status').annotate(n=Count('id')))
    from django.db import connection
    return {
        'config': {
            'sms_enabled': c['enabled'], 'sms_provider': c['provider'], 'sms_sender_id': c['sender_id'], 'sms_country_code': c['country_code'],
            'sms_app_id_set': bool(c['app_id']), 'sms_app_id_hint': f'…{c["app_id"][-4:]}' if c['app_id'] else '', 'sms_app_secret_set': bool(c['app_secret']),
            'credentials_from_env': c['credentials_from_env'], 'app_url': s.get('app_url'), 'sms_overdue_reminders': s.get('sms_overdue_reminders') == '1',
            'sms_overdue_hour': _int(s.get('sms_overdue_hour')) or 8,
        },
        'cron_path': f'api/cron?key={s.get("cron_key", "")}',
        'database': connection.vendor,
        'problems': config_problems(c),
        'provider_error': sms_worker.last_provider_error,
        'paused_until': sms_worker.paused_until.isoformat() if sms_worker.paused_until and sms_worker.paused_until > utcnow() else None,
        'counts': counts,
        'sent_24h': Outbox.objects.filter(channel='sms', status='sent', sent_at__gt=utcnow() - timedelta(days=1)).count(),
        'users_without_phone': User.objects.filter(is_active=True).exclude(role='SYSADMIN').filter(Q(phone__isnull=True) | Q(phone='')).count(),
    }


@api(('GET', 'PUT'), admin=True)
def admin_sms(request, user, body):
    if request.method == 'GET':
        return ok(sms_status())
    if body.get('sms_provider') not in ('test', 'letsfish'):
        fail(400, 'Choose an SMS provider')
    sender = str(body.get('sms_sender_id') or '').strip()
    if len(sender) > 11:
        fail(400, 'Sender ID must be 11 characters or fewer')
    if sender and not re.fullmatch(r'[A-Za-z0-9 .\-]+', sender):
        fail(400, 'Sender ID may contain only letters, numbers, spaces, dots and dashes')
    cc = re.sub(r'\D', '', str(body.get('sms_country_code') or '233'))
    if not re.fullmatch(r'\d{1,4}', cc):
        fail(400, 'Country code must be 1–4 digits, e.g. 233')
    try:
        hour = round(float(body.get('sms_overdue_hour')))
    except (TypeError, ValueError):
        hour = -1
    if not 0 <= hour <= 23:
        fail(400, 'Reminder hour must be between 0 and 23')
    app_url = str(body.get('app_url') or '').strip().rstrip('/')
    if app_url and (not re.fullmatch(r'https?://\S+', app_url) or len(app_url) > 200):
        fail(400, 'System address must start with http:// or https://')
    set_setting('sms_enabled', '1' if body.get('sms_enabled') else '0')
    set_setting('sms_provider', body['sms_provider'])
    set_setting('sms_sender_id', sender)
    set_setting('sms_country_code', cc)
    set_setting('sms_overdue_reminders', '1' if body.get('sms_overdue_reminders') else '0')
    set_setting('sms_overdue_hour', hour)
    set_setting('app_url', app_url)
    # Blank credential fields keep the saved value, so the secret never has to be shown again.
    if str(body.get('sms_app_id') or '').strip():
        set_setting('sms_app_id', str(body['sms_app_id']).strip()[:200])
    if str(body.get('sms_app_secret') or '').strip():
        set_setting('sms_app_secret', str(body['sms_app_secret']).strip()[:500])
    if body.get('clear_credentials'):
        set_setting('sms_app_id', '')
        set_setting('sms_app_secret', '')
    sms_worker.paused_until = None
    sms_worker.last_provider_error = None
    return ok(sms_status())


@api(('POST',), admin=True)
def admin_sms_test(request, user, body):
    r = sms_worker.send_now(body.get('phone'), f'Test message from RHD e-Registry, sent by {user.name}. SMS alerts are working.')
    if not r.get('ok'):
        fail(400, r.get('error') or 'The test message was not sent')
    return ok({'ok': True, 'to': r['to'], 'reference': r.get('ref'), 'status': r.get('status')})


@api(('POST',), admin=True)
def admin_outbox_retry(request, user, body, outbox_id):
    n = Outbox.objects.filter(pk=outbox_id, status__in=['failed', 'retry']).update(status='pending', attempts=0, next_attempt_at=None)
    if not n:
        fail(400, 'Only failed or waiting messages can be retried')
    sms_worker.paused_until = None
    return ok({'ok': True})


@api(admin=True)
def admin_outbox(request, user, body):
    qs = Outbox.objects.select_related('user', 'letter').order_by('-id')
    if request.GET.get('channel'):
        qs = qs.filter(channel=request.GET['channel'])
    if request.GET.get('status'):
        qs = qs.filter(status=request.GET['status'])
    rows = [{
        'id': o.id, 'channel': o.channel, 'recipient': o.recipient, 'message': o.message, 'created_at': iso(o.created_at),
        'sent_at': iso(o.sent_at), 'status': o.status, 'attempts': o.attempts, 'next_attempt_at': iso(o.next_attempt_at),
        'last_error': o.last_error, 'provider': o.provider, 'provider_ref': o.provider_ref, 'delivery_status': o.delivery_status,
        'delivery_checked_at': iso(o.delivery_checked_at), 'user_id': o.user_id, 'letter_id': o.letter_id,
        'user_name': o.user.name if o.user else None, 'ref_no': o.letter.ref_no if o.letter else None,
    } for o in qs[:200]]
    return ok({'rows': rows})


@csrf_exempt
def api_not_found(request, rest=''):
    if rest.startswith('setup/'):
        return JsonResponse({'error': 'The e-Registry is already set up.'}, status=409)
    return JsonResponse({'error': 'Not found'}, status=404)


urlpatterns = [
    path('api/login', login_view),
    path('api/logout', logout_view),
    path('api/public', public_info),
    path('api/health', health),
    path('api/cron', cron),
    path('api/me', me),
    path('api/me/password', me_password),
    path('api/me/profile', me_profile),
    path('api/meta', meta),
    path('api/letters', letters),
    path('api/letters.csv', letters_csv),
    path('api/letters/<int:letter_id>', letter_detail),
    path('api/letters/<int:letter_id>/actions', letter_action),
    path('api/letters/<int:letter_id>/attachments', letter_attach),
    path('api/attachments/<int:attachment_id>', attachment_download),
    path('api/dashboard', dashboard),
    path('api/notifications', notifications),
    path('api/notifications/read', notifications_read),
    path('api/admin/users', admin_users),
    path('api/admin/users/<int:user_id>', admin_user_update),
    path('api/admin/users/<int:user_id>/password', admin_user_password),
    path('api/admin/facilities', admin_facility_create),
    path('api/admin/facilities/<int:facility_id>', admin_facility_update),
    path('api/admin/settings', admin_settings),
    path('api/admin/sms', admin_sms),
    path('api/admin/sms/test', admin_sms_test),
    path('api/admin/outbox', admin_outbox),
    path('api/admin/outbox/<int:outbox_id>/retry', admin_outbox_retry),
]

urlpatterns.append(re_path(r'^api/(?P<rest>.*)$', api_not_found))
