from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from .models import Rol, Usuario


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
