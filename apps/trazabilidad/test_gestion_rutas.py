from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase, Client, skipUnlessDBFeature
from django.urls import reverse
from apps.usuarios.models import Rol, Usuario
from .models import RutaProceso, TipoProceso, SolicitudRuta, PartidaProceso, EventoProceso, EtapaRutaProceso
from .services_rutas import guardar_ruta, estado_ruta, guardar_operacion, solicitar_ruta, resolver_solicitud, rutas_equivalentes
from .selectors import resumen_procesamiento, partidas_con_situacion
from .services import iniciar_etapa_proceso, finalizar_etapa_proceso, enviar_a_tunel
from .test_procesamiento import DatosProcesamiento


class DatosGestionRutas(DatosProcesamiento):
    def nueva(self, **kwargs):
        datos = dict(usuario=self.jefe, especie=self.especie, nombre="Ruta nueva",
            descripcion="", activo=True, predeterminada=False, operaciones=[self.tipos["FILETEO"].pk])
        datos.update(kwargs)
        return guardar_ruta(**datos)

    def propuesta(self, **kwargs):
        datos = dict(usuario=self.usuario, especie=self.especie, operaciones=[self.tipos["FILETEO"].pk])
        datos.update(kwargs)
        return solicitar_ruta(**datos)

    def secuencia_post(self, **kwargs):
        datos = {"etapas-TOTAL_FORMS": "1", "etapas-INITIAL_FORMS": "0",
            "etapas-0-tipo_proceso": self.tipos["FILETEO"].pk, "etapas-0-ORDER": "1"}
        datos.update(kwargs)
        return datos


