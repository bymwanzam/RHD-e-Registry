"""URL routes. The browser app (public/) uses relative paths, so the site also works in a sub-folder."""
from pathlib import Path

import django.contrib.admin
from django.contrib import admin
from django.urls import path, re_path
from django.views.static import serve

from registry import api, setup_views, static_views

admin.site.site_header = 'RHD e-Registry · data administration'
admin.site.site_title = 'RHD e-Registry'

urlpatterns = [
    path('django-admin/', admin.site.urls),
    # Styles and scripts for the Django admin pages (served by the app, so no extra web-server set-up).
    re_path(r'^django-static/admin/(?P<path>.*)$', serve, {'document_root': Path(django.contrib.admin.__file__).parent / 'static' / 'admin'}),
    *api.urlpatterns,
    *setup_views.urlpatterns,
    re_path(r'^(?P<path>.*)$', static_views.serve_public),
]
