from datetime import timedelta
from types import SimpleNamespace

from django.contrib.auth.models import AnonymousUser
from django.template import Context, Template
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.inventario.models import EstanciaCaja
from apps.usuarios.models import Usuario

from .avisos import avisos_para
from .test_dashboard import AHORA, DatosDashboard

CAMPANA = Template("{% load navegacion %}{% campana %}")


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class AvisosTests(DatosDashboard, TestCase):
    def titulos(self, usuario, **kwargs):
        return [a["titulo"] for a in avisos_para(usuario, ahora=AHORA, **kwargs)["avisos"]]

    def por_vencer(self, dias):
        caja = self.caja(almacenada=True)
        caja.lote_produccion.fecha_vencimiento = timezone.localdate() + timedelta(days=dias)
        caja.lote_produccion.save()
        return caja

    def renderizar(self, usuario):
        request = RequestFactory().get("/")
        request.user = usuario
        request.resolver_match = SimpleNamespace(view_name="usuarios:inicio")
        return CAMPANA.render(Context({"request": request})), request

    def test_cada_rol_ve_solo_lo_que_le_toca(self):
        self.umbral("24.00")
        self.caja(almacenada=True, ingreso=AHORA - timedelta(hours=40))
        self.por_vencer(5)
        jefe = self.titulos(self.jefe)
        self.assertEqual(len(jefe), 2)
        self.assertIn("Merluza revisión: pasó el tiempo recomendado", jefe)
        self.assertTrue(any("vence el" in t for t in jefe))
        self.assertEqual(self.titulos(self.encargada), jefe)
        self.assertEqual(self.titulos(self.operaria), [])
        self.assertTrue(avisos_para(self.jefe, ahora=AHORA)["urgente"])

    def test_vencimientos_urgencia_orden_y_ver_mas(self):
        self.por_vencer(400)
        self.caja(almacenada=True)  # Sin fecha: se revisa en el dashboard, no en la campana.
        sin_guardar = self.caja()
        sin_guardar.lote_produccion.fecha_vencimiento = timezone.localdate()
        sin_guardar.lote_produccion.save()
        self.assertEqual(self.titulos(self.encargada), [])
        for dias in (3, 10, 20, 1, 6):
            self.por_vencer(dias)
        datos = avisos_para(self.encargada, ahora=AHORA)
        self.assertFalse(datos["urgente"])
        self.por_vencer(-2)
        datos = avisos_para(self.encargada, ahora=AHORA)
        self.assertTrue(datos["urgente"])
        self.assertEqual(datos["total"], 6)
        self.assertIn("venció el", datos["avisos"][0]["titulo"])
        self.assertEqual(datos["avisos"][0]["nivel"], "urgente")
        self.assertEqual(datos["avisos"][-1]["titulo"], "Ver 1 más")
        self.assertEqual(datos["avisos"][-1]["url"], reverse("inventario:lista") + "?estado=ALMACENADA")
        self.assertTrue(avisos_para(self.jefe, ahora=AHORA)["avisos"][-1]["url"].endswith("#vencimientos"))

    def test_campana_en_la_cabecera_usa_los_conteos_del_menu(self):
        caja = self.caja(almacenada=True)
        EstanciaCaja.objects.create(caja=caja, unidad_frio=self.mantencion, fecha_hora_ingreso=AHORA, ingresado_por=self.jefe)
        html, request = self.renderizar(self.encargada)
        self.assertIn(f"Caja #{caja.pk}: revisar ubicación", html)
        self.assertIn('aria-label="Avisos: 1"', html)
        self.assertIn("bg-cyan-700", html)
        with self.assertNumQueries(0):
            CAMPANA.render(Context({"request": request}))
        EstanciaCaja.objects.filter(caja=caja).delete()
        html, _ = self.renderizar(self.jefe)
        self.assertIn('aria-label="Avisos: ninguno"', html)
        self.assertIn("No hay avisos.", html)
        # La operaria no tiene campana: lo suyo está en su inicio.
        for usuario in (self.operaria, AnonymousUser(), Usuario(username="sin_rol_campana")):
            self.assertEqual(self.renderizar(usuario)[0].strip(), "")

    def test_numero_grande_y_rojo_si_hay_algo_urgente(self):
        self.umbral("24.00")
        for _ in range(12):
            self.caja(almacenada=True, ingreso=timezone.now() - timedelta(hours=40))
        html, _ = self.renderizar(self.jefe)
        self.assertIn(">9+</span>", html)
        self.assertIn("bg-red-600", html)
        self.assertIn("Ver 7 más", html)
        self.assertIn('aria-label="Avisos: 12"', html)

    def test_pagina_completa_muestra_la_campana(self):
        self.client.force_login(self.encargada)
        self.assertContains(self.client.get(reverse("usuarios:inicio")), "data-campana")
        self.client.force_login(self.operaria)
        self.assertNotContains(self.client.get(reverse("usuarios:inicio")), "data-campana")
