"""Letter workflow: who sees a letter, what they can do with it, and the standard route.

Downward example: RDHS → RDHS Secretary → Manager, Registry Unit → (dispatch) → DHD HR/Admin Manager → DDHS
Upward:          DDHS → Secretary → (dispatch) → Manager, Registry Unit → RDHS Secretary → RDHS → Deputy Director
"""
from datetime import timedelta

from django.db import transaction
from django.db.models import Exists, F, OuterRef, Q

from .models import Facility, Letter, LetterEvent, Notification, Outbox, RefCounter, User
from .roles import CATEGORIES, HEAD_ROLE, PRIORITIES, RECEIVER_ROLE, ROLES, SECRETARY_ROLE, role_of
from .sms import format_sms, normalize_phone
from .store import get_settings, utcnow


class HttpError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def fail(status, message):
    raise HttpError(status, message)


# ---------- lookups ----------
def user_by_role(facility_id, role):
    return User.objects.filter(facility_id=facility_id, role=role, is_active=True).order_by('id').first()


def display_name(u):
    if not u:
        return ''
    r = ROLES.get(u.role)
    return f'{u.name} ({r["short"]})' if r and r['short'] not in u.name else u.name


def label(letter):
    return letter.ref_no or f'"{letter.subject}"'


# ---------- visibility ----------
def scope_q(u):
    """Q filter limiting letters to those the user may see (None means all)."""
    r = role_of(u)
    if u.is_admin or r.get('scope') == 'all':
        return None
    involved = (Q(created_by=u) | Q(holder=u)
                | Q(Exists(LetterEvent.objects.filter(letter=OuterRef('pk')).filter(Q(actor=u) | Q(to_user=u)))))
    if r.get('scope') == 'facility':
        return Q(origin_facility_id=u.facility_id) | Q(dest_facility_id=u.facility_id) | involved
    return involved


def visible_letters(u, qs=None):
    qs = Letter.objects.all() if qs is None else qs
    q = scope_q(u)
    return qs if q is None else qs.filter(q)


def can_view(u, letter):
    return visible_letters(u).filter(pk=letter.pk).exists()


# ---------- overdue ----------
def overdue_cutoff():
    try:
        days = max(1, int(get_settings().get('sla_days') or 3))
    except ValueError:
        days = 3
    return utcnow() - timedelta(days=days)


def overdue_q():
    return ~Q(status='Closed') & (Q(desk_since__lt=overdue_cutoff()) | Q(due_date__isnull=False, due_date__lt=utcnow().date()))


# ---------- reference numbers ----------
def next_ref(facility, incoming):
    yy = str(utcnow().year)[-2:]
    prefix = f'{facility.code}/IN/{yy}' if incoming else f'{facility.code}/{yy}'
    RefCounter.objects.get_or_create(prefix=prefix, defaults={'seq': 0})
    RefCounter.objects.select_for_update().filter(prefix=prefix).update(seq=F('seq') + 1)
    seq = RefCounter.objects.get(prefix=prefix).seq
    return f'{prefix}/{seq:04d}'


# ---------- notifications ----------
def notify(to_user_id, letter, message, sms=False):
    """In-app notification; plus an SMS for events that need the officer to act (opts sms=True)."""
    if not to_user_id:
        return
    Notification.objects.create(user_id=to_user_id, letter=letter, message=message)
    settings = get_settings()
    u = User.objects.filter(pk=to_user_id).first()
    if not u or not u.is_active:
        return
    letter_id = letter.pk if letter else None
    if sms and settings.get('sms_enabled') == '1':
        phone = normalize_phone(u.phone, settings.get('sms_country_code') or '233')
        if phone:
            Outbox.objects.create(channel='sms', recipient=phone, message=format_sms(settings, message, letter_id),
                                  status='pending', user=u, letter=letter)
    if settings.get('email_enabled') == '1' and u.email:
        Outbox.objects.create(channel='email', recipient=u.email, message=message, status='pending', user=u, letter=letter)


