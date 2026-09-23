from django.db import IntegrityError, transaction
from django.test import TestCase

from .models import Usuario


class UsuarioIntegridadTests(TestCase):
    def test_usuario_normal_sin_rol_falla(self):
        with self.assertRaisesRegex(IntegrityError, "usuario_normal_debe_tener_rol"):
            with transaction.atomic():
                Usuario.objects.create_user(
                    username="usuario_sin_rol",
                    password="clave-solo-para-pruebas",
                    is_superuser=False,
                )
