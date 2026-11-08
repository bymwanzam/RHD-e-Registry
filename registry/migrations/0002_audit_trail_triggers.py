"""Database-level protection for the audit trail: entries cannot be updated or deleted.

The app itself never changes audit entries (LetterEvent.save/delete refuse to). The triggers add
protection against manual edits. Some shared hosts do not allow triggers; then this step is skipped.
"""
import logging

from django.db import migrations

log = logging.getLogger('registry')

MESSAGE = 'The audit trail is append-only'


def create_triggers(apps, schema_editor):
    conn = schema_editor.connection
    table = 'registry_letterevent'
    if conn.vendor == 'sqlite':
        stmts = [
            f"CREATE TRIGGER IF NOT EXISTS letterevent_no_update BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT, '{MESSAGE}'); END",
            f"CREATE TRIGGER IF NOT EXISTS letterevent_no_delete BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT, '{MESSAGE}'); END",
        ]
    elif conn.vendor == 'mysql':
        stmts = [
            f"CREATE TRIGGER letterevent_no_update BEFORE UPDATE ON {table} FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '{MESSAGE}'",
            f"CREATE TRIGGER letterevent_no_delete BEFORE DELETE ON {table} FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '{MESSAGE}'",
        ]
    else:
        return
    for sql in stmts:
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
        except Exception as e:  # noqa: BLE001 - hosts without the TRIGGER privilege
            log.warning('Could not create audit-trail trigger (%s). The app still never changes audit entries.', e)


def drop_triggers(apps, schema_editor):
    conn = schema_editor.connection
    for name in ('letterevent_no_update', 'letterevent_no_delete'):
        try:
            with conn.cursor() as cur:
                cur.execute(f'DROP TRIGGER IF EXISTS {name}')
        except Exception:  # noqa: BLE001
            pass


class Migration(migrations.Migration):
    # Not atomic: MySQL cannot create triggers inside a transaction-wrapped migration on some hosts.
    atomic = False
    dependencies = [('registry', '0001_initial')]
    operations = [migrations.RunPython(create_triggers, drop_triggers)]
