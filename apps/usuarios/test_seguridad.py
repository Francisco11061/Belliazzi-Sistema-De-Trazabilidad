from datetime import timedelta
import os
import subprocess
import sys
from unittest.mock import patch

from axes.handlers.database import AxesDatabaseHandler
from axes.models import AccessAttempt
from django.conf import settings
from django.db import connection
from django.test import Client, RequestFactory, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Rol, Usuario
from .seguridad import ip_cliente

AJUSTES = dict(AXES_ENABLED=True, PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])


@override_settings(**AJUSTES)
class SeguridadLoginTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = Usuario.objects.create_user(username='seguridad', password='Clave-prueba-93', rol=Rol.objects.get(codigo='JEFE'))
        cls.otro = Usuario.objects.create_user(username='otra_cuenta', password='Clave-prueba-93', rol=Rol.objects.get(codigo='ENCARGADA'))

    def login(self, username='seguridad', password='incorrecta', ip='192.0.2.8', **extra):
        return self.client.post(reverse('usuarios:login'), {'username':username, 'password':password, **extra}, REMOTE_ADDR=ip)

    def bloquear(self, username='seguridad', ip='192.0.2.8'):
        for _ in range(4):
            self.assertEqual(self.login(username=username, ip=ip).status_code, 200)
        return self.login(username=username, ip=ip)

    def test_cinco_fallos_429_y_retry_after_sin_extender_bloqueo(self):
        response=self.bloquear()
        self.assertEqual(response.status_code,429)
        self.assertContains(response,'Demasiados intentos de inicio de sesión',status_code=429)
        self.assertTrue(1<=int(response['Retry-After'])<=900)
        attempt=AccessAttempt.objects.get()
        self.assertEqual(attempt.failures_since_start,5)
        timestamp=attempt.attempt_time
        response=self.login(password='Clave-prueba-93')
        self.assertEqual(response.status_code,429)
        attempt.refresh_from_db()
        self.assertEqual(attempt.attempt_time,timestamp)
        self.assertNotIn('_auth_user_id',self.client.session)

    def test_expira_y_login_correcto_limpia_fallos(self):
        self.bloquear()
        AccessAttempt.objects.update(attempt_time=timezone.now()-timedelta(minutes=16))
        response=self.login(password='Clave-prueba-93')
        self.assertRedirects(response,reverse('reportes:dashboard'),fetch_redirect_response=False)
        self.assertFalse(AccessAttempt.objects.exists())
        self.assertEqual(int(self.client.session['_auth_user_id']),self.user.pk)

    def test_dos_usuarios_misma_ip_y_usuario_distintas_ip(self):
        self.bloquear()
        self.assertEqual(self.login(username='otra_cuenta',password='Clave-prueba-93').status_code,302)
        self.client.logout()
        self.assertEqual(self.login(password='Clave-prueba-93',ip='192.0.2.9').status_code,302)
        self.client.logout()
        self.assertEqual(self.login(password='Clave-prueba-93').status_code,429)

    def test_usuario_inexistente_mismo_mensaje_y_no_credenciales_guardadas(self):
        response=self.login(username='no_existe', password='SECRETO-PRUEBA', token='TOKEN-PRUEBA', csrfmiddlewaretoken='CSRF-PRUEBA', custom_access_key='OTRO-SECRETO')
        self.assertContains(response,'Usuario o contraseña incorrectos')
        attempt=AccessAttempt.objects.get()
        self.assertEqual(attempt.post_data,'')
        self.assertEqual(attempt.get_data,'')
        self.assertNotContains(response,'SECRETO-PRUEBA')
        self.assertContains(response,'value="no_existe"')
        for _ in range(4):response=self.login(username='no_existe')
        self.assertEqual(response.status_code,429)
        self.assertNotContains(response,'no_existe',status_code=429)

    def test_next_y_cambio_obligatorio_permisos_sesiones(self):
        destino=reverse('trazabilidad:lista_recepciones')
        self.assertRedirects(self.login(password='Clave-prueba-93',next=destino),destino,fetch_redirect_response=False)
        self.client.logout()
        self.user.cambio_contrasena_pendiente=True
        self.user.save(update_fields=['cambio_contrasena_pendiente'])
        self.assertRedirects(self.login(password='Clave-prueba-93',next=destino),reverse('usuarios:cambiar_contrasena'),fetch_redirect_response=False)

    def test_csrf_no_se_omite_por_axes(self):
        cliente=Client(enforce_csrf_checks=True)
        self.assertEqual(cliente.post(reverse('usuarios:login'),{'username':'seguridad','password':'incorrecta'}).status_code,403)
        self.assertFalse(AccessAttempt.objects.exists())

    def test_ua_y_xff_no_eluden_conteo(self):
        for i in range(5):
            response=self.client.post('/login/',{'username':'seguridad','password':'incorrecta'},REMOTE_ADDR='192.0.2.8',HTTP_USER_AGENT=f'Agente-{i}',HTTP_X_FORWARDED_FOR=f'198.51.100.{i}')
        self.assertEqual(response.status_code,429)
        self.assertEqual(set(AccessAttempt.objects.values_list('ip_address',flat=True)),{'192.0.2.8'})

    def test_reset_autorizado_por_pareja(self):
        self.bloquear()
        self.bloquear(username='otra_cuenta')
        AxesDatabaseHandler().reset_attempts(username='seguridad',ip_address='192.0.2.8')
        self.assertEqual(self.login(password='Clave-prueba-93').status_code,302)
        self.client.logout()
        self.assertEqual(self.login(username='otra_cuenta').status_code,429)


