"""WSGI 入口（Windows 上用 waitress，Linux 上用 gunicorn）。"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "eep.settings")

application = get_wsgi_application()
