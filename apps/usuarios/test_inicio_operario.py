from django.test import TestCase
from django.urls import reverse

from .forms import CrearUsuarioForm, RolUsuarioForm
from .models import Rol, Usuario


class InicioOperarioTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.operario = Usuario.objects.create_user(username="operario_inicio", first_name="Francisco", rol=Rol.objects.get(codigo="OPERARIA"))
        cls.jefe = Usuario.objects.create_user(username="jefe_inicio", rol=Rol.objects.get(codigo="JEFE"))
        cls.encargada = Usuario.objects.create_user(username="encargada_inicio", rol=Rol.objects.get(codigo="ENCARGADA"))
        cls.tecnico = Usuario.objects.create_superuser(username="tecnico_inicio")

    def test_inicio_operario_saluda_y_ofrece_lo_que_puede_hacer(self):
        self.client.force_login(self.operario)
        response = self.client.get(reverse("usuarios:inicio"))
        self.assertContains(response, "Hola, Francisco")
        self.assertContains(response, "Nada pendiente en planta")
        for destino in ("producto_terminado:seleccionar_lote_caja", "trazabilidad:lista_partidas"):
            self.assertContains(response, f'href="{reverse(destino)}"')
            self.assertEqual(self.client.get(reverse(destino)).status_code, 200)
        self.assertNotContains(response, f'href="{reverse("trazabilidad:nueva_recepcion")}"')

    def test_sidebar_sin_enlaces_administrativos_ni_despachos(self):
        self.client.force_login(self.operario)
        response = self.client.get(reverse("usuarios:inicio"))
        for destino in ("inventario:lista_despachos", "trazabilidad:lista_recepciones", "usuarios:lista_usuarios",
                        "reportes:dashboard", "reportes:sernapesca", "reportes:umbrales"):
            self.assertNotContains(response, f'href="{reverse(destino)}"')

    def test_rol_visible_sin_cambiar_codigo_ni_nombre_persistido(self):
        rol = self.operario.rol
        original = rol.nombre
        self.client.force_login(self.operario)
        response = self.client.get(reverse("usuarios:inicio"))
        self.assertContains(response, "Operario")
        self.assertNotContains(response, "Operaria")
        self.assertEqual(str(rol), "Operario")
        rol.refresh_from_db()
        self.assertEqual(rol.nombre, original)
        self.assertEqual(rol.codigo, "OPERARIA")

    def test_etiquetas_de_rol_en_listados_perfil_y_formularios(self):
        self.client.force_login(self.jefe)
        for ruta in (reverse("usuarios:lista_usuarios"), reverse("usuarios:detalle_usuario", args=[self.operario.pk])):
            response = self.client.get(ruta)
            self.assertContains(response, "Operario")
            self.assertNotContains(response, "Operaria")
        for formulario in (CrearUsuarioForm(), RolUsuarioForm()):
            opciones = dict((str(valor), etiqueta) for valor, etiqueta in formulario.fields["rol"].choices)
            self.assertEqual(opciones[str(self.operario.rol_id)], "Operario")

    def test_inicio_otros_roles_conserva_su_contenido(self):
        for usuario in (self.jefe, self.encargada, self.tecnico):
            self.client.force_login(usuario)
            response = self.client.get(reverse("usuarios:inicio"))
            if usuario != self.encargada:
                self.assertRedirects(response, reverse("reportes:dashboard"))
                continue
            self.assertContains(response, "Registrar recepción")
            for destino in ("trazabilidad:nueva_recepcion", "inventario:crear_despacho", "inventario:lista_despachos"):
                self.assertContains(response, f'href="{reverse(destino)}"')
        self.assertEqual(self.encargada.rol.nombre_visible, self.encargada.rol.nombre)
        self.assertEqual(self.jefe.rol.nombre_visible, self.jefe.rol.nombre)