class IPProxyTests(TestCase):
    def ip(self, peer, xff):
        return ip_cliente(RequestFactory().post('/login/',REMOTE_ADDR=peer,HTTP_X_FORWARDED_FOR=xff))

    @override_settings(TRUSTED_PROXY_CIDRS=[])
    def test_sin_proxy_ignora_encabezado(self):
        self.assertEqual(self.ip('192.0.2.1','1.2.3.4'),'192.0.2.1')

    @override_settings(TRUSTED_PROXY_CIDRS=['10.1.0.0/24'])
    def test_alb_append_no_confia_en_prefijo_del_cliente(self):
        self.assertEqual(self.ip('10.1.0.8','1.2.3.4, 192.0.2.1'),'192.0.2.1')
        self.assertEqual(self.ip('192.0.2.2','1.2.3.4'),'192.0.2.2')
        self.assertEqual(self.ip('10.1.0.8','1.2.3.4, [2001:db8::8]:54321'),'2001:db8::8')
        self.assertEqual(self.ip('10.1.0.8','192.0.2.1:54321'),'192.0.2.1')
        self.assertEqual(self.ip('10.1.0.8','texto-invalido'),'10.1.0.8')
        self.assertEqual(self.ip('10.1.0.8','192.0.2.1, 10.1.0.9'),'192.0.2.1')


@override_settings(**AJUSTES)
class AxesEntreProcesosTests(TransactionTestCase):
    def test_base_compartida_bloquea_otro_proceso(self):
        for _ in range(5):
            self.client.post('/login/',{'username':'prueba_compartida','password':'incorrecta'},REMOTE_ADDR='192.0.2.40')
        self.assertTrue(AccessAttempt.objects.exists())
        script = """import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); import django; django.setup()
from django.test import Client,override_settings
with override_settings(ALLOWED_HOSTS=['testserver']):
 print(Client().post('/login/',{'username':'prueba_compartida','password':'incorrecta'},REMOTE_ADDR='192.0.2.40').status_code)
"""
        result=subprocess.run([sys.executable,'-c',script],env=os.environ|{'DB_NAME':connection.settings_dict['NAME'],'AXES_ENABLED':'True'},capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),'429')