def add_event(letter, actor, action, to_user=None, note=None, status=None):
    LetterEvent.objects.create(letter=letter, actor=actor, action=action, to_user=to_user, note=note or None,
                               status=status or letter.status)


# ---------- route logic ----------
def at_origin(letter):
    return not letter.dispatched_at


def receiver_for(fac):
    return user_by_role(fac.id, RECEIVER_ROLE[fac.level]) or user_by_role(fac.id, HEAD_ROLE[fac.level])


def can_dispatch(u, letter):
    if not at_origin(letter):
        return False
    r = role_of(u)
    # At the region the Registry Unit dispatches; at a district or hospital the secretary
    # (or the HR/Admin Manager) dispatches straight to the regional Registry Unit.
    return bool(r.get('registry') or (u.facility.level != 'REGION' and (r.get('secretary') or r.get('receiver'))))


def available_actions(u, letter):
    if letter.status == 'Closed' or letter.holder_id != u.id:
        return []
    r = role_of(u)
    if letter.status == 'Dispatched':
        return ['receive']
    acts = ['forward']
    if at_origin(letter):
        if can_dispatch(u, letter):
            acts.append('dispatch')
    else:
        if r.get('minute'):
            acts.append('minute')
        if letter.status in ('Minuted', 'Received'):
            acts.append('actioned')
        if r.get('head') or r.get('minute') or r.get('registry') or letter.status == 'Actioned':
            acts.append('close')
        acts.append('reply')
    return acts


def suggestion(u, letter):
    """The next desk on the standard route, so the officer only has to confirm."""
    acts = available_actions(u, letter)
    if not acts:
        return None
    r = role_of(u)
    fac = u.facility

    def pick(role, why):
        s = user_by_role(fac.id, role)
        return {'action': 'forward', 'to_user_id': s.id, 'to_name': display_name(s), 'why': why} if s and s.id != u.id else None

    if letter.status == 'Dispatched':
        nxt = None
        if fac.level == 'REGION' and r.get('registry'):
            nxt = user_by_role(fac.id, SECRETARY_ROLE['REGION'])
        elif r.get('receiver') or r.get('registry'):
            nxt = user_by_role(fac.id, HEAD_ROLE[fac.level])
        return {'action': 'receive', 'to_user_id': nxt.id if nxt else None, 'to_name': display_name(nxt) if nxt else None,
                'why': 'Acknowledge receipt' + (f' and send to the {ROLES[nxt.role]["short"]}' if nxt else '')}
    if at_origin(letter):
        if 'dispatch' in acts:
            dest = letter.dest_facility
            to = receiver_for(dest)
            return {'action': 'dispatch', 'to_user_id': to.id if to else None, 'to_name': display_name(to) if to else None,
                    'why': f'Dispatch to {dest.name}'}
        if r.get('secretary') and fac.level == 'REGION':
            return pick('MRU', 'Log the letter and send it to the Registry Unit')
        if not r.get('secretary'):
            return pick(SECRETARY_ROLE[fac.level], 'Send to the secretary to log and dispatch')
        return None
    # At destination
    head_short = ROLES[HEAD_ROLE[fac.level]]['short']
    if r.get('registry'):
        return pick(SECRETARY_ROLE['REGION'], 'Send to the RDHS Secretary')
    if r.get('secretary'):
        return pick(HEAD_ROLE[fac.level], f'Present to the {head_short}')
    if r.get('receiver'):
        return pick(HEAD_ROLE[fac.level], f'Send to the {head_short}')
    if r.get('minute') and letter.status == 'Received':
        return {'action': 'minute', 'to_user_id': None, 'to_name': None, 'why': 'Minute to an officer for action'}
    if letter.status == 'Minuted':
        return {'action': 'actioned', 'why': 'Record the action taken'}
    if letter.status == 'Actioned':
        return {'action': 'close', 'why': 'Close the letter'}
    return None


