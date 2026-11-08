"""Weekday overdue reminders: once a day, after the configured hour, every officer holding overdue
letters gets one in-app notification and (if SMS is on) one SMS summarising them."""
from collections import OrderedDict

from django.utils import timezone

from . import workflow as wf
from .models import Letter
from .store import get_settings, set_setting


def run_overdue_reminders(at=None):
    at = timezone.localtime(at) if at is not None else timezone.localtime()
    s = get_settings()
    if s.get('sms_overdue_reminders') != '1':
        return 0
    if at.weekday() >= 5:                     # Saturday, Sunday
        return 0
    try:
        hour = int(s.get('sms_overdue_hour') or 8)
    except ValueError:
        hour = 8
    if at.hour < hour:
        return 0
    today = at.date().isoformat()
    if s.get('last_overdue_run') == today:
        return 0
    set_setting('last_overdue_run', today)

    by_holder = OrderedDict()
    for letter in Letter.objects.filter(holder__isnull=False).filter(wf.overdue_q()).order_by('holder_id', 'desk_since'):
        by_holder.setdefault(letter.holder_id, []).append(letter)
    for holder_id, letters in by_holder.items():
        refs = ', '.join(l.ref_no or f'"{l.subject[:30]}"' for l in letters[:4])
        more = f' and {len(letters) - 4} more' if len(letters) > 4 else ''
        msg = (f'Reminder: {refs} is overdue on your desk. Please act on it.' if len(letters) == 1
               else f'Reminder: {len(letters)} letters are overdue on your desk ({refs}{more}). Please act on them.')
        wf.notify(holder_id, letters[0] if len(letters) == 1 else None, msg, sms=True)
    return len(by_holder)
