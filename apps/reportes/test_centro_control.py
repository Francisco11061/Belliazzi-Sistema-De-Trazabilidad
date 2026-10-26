from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import urlsplit, parse_qs
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.inventario.models import DetalleDespacho, EstanciaCaja, Despacho
from apps.trazabilidad.models import (PartidaProceso, EventoProceso, TipoProceso, Pesaje,
    ConsumoLote, SolicitudRuta, EstanciaPartida)
from .selectors import dashboard_operacional
from .test_dashboard import DatosDashboard, AHORA


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class CentroControlTests(DatosDashboard, TestCase):
    def datos(self, **kwargs):
        return dashboard_operacional(ahora=AHORA, **kwargs)

    def proceso(self, partida, terminado=False):
        tipo = TipoProceso.objects.get(codigo="PROCESAMIENTO")
        EventoProceso.objects.create(partida=partida, tipo_proceso=tipo, fecha_hora_inicio=AHORA-timedelta(hours=4),
            fecha_hora_termino=AHORA-timedelta(hours=2) if terminado else None, iniciado_por=self.jefe)

    def test_estados_actuales_divisiones_y_enlaces_filtrados(self):
        padre = self.partida()
        padre.estado = "DIVIDIDA"
        padre.save()
        hijas = [PartidaProceso.objects.create(detalle_recepcion=padre.detalle_recepcion, partida_padre=padre,
            cantidad_inicial_kg=50, creado_por=self.jefe) for _ in range(2)]
        self.proceso(padre)
        self.proceso(hijas[0])
        datos = self.datos(desde=date(2000,1,1), hasta=date(2000,1,2))
        self.assertEqual(datos["procesamiento_activo"], 1)
        self.assertEqual(sum(f["cantidad"] for f in datos["produccion"]), 2)
        self.client.force_login(self.jefe)
        respuesta = self.client.get(datos["proceso_url"])
        self.assertEqual([p.pk for p in respuesta.context["page_obj"]], [hijas[0].pk])
        self.assertEqual(parse_qs(urlsplit(datos["proceso_url"]).query)["situacion"], ["EN_PROCESO"])

    def test_empaque_saldo_real_y_datos_ambiguos(self):
        p = self.partida()
        self.proceso(p, terminado=True)
        tunel = type(self.unidad).objects.create(nombre="Túnel prueba centro", tipo="TUNEL_CONGELADO")
        EstanciaPartida.objects.create(partida=p, unidad_frio=tunel, fecha_hora_ingreso=AHORA-timedelta(hours=2),
            fecha_hora_salida=AHORA-timedelta(hours=1), ingresado_por=self.jefe)
        Pesaje.objects.create(partida=p, tipo=Pesaje.POSTPROCESO, peso_kg=80, fecha_hora_evento=AHORA, registrado_por=self.jefe)
        ConsumoLote.objects.create(partida=p, lote_produccion=self.lote(), cantidad_kg_utilizada=30)
        datos = self.datos()
        self.assertEqual(datos["listos_empaque"], 1)
        self.assertEqual(datos["saldo_empaque"]["kg"], Decimal("50"))
        Pesaje.objects.create(partida=p, tipo=Pesaje.POSTPROCESO, peso_kg=90, fecha_hora_evento=AHORA, registrado_por=self.jefe)
        datos = self.datos()
        self.assertIsNone(datos["saldo_empaque"]["kg"])
        self.assertEqual(datos["postproceso"]["incompletos"], 1)
        self.assertIsNone(datos["postproceso"]["rendimiento"])

    def test_ranking_peso_real_periodo_especie_y_bajas_excluidas(self):
        primero = self.despacho(("12.50", None))
        segundo = self.despacho(("8.00",))
        lote = segundo.detalles.get().caja.lote_produccion
        lote.especie = self.otra_especie
        lote.save()
        self.baja("99")
        self.despacho(("500",), fecha=AHORA-timedelta(days=40))
        datos = self.datos(especie=self.especie)
        self.assertEqual(len(datos["ranking"]), 1)
        self.assertEqual(datos["ranking"][0]["peso_conocido"], Decimal("12.50"))
        self.assertEqual(datos["ranking"][0]["sin_peso"], 1)
        self.client.force_login(self.jefe)
        respuesta = self.client.get(datos["ranking"][0]["url"])
        self.assertEqual([d.pk for d in respuesta.context["page_obj"]], [primero.pk])

    def test_stock_camaras_fechas_no_cambian_estado_actual(self):
        self.caja("12", almacenada=True)
        self.caja(None, almacenada=True)
        self.caja("400")
        terminal = self.caja("500", almacenada=True)
        d = Despacho.objects.create(fecha_hora_despacho=AHORA, registrado_por=self.jefe)
        DetalleDespacho.objects.create(despacho=d, caja=terminal)
        self.umbral("0.5")
        datos = self.datos(desde=date(2000,1,1), hasta=date(2000,1,2))
        self.assertEqual(datos["stock"]["peso_conocido"], Decimal("12"))
        self.assertEqual(datos["camaras"][0]["cajas"], 2)
        self.assertEqual(datos["frio"]["total"], 2)
        self.assertEqual(datos["kg_periodo"], 0)
        self.assertEqual(datos["asuntos"], 4)  # Dos permanencias, un pendiente y una falta de peso.

    def test_top_cinco_y_otras_con_desglose_completo(self):
        for i in range(8):
            especie = type(self.especie).objects.create(nombre=f"Especie adicional {i}")
            caja = self.caja(str(i+1), almacenada=True, especie=especie)
            despacho = self.despacho((str(i+1),))
            lote = despacho.detalles.get().caja.lote_produccion
            lote.especie = especie
            lote.save()
        datos = self.datos()
        self.assertEqual([r["peso_conocido"] for r in datos["ranking"]], list(map(Decimal, [8,7,6,5,4])))
        self.assertEqual(len(datos["especies"]), 8)
        self.assertEqual(datos["graficos"]["especies"]["labels"][-1], "Otras")
        self.assertEqual(sum(datos["graficos"]["especies"]["valores"]), Decimal("36"))

    def test_despacho_mixto_enlace_muestra_totales_especie_filtrada(self):
        despacho = self.despacho(("12",))
        otra = self.caja("20", especie=self.otra_especie)
        DetalleDespacho.objects.create(despacho=despacho, caja=otra)
        self.client.force_login(self.jefe)
        respuesta = self.client.get(self.datos(especie=self.especie)["despachos_url"])
        fila = list(respuesta.context["page_obj"])[0]
        self.assertEqual(fila.peso_conocido, Decimal("12"))
        self.assertEqual(fila.cantidad_cajas, 1)

    def test_rendimiento_mismos_seguimientos_excluye_padre(self):
        padre = self.partida()
        hija = PartidaProceso.objects.create(detalle_recepcion=padre.detalle_recepcion, partida_padre=padre,
            cantidad_inicial_kg=50, creado_por=self.jefe)
        for p, peso in ((padre, 90), (hija, 40)):
            Pesaje.objects.create(partida=p, tipo=Pesaje.POSTPROCESO, peso_kg=peso, fecha_hora_evento=AHORA, registrado_por=self.jefe)
        datos = self.datos()["postproceso"]
        self.assertEqual(datos["inicial"], Decimal("50"))
        self.assertEqual(datos["final"], Decimal("40"))
        self.assertEqual(datos["rendimiento"], Decimal("80"))

    @override_settings(DASHBOARD_VENCIMIENTOS_DIAS=10)
    def test_vencimientos_stock_parcial_fechas_y_sin_fecha(self):
        for dias in (-1, 0, 10, 11, None):
            caja = self.caja(almacenada=True)
            lote = caja.lote_produccion
            lote.fecha_elaboracion = AHORA.date()-timedelta(days=40)
            lote.fecha_vencimiento = AHORA.date()+timedelta(days=dias) if dias is not None else None
            lote.save()
        datos = self.datos()
        self.assertEqual(datos["vencimientos_resumen"], dict(vencidos=1, proximos=2, sin_fecha=1))
        self.assertEqual(len(datos["vencimientos"]), 4)
        self.assertEqual(datos["ventana_vencimientos"], 10)
        caja = self.caja(almacenada=False)
        self.assertNotIn(caja.lote_produccion_id, [f["lote_produccion_id"] for f in self.datos()["vencimientos"]])

    def test_antiguedad_ingreso_actual_no_armado(self):
        vieja = self.caja(almacenada=True, ingreso=AHORA-timedelta(days=10))
        nueva = self.caja(almacenada=True)
        type(nueva).objects.filter(pk=nueva.pk).update(fecha_armado=AHORA-timedelta(days=100))
        self.assertEqual([c.pk for c in self.datos()["antiguos"]], [vieja.pk, nueva.pk])

    def test_solicitudes_pendientes_respetan_especie(self):
        SolicitudRuta.objects.create(especie=self.especie, solicitante=self.operaria)
        SolicitudRuta.objects.create(especie=self.otra_especie, solicitante=self.operaria)
        datos = self.datos(especie=self.especie)
        self.assertEqual(datos["solicitudes_ruta_pendientes"], 1)
        self.client.force_login(self.jefe)
        respuesta = self.client.get(datos["pendientes_control"][-1]["url"])
        self.assertEqual(respuesta.context["page_obj"].paginator.count, 1)

    def test_enlace_inventario_sin_peso(self):
        vacia = self.caja(None, almacenada=True)
        self.caja("12", almacenada=True)
        self.caja(None)
        self.client.force_login(self.jefe)
        respuesta = self.client.get(self.datos()["pendientes_control"][2]["url"])
        self.assertEqual([c.pk for c in respuesta.context["page_obj"]], [vacia.pk])

    def test_sin_datos_y_graficos_sin_peso_no_inventan_ceros(self):
        self.caja(None, almacenada=True)
        self.client.force_login(self.jefe)
        respuesta = self.client.get(reverse("reportes:dashboard"))
        self.assertContains(respuesta, "No hay peso neto documentado para graficar.")
        self.assertNotContains(respuesta, 'id="especies-chart"')
        self.assertContains(respuesta, "Sin peso")

    def test_consultas_constantes_y_respuesta_completa(self):
        self.umbral("1")
        self.caja(almacenada=True)
        self.client.force_login(self.jefe)
        with CaptureQueriesContext(connection) as antes:
            self.client.get(reverse("reportes:dashboard"))
        for _ in range(12):
            self.caja(almacenada=True)
            self.partida()
        with CaptureQueriesContext(connection) as despues:
            respuesta = self.client.get(reverse("reportes:dashboard"))
        self.assertEqual(len(antes), len(despues))
        self.assertContains(respuesta, "Top 5 de especies despachadas")

    def test_jefe_entrada_next_y_cambio_obligatorio(self):
        self.jefe.set_password("Prueba-segura-789")
        self.jefe.save()
        login = reverse("usuarios:login")
        respuesta = self.client.post(login, {"username": self.jefe.username, "password": "Prueba-segura-789"})
        self.assertRedirects(respuesta, reverse("reportes:dashboard"), fetch_redirect_response=False)
        self.assertRedirects(self.client.get(reverse("usuarios:inicio")), reverse("reportes:dashboard"), fetch_redirect_response=False)
        self.client.logout()
        destino = reverse("trazabilidad:consulta_integral")
        respuesta = self.client.post(login, {"username": self.jefe.username, "password": "Prueba-segura-789", "next": destino})
        self.assertRedirects(respuesta, destino, fetch_redirect_response=False)
        self.client.logout()
        self.jefe.cambio_contrasena_pendiente = True
        self.jefe.save()
        respuesta = self.client.post(login, {"username": self.jefe.username, "password": "Prueba-segura-789", "next": destino})
        self.assertRedirects(respuesta, reverse("usuarios:cambiar_contrasena"), fetch_redirect_response=False)

    def test_personal_conserva_inicio_y_no_dashboard(self):
        for persona in (self.encargada, self.operaria):
            self.client.force_login(persona)
            self.assertEqual(self.client.get(reverse("usuarios:inicio")).status_code, 200)
            self.assertEqual(self.client.get(reverse("reportes:dashboard")).status_code, 403)

    def test_preview_con_datos_reales_de_prueba(self):
        self.umbral("24")
        for peso, especie in (("18.75", self.especie), ("24.50", self.otra_especie), (None, self.especie)):
            self.caja(peso, almacenada=True, especie=especie, ingreso=AHORA-timedelta(days=3))
        self.caja()
        self.despacho(("22", "18.5"))
        for i, nombre in enumerate(("Salmón", "Reineta", "Congrio dorado", "Jibia", "Merluza austral", "Bacalao de profundidad")):
            especie = type(self.especie).objects.create(nombre=nombre)
            self.caja(str(10+i), almacenada=True, especie=especie)
            despacho = self.despacho((str(10+i),))
            lote = despacho.detalles.get().caja.lote_produccion
            lote.especie = especie
            lote.save()
        p = self.partida()
        self.proceso(p)
        self.client.force_login(self.jefe)
        with patch("apps.reportes.views.timezone.now", return_value=AHORA):
            respuesta = self.client.get(reverse("reportes:dashboard"))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'id="dashboard-datos"')
