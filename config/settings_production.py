"""Producción explícita: no carga .env local ni contiene secretos de ejemplo."""
from urllib.parse import urlsplit

from .settings import *  # noqa: F403

DEBUG = False
for variable in ('SECRET_KEY', 'DB_NAME', 'DB_USER', 'DB_PASSWORD', 'DB_HOST'):
    if not os.environ.get(variable):
        raise ImproperlyConfigured(f'Producción requiere {variable} en el entorno del proceso.')
if len(SECRET_KEY) < 50 or len(set(SECRET_KEY)) < 5 or SECRET_KEY.startswith('django-insecure-'):
    raise ImproperlyConfigured('Producción requiere SECRET_KEY aleatoria de al menos 50 caracteres.')
if not ALLOWED_HOSTS or any('*' in host for host in ALLOWED_HOSTS):
    raise ImproperlyConfigured('Define ALLOWED_HOSTS explícitos para producción, sin comodines.')

CSRF_TRUSTED_ORIGINS = env.list('CSRF_TRUSTED_ORIGINS', default=[])
if not CSRF_TRUSTED_ORIGINS or any(
    urlsplit(origin).scheme != 'https' or not urlsplit(origin).netloc or '*' in origin
    for origin in CSRF_TRUSTED_ORIGINS
):
    raise ImproperlyConfigured('Define orígenes CSRF HTTPS explícitos para producción.')

if DB_ENGINE != 'postgresql':
    raise ImproperlyConfigured('La configuración de producción requiere DB_ENGINE=postgresql.')
DATABASES['default']['OPTIONS']['sslmode'] = env('DB_SSLMODE', default='verify-full')
if DATABASES['default']['OPTIONS']['sslmode'] != 'verify-full':
    raise ImproperlyConfigured('Producción requiere DB_SSLMODE=verify-full.')
if not env('DB_SSLROOTCERT', default='') or not Path(env('DB_SSLROOTCERT')).is_file():
    raise ImproperlyConfigured('DB_SSLROOTCERT debe apuntar al certificado CA de la base de datos.')

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
# Activar solo detrás del Nginx descrito en deploy/nginx.conf.example,
# sin acceso directo a Gunicorn y con entrada al Nginx restringida al ALB.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https') if env.bool('TRUST_PROXY_HEADERS', default=False) else None
USE_X_FORWARDED_HOST = False

# HSTS debe habilitarse tras validar el dominio y HTTPS. No se oculta W004.
SECURE_HSTS_SECONDS = env.int('SECURE_HSTS_SECONDS', default=0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool('SECURE_HSTS_INCLUDE_SUBDOMAINS', default=False)
SECURE_HSTS_PRELOAD = env.bool('SECURE_HSTS_PRELOAD', default=False)
if SECURE_HSTS_SECONDS < 0:
    raise ImproperlyConfigured('SECURE_HSTS_SECONDS no puede ser negativo.')
AXES_ENABLED = True

LOGGING = {
    'version': 1, 'disable_existing_loggers': False,
    'handlers': {'console': {'class': 'logging.StreamHandler'}},
    'loggers': {'django': {'handlers': ['console'], 'level': 'WARNING', 'propagate': False}},
}
