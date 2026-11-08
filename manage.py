#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys

# Django and PyMySQL are bundled in vendor/ by the release package, so the app runs even if
# "Run Pip Install" was not used. Packages installed in the virtual environment take priority.
_vendor = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vendor')
if os.path.isdir(_vendor):
    sys.path.append(_vendor)


def main():
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'eregistry.settings')
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
