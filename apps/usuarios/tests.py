from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.urls import reverse

from .models import Rol, Usuario
from .permisos import (
    ROL_ENCARGADA,
    ROL_JEFE,
    ROL_OPERARIA,
    roles_requeridos,
    tiene_rol,
)


class UsuarioIntegridadTests(TestCase):
    def test_usuario_normal_sin_rol_falla(self):
        with self.assertRaisesRegex(IntegrityError, "usuario_normal_debe_tener_rol"):
            with transaction.atomic():
                Usuario.objects.create_user(
                    username="usuario_sin_rol",
                    password="clave-solo-para-pruebas",
                    is_superuser=False,
                )


class AutenticacionUsuarioTests(TestCase):
    def setUp(self):
        rol = Rol.objects.get(codigo="JEFE")
        self.usuario = Usuario.objects.create_user(
            username="jefe_prueba",
            password="clave-prueba-123",
            rol=rol,
        )

    def test_inicio_requiere_autenticacion(self):
        response = self.client.get(reverse("usuarios:inicio"))
        self.assertRedirects(
            response,
            f"{reverse('usuarios:login')}?next={reverse('usuarios:inicio')}",
        )

    def test_login_valido_redirige_a_inicio(self):
        response = self.client.post(
            reverse("usuarios:login"),
            {"username": "jefe_prueba", "password": "clave-prueba-123"},
        )
        self.assertRedirects(response, reverse("usuarios:inicio"))
        self.assertEqual(self.client.session["_auth_user_id"], str(self.usuario.pk))

    def test_login_invalido_no_autentica(self):
        response = self.client.post(
            reverse("usuarios:login"),
            {"username": "jefe_prueba", "password": "incorrecta"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "usuarios/login.html")
        self.assertTrue(response.context["form"].errors)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_usuario_autenticado_puede_ver_inicio(self):
        self.assertTrue(
            self.client.login(username="jefe_prueba", password="clave-prueba-123")
        )
        response = self.client.get(reverse("usuarios:inicio"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "usuarios/inicio.html")

    def test_logout_por_post(self):
        self.assertTrue(
            self.client.login(username="jefe_prueba", password="clave-prueba-123")
        )
        response = self.client.post(reverse("usuarios:logout"))
        self.assertRedirects(response, reverse("usuarios:login"))
        self.assertNotIn("_auth_user_id", self.client.session)


@roles_requeridos(ROL_JEFE)
def vista_solo_jefe(request):
    return HttpResponse("ok")


class PermisosRolTests(TestCase):
    def setUp(self):
        rol_jefe = Rol.objects.get(codigo=ROL_JEFE)
        rol_encargada = Rol.objects.get(codigo=ROL_ENCARGADA)
        rol_operaria = Rol.objects.get(codigo=ROL_OPERARIA)
        self.usuario_jefe = Usuario.objects.create_user(
            username="usuario_jefe",
            password="clave-prueba-123",
            rol=rol_jefe,
        )
        self.usuario_encargada = Usuario.objects.create_user(
            username="usuario_encargada",
            password="clave-prueba-123",
            rol=rol_encargada,
        )
        self.usuario_operaria = Usuario.objects.create_user(
            username="usuario_operaria",
            password="clave-prueba-123",
            rol=rol_operaria,
        )
        self.superusuario = Usuario.objects.create_superuser(
            username="tecnico_permisos",
            password="clave-prueba-123",
        )
        self.factory = RequestFactory()

    def test_tiene_rol_identifica_rol_correcto(self):
        self.assertTrue(tiene_rol(self.usuario_jefe, ROL_JEFE))
        self.assertFalse(tiene_rol(self.usuario_jefe, ROL_OPERARIA))

    def test_superusuario_supera_comprobacion_de_rol(self):
        self.assertTrue(tiene_rol(self.superusuario, ROL_OPERARIA))

    def test_usuario_anonimo_no_tiene_rol(self):
        self.assertFalse(tiene_rol(AnonymousUser(), ROL_JEFE))

    def test_decorador_permite_rol_correcto(self):
        request = self.factory.get("/")
        request.user = self.usuario_jefe
        response = vista_solo_jefe(request)
        self.assertEqual(response.status_code, 200)

    def test_decorador_permite_superusuario(self):
        request = self.factory.get("/")
        request.user = self.superusuario
        response = vista_solo_jefe(request)
        self.assertEqual(response.status_code, 200)

    def test_decorador_rechaza_rol_no_permitido(self):
        request = self.factory.get("/")
        request.user = self.usuario_operaria
        with self.assertRaises(PermissionDenied):
            vista_solo_jefe(request)
