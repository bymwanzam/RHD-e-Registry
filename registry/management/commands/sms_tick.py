"""Sends queued SMS, retries failures, checks delivery and runs the overdue reminder once.

Alternative to the cron "wake-up" link, for a cPanel cron job such as:
  cd ~/rhd-e-registry && ~/virtualenv/rhd-e-registry/3.12/bin/python manage.py sms_tick
"""
from django.core.management.base import BaseCommand

from registry import setup_state
from registry.reminders import run_overdue_reminders
from registry.sms import worker


class Command(BaseCommand):
    help = 'Send queued SMS and run the weekday overdue reminder once.'

    def handle(self, *args, **options):
        setup_state.ensure_ready(force=True)
        worker.hooks = [run_overdue_reminders]
        worker.tick()
        self.stdout.write('SMS queue processed.')
