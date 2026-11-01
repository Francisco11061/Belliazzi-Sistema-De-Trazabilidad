import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile

from django.test import SimpleTestCase


class ConfiguracionEntornosTests(SimpleTestCase):
    def consultar(self, production=False, **cambios):
        env = {k: v for k, v in os.environ.items() if not k.startswith(('DB_', 'SECURE_', 'CSRF_', 'TRUST_', 'AXES_'))}
        env.update(DJANGO_SETTINGS_MODULE='config.settings_production' if production else 'config.settings',
                   SECRET_KEY=secrets.token_urlsafe(64), DEBUG='True', ALLOWED_HOSTS='belliazzi.example.com',
                   CSRF_TRUSTED_ORIGINS='https://belliazzi.example.com', DB_ENGINE='postgresql' if production else 'mysql',
                   DB_NAME='configuration_only', DB_USER='configuration_only', DB_PASSWORD=secrets.token_urlsafe(24),
                   DB_HOST='database.internal', DB_PORT='5432' if production else '3306',
                   DB_SSLMODE='verify-full' if production else 'prefer', STATIC_ROOT=str(Path(tempfile.gettempdir())/'belliazzi-config-static'))
        # Valida configuración sin intentar conectar: este archivo NO es un CA utilizable.
        with tempfile.TemporaryDirectory() as folder:
            ca = Path(folder)/'configuration-only.pem'
            ca.write_text('configuration test only', encoding='utf8')
            env['DB_SSLROOTCERT'] = str(ca)
            env.update(cambios)
            script = """import json,django;django.setup()
from django.conf import settings as s
from django.test import RequestFactory
r=RequestFactory().get('/',HTTP_X_FORWARDED_PROTO='https')
print(json.dumps(dict(debug=s.DEBUG,engine=s.DATABASES['default']['ENGINE'],options=s.DATABASES['default']['OPTIONS'],secure=s.SESSION_COOKIE_SECURE,csrf=s.CSRF_COOKIE_SECURE,redirect=s.SECURE_SSL_REDIRECT,hsts=s.SECURE_HSTS_SECONDS,proxy_secure=r.is_secure(),axes=s.AXES_ENABLED)))
"""
            return subprocess.run([sys.executable,'-c',script],env=env,capture_output=True,text=True,timeout=20)

    def test_mysql_conserva_opciones_locales(self):
        result = self.consultar()
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['engine'], 'django.db.backends.mysql')
        self.assertEqual(data['options'], {'charset': 'utf8mb4'})
        self.assertTrue(data['debug'])
        self.assertFalse(data['secure'])

    def test_postgresql_sin_opciones_mysql(self):
        result = self.consultar(DB_ENGINE='postgresql', DB_PORT='5432')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('charset', json.loads(result.stdout)['options'])

    def test_produccion_segura_no_hereda_debug(self):
        result = self.consultar(production=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertFalse(data['debug'])
        self.assertTrue(data['secure'] and data['csrf'] and data['redirect'] and data['axes'])
        self.assertEqual(data['hsts'], 0)
        self.assertFalse(data['proxy_secure'])

    def test_proxy_y_hsts_requieren_configuracion_explicita(self):
        result = self.consultar(production=True, TRUST_PROXY_HEADERS='True', SECURE_HSTS_SECONDS='300')
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertTrue(data['proxy_secure'])
        self.assertEqual(data['hsts'], 300)

    def test_produccion_rechaza_configuracion_insegura(self):
        for cambios in ({'SECRET_KEY':'development'}, {'ALLOWED_HOSTS':'*'}, {'CSRF_TRUSTED_ORIGINS':'http://example.com'},
                        {'DB_ENGINE':'mysql'}, {'DB_SSLMODE':'disable'}, {'DB_SSLROOTCERT':''}, {'DB_HOST':''}):
            with self.subTest(variables=list(cambios)):
                self.assertNotEqual(self.consultar(production=True, **cambios).returncode, 0)

    def test_motor_desconocido_no_se_sustituye_silenciosamente(self):
        self.assertNotEqual(self.consultar(DB_ENGINE='sqlite').returncode, 0)
