"""The HTTP API, as the browser app calls it."""
import base64
import json

from django.test import TestCase

from registry import setup_state as st
from registry.models import User

from .fixtures import PASSWORD, F, U, seed_sample_data


class ApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed_sample_data()

    def login(self, username, password=PASSWORD):
        r = self.client.post('/api/login', json.dumps({'username': username, 'password': password}), content_type='application/json')
        return r

    def get(self, url):
        return self.client.get(url)

    def post(self, url, body, method='post'):
        return getattr(self.client, method)(url, json.dumps(body), content_type='application/json')

    def test_login_me_logout(self):
        self.assertEqual(self.get('/api/me').status_code, 401)
        self.assertEqual(self.login('rdhs', 'wrong').status_code, 401)
        r = self.login('RDHS')      # usernames are not case-sensitive
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['user']['role_short'], 'RDHS')
        me = self.get('/api/me').json()
        self.assertEqual(set(me), {'user', 'unread', 'desk'})
        self.assertEqual(self.post('/api/logout', {}).status_code, 200)
        self.assertEqual(self.get('/api/me').status_code, 401)

    def test_first_admin_must_change_password(self):
        self.assertEqual(self.login('admin', 'admin1234').json()['user']['must_change_password'], True)
        self.assertEqual(self.post('/api/me/password', {'current': 'admin1234', 'password': 'short'}).status_code, 400)
        self.assertEqual(self.post('/api/me/password', {'current': 'admin1234', 'password': 'a-new-password'}).status_code, 200)
        self.assertFalse(User.objects.get(username='admin').must_change_password)
        self.assertEqual(self.get('/api/me').status_code, 200, 'still signed in after changing the password')

    def test_json_required(self):
        self.login('rdhs')
        r = self.client.post('/api/letters', {'subject': 'x'})   # form post, as a cross-site attack would send
        self.assertEqual(r.status_code, 415)

    def test_letter_lifecycle_over_http(self):
        self.login('rdhs')
        r = self.post('/api/letters', {'subject': 'Over HTTP', 'dest_facility_id': F('DHDA').id, 'priority': 'Urgent'})
        self.assertEqual(r.status_code, 201)
        lid = r.json()['id']
        d = self.get(f'/api/letters/{lid}').json()
        self.assertEqual(set(d), {'letter', 'events', 'attachments', 'actions', 'suggestion', 'colleagues', 'destOfficers', 'parent', 'replies', 'can_attach'})
        self.assertEqual(d['suggestion']['to_user_id'], U('rdhs.sec').id)
        self.assertIn('Z', d['letter']['created_at'])
        pdf = base64.b64encode(b'%PDF-1.4 test').decode()
        r = self.post(f'/api/letters/{lid}/attachments', {'filename': 'scan.pdf', 'mime': 'application/pdf', 'data': pdf})
        self.assertEqual(r.status_code, 200)
        aid = r.json()['attachments'][0]['id']
        self.assertEqual(self.post(f'/api/letters/{lid}/attachments', {'filename': 'x.exe', 'mime': 'application/x-msdownload', 'data': 'AAAA'}).status_code, 400)
        dl = self.get(f'/api/attachments/{aid}')
        self.assertEqual((dl.status_code, dl['Content-Type']), (200, 'application/pdf'))
        r = self.post(f'/api/letters/{lid}/actions', {'type': 'forward', 'to_user_id': U('rdhs.sec').id})
        self.assertEqual(r.json()['letter']['holder_name'], U('rdhs.sec').name)
        self.assertEqual(r.json()['actions'], [])
        self.client.logout()
        self.login('b.ddhs')
        self.assertEqual(self.get(f'/api/letters/{lid}').status_code, 403, 'another district cannot open it')
        self.assertEqual(self.get(f'/api/attachments/{aid}').status_code, 403)

    def test_search_csv_dashboard(self):
        self.login('rdhs')
        r = self.get('/api/letters?q=midwives').json()
        self.assertEqual(r['total'], 1)
        self.assertEqual(r['rows'][0]['priority'], 'Urgent')
        self.assertEqual(self.get('/api/letters?box=desk&open=1').status_code, 200)
        csv = self.get('/api/letters.csv')
        self.assertEqual(csv['Content-Type'], 'text/csv; charset=utf-8')
        self.assertTrue(csv.content.decode('utf-8').startswith('﻿Reference,Subject'))
        d = self.get('/api/dashboard').json()
        self.assertEqual(set(d['kpi']), {'my_desk', 'my_overdue', 'open', 'overdue', 'closed_30', 'created_30'})
        self.assertEqual([s['status'] for s in d['by_status']], ['Drafted', 'Dispatched', 'Received', 'Minuted', 'Actioned', 'Closed'])
        self.assertEqual(sum(s['n'] for s in d['by_status']), 5)
        self.assertTrue(d['by_facility'] and d['by_officer'] and d['recent'])

    def test_meta_and_notifications(self):
        self.login('registry')
        m = self.get('/api/meta').json()
        self.assertIn('RDHS', m['roles'])
        self.assertNotIn('cron_key', m['settings'])
        self.assertGreaterEqual(len(m['facilities']), 5)
        rows = self.get('/api/notifications').json()['rows']
        self.assertTrue(rows)
        self.assertEqual(self.post('/api/notifications/read', {}).status_code, 200)
        self.assertEqual(self.get('/api/me').json()['unread'], 0)

    def test_admin_endpoints(self):
        self.login('rdhs')
        self.assertEqual(self.get('/api/admin/users').status_code, 403)
        self.client.logout()
        u = User.objects.get(username='admin')
        u.set_password('admin-pass-1')
        u.must_change_password = False
        u.save()
        self.login('admin', 'admin-pass-1')
        self.assertGreater(len(self.get('/api/admin/users').json()['rows']), 20)
        bad = self.post('/api/admin/users', {'name': 'X', 'username': 'xx1', 'password': 'password1', 'role': 'DDHS', 'facility_id': F('RHD').id})
        self.assertEqual(bad.status_code, 400)
        self.assertIn('belongs at a District Health Directorate', bad.json()['error'])
        good = self.post('/api/admin/users', {'name': 'New DDHS', 'username': 'new.ddhs', 'password': 'password1', 'role': 'DDHS', 'facility_id': F('DHDA').id, 'phone': '0244111222'})
        self.assertEqual(good.status_code, 201)
        self.assertEqual(self.post('/api/admin/users', {'name': 'Y', 'username': 'y.y', 'password': 'password1', 'role': 'DDHS', 'facility_id': F('DHDA').id, 'phone': '12'}).status_code, 400)
        self.assertEqual(self.post('/api/admin/facilities', {'name': 'Bolga Municipal Health Directorate', 'code': 'BOLDHD', 'level': 'DHD'}).status_code, 201)
        self.assertEqual(self.post('/api/admin/settings', {'sla_days': 5, 'region_name': 'Upper East Regional Health Directorate'}, method='put').status_code, 200)
        sms = self.get('/api/admin/sms').json()
        self.assertTrue(sms['cron_path'].startswith('api/cron?key='))
        key = sms['cron_path'].split('=', 1)[1]
        self.assertEqual(self.get('/api/cron?key=wrong').status_code, 403)
        self.assertEqual(self.get(f'/api/cron?key={key}').status_code, 200)

    def test_pages_and_assets(self):
        self.assertEqual(self.get('/').status_code, 200)
        self.assertIn(b'<title>RHD e-Registry</title>', b''.join(self.get('/').streaming_content))
        self.assertEqual(self.get('/styles.css')['Content-Type'], 'text/css; charset=utf-8')
        self.assertEqual(self.get('/fonts/inter-latin-400-normal.woff2')['Content-Type'], 'font/woff2')
        self.assertEqual(self.get('/nope.css').status_code, 404)
        self.assertEqual(self.get('/api/nope').status_code, 404)
        self.assertEqual(self.get('/api/setup/status').status_code, 409, 'set-up is locked once the site is set up')
        h = self.get('/api/health').json()
        self.assertTrue(h['ok'])
        self.assertIn('Content-Security-Policy', self.get('/'))


