"""Django settings for RHD e-Registry.

Database: MySQL / MariaDB (production, e.g. cPanel shared hosting) entered on the web set-up page or
through environment variables; SQLite for development ("manage.py runserver"). See DEPLOY.md.
"""
import secrets
from pathlib import Path

import pymysql

from . import envconfig as cfg

pymysql.install_as_MySQLdb()   # pure-Python MySQL driver: nothing to compile on shared hosting

BASE_DIR = cfg.ROOT
DATA_DIR = cfg.DATA_DIR
PUBLIC_DIR = BASE_DIR / 'public'


def _secret_key():
    """SECRET_KEY from the environment, else generated once and kept in the data folder."""
    if cfg.env.get('SECRET_KEY'):
        return cfg.env['SECRET_KEY']
    path = Path(DATA_DIR) / 'secret.key'
    try:
        return path.read_text().strip()
    except OSError:
        pass
    key = secrets.token_urlsafe(50)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(key)
        path.chmod(0o600)
    except OSError:
        pass
    return key


SECRET_KEY = _secret_key()
DEBUG = cfg.env.get('DJANGO_DEBUG') == '1' or (cfg.DEV and not cfg.TESTING)
ALLOWED_HOSTS = [h.strip() for h in cfg.env.get('ALLOWED_HOSTS', '*').split(',') if h.strip()]
CSRF_TRUSTED_ORIGINS = [o.strip() for o in cfg.env.get('CSRF_TRUSTED_ORIGINS', '').split(',') if o.strip()]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'registry',
]

MIDDLEWARE = [
    'registry.middleware.SecurityHeadersMiddleware',
    'registry.middleware.SetupMiddleware',          # shows the set-up page until the database is connected
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'registry.middleware.WorkerMiddleware',         # starts the SMS / reminder worker on first use
]

ROOT_URLCONF = 'eregistry.urls'
WSGI_APPLICATION = 'eregistry.wsgi.application'

TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [],
    'APP_DIRS': True,
    'OPTIONS': {'context_processors': [
        'django.template.context_processors.request',
        'django.contrib.auth.context_processors.auth',
        'django.contrib.messages.context_processors.messages',
    ]},
}]

DATABASES = {'default': cfg.database_settings()}
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
AUTH_USER_MODEL = 'registry.User'

# Sessions are signed cookies, so signing in does not need extra tables and works during set-up.
SESSION_ENGINE = 'django.contrib.sessions.backends.signed_cookies'
SESSION_COOKIE_NAME = 'sid'
SESSION_COOKIE_AGE = 12 * 3600
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Strict'
SESSION_COOKIE_SECURE = cfg.env.get('COOKIE_SECURE') == '1'
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
MESSAGE_STORAGE = 'django.contrib.messages.storage.cookie.CookieStorage'

PASSWORD_HASHERS = ['django.contrib.auth.hashers.PBKDF2PasswordHasher']
if cfg.TESTING:
    PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']   # fast tests only

LANGUAGE_CODE = 'en-gb'
TIME_ZONE = 'Africa/Accra'
USE_I18N = False
USE_TZ = True

STATIC_URL = 'django-static/'
STATIC_ROOT = DATA_DIR / 'django-static'

DATA_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024   # attachments arrive as base64 JSON (10 MB files)
X_FRAME_OPTIONS = 'DENY'
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

LOGGING = {
    'version': 1, 'disable_existing_loggers': False,
    'handlers': {'console': {'class': 'logging.StreamHandler'}},
    'root': {'handlers': ['console'], 'level': 'WARNING'},
    'loggers': {
        'registry': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
        'django.request': {'handlers': ['console'], 'level': 'ERROR', 'propagate': False},   # 4xx answers are normal
    },
}
