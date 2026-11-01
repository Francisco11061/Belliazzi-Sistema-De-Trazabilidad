"""Linux: uv run gunicorn --config deploy/gunicorn.conf.py config.wsgi:application."""
import os

raw_env = ['DJANGO_SETTINGS_MODULE=config.settings_production']
# Nginx es el único punto de entrada. No exponer Gunicorn en la red.
bind = '127.0.0.1:8001'
forwarded_allow_ips = '127.0.0.1,::1'
# Django controla el opt-in TRUST_PROXY_HEADERS; no inferir HTTPS antes de él.
secure_scheme_headers = {}
workers = int(os.environ.get('WEB_WORKERS', '2'))
worker_class = 'sync'
timeout = int(os.environ.get('WEB_TIMEOUT', '60'))
graceful_timeout = 30
accesslog = '-'
errorlog = '-'
capture_output = True
# No registrar querystrings, cookies ni cuerpos de formularios.
access_log_format = '%(t)s "%(m)s %(U)s" %(s)s %(L)s'