class SetupKeyTests(TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        self.tmp = Path(tempfile.mkdtemp())
        self._orig = st.SETUP_KEY_FILE
        st.SETUP_KEY_FILE = self.tmp / 'SETUP-KEY.txt'

    def tearDown(self):
        import os
        st.SETUP_KEY_FILE = self._orig
        os.environ.pop('SETUP_KEY', None)

    def test_generated_key_is_stable(self):
        k = st.setup_key()
        self.assertRegex(k, r'^[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}$')
        self.assertEqual(st.setup_key(), k)
        self.assertEqual(st.setup_key_source(), 'file')

    def test_environment_key_wins(self):
        import os
        os.environ['SETUP_KEY'] = 'H75S-PYMR-N3PS'
        self.assertEqual(st.setup_key_source(), 'env')
        self.assertEqual(st.norm_key(' h75s-pymr-n3ps '), st.norm_key(st.setup_key()))

    def test_own_file_with_label_or_bom(self):
        for text in ('﻿Ghana2026RHD\r\n', 'Setup key: Ghana2026RHD\n', 'key = "Ghana2026RHD"\n', '# my key\nGhana2026RHD\n'):
            st.SETUP_KEY_FILE.write_text(text, encoding='utf-8')
            self.assertEqual(st.norm_key(st.setup_key()), 'GHANA2026RHD', text)

    def test_unusable_file_is_replaced(self):
        st.SETUP_KEY_FILE.write_text('\n\nab\n')
        self.assertRegex(st.setup_key(), r'^[A-Z2-9]{4}-')
