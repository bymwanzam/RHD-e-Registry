"""Stored settings, first-run set-up, and small helpers shared by the app."""
import os
import secrets
from datetime import date, datetime, timezone as dt_timezone

from django.db import transaction

from .models import Facility, Setting, User

DEFAULT_SETTINGS = {
    'sla_days': '3',          # days a letter may sit on one desk before it is flagged overdue
    'region_name': 'Regional Health Directorate',
    'sms_enabled': '0',
    'email_enabled': '0',
    'sms_provider': 'test',   # 'test' (log only, nothing sent) or 'letsfish'
    'sms_sender_id': '',
    'sms_app_id': '',         # LetsFish App ID (env LETSFISH_APP_ID overrides)
    'sms_app_secret': '',     # LetsFish App Secret (env LETSFISH_APP_SECRET overrides); never sent to browsers
    'sms_country_code': '233',
    'app_url': '',            # first value comes from APP_URL; editable in Administration
    'sms_overdue_reminders': '1',
    'sms_overdue_hour': '8',
    'last_overdue_run': '',
}
SECRET_SETTINGS = {'sms_app_secret', 'sms_app_id', 'cron_key'}


def ensure_default_settings():
    existing = set(Setting.objects.values_list('key', flat=True))
    for k, v in DEFAULT_SETTINGS.items():
        if k not in existing:
            if k == 'app_url':
                v = (os.environ.get('APP_URL') or '').rstrip('/')
            Setting.objects.get_or_create(key=k, defaults={'value': v})
    if 'cron_key' not in existing:
        Setting.objects.get_or_create(key='cron_key', defaults={'value': secrets.token_hex(18)})


def get_settings():
    return dict(Setting.objects.values_list('key', 'value'))


def public_settings():
    return {k: v for k, v in get_settings().items() if k not in SECRET_SETTINGS}


def set_setting(key, value):
    Setting.objects.update_or_create(key=key, defaults={'value': str(value)})


def seed_first_run():
    """On an empty database: the Regional Health Directorate and the administrator "admin" / "admin1234",
    who must choose a new password at first sign-in. Returns True if it created them."""
    with transaction.atomic():
        ensure_default_settings()
        if User.objects.exists():
            return False
        rhd = Facility.objects.filter(level='REGION').order_by('id').first() or \
            Facility.objects.create(name='Regional Health Directorate', code='RHD', level='REGION')
        User.objects.create_user('admin', 'admin1234', name='System Administrator', role='SYSADMIN', facility=rhd,
                                 is_admin=True, must_change_password=True)
        return True


# ---------------- JSON helpers ----------------
def iso(value):
    """ISO-8601 text for dates and times (UTC with Z), as the browser app expects."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(dt_timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    if isinstance(value, date):
        return value.isoformat()
    return value


def utcnow():
    return datetime.now(dt_timezone.utc)
