"""Sample directorates, hospitals, one officer per role, and letters moved through the real workflow.
Used only by the tests; the app itself starts with just the administrator account."""
from registry import workflow as wf
from registry.models import Facility, User
from registry.roles import ROLES
from registry.store import seed_first_run

PASSWORD = 'test1234'


def U(username):
    return User.objects.select_related('facility').get(username=username)


def F(code):
    return Facility.objects.get(code=code)


def act(username, letter_id, body):
    return wf.perform_action(U(username), letter_id, body)


def seed_sample_data():
    seed_first_run()
    rhd = Facility.objects.get(code='RHD')

    def add_user(name, username, role, fac):
        return User.objects.create_user(username, PASSWORD, name=name, role=role, facility=fac)

    dhd_a = Facility.objects.create(name='District A Health Directorate', code='DHDA', level='DHD')
    dh_a = Facility.objects.create(name='District A Hospital', code='DHA', level='HOSPITAL', parent=dhd_a)
    dhd_b = Facility.objects.create(name='District B Health Directorate', code='DHDB', level='DHD')
    dh_b = Facility.objects.create(name='District B Hospital', code='DHB', level='HOSPITAL', parent=dhd_b)

    for role, un in [('RDHS', 'rdhs'), ('RDHS_SEC', 'rdhs.sec'), ('MRU', 'registry'), ('DDHR', 'ddhr'), ('DDCC', 'ddcc'),
                     ('DDPH', 'ddph'), ('DDF', 'ddf'), ('DDA', 'dda')]:
        add_user(f'Test {ROLES[role]["short"]}', un, role, rhd)
    for fac, p in [(dhd_a, 'a'), (dhd_b, 'b')]:
        for role, un in [('DDHS', 'ddhs'), ('DHD_HR', 'hr'), ('DHD_EO', 'eo'), ('DHD_SEC', 'sec')]:
            add_user(f'Test {ROLES[role]["short"]} {p.upper()}', f'{p}.{un}', role, fac)
    for fac, p in [(dh_a, 'ha'), (dh_b, 'hb')]:
        for role, un in [('HOSP_ADMIN', 'admin'), ('DDNS', 'ddns'), ('HOSP_ACC', 'acc'), ('HOSP_HR', 'hr'), ('HOSP_EO', 'eo'), ('HOSP_SEC', 'sec')]:
            add_user(f'Test {ROLES[role]["short"]} {p.upper()}', f'{p}.{un}', role, fac)

    # 1. Downward route: RDHS -> Secretary -> Registry -> DHD HR/Admin -> DDHS -> officer
    l1 = wf.create_letter(U('rdhs'), {'subject': 'Posting of newly recruited staff nurses', 'summary': 'Posting letters for District A.', 'dest_facility_id': dhd_a.id})
    act('rdhs', l1, {'type': 'forward', 'to_user_id': U('rdhs.sec').id, 'note': 'Please log and dispatch'})
    act('rdhs.sec', l1, {'type': 'forward', 'to_user_id': U('registry').id})
    act('registry', l1, {'type': 'dispatch'})
    act('a.hr', l1, {'type': 'receive', 'to_user_id': U('a.ddhs').id})
    act('a.ddhs', l1, {'type': 'minute', 'to_user_id': U('a.hr').id, 'note': 'HR: arrange orientation.'})
    # 2. Upward route waiting at the Registry Unit
    l2 = wf.create_letter(U('b.ddhs'), {'subject': 'Request for additional midwives', 'category': 'Request', 'priority': 'Urgent', 'dest_facility_id': rhd.id})
    act('b.ddhs', l2, {'type': 'forward', 'to_user_id': U('b.sec').id})
    act('b.sec', l2, {'type': 'dispatch'})
    # 3. Hospital to region, fully closed
    l3 = wf.create_letter(U('ha.admin'), {'subject': 'Quarterly staff returns, Q3', 'category': 'Report', 'dest_facility_id': rhd.id})
    act('ha.admin', l3, {'type': 'forward', 'to_user_id': U('ha.sec').id})
    act('ha.sec', l3, {'type': 'dispatch'})
    act('registry', l3, {'type': 'receive', 'to_user_id': U('rdhs.sec').id})
    act('rdhs.sec', l3, {'type': 'forward', 'to_user_id': U('rdhs').id})
    act('rdhs', l3, {'type': 'minute', 'to_user_id': U('ddhr').id, 'note': 'DDHR: update the staff database.'})
    act('ddhr', l3, {'type': 'actioned', 'note': 'Updated.'})
    act('ddhr', l3, {'type': 'close'})
    # 4. Incoming paper letter; 5. a draft
    wf.create_letter(U('registry'), {'incoming': True, 'subject': 'Circular on annual leave roster', 'external_sender': 'Ministry of Health', 'external_ref': 'MOH/HR/112', 'category': 'Circular'})
    wf.create_letter(U('ddph'), {'subject': 'Measles campaign micro-plans', 'category': 'Memo', 'dest_facility_id': dhd_b.id})