# ---------- create ----------
def _date_or_fail(value, what):
    if not value:
        return None
    from datetime import date as _date
    s = str(value)[:10]
    try:
        return _date.fromisoformat(s)
    except ValueError:
        fail(400, f'Invalid {what}')


def create_letter(u, body):
    subject = str(body.get('subject') or '').strip()
    if not subject:
        fail(400, 'Subject is required')
    if len(subject) > 300:
        fail(400, 'Subject is too long')
    category = body.get('category') if body.get('category') in CATEGORIES else 'Letter'
    priority = body.get('priority') if body.get('priority') in PRIORITIES else 'Normal'
    due = _date_or_fail(body.get('due_date'), 'due date')
    letter_date = _date_or_fail(body.get('letter_date'), 'letter date')
    summary = str(body['summary'])[:20000] if body.get('summary') else None
    r = role_of(u)
    if r.get('system') and not u.is_admin:
        fail(403, 'Not allowed')
    now = utcnow()

    with transaction.atomic():
        if body.get('incoming'):
            # A paper letter from outside the system, recorded on arrival.
            if not (r.get('secretary') or r.get('registry') or r.get('receiver') or u.is_admin):
                fail(403, 'Only secretaries, registry and HR/Admin staff can record incoming letters')
            sender = str(body.get('external_sender') or '').strip()[:255]
            if not sender:
                fail(400, 'Sender is required for an incoming letter')
            ext_ref = str(body['external_ref']).strip()[:120] if body.get('external_ref') else None
            fac = u.facility
            ref = next_ref(fac, True)
            letter = Letter.objects.create(
                ref_no=ref, subject=subject, summary=summary, category=category, priority=priority, external_sender=sender,
                external_ref=ext_ref, letter_date=letter_date, dest_facility=fac, created_by=u, holder=u, desk_since=now,
                status='Received', due_date=due, created_at=now, dispatched_at=now, received_at=now, updated_at=now)
            add_event(letter, u, 'Recorded incoming', None,
                      f'From {sender}{f" (their ref {ext_ref})" if ext_ref else ""}. Logged as {ref}', 'Received')
        else:
            dest = Facility.objects.filter(pk=_int(body.get('dest_facility_id'))).first()
            if not dest or not dest.active:
                fail(400, 'Choose a destination')
            if dest.id == u.facility_id:
                fail(400, 'Destination must be a different directorate or hospital')
            parent = None
            if body.get('parent_id'):
                parent = Letter.objects.filter(pk=_int(body.get('parent_id'))).first()
                if not parent or not can_view(u, parent):
                    fail(400, 'Original letter not found')
            letter = Letter.objects.create(
                subject=subject, summary=summary, category=category, priority=priority, origin_facility=u.facility,
                dest_facility=dest, created_by=u, holder=u, desk_since=now, status='Drafted', due_date=due, parent=parent,
                created_at=now, updated_at=now)
            add_event(letter, u, 'Created', None, f'To {dest.name}', 'Drafted')
            if parent:
                add_event(parent, u, 'Reply started', None, f'Reply drafted (letter #{letter.id})', parent.status)
                if parent.created_by_id != u.id:
                    notify(parent.created_by_id, parent, f'{display_name(u)} started a reply to {label(parent)}')
        return letter.id


# ---------- actions ----------
def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def move_to(letter, to_user, **fields):
    now = utcnow()
    values = {'holder': to_user, 'desk_since': now, 'updated_at': now, **fields}
    for k, v in values.items():
        setattr(letter, k, v)
    letter.save(update_fields=[('holder' if k == 'holder' else k) for k in values])
    return letter


def recipient_in(user_id, facility_id, actor):
    to = User.objects.filter(pk=_int(user_id)).first()
    if not to or not to.is_active or ROLES.get(to.role, {}).get('system'):
        fail(400, 'Choose a valid recipient')
    if facility_id is not None and to.facility_id != facility_id:
        fail(400, 'Recipient must be at the same directorate or hospital')
    if to.id == actor.id:
        fail(400, 'You cannot send a letter to yourself')
    return to


