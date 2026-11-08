import json
from datetime import datetime, timedelta, timezone as dt_timezone

from django.test import TestCase

from registry import sms as SMS
from registry import workflow as wf
from registry.models import Notification, Outbox, User
from registry.reminders import run_overdue_reminders
from registry.store import public_settings, set_setting, utcnow

from .fixtures import F, U, seed_sample_data


def mock_fetch(reply):
    """Stand-in for the LetsFish API: records requests and answers as told."""
    calls = []

    def fetch(method, url, headers=None, body=None, timeout=20):
        calls.append({'method': method, 'url': url, 'headers': headers or {}, 'body': json.loads(body) if body else None})
        status, payload, hdrs = reply(method, url, body)
        return status, hdrs or {}, json.dumps(payload)
    fetch.calls = calls
    return fetch


def accepted(method, url, body):
    if method == 'GET':
        return 200, {'success': True, 'data': {'status': 'delivered', 'reference': 'sms_1'}}, None
    to = json.loads(body)['recipients'][0]
    return 202, {'success': True, 'data': [{'recipient': to, 'reference': f'sms_{to[-4:]}', 'status': 'queued'}]}, None


def configure(**extra):
    values = {'sms_enabled': '1', 'sms_provider': 'letsfish', 'sms_app_id': 'APPID', 'sms_app_secret': 'SECRET', 'sms_sender_id': 'GHS-RHD', 'app_url': ''}
    values.update(extra)
    for k, v in values.items():
        set_setting(k, v)


def queue(recipient):
    return Outbox.objects.create(channel='sms', recipient=recipient, message='x', status='pending')


def outbox():
    return list(Outbox.objects.filter(channel='sms').order_by('id'))


class FormattingTests(TestCase):
    def test_phone_numbers(self):
        n = SMS.normalize_phone
        self.assertEqual(n('0244 123 456'), '233244123456')
        self.assertEqual(n('+233 24-412-3456'), '233244123456')
        self.assertEqual(n('00233244123456'), '233244123456')
        self.assertEqual(n('233244123456'), '233244123456')
        self.assertEqual(n('244123456'), '233244123456')
        self.assertIsNone(n('02441234'))
        self.assertIsNone(n('hello'))
        self.assertIsNone(n(''))

    def test_gsm_text_and_link(self):
        m = SMS.format_sms({'app_url': 'https://reg.example.org/'}, 'Communiqué “urgent” → DDHS', 7)
        self.assertEqual(m, 'e-Registry: Communique "urgent" -> DDHS https://reg.example.org/#/letter/7')
        long = SMS.format_sms({}, 'x' * 1000, None)
        self.assertTrue(len(long) <= 306 and long.endswith('...'))


class SmsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed_sample_data()

    def setUp(self):
        Outbox.objects.all().delete()

    def test_queued_only_for_desk_events_and_valid_numbers(self):
        configure()
        User.objects.filter(username='rdhs.sec').update(phone='0244000001')
        User.objects.filter(username='registry').update(phone='not a number')
        lid = wf.create_letter(U('rdhs'), {'subject': 'SMS check', 'dest_facility_id': F('DHDA').id})
        wf.perform_action(U('rdhs'), lid, {'type': 'forward', 'to_user_id': U('rdhs.sec').id})
        wf.perform_action(U('rdhs.sec'), lid, {'type': 'forward', 'to_user_id': U('registry').id})
        rows = outbox()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0].recipient, rows[0].status), ('233244000001', 'pending'))
        self.assertRegex(rows[0].message, r'^e-Registry: .*sent "SMS check" to your desk$')
        self.assertTrue(Notification.objects.filter(user=U('registry')).exists(), 'in-app alerts still arrive')

    def test_nothing_queued_when_off(self):
        configure(sms_enabled='0')
        lid = wf.create_letter(U('rdhs'), {'subject': 'Off', 'dest_facility_id': F('DHDA').id})
        wf.perform_action(U('rdhs'), lid, {'type': 'forward', 'to_user_id': U('rdhs.sec').id})
        self.assertEqual(len(outbox()), 0)

    def test_sends_documented_request(self):
        configure()
        User.objects.filter(username='rdhs.sec').update(phone='0244000001')
        lid = wf.create_letter(U('rdhs'), {'subject': 'Send me', 'dest_facility_id': F('DHDA').id})
        wf.perform_action(U('rdhs'), lid, {'type': 'forward', 'to_user_id': U('rdhs.sec').id})
        f = mock_fetch(accepted)
        w = SMS.SmsWorker(fetch=f)
        self.assertEqual(w.send_due(), 1)
        c = f.calls[0]
        self.assertEqual((c['method'], c['url']), ('POST', 'https://api.letsfish.africa/v1/sms'))
        self.assertEqual(c['headers']['Authorization'], 'Bearer APPID.SECRET')
        self.assertEqual(sorted(c['body']), ['message', 'recipients', 'sender_id'])
        self.assertEqual((c['body']['sender_id'], c['body']['recipients']), ('GHS-RHD', ['233244000001']))
        row = outbox()[0]
        self.assertEqual((row.status, row.provider_ref, row.delivery_status), ('sent', 'sms_0001', 'queued'))
        w.poll_delivery()
        self.assertEqual(len(f.calls), 1, 'delivery is not checked in the first 10 minutes')
        Outbox.objects.update(sent_at=utcnow() - timedelta(minutes=15))
        w.poll_delivery()
        self.assertEqual(f.calls[1]['url'], 'https://api.letsfish.africa/v1/sms/sms_0001')
        self.assertEqual(outbox()[0].delivery_status, 'delivered')

    def test_two_workers_never_double_send(self):
        configure()
        for i in range(5):
            queue(f'23324400003{i}')
        f = mock_fetch(accepted)
        SMS.SmsWorker(fetch=f).send_due()
        SMS.SmsWorker(fetch=f).send_due()
        sent_to = [c['body']['recipients'][0] for c in f.calls]
        self.assertEqual(len(sent_to), 5)
        self.assertEqual(len(set(sent_to)), 5)

    def test_retry_with_backoff_then_fail(self):
        configure()
        queue('233244000009')
        f = mock_fetch(lambda m, u, b: (500, {'success': False, 'error': {'status': 500, 'title': 'Internal error'}}, None))
        w = SMS.SmsWorker(fetch=f)
        for i in range(1, SMS.MAX_ATTEMPTS + 1):
            w.send_due()
            r = outbox()[0]
            self.assertEqual(r.attempts, i)
            self.assertIn('LetsFish 500: Internal error', r.last_error)
            self.assertEqual(r.status, 'retry' if i < SMS.MAX_ATTEMPTS else 'failed')
            self.assertGreater(r.next_attempt_at, utcnow())
            Outbox.objects.update(next_attempt_at=None)
        self.assertEqual(len(f.calls), SMS.MAX_ATTEMPTS)

    def test_rejected_and_account_errors(self):
        configure()
        queue('233244000010')
        w = SMS.SmsWorker(fetch=mock_fetch(lambda m, u, b: (400, {'success': False, 'error': {'status': 400, 'title': 'Invalid request value', 'detail': 'bad recipient'}}, None)))
        w.send_due()
        self.assertEqual(outbox()[0].status, 'failed')
        self.assertIn('bad recipient', outbox()[0].last_error)
        Outbox.objects.all().delete()
        queue('233244000011')
        queue('233244000012')
        f = mock_fetch(lambda m, u, b: (402, {'success': False, 'error': {'status': 402, 'title': 'Insufficient balance'}}, None))
        w = SMS.SmsWorker(fetch=f)
        w.send_due()
        self.assertEqual(len(f.calls), 1, 'stops after the first account-wide error')
        self.assertEqual((outbox()[0].status, outbox()[0].attempts), ('retry', 0))
        self.assertIn('Insufficient balance', w.last_provider_error['error'])
        w.send_due()
        self.assertEqual(len(f.calls), 1, 'queue is paused')

    def test_incomplete_settings(self):
        configure(sms_sender_id='')
        queue('233244000013')
        f = mock_fetch(accepted)
        SMS.SmsWorker(fetch=f).send_due()
        self.assertEqual(len(f.calls), 0)
        self.assertEqual(SMS.config_problems(SMS.sms_config()), ['Sender ID is not set'])

    def test_test_mode(self):
        configure(sms_provider='test')
        f = mock_fetch(accepted)
        r = SMS.SmsWorker(fetch=f).send_now('0244000014', 'hello')
        self.assertTrue(r['ok'])
        self.assertEqual(len(f.calls), 0)
        self.assertEqual(outbox()[0].provider, 'test')

    def test_overdue_reminder(self):
        configure(sms_overdue_reminders='1', sms_overdue_hour='8', last_overdue_run='')
        User.objects.filter(username='dda').update(phone='0244000020')
        for subj in ('Old one', 'Old two'):
            lid = wf.create_letter(U('dda'), {'subject': subj, 'dest_facility_id': F('DHDA').id})
            from registry.models import Letter
            Letter.objects.filter(pk=lid).update(desk_since=utcnow() - timedelta(days=10))
        acc = dt_timezone.utc   # Africa/Accra is UTC+0
        self.assertEqual(run_overdue_reminders(datetime(2026, 11, 7, 9, tzinfo=acc)), 0, 'Saturday')
        self.assertEqual(run_overdue_reminders(datetime(2026, 11, 9, 7, tzinfo=acc)), 0, 'before the hour')
        self.assertGreaterEqual(run_overdue_reminders(datetime(2026, 11, 9, 9, tzinfo=acc)), 1)
        rows = [r for r in outbox() if r.recipient == '233244000020']
        self.assertEqual(len(rows), 1)
        self.assertIn('letters are overdue on your desk', rows[0].message)
        self.assertEqual(run_overdue_reminders(datetime(2026, 11, 9, 15, tzinfo=acc)), 0, 'once a day')

    def test_secrets_not_public(self):
        configure()
        pub = public_settings()
        for k in ('sms_app_secret', 'sms_app_id', 'cron_key'):
            self.assertNotIn(k, pub)
        self.assertEqual(pub['sms_sender_id'], 'GHS-RHD')
