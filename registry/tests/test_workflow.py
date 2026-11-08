from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.utils import DatabaseError
from django.test import TestCase

from registry import workflow as wf
from registry.models import AuditTrailLocked, Letter, LetterEvent
from registry.store import utcnow

from .fixtures import F, U, act, seed_sample_data


def L(pk):
    return Letter.objects.get(pk=pk)


class WorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed_sample_data()

    def test_downward_route(self):
        """RDHS -> Secretary -> Registry -> DHD HR/Admin -> DDHS -> officer -> closed."""
        lid = wf.create_letter(U('rdhs'), {'subject': 'Test circular', 'dest_facility_id': F('DHDB').id})
        self.assertEqual(L(lid).status, 'Drafted')
        self.assertIsNone(L(lid).ref_no)
        s = wf.suggestion(U('rdhs'), L(lid))
        self.assertEqual(s['to_user_id'], U('rdhs.sec').id)
        act('rdhs', lid, {'type': 'forward', 'to_user_id': s['to_user_id']})
        s = wf.suggestion(U('rdhs.sec'), L(lid))
        self.assertEqual(s['to_user_id'], U('registry').id)
        act('rdhs.sec', lid, {'type': 'forward', 'to_user_id': s['to_user_id']})
        self.assertRegex(L(lid).ref_no, r'^RHD/\d{2}/\d{4}$')
        s = wf.suggestion(U('registry'), L(lid))
        self.assertEqual((s['action'], s['to_user_id']), ('dispatch', U('b.hr').id))
        act('registry', lid, {'type': 'dispatch'})
        self.assertEqual((L(lid).status, L(lid).holder_id), ('Dispatched', U('b.hr').id))
        s = wf.suggestion(U('b.hr'), L(lid))
        self.assertEqual((s['action'], s['to_user_id']), ('receive', U('b.ddhs').id))
        act('b.hr', lid, {'type': 'receive', 'to_user_id': s['to_user_id']})
        self.assertEqual((L(lid).status, L(lid).holder_id), ('Received', U('b.ddhs').id))
        act('b.ddhs', lid, {'type': 'minute', 'to_user_id': U('b.eo').id, 'note': 'Please act'})
        self.assertEqual(L(lid).status, 'Minuted')
        act('b.eo', lid, {'type': 'actioned', 'note': 'Done'})
        act('b.eo', lid, {'type': 'close'})
        self.assertEqual((L(lid).status, L(lid).holder_id), ('Closed', None))
        actions = list(LetterEvent.objects.filter(letter_id=lid).order_by('id').values_list('action', flat=True))
        self.assertEqual(actions, ['Created', 'Forwarded', 'Logged', 'Forwarded', 'Dispatched', 'Received', 'Forwarded', 'Minuted', 'Actioned', 'Closed'])

    def test_upward_route(self):
        lid = wf.create_letter(U('hb.admin'), {'subject': 'Staff request', 'dest_facility_id': F('RHD').id})
        act('hb.admin', lid, {'type': 'forward', 'to_user_id': wf.suggestion(U('hb.admin'), L(lid))['to_user_id']})
        self.assertEqual(L(lid).holder_id, U('hb.sec').id)
        s = wf.suggestion(U('hb.sec'), L(lid))
        self.assertEqual((s['action'], s['to_user_id']), ('dispatch', U('registry').id))
        act('hb.sec', lid, {'type': 'dispatch'})
        self.assertTrue(L(lid).ref_no.startswith('DHB/'))
        act('registry', lid, {'type': 'receive', 'to_user_id': wf.suggestion(U('registry'), L(lid))['to_user_id']})
        self.assertEqual(L(lid).holder_id, U('rdhs.sec').id)
        act('rdhs.sec', lid, {'type': 'forward', 'to_user_id': wf.suggestion(U('rdhs.sec'), L(lid))['to_user_id']})
        self.assertEqual(L(lid).holder_id, U('rdhs').id)
        act('rdhs', lid, {'type': 'minute', 'to_user_id': U('ddhr').id, 'note': 'DDHR to handle'})
        self.assertEqual(L(lid).holder_id, U('ddhr').id)
        self.assertIn('reply', wf.available_actions(U('ddhr'), L(lid)))

    def test_only_holder_can_act(self):
        lid = wf.create_letter(U('rdhs'), {'subject': 'Permissions', 'dest_facility_id': F('DHDA').id})
        with self.assertRaisesRegex(wf.HttpError, 'not available'):
            act('rdhs.sec', lid, {'type': 'forward', 'to_user_id': U('registry').id})
        with self.assertRaisesRegex(wf.HttpError, 'not available'):
            act('rdhs', lid, {'type': 'dispatch'})
        with self.assertRaisesRegex(wf.HttpError, 'same directorate'):
            act('rdhs', lid, {'type': 'forward', 'to_user_id': U('a.hr').id})

    def test_failed_action_rolls_back(self):
        lid = wf.create_letter(U('rdhs'), {'subject': 'Rollback check', 'dest_facility_id': F('DHDA').id})
        before = LetterEvent.objects.filter(letter_id=lid).count()
        with self.assertRaisesRegex(wf.HttpError, 'valid recipient'):
            act('rdhs', lid, {'type': 'forward', 'to_user_id': 999999})
        self.assertEqual(LetterEvent.objects.filter(letter_id=lid).count(), before)
        self.assertEqual(L(lid).holder_id, U('rdhs').id)

    def test_reference_numbers_unique(self):
        sec, reg = U('rdhs.sec'), U('registry')
        ids = []
        for i in range(8):
            lid = wf.create_letter(U('rdhs'), {'subject': f'Batch {i}', 'dest_facility_id': F('DHDA').id})
            act('rdhs', lid, {'type': 'forward', 'to_user_id': sec.id})
            ids.append(lid)
        for lid in ids:
            wf.perform_action(sec, lid, {'type': 'forward', 'to_user_id': reg.id})
        refs = [L(i).ref_no for i in ids]
        self.assertEqual(len(set(refs)), len(refs))

    def test_role_based_access(self):
        l = L(wf.create_letter(U('ddph'), {'subject': 'Private memo', 'dest_facility_id': F('DHDA').id}))
        self.assertTrue(wf.can_view(U('ddph'), l))
        self.assertTrue(wf.can_view(U('rdhs'), l), 'RDHS sees all letters')
        self.assertFalse(wf.can_view(U('ddcc'), l), 'another deputy director does not')
        self.assertTrue(wf.can_view(U('a.ddhs'), l), 'DDHS sees letters addressed to their directorate')
        self.assertFalse(wf.can_view(U('b.ddhs'), l), 'other districts do not')
        self.assertFalse(wf.can_view(U('a.eo'), l), 'an executive officer sees it only once minuted to them')

    def test_audit_trail_append_only(self):
        e = LetterEvent.objects.first()
        e.note = 'tampered'
        with self.assertRaises(AuditTrailLocked):
            e.save()
        with self.assertRaises(AuditTrailLocked):
            e.delete()
        # Database-level triggers (migration 0002) block direct SQL too.
        with self.assertRaisesRegex(DatabaseError, 'append-only'), transaction.atomic():
            LetterEvent.objects.filter(pk=e.pk).update(note='tampered')
        with self.assertRaisesRegex(DatabaseError, 'append-only'), transaction.atomic():
            LetterEvent.objects.filter(pk=e.pk).delete()

    def test_incoming_letter(self):
        lid = wf.create_letter(U('registry'), {'incoming': True, 'subject': 'From MOH', 'external_sender': 'Ministry of Health'})
        self.assertEqual(L(lid).status, 'Received')
        self.assertRegex(L(lid).ref_no, r'^RHD/IN/\d{2}/\d{4}$')
        with self.assertRaisesRegex(wf.HttpError, 'Only secretaries'):
            wf.create_letter(U('ddcc'), {'incoming': True, 'subject': 'x', 'external_sender': 'y'})

    def test_overdue(self):
        lid = wf.create_letter(U('dda'), {'subject': 'Old letter', 'dest_facility_id': F('DHDA').id})
        Letter.objects.filter(pk=lid).update(desk_since=utcnow() - timedelta(days=10))
        self.assertTrue(Letter.objects.filter(pk=lid).filter(wf.overdue_q()).exists())

    def test_unicode_round_trip(self):
        subject = 'Communiqué — Nkwanta “Ɔbaa” ₵500 ✓'
        lid = wf.create_letter(U('rdhs'), {'subject': subject, 'dest_facility_id': F('DHDA').id})
        self.assertEqual(L(lid).subject, subject)