class GestionRutasTests(DatosGestionRutas, TestCase):
    def setUp(self):
        self.preparar()
        self.client.force_login(self.jefe)

    def test_crear_editar_duplicar_desactivar_y_default(self):
        ruta = self.nueva(predeterminada=True)
        self.assertEqual(list(ruta.etapas.values_list("tipo_proceso_id", flat=True)), [self.tipos["FILETEO"].pk])
        otra = self.nueva(nombre="Otra", operaciones=[self.tipos["DESCABEZADO"].pk], predeterminada=True)
        ruta.refresh_from_db()
        self.assertFalse(ruta.predeterminada)
        self.nueva(ruta=otra, nombre="Renombrada", operaciones=[self.tipos["DESCABEZADO"].pk])
        self.iniciar(ruta=ruta)
        with self.assertRaises(ValidationError):
            self.nueva(ruta=ruta, nombre="No permitido")
        copia = self.nueva(nombre="Copia", activo=False)
        self.assertNotEqual(copia.pk, ruta.pk)
        estado_ruta(usuario=self.jefe, ruta=ruta, activo=False)
        self.partida.refresh_from_db()
        self.assertEqual(self.partida.ruta_proceso_id, ruta.pk)
        self.assertTrue(EventoProceso.objects.exists())

    def test_operaciones_referenciadas_y_reservadas(self):
        operacion = guardar_operacion(usuario=self.jefe, codigo="PRUEBA", nombre="Operación de prueba", activo=True)
        ruta = self.nueva(operaciones=[operacion.pk])
        with self.assertRaises(ValidationError):
            guardar_operacion(usuario=self.jefe, operacion=operacion, codigo="OTRO", nombre="Otro nombre", activo=True)
        guardar_operacion(usuario=self.jefe, operacion=operacion, codigo=operacion.codigo, nombre=operacion.nombre, activo=False)
        with self.assertRaises(ValidationError):
            self.iniciar(ruta=ruta)
        with self.assertRaises(ValidationError):
            self.nueva(operaciones=[self.tipos["PROCESAMIENTO"].pk])
        with self.assertRaises(ValidationError):
            guardar_operacion(usuario=self.jefe, codigo="PROCESAMIENTO", nombre="Evento", activo=True)

    def test_solicitud_no_produce_aprobacion_reutiliza_y_rechazo(self):
        uno = self.propuesta()
        dos = self.propuesta(usuario=self.jefe)
        self.assertEqual(self.propuesta().pk, uno.pk)
        self.assertFalse(EventoProceso.objects.exists())
        self.assertEqual(PartidaProceso.objects.count(), 1)
        primero = resolver_solicitud(usuario=self.jefe, solicitud=uno, aprobar=True, nombre="Aprobada")
        segundo = resolver_solicitud(usuario=self.jefe, solicitud=dos, aprobar=True, nombre="No duplicada")
        self.assertEqual(primero.ruta_aprobada_id, segundo.ruta_aprobada_id)
        self.assertEqual(RutaProceso.objects.filter(nombre="Aprobada").count(), 1)
        with self.assertRaises(ValidationError):
            resolver_solicitud(usuario=self.jefe, solicitud=uno, aprobar=False)
        otra = self.propuesta(operaciones=[self.tipos["DESCABEZADO"].pk])
        resolver_solicitud(usuario=self.jefe, solicitud=otra, aprobar=False, respuesta="No autorizada")
        otra.refresh_from_db()
        self.assertEqual(otra.estado, "RECHAZADA")
        self.assertIsNone(otra.ruta_aprobada_id)

    def test_equivalencias_ambiguas_exigen_seleccion(self):
        solicitud = self.propuesta()
        primera = self.nueva()
        segunda = self.crear_ruta(nombre="Legado equivalente", codigos=["FILETEO"])
        self.assertEqual(len(rutas_equivalentes(self.especie, [self.tipos["FILETEO"].pk])), 2)
        with self.assertRaises(ValidationError):
            resolver_solicitud(usuario=self.jefe, solicitud=solicitud, aprobar=True)
        resolver_solicitud(usuario=self.jefe, solicitud=solicitud, aprobar=True, ruta_existente=segunda)
        self.client.force_login(self.usuario)
        url = reverse("trazabilidad:seleccionar_operaciones", args=[self.partida.pk])
        datos = self.secuencia_post(cantidad_kg="500", accion="iniciar")
        self.assertContains(self.client.post(url, datos), "Selecciona una ruta autorizada")
        self.assertFalse(EventoProceso.objects.exists())
        self.assertEqual(self.client.post(url, {**datos, "ruta": primera.pk}).status_code, 302)

    def test_flujo_sin_emparrillado_parcial_y_etapas_ordenadas(self):
        ruta = self.nueva(operaciones=[self.tipos["DESCABEZADO"].pk, self.tipos["FILETEO"].pk])
        seleccionada, restante = self.iniciar(ruta=ruta, cantidad="100")
        etapas = list(ruta.etapas.all())
        with self.assertRaises(ValidationError):
            iniciar_etapa_proceso(partida=seleccionada, etapa=etapas[1], usuario=self.usuario)
        for etapa in etapas:
            iniciar_etapa_proceso(partida=seleccionada, etapa=etapa, usuario=self.usuario)
            finalizar_etapa_proceso(partida=seleccionada, etapa=etapa, usuario=self.usuario)
        resumen = resumen_procesamiento(partidas_con_situacion().get(pk=seleccionada.pk))
        self.assertTrue(resumen["ruta_completa"])
        enviar_a_tunel(partida=seleccionada, unidad=self.tunel, usuario=self.usuario)
        self.assertEqual(restante.cantidad_inicial_kg, Decimal("400"))
        self.assertFalse(restante.eventos.exists())

    def test_permisos_backend_csrf_y_get_sin_escrituras(self):
        solicitud = self.propuesta()
        for usuario in (self.usuario, Usuario.objects.create_user(username="encargada_rutas", rol=Rol.objects.get(codigo="ENCARGADA"))):
            self.client.force_login(usuario)
            for nombre, args in (("rutas", []), ("nueva_ruta", []), ("operaciones", []), ("resolver_solicitud", [solicitud.pk])):
                self.assertEqual(self.client.get(reverse("trazabilidad:"+nombre, args=args)).status_code, 403)
                self.assertEqual(self.client.post(reverse("trazabilidad:"+nombre, args=args), {}).status_code, 403)
            with self.assertRaises(PermissionDenied):
                self.nueva(usuario=usuario)
            with self.assertRaises(PermissionDenied):
                resolver_solicitud(usuario=usuario, solicitud=solicitud, aprobar=True, nombre="No")
        self.client.force_login(self.jefe)
        for nombre in ("rutas", "nueva_ruta", "operaciones", "solicitudes_ruta"):
            self.assertEqual(self.client.get(reverse("trazabilidad:"+nombre)).status_code, 200)
        self.assertEqual(self.client.get(reverse("trazabilidad:estado_ruta", args=[self.ruta.pk])).status_code, 405)
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.jefe)
        self.assertEqual(cliente.post(reverse("trazabilidad:nueva_ruta"), {}).status_code, 403)

    def test_formularios_creacion_solicitud_y_aprobacion(self):
        respuesta = self.client.post(reverse("trazabilidad:nueva_ruta"), self.secuencia_post(
            nombre="Web", especie=self.especie.pk, activo="on"))
        self.assertEqual(respuesta.status_code, 302)
        self.client.force_login(self.usuario)
        datos = self.secuencia_post(cantidad_kg="50", accion="iniciar")
        self.assertEqual(self.client.post(reverse("trazabilidad:seleccionar_operaciones", args=[self.partida.pk]), datos).status_code, 302)
        self.assertEqual(PartidaProceso.objects.filter(partida_padre=self.partida).count(), 2)

    def test_no_duplicados_y_secuencia_invalida(self):
        self.nueva()
        with self.assertRaises(ValidationError):
            self.nueva(nombre="Duplicada")
        with self.assertRaises(ValidationError):
            self.nueva(operaciones=[])
        datos = self.secuencia_post(nombre="Orden inválido", especie=self.especie.pk, activo="on")
        datos["etapas-0-ORDER"] = "0"
        self.assertContains(self.client.post(reverse("trazabilidad:nueva_ruta"), datos), "orden positivo")

    def test_solicitud_y_resolucion_desde_interfaz(self):
        self.client.force_login(self.usuario)
        url = reverse("trazabilidad:seleccionar_operaciones", args=[self.partida.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        datos = self.secuencia_post(cantidad_kg="100", accion="buscar")
        self.assertContains(self.client.post(url, datos), "Solicitar nueva ruta")
        self.assertEqual(self.client.post(url, {**datos, "accion": "solicitar"}).status_code, 302)
        solicitud = SolicitudRuta.objects.get()
        self.assertFalse(EventoProceso.objects.exists())
        self.assertEqual(self.client.get(reverse("trazabilidad:solicitudes_ruta")).status_code, 200)
        self.client.force_login(self.jefe)
        resolver = reverse("trazabilidad:resolver_solicitud", args=[solicitud.pk])
        self.assertEqual(self.client.get(resolver).status_code, 200)
        self.assertEqual(self.client.post(resolver, {"decision": "aprobar", "nombre": "Aprobada por web"}).status_code, 302)
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, "APROBADA")
        self.assertFalse(EventoProceso.objects.exists())


class ConcurrenciaRutasTests(DatosGestionRutas, TransactionTestCase):
    def setUp(self):
        self.preparar()

    @skipUnlessDBFeature("has_select_for_update")
    def test_dos_creaciones_concurrentes_no_duplican(self):
        barrera = Barrier(2)
        def crear(nombre):
            close_old_connections()
            try:
                barrera.wait(timeout=15)
                self.nueva(nombre=nombre)
                return "creada"
            except ValidationError:
                return "existente"
            finally:
                connection.close()
        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            resultados = list(ejecutor.map(crear, ["Concurrente A", "Concurrente B"]))
        self.assertCountEqual(resultados, ["creada", "existente"])

    @skipUnlessDBFeature("has_select_for_update")
    def test_dos_aprobaciones_concurrentes_reutilizan(self):
        uno, dos = self.propuesta(), self.propuesta(usuario=self.jefe)
        barrera = Barrier(2)
        def aprobar(solicitud):
            close_old_connections()
            try:
                barrera.wait(timeout=15)
                return resolver_solicitud(usuario=self.jefe, solicitud=solicitud, aprobar=True, nombre=f"Aprobada {solicitud.pk}").ruta_aprobada_id
            finally:
                connection.close()
        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            ids = list(ejecutor.map(aprobar, [uno, dos]))
        self.assertEqual(ids[0], ids[1])
