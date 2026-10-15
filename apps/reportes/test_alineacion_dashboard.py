from datetime import date, timedelta
from decimal import Decimal

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.trazabilidad.models import EventoProceso, EstanciaPartida, MermaProceso, Pesaje, TipoProceso
from .selectors import dashboard_operacional, periodo_libre
from .test_dashboard import AHORA, DatosDashboard


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class AlineacionDashboardTests(DatosDashboard, TestCase):
    def datos(self, **kwargs):
        return dashboard_operacional(ahora=AHORA, desde=AHORA.date(), hasta=AHORA.date(), **kwargs)

    def test_recibido_postproceso_y_cajas_no_duplican_pesos(self):
        partida = self.partida()
        Pesaje.objects.create(partida=partida, tipo=Pesaje.POSTPROCESO, peso_kg=110,
                              fecha_hora_evento=AHORA, registrado_por=self.jefe)
        Pesaje.objects.create(partida=partida, tipo=Pesaje.POSTPROCESO, peso_kg=999,
                              fecha_hora_evento=AHORA + timedelta(seconds=1), registrado_por=self.jefe)
        self.caja("7.50")
        self.caja(None)
        datos = self.datos()
        self.assertEqual(datos["recibido"], dict(recepciones=1, kg=Decimal("100")))
        self.assertEqual(datos["postproceso"], dict(registros=1, inicial=Decimal("100"), final=Decimal("110"),
                                                   superiores=1, rendimiento=Decimal("110.00")))
        self.assertEqual(datos["producidas"], dict(cajas=2, peso_conocido=Decimal("7.50"), sin_peso=1))
        self.assertEqual(datos["colores"]["post"]["estado"], "REVISION")
        self.assertEqual(self.datos(especie=self.otra_especie)["postproceso"]["registros"], 0)

    def test_especie_aplica_a_indicadores_actuales_periodo_y_alertas(self):
        self.umbral("1")
        self.caja("7", almacenada=True, ingreso=AHORA-timedelta(hours=2))
        self.caja("3", almacenada=True, especie=self.otra_especie)
        self.caja("2")
        self.despacho()
        self.baja()
        MermaProceso.objects.create(partida=self.partida(), tipo="MERMA", cantidad_kg=2, motivo="Prueba",
                                    fecha_hora_evento=AHORA, registrado_por=self.jefe)
        datos = self.datos(especie=self.otra_especie)
        self.assertEqual(datos["stock"]["peso_conocido"], Decimal("3"))
        for valor in (datos["pendientes"], datos["frio"]["total"], datos["recibido"]["recepciones"],
                      datos["cajas_periodo"], datos["bajas"]["cajas"], sum(m["kg"] for m in datos["mermas"])):
            self.assertEqual(valor, 0)
        self.assertEqual(datos["producidas"]["cajas"], 1)

    def test_limites_chilenos_inclusivos_y_stock_independiente(self):
        periodo = periodo_libre(date(2026, 9, 30), date(2026, 9, 30))
        self.despacho(("1",), periodo["inicio"])
        self.despacho(("2",), periodo["fin"]-timedelta(microseconds=1))
        self.despacho(("9",), periodo["fin"])
        self.despacho(("9",), periodo["inicio"]-timedelta(microseconds=1))
        self.caja("5", almacenada=True)
        self.assertEqual(self.datos()["kg_periodo"], Decimal("3"))
        otro = dashboard_operacional(ahora=AHORA, desde=date(2000,1,1), hasta=date(2000,1,1))
        self.assertEqual(otro["stock"]["peso_conocido"], Decimal("5"))
        self.assertEqual(otro["kg_periodo"], 0)

    def test_indicadores_fuera_del_rango_y_sin_datos(self):
        partida = self.partida()
        Pesaje.objects.create(partida=partida, tipo=Pesaje.POSTPROCESO, peso_kg=90,
                              fecha_hora_evento=AHORA, registrado_por=self.jefe)
        MermaProceso.objects.create(partida=partida, tipo="DESCARTE", cantidad_kg=2,
            fecha_hora_evento=AHORA, registrado_por=self.jefe, motivo="Prueba")
        self.caja("4")
        self.baja()
        self.despacho()
        datos = dashboard_operacional(ahora=AHORA, desde=date(2000,1,1), hasta=date(2000,1,2))
        for grupo, campos in (("recibido", ("recepciones", "kg")), ("postproceso", ("registros", "inicial", "final", "superiores")),
                              ("producidas", ("cajas", "peso_conocido", "sin_peso")), ("bajas", ("cajas", "peso_conocido", "sin_peso"))):
            for campo in campos:
                self.assertEqual(datos[grupo][campo], 0, (grupo, campo))
        self.assertEqual(sum(m["kg"] for m in datos["mermas"]), 0)
        self.assertEqual(datos["cajas_periodo"], 0)
        self.assertTrue(all(t["registros"] == 0 for t in datos["tiempos"]))

    def test_tiempos_solo_completos_por_fecha_termino(self):
        partida = self.partida()
        for duracion in (2, 4):
            EstanciaPartida.objects.create(partida=partida, unidad_frio=self.mantencion,
                fecha_hora_ingreso=AHORA-timedelta(hours=duracion), fecha_hora_salida=AHORA, ingresado_por=self.jefe)
        EstanciaPartida.objects.create(partida=partida, unidad_frio=self.mantencion,
            fecha_hora_ingreso=AHORA-timedelta(hours=50), ingresado_por=self.jefe)
        tipo = TipoProceso.objects.create(codigo="PRUEBA_TIEMPO", nombre="Fileteo de prueba")
        EventoProceso.objects.create(partida=partida, tipo_proceso=tipo, fecha_hora_inicio=AHORA-timedelta(hours=1),
            fecha_hora_termino=AHORA, iniciado_por=self.jefe)
        filas = self.datos()["tiempos"]
        self.assertEqual(filas[0]["promedio"], timedelta(hours=3))
        self.assertEqual(filas[0]["registros"], 2)
        self.assertEqual(filas[1]["promedio"], timedelta(hours=1))
        self.assertEqual(filas[-1]["registros"], 0)

    def test_semaforo_vacio_no_configurado_pendientes_mermas_y_criticas(self):
        datos = self.datos()
        self.assertEqual(datos["colores"]["operacion"]["estado"], "REVISION")
        for nombre in ("pendientes", "sin_peso", "mermas", "bajas", "post"):
            self.assertEqual(datos["colores"][nombre]["estado"], "COMPLETADA")
        self.assertIsNone(datos["postproceso"]["rendimiento"])
        self.umbral("10")
        self.assertEqual(self.datos()["colores"]["operacion"]["estado"], "COMPLETADA")
        self.caja()
        self.caja(None, almacenada=True)
        self.baja()
        MermaProceso.objects.create(partida=self.partida(), tipo="PERDIDA", cantidad_kg=1, motivo="Prueba",
                                    fecha_hora_evento=AHORA, registrado_por=self.jefe)
        datos = self.datos()
        for nombre in ("pendientes", "sin_peso", "mermas", "bajas"):
            self.assertEqual(datos["colores"][nombre]["estado"], "REVISION")
        self.caja(almacenada=True, ingreso=AHORA-timedelta(hours=11))
        self.assertEqual(self.datos()["colores"]["operacion"]["estado"], "BAJA")

    def test_frio_no_inventa_datos_al_80_por_ciento(self):
        self.umbral("10")
        for horas in (7, 8, 10):
            self.caja(almacenada=True, ingreso=AHORA-timedelta(hours=horas))
        datos = self.datos()
        self.assertEqual(datos["frio"]["total"], 0)
        self.assertEqual(datos["frio"]["elementos"], [])

    def test_formulario_rango_invalido_y_filtros_persistentes(self):
        self.client.force_login(self.jefe)
        respuesta = self.client.get(reverse("reportes:dashboard"), dict(desde="2026-09-30", hasta="2026-09-01", especie=self.especie.pk))
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn("hasta", respuesta.context["form"].errors)
        self.assertEqual(respuesta.context["periodo"]["valor"], "semana")
        self.assertContains(respuesta, 'value="2026-09-30"')
        self.assertContains(respuesta, f"especie={self.especie.pk}")
        self.assertIn("no-store", respuesta.headers["Cache-Control"])

    def test_consultas_no_crecen_con_registros(self):
        self.umbral()
        self.partida()
        with CaptureQueriesContext(connection) as antes:
            self.datos(especie=self.especie)
        for _ in range(5):
            self.partida()
            self.caja(almacenada=True)
        with CaptureQueriesContext(connection) as despues:
            self.datos(especie=self.especie)
        self.assertEqual(len(antes), len(despues))
