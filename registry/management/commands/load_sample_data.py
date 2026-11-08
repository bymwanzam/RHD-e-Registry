"""Loads the test sample data (directorates, officers, letters) into a development database.
Never run this on the live system."""
from django.core.management.base import BaseCommand, CommandError

from eregistry import envconfig as cfg
from registry.models import Letter


class Command(BaseCommand):
    help = 'Load sample directorates, officers (password test1234) and letters into a DEVELOPMENT database.'

    def handle(self, *args, **options):
        if not cfg.DEV:
            raise CommandError('Only for development (set ERG_DEV=1). This would add fake officers to a real system.')
        if Letter.objects.exists():
            raise CommandError('The database already has letters.')
        from registry.tests.fixtures import seed_sample_data
        seed_sample_data()
        self.stdout.write('Sample data loaded. Officers sign in with password test1234 (e.g. "registry", "rdhs").')
