from django.test import TestCase
from django.urls import reverse

from apps.usuarios.models import Rol, Usuario
from .models import Caja
from .test_cajas import DatosCajas


class PermisosLotesCajasTests(DatosCajas, TestCase):
    def setUp(self):
        self.preparar_cajas()
        self.caja_registrada = self.caja()

    def test_roles_lotes_y_registro_de_cajas_independiente(self):
        for codigo in ("OPERARIA", "ENCARGADA", "JEFE"):
            usuario = Usuario.objects.create_user(username=f"ux_{codigo}", rol=Rol.objects.get(codigo=codigo))
            self.client.force_login(usuario)
            lotes = [self.url_lote("lista_lotes"), self.url_lote("detalle_lote", self.lote.pk),
                     self.url_lote("crear_lote", self.partida.pk), self.url_lote("agregar_producto_lote", self.lote.pk)]
            with self.subTest(rol=codigo):
                for url in lotes:
                    self.assertEqual(self.client.get(url).status_code, 403 if codigo == "OPERARIA" else 200)
                    if codigo == "OPERARIA":
                        self.assertEqual(self.client.post(url, {}).status_code, 403)
                selector = reverse("producto_terminado:seleccionar_lote_caja")
                self.assertContains(self.client.get(selector), self.lote.codigo_visible)
                self.assertRedirects(self.client.get(selector, {"lote": self.lote.pk}), self.url_lote("crear_caja", self.lote.pk))
                respuesta = self.client.post(self.url_lote("crear_caja", self.lote.pk), self.datos_caja(peso="20"))
                self.assertEqual(respuesta.status_code, 302)
                self.assertEqual(Caja.objects.latest("pk").registrado_por, usuario)
                if codigo == "OPERARIA":
                    for url in [reverse("usuarios:inicio"), self.url_lote("lista_cajas"),
                                self.url_lote("detalle_caja", self.caja_registrada.pk),
                                self.url_lote("crear_caja", self.lote.pk),
                                reverse("trazabilidad:detalle_partida", args=[self.partida.pk]),
                                reverse("producto_terminado:consulta_caja_qr", args=[self.caja_registrada.identificador_qr])]:
                        page = self.client.get(url)
                        for destino in lotes:
                            self.assertNotContains(page, f'href="{destino}"')

    def test_selector_no_escribe_y_rechaza_lote_fuera_de_opciones(self):
        self.client.force_login(self.usuario)
        url = reverse("producto_terminado:seleccionar_lote_caja")
        cantidad = Caja.objects.count()
        respuesta = self.client.get(url, {"lote": "999999999"})
        self.assertTrue(respuesta.context["form"].errors)
        self.assertEqual(Caja.objects.count(), cantidad)
        self.assertEqual(self.client.post(url, {"lote": self.lote.pk}).status_code, 405)