def ensure_ref(letter, u, force):
    """Secretaries and the Registry Unit log the letter (assign a reference) the first time they handle it."""
    if letter.ref_no:
        return letter
    r = role_of(u)
    if not force and not (r.get('secretary') or r.get('registry')):
        return letter
    ref = next_ref(letter.origin_facility or u.facility, False)
    letter.ref_no = ref
    letter.save(update_fields=['ref_no'])
    add_event(letter, u, 'Logged', None, f'Reference number {ref} assigned', letter.status)
    return letter


def perform_action(u, letter_id, body):
    with transaction.atomic():
        letter = Letter.objects.select_for_update().filter(pk=letter_id).first()
        if not letter:
            fail(404, 'Letter not found')
        kind = body.get('type')
        acts = available_actions(u, letter)
        if kind not in acts or kind == 'reply':
            fail(403, 'That action is not available to you on this letter')
        note = str(body['note']).strip()[:4000] if body.get('note') else None
        note = note or None

        if kind == 'forward':
            to = recipient_in(body.get('to_user_id'), u.facility_id, u)
            ensure_ref(letter, u, False)
            move_to(letter, to)
            add_event(letter, u, 'Forwarded', to, note)
            notify(to.id, letter, f'{display_name(u)} sent {label(letter)} to your desk', sms=True)
        elif kind == 'dispatch':
            dest = letter.dest_facility
            to = recipient_in(body.get('to_user_id'), dest.id, u) if body.get('to_user_id') else receiver_for(dest)
            if not to:
                fail(400, f'No active officer at {dest.name} to receive this letter. Ask the administrator to add one.')
            ensure_ref(letter, u, True)
            move_to(letter, to, status='Dispatched', dispatched_at=utcnow())
            add_event(letter, u, 'Dispatched', to, note or f'To {dest.name}', 'Dispatched')
            notify(to.id, letter, f'Letter {label(letter)} dispatched to you from {u.facility.name}', sms=True)
        elif kind == 'receive':
            move_to(letter, u, status='Received', received_at=utcnow())
            add_event(letter, u, 'Received', None, note, 'Received')
            if letter.created_by_id != u.id:
                notify(letter.created_by_id, letter, f'{label(letter)} was received by {display_name(u)}')
            if body.get('to_user_id'):
                to = recipient_in(body.get('to_user_id'), u.facility_id, u)
                move_to(letter, to)
                add_event(letter, u, 'Forwarded', to, None)
                notify(to.id, letter, f'{display_name(u)} sent {label(letter)} to your desk', sms=True)
        elif kind == 'minute':
            if not note:
                fail(400, 'Write the minute (instruction) for the officer')
            to = recipient_in(body.get('to_user_id'), u.facility_id, u)
            move_to(letter, to, status='Minuted')
            add_event(letter, u, 'Minuted', to, note, 'Minuted')
            notify(to.id, letter, f'{display_name(u)} minuted {label(letter)} to you: {note[:120]}', sms=True)
        elif kind == 'actioned':
            if not note:
                fail(400, 'Describe the action taken')
            move_to(letter, u, status='Actioned')
            add_event(letter, u, 'Actioned', None, note, 'Actioned')
            minuter = LetterEvent.objects.filter(letter=letter, action='Minuted').order_by('-id').first()
            if minuter and minuter.actor_id != u.id:
                notify(minuter.actor_id, letter, f'{display_name(u)} actioned {label(letter)}')
        elif kind == 'close':
            move_to(letter, None, status='Closed', closed_at=utcnow(), desk_since=None)
            add_event(letter, u, 'Closed', None, note, 'Closed')
            if letter.created_by_id != u.id:
                notify(letter.created_by_id, letter, f'{label(letter)} was closed by {display_name(u)}')
        else:
            fail(400, 'Unknown action')
        return letter.id
