"""Roles and organisational levels.

Flags:
  head       - signs letters and minutes them at their level (RDHS, DDHS, Hospital Administrator)
  minute     - may minute a letter to another officer
  secretary  - logs letters (assigns reference numbers) for their head
  registry   - Registry Unit: registers and dispatches letters between facilities
  receiver   - default receiving desk for letters dispatched to their facility
  scope      - 'all' (sees every letter), 'facility' (every letter to/from their facility),
               otherwise only letters they created, held, or were sent/minuted.
"""

LEVELS = {
    'REGION': 'Regional Health Directorate',
    'DHD': 'District Health Directorate',
    'HOSPITAL': 'District Hospital',
}

ROLES = {
    # Regional level
    'RDHS': {'label': 'Regional Director of Health Services', 'short': 'RDHS', 'level': 'REGION', 'head': True, 'minute': True, 'scope': 'all'},
    'RDHS_SEC': {'label': 'RDHS Secretary', 'short': 'RDHS Secretary', 'level': 'REGION', 'secretary': True, 'scope': 'all'},
    'MRU': {'label': 'Manager, Registry Unit', 'short': 'Manager, Registry', 'level': 'REGION', 'registry': True, 'receiver': True, 'scope': 'all'},
    'DDHR': {'label': 'Deputy Director, Human Resource', 'short': 'DDHR', 'level': 'REGION', 'minute': True},
    'DDCC': {'label': 'Deputy Director, Clinical Care', 'short': 'DDCC', 'level': 'REGION', 'minute': True},
    'DDPH': {'label': 'Deputy Director, Public Health', 'short': 'DDPH', 'level': 'REGION', 'minute': True},
    'DDF': {'label': 'Deputy Director, Finance', 'short': 'DDF', 'level': 'REGION', 'minute': True},
    'DDA': {'label': 'Deputy Director, Administration', 'short': 'DDA', 'level': 'REGION', 'minute': True},
    # District Health Directorate
    'DDHS': {'label': 'District Director of Health Services', 'short': 'DDHS', 'level': 'DHD', 'head': True, 'minute': True, 'scope': 'facility'},
    'DHD_HR': {'label': 'HR Officer / Administrative Manager', 'short': 'HR/Admin Manager', 'level': 'DHD', 'receiver': True, 'scope': 'facility'},
    'DHD_EO': {'label': 'Executive Officer', 'short': 'Executive Officer', 'level': 'DHD'},
    'DHD_SEC': {'label': 'Secretary', 'short': 'Secretary', 'level': 'DHD', 'secretary': True, 'scope': 'facility'},
    # District Hospital
    'HOSP_ADMIN': {'label': 'Hospital Administrator', 'short': 'Hospital Administrator', 'level': 'HOSPITAL', 'head': True, 'minute': True, 'scope': 'facility'},
    'DDNS': {'label': 'Deputy Director of Nursing Services', 'short': 'DDNS', 'level': 'HOSPITAL', 'minute': True},
    'HOSP_ACC': {'label': 'Hospital Accountant', 'short': 'Hospital Accountant', 'level': 'HOSPITAL'},
    'HOSP_HR': {'label': 'HR Officer / Administrative Manager', 'short': 'HR/Admin Manager', 'level': 'HOSPITAL', 'receiver': True, 'scope': 'facility'},
    'HOSP_EO': {'label': 'Executive Officer', 'short': 'Executive Officer', 'level': 'HOSPITAL'},
    'HOSP_SEC': {'label': 'Secretary', 'short': 'Secretary', 'level': 'HOSPITAL', 'secretary': True, 'scope': 'facility'},
    # System
    'SYSADMIN': {'label': 'System Administrator', 'short': 'System Admin', 'level': 'ANY', 'scope': 'all', 'system': True},
}

HEAD_ROLE = {'REGION': 'RDHS', 'DHD': 'DDHS', 'HOSPITAL': 'HOSP_ADMIN'}
SECRETARY_ROLE = {'REGION': 'RDHS_SEC', 'DHD': 'DHD_SEC', 'HOSPITAL': 'HOSP_SEC'}
RECEIVER_ROLE = {'REGION': 'MRU', 'DHD': 'DHD_HR', 'HOSPITAL': 'HOSP_HR'}

STATUSES = ['Drafted', 'Dispatched', 'Received', 'Minuted', 'Actioned', 'Closed']
CATEGORIES = ['Letter', 'Memo', 'Circular', 'Communiqué', 'Report', 'Request']
PRIORITIES = ['Normal', 'Urgent', 'Confidential']


def role_of(user):
    return ROLES.get(getattr(user, 'role', None), {}) if user else {}
