from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.apps import apps
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.inventario.models import BajaCaja, Despacho, DetalleDespacho, EstanciaCaja
from apps.inventario.selectors import cajas_con_inventario, resumen_inventario
from apps.trazabilidad.models import (Caja, DetalleRecepcion, Especie, EstanciaPartida, LoteProduccion,
                                     MermaProceso, OrigenSernapesca, PartidaProceso, Recepcion, UnidadFrio)
from apps.usuarios.models import Rol, Usuario
from .selectors import (actividad_reciente, alertas_frio, dashboard_operacional, despachos_del_dia,
                        mermas_y_bajas, periodo_actual, resumen_stock, serie_despachos)
from .services import configurar_umbral


AHORA = datetime(2026, 9, 30, 15, 0, tzinfo=dt_timezone.utc)


class DatosDashboard:
    @classmethod
    def setUpTestData(cls):
        cls.jefe = Usuario.objects.create_user(username="jefe_dashboard", rol=Rol.objects.get(codigo="JEFE"))
        cls.encargada = Usuario.objects.create_user(username="encargada_dashboard", rol=Rol.objects.get(codigo="ENCARGADA"))
        cls.operaria = Usuario.objects.create_user(username="operaria_dashboard", rol=Rol.objects.get(codigo="OPERARIA"))
        cls.tecnico = Usuario.objects.create_superuser(username="tecnico_dashboard", password="Prueba-89!segura")
        cls.especie = Especie.objects.create(nombre="Merluza revisión")
        cls.otra_especie = Especie.objects.create(nombre="Congrio revisión")
        cls.unidad = UnidadFrio.objects.create(nombre="Unidad revisión", tipo="ALMACENAMIENTO")
        cls.mantencion = UnidadFrio.objects.create(nombre="Mantención revisión", tipo="MANTENCION")

    def lote(self, especie=None):
        return LoteProduccion.objects.create(codigo_lote=f"LOTE-{LoteProduccion.objects.count() + 1}",
                                            especie=especie or self.especie, fecha_elaboracion=AHORA.date(),
                                            registrado_por=self.jefe)

    def caja(self, peso="10.00", almacenada=False, especie=None, unidad=None, ingreso=None):
        caja = Caja.objects.create(lote_produccion=self.lote(especie), peso_neto_kg=peso,
                                   peso_total_kg=Decimal("999.00"), fecha_armado=AHORA, registrado_por=self.jefe)
        if almacenada:
            EstanciaCaja.objects.create(caja=caja, unidad_frio=unidad or self.unidad,
                                        fecha_hora_ingreso=ingreso or AHORA - timedelta(hours=1), ingresado_por=self.jefe)
        return caja

    def despacho(self, pesos=("24.50",), fecha=AHORA):
        despacho = Despacho.objects.create(fecha_hora_despacho=fecha, registrado_por=self.jefe)
        for peso in pesos:
            DetalleDespacho.objects.create(despacho=despacho, caja=self.caja(peso))
        return despacho

    def baja(self, peso="5.00", fecha=AHORA):
        return BajaCaja.objects.create(caja=self.caja(peso), tipo="DANO", motivo="Prueba",
                                      fecha_hora_evento=fecha, registrado_por=self.jefe)

    def partida(self):
        recepcion = Recepcion.objects.create(fecha_hora_recepcion=AHORA, registrado_por=self.jefe)
        origen = OrigenSernapesca.objects.create(folio_origen="PRUEBA")
        detalle = DetalleRecepcion.objects.create(recepcion=recepcion, especie=self.especie,
                                                 origen_sernapesca=origen, peso_origen_kg=100, peso_recepcion_kg=100)
        return PartidaProceso.objects.create(detalle_recepcion=detalle, cantidad_inicial_kg=100, creado_por=self.jefe)

    def umbral(self, horas="24.00", unidad=None):
        return configurar_umbral(usuario=self.jefe, unidad=unidad or self.unidad,
                                 horas=Decimal(horas) if horas is not None else None)

    def url(self, nombre="dashboard"):
        return reverse("reportes:" + nombre)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class DashboardPermisosTests(DatosDashboard, TestCase):
    def test_acceso_por_roles_y_sidebar(self):
        for usuario, permitido in ((self.jefe, True), (self.tecnico, True),
                                   (self.encargada, False), (self.operaria, False)):
            with self.subTest(usuario=usuario.username):
                self.client.force_login(usuario)
                for nombre in ("dashboard", "umbrales"):
                    self.assertEqual(self.client.get(self.url(nombre)).status_code, 200 if permitido else 403)
                inicio = self.client.get(reverse("usuarios:inicio"))
                enlace = f'href="{self.url()}"'
                if permitido:
                    self.assertContains(inicio, enlace)
                else:
                    self.assertNotContains(inicio, enlace)

    def test_anonimo_redirigido_a_login(self):
        self.assertEqual(self.client.get(self.url()).status_code, 302)

    def test_dashboard_es_solo_lectura_por_get(self):
        self.client.force_login(self.jefe)
        self.assertEqual(self.client.post(self.url()).status_code, 405)

    def test_pendiente_de_contrasena_no_accede(self):
        self.jefe.cambio_contrasena_pendiente = True
        self.jefe.save()
        self.client.force_login(self.jefe)
        self.assertRedirects(self.client.get(self.url()), reverse("usuarios:cambiar_contrasena"))

    def test_superusuario_sin_rol(self):
        self.client.force_login(self.tecnico)
        self.assertIsNone(self.tecnico.rol)
        self.assertContains(self.client.get(self.url()), "Dashboard operacional")

    def test_estados_vacios(self):
        self.client.force_login(self.jefe)
        response = self.client.get(self.url())
        for mensaje in ("Aún no hay despachos en este período.", "No hay cajas almacenadas actualmente.",
                        "Sin alertas operativas en este momento.", "Aún no hay actividad reciente.",
                        "No hay umbrales de frío configurados."):
            self.assertContains(response, mensaje)
        self.assertNotContains(response, '<canvas')

    def test_periodo_backend_sin_javascript_y_fallback(self):
        self.client.force_login(self.jefe)
        for enviado, esperado in (("semana", "semana"), ("mes", "mes"), ("ano", "ano"), ("invalido", "semana")):
            response = self.client.get(self.url(), {"periodo": enviado})
            self.assertEqual(response.context["periodo"]["valor"], esperado)
            self.assertContains(response, f'?periodo={esperado}')

    def test_json_script_escapa_nombres_maliciosos(self):
        self.especie.nombre = '</script><script>alert(1)</script>'
        self.especie.save()
        self.caja(almacenada=True)
        self.client.force_login(self.jefe)
        response = self.client.get(self.url())
        self.assertNotContains(response, self.especie.nombre)
        self.assertContains(response, r'\u003C/script\u003E')
        self.assertContains(response, 'type="application/json"')
        self.assertContains(response, 'Ver datos por especie')


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class DashboardMetricasTests(DatosDashboard, TestCase):
    def test_kpi_coinciden_con_inventario(self):
        self.caja("24.32", almacenada=True)
        self.caja(None, almacenada=True)
        self.caja("2.00")
        self.despacho()
        self.baja()
        esperado = resumen_inventario(cajas_con_inventario())
        datos = dashboard_operacional(ahora=AHORA)
        self.assertEqual(datos["stock"], esperado["stock"])
        self.assertEqual(datos["pendientes"], esperado["pendientes"])
        self.assertEqual(datos["stock"], {"cajas": 2, "peso_conocido": Decimal("24.32"), "sin_peso": 1})
        self.assertEqual(datos["pendientes"], 1)
        self.assertEqual(datos["despachos_hoy"]["despachos"], 1)
        self.assertEqual(datos["despachos_hoy"]["peso_conocido"], Decimal("24.50"))

    def test_terminales_con_estancia_abierta_historica_no_son_stock_ni_pendientes(self):
        for salida in ("despacho", "baja"):
            caja = self.caja(almacenada=True)
            if salida == "despacho":
                despacho = Despacho.objects.create(fecha_hora_despacho=AHORA, registrado_por=self.jefe)
                DetalleDespacho.objects.create(despacho=despacho, caja=caja)
            else:
                BajaCaja.objects.create(caja=caja, motivo="Prueba", fecha_hora_evento=AHORA, registrado_por=self.jefe)
        datos = dashboard_operacional(ahora=AHORA)
        self.assertEqual(datos["stock"]["cajas"], 0)
        self.assertEqual(datos["pendientes"], 0)

    def test_especies_usan_peso_real_no_nominal_y_excluyen_no_almacenadas(self):
        self.caja("12.30", almacenada=True)
        self.caja("5.20", almacenada=True)
        self.caja(None, almacenada=True)
        self.caja("8.15", almacenada=True, especie=self.otra_especie)
        self.caja("100.00")
        datos = dashboard_operacional(ahora=AHORA)
        self.assertEqual([f["peso_conocido"] for f in datos["especies"]], [Decimal("17.50"), Decimal("8.15")])
        self.assertEqual(datos["especies"][0]["sin_peso"], 1)
        self.assertEqual(datos["stock"]["peso_conocido"], Decimal("25.65"))

    def test_stock_solo_peso_desconocido(self):
        self.caja(None, almacenada=True)
        datos = dashboard_operacional(ahora=AHORA)
        self.assertEqual(datos["stock"]["peso_conocido"], Decimal("0.00"))
        self.assertEqual(datos["stock"]["sin_peso"], 1)
        self.assertEqual(datos["graficos"]["especies"]["valores"], [Decimal("0.00")])

    def test_selector_antiguo_reutiliza_estado_oficial_y_peso_real(self):
        self.caja("8.40", almacenada=True)
        self.caja(None, almacenada=True)
        self.caja("99.00", almacenada=True, unidad=self.mantencion)
        self.assertEqual(resumen_stock(), dict(total_cajas=2, peso_total_kg=Decimal("8.40"), sin_peso=1))

    def test_dos_estancias_no_multiplican_stock(self):
        caja = self.caja("12.00", almacenada=True)
        EstanciaCaja.objects.create(caja=caja, unidad_frio=self.unidad, fecha_hora_ingreso=AHORA, ingresado_por=self.jefe)
        self.assertEqual(dashboard_operacional(ahora=AHORA)["stock"]["cajas"], 0)

    def test_despachos_hoy_limites_chilenos_y_pesos_null(self):
        # Chile: 03:00 UTC es medianoche en esta fecha, no 00:00 UTC.
        self.despacho(("2.00",), AHORA.replace(hour=2, minute=59))
        self.despacho(("3.25", None), AHORA.replace(hour=3))
        self.despacho(("4.50",), (AHORA + timedelta(days=1)).replace(hour=2, minute=59))
        self.despacho(("99.00",), (AHORA + timedelta(days=1)).replace(hour=3))
        self.assertEqual(despachos_del_dia(AHORA), dict(despachos=2, cajas=3, peso_conocido=Decimal("7.75"), sin_peso=1))

    def test_series_semana_mes_ano_y_sumatoria(self):
        self.despacho(("11.25", "2.50", None), AHORA)
        for valor, cantidad in (("semana", 7), ("mes", 30), ("ano", 12)):
            serie = serie_despachos(periodo_actual(valor, AHORA))
            self.assertEqual(len(serie), cantidad)
            self.assertEqual(sum(f["peso_conocido"] for f in serie), Decimal("13.75"))
            self.assertEqual(sum(f["sin_peso"] for f in serie), 1)

    def test_serie_limites_inicio_incluido_fin_excluido(self):
        periodo = periodo_actual("semana", AHORA)
        self.despacho(("8.00",), periodo["inicio"])
        self.despacho(("100.00",), periodo["inicio"] - timedelta(microseconds=1))
        self.despacho(("200.00",), periodo["fin"])
        self.assertEqual(sum(f["peso_conocido"] for f in serie_despachos(periodo)), Decimal("8.00"))

    def test_periodos_vacios_ceros_decimales(self):
        for valor in ("semana", "mes", "ano", "otro"):
            self.assertTrue(all(f["peso_conocido"] == Decimal("0.00") for f in serie_despachos(periodo_actual(valor, AHORA))))

    def test_semana_cruza_ano_y_mes_bisiesto(self):
        ahora = datetime(2027, 1, 1, 12, tzinfo=dt_timezone.utc)
        periodo = periodo_actual("semana", ahora)
        self.assertEqual(periodo["desde"], date(2026, 12, 28))
        self.assertEqual(periodo["hasta"], date(2027, 1, 3))
        self.assertEqual(len(periodo_actual("mes", ahora.replace(year=2028, month=2))["segmentos"]), 29)

    def test_cambio_horario_chile_agrupa_dias_sin_tablas_mysql(self):
        periodo = periodo_actual("mes", datetime(2026, 9, 6, 15, tzinfo=dt_timezone.utc))
        self.despacho(("1.00",), datetime(2026, 9, 6, 3, 59, tzinfo=dt_timezone.utc))
        self.despacho(("2.00",), datetime(2026, 9, 6, 4, 0, tzinfo=dt_timezone.utc))
        serie = serie_despachos(periodo)
        self.assertEqual(serie[4]["peso_conocido"], Decimal("1.00"))
        self.assertEqual(serie[5]["peso_conocido"], Decimal("2.00"))

    def test_mermas_tipos_y_bajas_permanecen_separados(self):
        partida = self.partida()
        for tipo, kg in (("MERMA", "12.40"), ("DESCARTE", "3.20"), ("PERDIDA", "1.00")):
            MermaProceso.objects.create(partida=partida, tipo=tipo, cantidad_kg=kg, motivo="Prueba",
                                        fecha_hora_evento=AHORA, registrado_por=self.jefe)
        MermaProceso.objects.create(partida=partida, tipo="MERMA", cantidad_kg=999, motivo="Otro período",
                                    fecha_hora_evento=AHORA - timedelta(days=40), registrado_por=self.jefe)
        self.baja("8.00")
        self.baja(None)
        self.baja("99.00", AHORA - timedelta(days=40))
        mermas, bajas = mermas_y_bajas(periodo_actual("semana", AHORA))
        self.assertEqual([f["kg"] for f in mermas], [Decimal("12.40"), Decimal("3.20"), Decimal("1.00")])
        self.assertEqual(bajas, dict(cajas=2, peso_conocido=Decimal("8.00"), sin_peso=1))


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class AlertasFrioTests(DatosDashboard, TestCase):
    def test_unidad_sin_umbral_no_genera_alertas(self):
        self.caja(almacenada=True, ingreso=AHORA - timedelta(days=90))
        self.assertEqual(alertas_frio(AHORA)["total"], 0)

    def test_bajo_igual_y_sobre_umbral_estricto(self):
        self.umbral()
        for segundos, esperado in ((86399, 0), (86400, 0), (86401, 1)):
            caja = self.caja(almacenada=True, ingreso=AHORA - timedelta(seconds=segundos))
            self.assertEqual(alertas_frio(AHORA)["total"], esperado)
            EstanciaCaja.objects.filter(caja=caja).update(fecha_hora_salida=AHORA)

    def test_duracion_y_exceso_correctos(self):
        self.umbral()
        self.caja(almacenada=True, ingreso=AHORA - timedelta(hours=27, minutes=10))
        alerta = alertas_frio(AHORA)["elementos"][0]
        self.assertEqual(alerta["duracion"], timedelta(hours=27, minutes=10))
        self.assertEqual(alerta["exceso"], timedelta(hours=3, minutes=10))
        self.assertEqual(alerta["tiempo"], "27 h 10 min")
        self.assertEqual(alerta["exceso_texto"], "3 h 10 min")

    def test_estancia_partida_abierta_genera_enlace(self):
        self.umbral("2.50", self.mantencion)
        partida = self.partida()
        EstanciaPartida.objects.create(partida=partida, unidad_frio=self.mantencion,
                                       fecha_hora_ingreso=AHORA - timedelta(hours=3), ingresado_por=self.jefe)
        alerta = alertas_frio(AHORA)["elementos"][0]
        self.assertEqual(alerta["url"], reverse("trazabilidad:detalle_partida", args=[partida.pk]))
        self.assertEqual(alerta["especie"], self.especie.nombre)

    def test_cerradas_no_generan_alertas(self):
        self.umbral()
        caja = self.caja(almacenada=True, ingreso=AHORA - timedelta(days=3))
        EstanciaCaja.objects.filter(caja=caja).update(fecha_hora_salida=AHORA)
        self.umbral("1.00", self.mantencion)
        EstanciaPartida.objects.create(partida=self.partida(), unidad_frio=self.mantencion,
                                       fecha_hora_ingreso=AHORA - timedelta(days=3), fecha_hora_salida=AHORA,
                                       ingresado_por=self.jefe)
        self.assertEqual(alertas_frio(AHORA)["total"], 0)

    def test_unidad_inactiva_con_estancia_abierta_sigue_alertando(self):
        self.umbral()
        self.unidad.activo = False
        self.unidad.save(update_fields=["activo"])
        self.caja(almacenada=True, ingreso=AHORA - timedelta(days=2))
        self.assertEqual(alertas_frio(AHORA)["total"], 1)

    def test_quitar_umbral_deja_de_alertar(self):
        self.umbral()
        self.caja(almacenada=True, ingreso=AHORA - timedelta(days=2))
        self.assertEqual(alertas_frio(AHORA)["total"], 1)
        self.umbral(None)
        self.assertEqual(alertas_frio(AHORA)["total"], 0)

    def test_umbrales_independientes_para_unidades(self):
        self.umbral("2.00")
        self.umbral("4.00", self.mantencion)
        for unidad in (self.unidad, self.mantencion):
            self.caja(almacenada=True, unidad=unidad, ingreso=AHORA - timedelta(hours=3))
        self.assertEqual(alertas_frio(AHORA)["total"], 1)

    def test_ingreso_futuro_no_alerta(self):
        self.umbral("1.00")
        self.caja(almacenada=True, ingreso=AHORA + timedelta(hours=3))
        self.assertEqual(alertas_frio(AHORA)["total"], 0)

    def test_duracion_real_utc_al_cruzar_cambio_horario(self):
        self.umbral("1.50")
        ingreso = datetime(2026, 9, 6, 3, 30, tzinfo=dt_timezone.utc)
        ahora = datetime(2026, 9, 6, 5, 30, tzinfo=dt_timezone.utc).astimezone(ZoneInfo("America/Santiago"))
        self.caja(almacenada=True, ingreso=ingreso)
        self.assertEqual(alertas_frio(ahora)["elementos"][0]["duracion"], timedelta(hours=2))

    def test_limita_lista_y_conserva_total_sin_n_mas_uno(self):
        self.umbral("1.00")
        for n in range(12):
            self.caja(almacenada=True, ingreso=AHORA - timedelta(hours=n + 2))
        with self.assertNumQueries(5):
            datos = alertas_frio(AHORA)
            self.assertEqual(datos["total"], 12)
            self.assertEqual(len(datos["elementos"]), 10)
            self.assertEqual(datos["elementos"][0]["duracion"], timedelta(hours=13))


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ConfiguracionUmbralTests(DatosDashboard, TestCase):
    def test_jefe_y_superuser_establecen_modifican_y_quitan(self):
        for usuario in (self.jefe, self.tecnico):
            self.client.force_login(usuario)
            for valor in ("12.50", "36.25", ""):
                response = self.client.post(self.url("umbrales"), {
                    "unidad": self.unidad.pk, f"u{self.unidad.pk}-umbral_alerta_horas": valor,
                })
                self.assertRedirects(response, self.url("umbrales"))
                self.unidad.refresh_from_db()
                self.assertEqual(self.unidad.umbral_alerta_horas, Decimal(valor) if valor else None)

    def test_servicio_rechaza_no_jefes_y_anonimos(self):
        for usuario in (self.encargada, self.operaria, AnonymousUser()):
            with self.assertRaises(PermissionDenied):
                configurar_umbral(usuario=usuario, unidad=self.unidad, horas=Decimal("12.00"))

    def test_post_directo_no_autorizado(self):
        for usuario in (self.encargada, self.operaria):
            self.client.force_login(usuario)
            self.assertEqual(self.client.post(self.url("umbrales"), {
                "unidad": self.unidad.pk, f"u{self.unidad.pk}-umbral_alerta_horas": "12",
            }).status_code, 403)
        self.unidad.refresh_from_db()
        self.assertIsNone(self.unidad.umbral_alerta_horas)

    def test_rechaza_no_positivo_demasiados_decimales_y_nan(self):
        self.client.force_login(self.jefe)
        for valor in ("0", "-1", "0.001", "NaN", "1000000"):
            with self.assertRaises(ValidationError):
                configurar_umbral(usuario=self.jefe, unidad=self.unidad, horas=Decimal(valor))
            response = self.client.post(self.url("umbrales"), {
                "unidad": self.unidad.pk, f"u{self.unidad.pk}-umbral_alerta_horas": valor,
            })
            self.assertContains(response, 'role="alert"')
        self.unidad.refresh_from_db()
        self.assertIsNone(self.unidad.umbral_alerta_horas)

    def test_constraint_base_datos_umbral_positivo(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            UnidadFrio.objects.filter(pk=self.unidad.pk).update(umbral_alerta_horas=0)

    def test_servicio_relee_actor_inactivo(self):
        Usuario.objects.filter(pk=self.jefe.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            self.umbral()

    def test_solo_modifica_umbral_y_protege_csrf(self):
        self.client.force_login(self.jefe)
        anterior = UnidadFrio.objects.values().get(pk=self.unidad.pk)
        self.client.post(self.url("umbrales"), {"unidad": self.unidad.pk,
            f"u{self.unidad.pk}-umbral_alerta_horas": "2.00", "activo": "", "nombre": "Alterado"})
        posterior = UnidadFrio.objects.values().get(pk=self.unidad.pk)
        posterior["umbral_alerta_horas"] = anterior["umbral_alerta_horas"]
        self.assertEqual(anterior, posterior)
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.jefe)
        self.assertEqual(cliente.post(self.url("umbrales"), {"unidad": self.unidad.pk}).status_code, 403)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ActividadDashboardTests(DatosDashboard, TestCase):
    def test_cuatro_fuentes_ordenadas_por_fecha_de_registro(self):
        recepcion = Recepcion.objects.create(fecha_hora_recepcion=AHORA, registrado_por=self.jefe)
        lote = self.lote()
        despacho = Despacho.objects.create(fecha_hora_despacho=AHORA, registrado_por=self.jefe)
        baja = self.baja()
        for n, obj in enumerate((recepcion, lote, despacho, baja)):
            type(obj).objects.filter(pk=obj.pk).update(creado_en=AHORA + timedelta(minutes=n))
        # El lote auxiliar de la caja de baja queda fuera del conjunto más reciente.
        LoteProduccion.objects.exclude(pk=lote.pk).update(creado_en=AHORA - timedelta(days=1))
        actividad = actividad_reciente(4)
        self.assertEqual([a["url"] for a in actividad], [
            reverse("producto_terminado:detalle_caja", args=[baja.caja_id]),
            reverse("inventario:detalle_despacho", args=[despacho.pk]),
            reverse("producto_terminado:detalle_lote", args=[lote.pk]),
            reverse("trazabilidad:detalle_recepcion", args=[recepcion.pk]),
        ])

    def test_limite_y_consultas_constantes(self):
        for _ in range(12):
            self.lote()
        with self.assertNumQueries(4):
            self.assertEqual(len(actividad_reciente()), 8)

    def test_actividad_vacia(self):
        self.assertEqual(actividad_reciente(), [])

    def test_dashboard_no_escribe_ni_altera_historial(self):
        self.umbral("1.00")
        self.caja(almacenada=True, ingreso=AHORA - timedelta(hours=3))
        self.despacho()
        self.baja()
        modelos = [m for m in apps.get_models() if m._meta.app_label in ("trazabilidad", "inventario")]
        antes = {m._meta.label: list(m.objects.order_by("pk").values()) for m in modelos}
        with CaptureQueriesContext(connection) as consultas:
            datos = dashboard_operacional(ahora=AHORA)
        self.assertTrue(datos["frio"]["total"])
        self.assertTrue(all(q["sql"].lstrip().upper().startswith("SELECT") for q in consultas))
        self.assertEqual(antes, {m._meta.label: list(m.objects.order_by("pk").values()) for m in modelos})
        self.assertLessEqual(len(consultas), 20)

    def test_avisos_pendiente_y_peso_no_documentado(self):
        self.caja()
        self.caja(None, almacenada=True)
        self.client.force_login(self.jefe)
        response = self.client.get(self.url())
        self.assertContains(response, "Aviso · Cajas pendientes de almacenar")
        self.assertContains(response, "Aviso · Peso real no documentado")

    def test_umbral_inicial_null_sin_alertas_persistentes(self):
        self.assertTrue(all(u.umbral_alerta_horas is None for u in UnidadFrio.objects.all()))
        self.assertEqual(list(apps.get_app_config("reportes").get_models()), [])
