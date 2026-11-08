"""Entry point for cPanel "Setup Python App" (Phusion Passenger).

Set "Application startup file" to passenger_wsgi.py and "Application Entry point" to application.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Django and PyMySQL are bundled in vendor/ by the release package, so the app runs even if
# "Run Pip Install" was not used. Packages installed in the virtual environment take priority.
_vendor = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vendor')
if os.path.isdir(_vendor):
    sys.path.append(_vendor)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'eregistry.settings')

from eregistry.wsgi import application  # noqa: E402,F401
