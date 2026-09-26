from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch

from django.contrib import admin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, close_old_connections, connection, connections, transaction
from django.db.models import Sum
from django.test import RequestFactory, TestCase, TransactionTestCase, skipUnlessDBFeature
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.usuarios.models import Rol, Usuario
from .forms import AgregarConsumoLoteForm, CrearLoteForm
from .models import ConsumoLote, EstanciaPartida, LoteProduccion, PartidaProceso, Pesaje
from .selectors import lotes_con_totales, partidas_con_disponibilidad, partidas_para_lote
from .services import (
    agregar_consumo_lote, crear_lote_produccion, enviar_a_tunel, finalizar_etapa_proceso,
    iniciar_etapa_proceso, registrar_peso_postproceso, retirar_de_tunel,
)
from .test_postproceso import DatosPostproceso


class DatosLotes(DatosPostproceso):
    def crear_lote(self, **cambios):
        datos = dict(partida=self.partida, codigo="TEST-001", cantidad_kg=Decimal("100"),
                     fecha_elaboracion=date(2026, 9, 26), fecha_vencimiento=date(2026, 10, 26),
                     observaciones="Prueba de lote", usuario=self.usuario)
        datos.update(cambios)
        return crear_lote_produccion(**datos)

    def otra_lista(self, peso="40", ruta=None, especie=None):
        partida = self.crear_partida()
        if especie:
            detalle = partida.detalle_recepcion
            detalle.especie = especie
            detalle.save(update_fields=["especie"])
        ruta = ruta or self.ruta
        self.iniciar(partida=partida, ruta=ruta)
        for etapa in ruta.etapas.all():
            iniciar_etapa_proceso(partida=partida, etapa=etapa, usuario=self.usuario)
            finalizar_etapa_proceso(partida=partida, etapa=etapa, usuario=self.usuario)
        enviar_a_tunel(partida=partida, unidad=self.tunel, usuario=self.usuario)
        retirar_de_tunel(partida=partida, usuario=self.usuario)
        registrar_peso_postproceso(partida=partida, peso_kg=Decimal(peso), usuario=self.usuario)
        return partida

    def disponible(self, partida=None):
        return partidas_con_disponibilidad().get(pk=(partida or self.partida).pk).disponible_lote_kg

    def agregar(self, lote, partida=None, cantidad="25", usuario=None):
        return agregar_consumo_lote(lote=lote, partida=partida or self.partida,
                                   cantidad_kg=Decimal(cantidad), usuario=usuario or self.usuario)

    def url_lote(self, nombre, pk=None):
        return reverse("producto_terminado:" + nombre, args=[pk] if pk is not None else [])

    def datos_form(self, codigo="WEB-001", cantidad="100"):
        return {"codigo": codigo, "cantidad_kg": cantidad, "fecha_elaboracion": "2026-09-26",
                "fecha_vencimiento": "2026-10-26", "observaciones": "Prueba web"}


class LotesTests(DatosLotes, TestCase):
    def setUp(self):
        self.preparar()
        self.pendiente()
        self.pesar()

    def test_crear_lote_desde_seguimiento_lista_empaque(self):
        lote = self.crear_lote(codigo="  TEST-001  ", observaciones="  Observación  ")
        self.assertEqual(lote.codigo_lote, "TEST-001")
        self.assertEqual(lote.observaciones, "Observación")
        self.assertEqual(lote.registrado_por, self.usuario)
        self.assertEqual(lote.consumos.get().cantidad_kg_utilizada, Decimal("100"))
        self.assertEqual(lote.consumos.get().partida, self.partida)

    def test_lote_toma_especie_y_ruta_del_seguimiento(self):
        lote = self.crear_lote()
        self.assertEqual(lote.especie_id, self.especie.pk)
        self.assertEqual(lote.ruta_proceso_id, self.ruta.pk)
        self.assertNotIn("especie", CrearLoteForm().fields)
        self.assertNotIn("ruta_proceso", CrearLoteForm().fields)

    def test_codigo_lote_obligatorio_longitud_y_trim(self):
        for codigo in ("", "   \t", "x" * 81, None):
            with self.subTest(codigo=codigo), self.assertRaises(ValidationError):
                self.crear_lote(codigo=codigo)
        self.assertFalse(LoteProduccion.objects.exists())
        self.assertFalse(ConsumoLote.objects.exists())

    def test_codigo_lote_unico(self):
        self.crear_lote()
        with self.assertRaises(ValidationError):
            self.crear_lote(codigo="  TEST-001  ", cantidad_kg=Decimal("1"))
        self.assertEqual(LoteProduccion.objects.count(), 1)
        self.assertEqual(ConsumoLote.objects.count(), 1)
        self.assertEqual(self.disponible(), Decimal("25"))

    def test_fecha_vencimiento_no_anterior_elaboracion(self):
        with self.assertRaises(ValidationError):
            self.crear_lote(fecha_vencimiento=date(2026, 9, 25))
        self.assertFalse(LoteProduccion.objects.exists())
        form = CrearLoteForm(self.datos_form() | {"fecha_vencimiento": "2026-09-25"})
        self.assertFalse(form.is_valid())
        self.assertIn("fecha_vencimiento", form.errors)

    def test_fechas_iguales_y_vencimiento_opcional_sin_formula(self):
        lote = self.crear_lote(fecha_vencimiento=date(2026, 9, 26))
        self.assertEqual(lote.fecha_elaboracion, lote.fecha_vencimiento)
        otro = self.crear_lote(codigo="SIN-VENCIMIENTO", cantidad_kg=Decimal("25"), fecha_vencimiento=None)
        self.assertIsNone(otro.fecha_vencimiento)

    def test_cantidad_consumo_mayor_cero_precision_y_decimal(self):
        for valor in (Decimal("0"), Decimal("-1"), Decimal("1.001"), Decimal("100000000"),
                      Decimal("NaN"), Decimal("Infinity"), "10", 10.0):
            with self.subTest(valor=valor), self.assertRaises(ValidationError):
                self.crear_lote(cantidad_kg=valor)
        self.assertFalse(LoteProduccion.objects.exists())
        self.assertFalse(ConsumoLote.objects.exists())

    def test_no_consumir_mas_que_peso_postproceso(self):
        # Recepción e inicial son 500; el límite real es 125.
        with self.assertRaises(ValidationError):
            self.crear_lote(cantidad_kg=Decimal("126"))
        self.assertFalse(LoteProduccion.objects.exists())
        self.assertFalse(ConsumoLote.objects.exists())

    def test_consumo_parcial_deja_disponible_sin_dividir_ni_cambiar_estado(self):
        cantidad_partidas = PartidaProceso.objects.count()
        self.crear_lote()
        self.assertEqual(self.disponible(), Decimal("25"))
        self.assertEqual(self.situacion(), "LISTA_PARA_EMPAQUE")
        self.partida.refresh_from_db()
        self.assertEqual(self.partida.estado, "ACTIVA")
        self.assertEqual(self.partida.cantidad_inicial_kg, Decimal("500"))
        self.assertEqual(self.partida.detalle_recepcion.peso_recepcion_kg, Decimal("500"))
        self.assertEqual(PartidaProceso.objects.count(), cantidad_partidas)

    def test_varios_lotes_consumen_hasta_limite(self):
        self.crear_lote(codigo="A", cantidad_kg=Decimal("80"))
        self.crear_lote(codigo="B", cantidad_kg=Decimal("20"))
        self.assertEqual(self.disponible(), Decimal("25"))
        with self.assertRaises(ValidationError):
            self.crear_lote(codigo="EXCESO", cantidad_kg=Decimal("30"))
        self.crear_lote(codigo="C", cantidad_kg=Decimal("25"))
        self.assertEqual(self.disponible(), Decimal("0"))
        self.assertEqual(self.situacion(), "LISTA_PARA_EMPAQUE")
        self.assertEqual(ConsumoLote.objects.count(), 3)
        self.assertEqual(LoteProduccion.objects.count(), 3)
        with self.assertRaises(ValidationError):
            self.crear_lote(codigo="D", cantidad_kg=Decimal("0.01"))

    def test_lote_recibe_varios_seguimientos_total_es_suma(self):
        lote = self.crear_lote()
        otra = self.otra_lista()
        self.agregar(lote, otra)
        actual = lotes_con_totales().get(pk=lote.pk)
        self.assertEqual(actual.total_asignado, Decimal("125"))
        self.assertEqual(actual.numero_fuentes, 2)
        self.assertEqual(self.disponible(otra), Decimal("15"))
        self.assertEqual(self.disponible(), Decimal("25"))

    def test_no_mezclar_especies(self):
        lote = self.crear_lote()
        ruta = self.crear_ruta(especie=self.otra_especie)
        otra = self.otra_lista(ruta=ruta, especie=self.otra_especie)
        with self.assertRaisesRegex(ValidationError, "misma especie"):
            self.agregar(lote, otra)
        self.assertEqual(lote.consumos.count(), 1)
        self.assertEqual(self.disponible(otra), Decimal("40"))

    def test_no_mezclar_rutas(self):
        lote = self.crear_lote()
        otra = self.otra_lista(ruta=self.crear_ruta(nombre="Otra ruta"))
        with self.assertRaisesRegex(ValidationError, "misma ruta"):
            self.agregar(lote, otra)
        self.assertEqual(lote.consumos.count(), 1)
        self.assertEqual(self.disponible(otra), Decimal("40"))

    def test_no_asignar_sin_peso_o_pendiente_pesaje(self):
        self.partida.pesajes.all().delete()
        self.assertEqual(self.situacion(), "PENDIENTE_PESAJE")
        with self.assertRaises(ValidationError):
            self.crear_lote()
        self.assertFalse(LoteProduccion.objects.exists())

    def test_no_asignar_disponible_proceso_congelacion(self):
        otra = self.crear_partida()
        with self.assertRaises(ValidationError):
            self.crear_lote(partida=otra)
        self.iniciar(partida=otra)
        with self.assertRaises(ValidationError):
            self.crear_lote(partida=otra)
        self.completar(partida=otra)
        enviar_a_tunel(partida=otra, unidad=self.tunel, usuario=self.usuario)
        with self.assertRaises(ValidationError):
            self.crear_lote(partida=otra)
        self.assertFalse(LoteProduccion.objects.exists())

    def test_no_asignar_dividida_consumida_cerrada_con_instancia_obsoleta(self):
        for estado in ("DIVIDIDA", "CONSUMIDA", "CERRADA"):
            PartidaProceso.objects.filter(pk=self.partida.pk).update(estado=estado)
            with self.subTest(estado=estado), self.assertRaises(ValidationError):
                self.crear_lote()
        self.assertFalse(LoteProduccion.objects.exists())

    def test_no_asignar_con_estancia_abierta_o_sin_ruta(self):
        estancia = EstanciaPartida.objects.create(partida=self.partida, unidad_frio=self.tunel,
                                                 fecha_hora_ingreso=timezone.now(), ingresado_por=self.usuario)
        with self.assertRaises(ValidationError):
            self.crear_lote()
        estancia.delete()
        PartidaProceso.objects.filter(pk=self.partida.pk).update(ruta_proceso=None)
        with self.assertRaises(ValidationError):
            self.crear_lote()

    def test_historial_con_pesajes_ambiguos_no_se_ofrece(self):
        Pesaje.objects.create(partida=self.partida, tipo=Pesaje.POSTPROCESO, peso_kg=200,
                              fecha_hora_evento=timezone.now(), registrado_por=self.usuario)
        self.assertFalse(partidas_para_lote().exists())
        with self.assertRaises(ValidationError):
            self.crear_lote()

    def test_mismo_seguimiento_no_se_duplica_en_mismo_lote(self):
        lote = self.crear_lote()
        with self.assertRaisesRegex(ValidationError, "ya fue asignado"):
            self.agregar(lote, cantidad="10")
        self.assertEqual(lote.consumos.get().cantidad_kg_utilizada, Decimal("100"))
        self.assertEqual(self.disponible(), Decimal("25"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            ConsumoLote.objects.create(partida=self.partida, lote_produccion=lote, cantidad_kg_utilizada=1)

    def test_mermas_no_se_descontan_nuevamente(self):
        self.partida.pesajes.all().delete()
        self.merma("100")
        self.pesar("125")
        self.assertEqual(self.disponible(), Decimal("125"))
        self.crear_lote(cantidad_kg=Decimal("125"))
        self.assertEqual(self.disponible(), Decimal("0"))
        self.assertEqual(self.partida.mermas.get().cantidad_kg, Decimal("100"))

    def test_rollback_creacion_si_falla_consumo_despues_de_insert(self):
        original = ConsumoLote.save

        def guardar_y_fallar(objeto, *args, **kwargs):
            original(objeto, *args, **kwargs)
            raise RuntimeError("Fallo tras insertar consumo")

        with patch.object(ConsumoLote, "save", guardar_y_fallar), self.assertRaises(RuntimeError):
            self.crear_lote()
        self.assertFalse(LoteProduccion.objects.exists())
        self.assertFalse(ConsumoLote.objects.exists())
        self.assertEqual(self.disponible(), Decimal("125"))

    def test_rollback_agregar_exceso_o_lote_inexistente(self):
        lote = self.crear_lote()
        otra = self.otra_lista()
        with self.assertRaises(ValidationError):
            self.agregar(lote, otra, "41")
        with self.assertRaises(ValidationError):
            self.agregar(LoteProduccion(pk=999999), otra)
        self.assertEqual(lote.consumos.count(), 1)
        self.assertEqual(self.disponible(otra), Decimal("40"))

    def test_lote_historico_sin_ruta_se_preserva_y_no_recibe_aportes(self):
        lote = LoteProduccion.objects.create(codigo_lote="HISTORICO", especie=self.especie,
                                             fecha_elaboracion=date(2025, 1, 1), registrado_por=self.usuario)
        self.assertIsNone(lote.ruta_proceso_id)
        with self.assertRaises(ValidationError):
            self.agregar(lote)
        self.assertFalse(partidas_para_lote(lote).exists())
        self.client.force_login(self.usuario)
        respuesta = self.client.get(self.url_lote("detalle_lote", lote.pk))
        self.assertContains(respuesta, "Sin ruta documentada")
        self.assertNotContains(respuesta, ">Agregar producto</a>")

    def test_form_agregar_solo_compatibles_disponibles_no_repetidos(self):
        lote = self.crear_lote()
        compatible = self.otra_lista()
        self.otra_lista(ruta=self.crear_ruta(nombre="Otra ruta"))
        self.crear_partida()
        form = AgregarConsumoLoteForm(lote=lote)
        self.assertEqual([p.pk for p in form.fields["partida"].queryset], [compatible.pk])
        self.assertIn("40,00 kg disponibles", str(form["partida"]))

    def test_detalle_lote_muestra_origenes(self):
        lote = self.crear_lote()
        otra = self.otra_lista()
        self.agregar(lote, otra)
        self.client.force_login(self.usuario)
        respuesta = self.client.get(self.url_lote("detalle_lote", lote.pk))
        for texto in ("TEST-001", "Origen del producto", "125,00 kg", "2 seguimientos", "100,00 kg", "25,00 kg"):
            self.assertContains(respuesta, texto)
        for partida in (self.partida, otra):
            self.assertContains(respuesta, f"Seguimiento #{partida.pk}")
            self.assertContains(respuesta, f"Recepción #{partida.detalle_recepcion.recepcion_id}")
            self.assertContains(respuesta, reverse("trazabilidad:detalle_partida", args=[partida.pk]))

    def test_detalle_seguimiento_muestra_lotes_y_oculta_accion_sin_saldo(self):
        self.client.force_login(self.usuario)
        url = reverse("trazabilidad:detalle_partida", args=[self.partida.pk])
        self.assertContains(self.client.get(url), "Asignar a lote de producción")
        lote = self.crear_lote()
        respuesta = self.client.get(url)
        for texto in ("125,00 kg", "100,00 kg", "25,00 kg", "TEST-001", self.url_lote("detalle_lote", lote.pk)):
            self.assertContains(respuesta, texto)
        self.crear_lote(codigo="RESTO", cantidad_kg=Decimal("25"))
        respuesta = self.client.get(url)
        self.assertContains(respuesta, "Todo el producto postproceso ya fue asignado a lotes de producción.")
        self.assertNotContains(respuesta, "Asignar a lote de producción")
        self.assertContains(respuesta, "Lista para empaque")

    def test_permisos_operativos_y_especie_ruta_post_no_confiables(self):
        encargada, _ = Rol.objects.get_or_create(codigo="ENCARGADA", defaults={"nombre": "Encargada"})
        usuarios = [self.usuario, self.jefe, Usuario.objects.create_user(username="enc_lotes", rol=encargada),
                    Usuario.objects.create_superuser(username="super_lotes")]
        for i, usuario in enumerate(usuarios):
            otra = self.otra_lista()
            self.client.force_login(usuario)
            url = self.url_lote("crear_lote", otra.pk)
            self.assertEqual(self.client.get(url).status_code, 200)
            respuesta = self.client.post(url, self.datos_form(f"ROL-{i}", "20") | {
                "especie": self.otra_especie.pk, "ruta_proceso": "999999",
            })
            self.assertEqual(respuesta.status_code, 302)
            lote = LoteProduccion.objects.get(codigo_lote=f"ROL-{i}")
            self.assertEqual(lote.especie_id, self.especie.pk)
            self.assertEqual(lote.ruta_proceso_id, self.ruta.pk)
            self.assertEqual(lote.registrado_por, usuario)
            self.assertEqual(self.client.get(self.url_lote("lista_lotes")).status_code, 200)
            self.assertEqual(self.client.get(respuesta.url).status_code, 200)
            self.assertEqual(self.client.post(self.url_lote("agregar_producto_lote", lote.pk),
                                             {"partida": self.partida.pk, "cantidad_kg": "1"}).status_code, 302)

    def test_anonimo_redirige_login_y_rol_ajeno_no_opera(self):
        lote = self.crear_lote()
        urls = [self.url_lote("lista_lotes"), self.url_lote("detalle_lote", lote.pk),
                self.url_lote("crear_lote", self.partida.pk), self.url_lote("agregar_producto_lote", lote.pk)]
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 302)
            self.assertEqual(self.client.post(url).status_code, 302)
        rol = Rol.objects.create(codigo="CONSULTOR", nombre="Consultor")
        usuario = Usuario.objects.create_user(username="consultor_lotes", rol=rol)
        self.client.force_login(usuario)
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(url).status_code, 403)
        with self.assertRaises(PermissionDenied):
            self.crear_lote(usuario=usuario, cantidad_kg=Decimal("1"))
        with self.assertRaises(PermissionDenied):
            self.agregar(lote, usuario=usuario, cantidad="1")

    def test_gets_no_escriben_y_post_invalido_conserva_form(self):
        self.client.force_login(self.usuario)
        url = self.url_lote("crear_lote", self.partida.pk)
        self.client.get(url)
        self.assertFalse(LoteProduccion.objects.exists())
        respuesta = self.client.post(url, self.datos_form(cantidad="126"))
        self.assertContains(respuesta, "no puede superar")
        self.assertEqual(respuesta.context["form"]["codigo"].value(), "WEB-001")
        self.assertFalse(LoteProduccion.objects.exists())
        for cantidad in ("0", "-1", "1.001"):
            self.assertFalse(CrearLoteForm(self.datos_form(cantidad=cantidad)).is_valid())

    def test_post_agregar_incompatible_o_duplicado_no_escribe(self):
        lote = self.crear_lote()
        otra = self.otra_lista(ruta=self.crear_ruta(nombre="Incompatible"))
        self.client.force_login(self.usuario)
        for partida in (self.partida, otra):
            respuesta = self.client.post(self.url_lote("agregar_producto_lote", lote.pk),
                                         {"partida": partida.pk, "cantidad_kg": "1"})
            self.assertEqual(respuesta.status_code, 200)
            self.assertIn("partida", respuesta.context["form"].errors)
        self.assertEqual(lote.consumos.count(), 1)

    def test_busqueda_paginacion_y_no_n_mas_uno(self):
        lote = self.crear_lote(codigo="BUSCAR-1", cantidad_kg=Decimal("1"))
        self.client.force_login(self.usuario)
        url = self.url_lote("lista_lotes")
        for busqueda in ("BUSCAR", self.especie.nombre):
            self.assertEqual([l.pk for l in self.client.get(url, {"q": busqueda}).context["page_obj"]], [lote.pk])
        with CaptureQueriesContext(connection) as pocas:
            self.client.get(url)
        for i in range(11):
            self.crear_lote(codigo=f"BUSCAR-{i+2}", cantidad_kg=Decimal("1"))
        with CaptureQueriesContext(connection) as muchas:
            respuesta = self.client.get(url, {"q": "BUSCAR"})
        self.assertEqual(len(pocas), len(muchas))
        self.assertEqual(len(respuesta.context["page_obj"]), 10)
        self.assertIn("q=BUSCAR", respuesta.context["pagina_siguiente"])
        self.assertEqual(len(self.client.get(url, {"q": "BUSCAR", "page": "2"}).context["page_obj"]), 2)
        self.assertContains(self.client.get(url, {"q": "NO-EXISTE"}), "No hay lotes")

    def test_detalle_y_selector_no_n_mas_uno(self):
        lote = self.crear_lote()
        self.client.force_login(self.usuario)
        url = self.url_lote("detalle_lote", lote.pk)
        with CaptureQueriesContext(connection) as pocas:
            self.client.get(url)
        for _ in range(3):
            self.agregar(lote, self.otra_lista(), "10")
        with CaptureQueriesContext(connection) as muchas:
            self.client.get(url)
        self.assertEqual(len(pocas), len(muchas))
        with CaptureQueriesContext(connection) as consultas:
            list(partidas_para_lote())
        self.assertEqual(len(consultas), 1)

    def test_sidebar_producto_terminado_y_un_solo_activo(self):
        self.client.force_login(self.usuario)
        respuesta = self.client.get(self.url_lote("lista_lotes"))
        self.assertContains(respuesta, "Producto terminado")
        self.assertContains(respuesta, 'aria-current="page"', count=1)
        self.assertEqual(self.url_lote("lista_lotes"), "/producto-terminado/lotes/")

    def test_admin_historico_sin_modificar_ni_borrar(self):
        lote = self.crear_lote()
        request = RequestFactory().get("/admin/")
        request.user = Usuario.objects.create_superuser(username="admin_lotes")
        self.client.force_login(request.user)
        for registro in (lote, lote.consumos.get()):
            configuracion = admin.site._registry[type(registro)]
            self.assertFalse(configuracion.has_add_permission(request))
            self.assertFalse(configuracion.has_change_permission(request, registro))
            self.assertFalse(configuracion.has_delete_permission(request, registro))
            self.assertFalse(configuracion.get_actions(request))
            prefijo = "admin:trazabilidad_" + registro._meta.model_name
            self.assertEqual(self.client.get(reverse(prefijo + "_change", args=[registro.pk])).status_code, 200)
            self.assertEqual(self.client.post(reverse(prefijo + "_delete", args=[registro.pk]), {"post": "yes"}).status_code, 403)


class ConcurrenciaLotesTests(DatosLotes, TransactionTestCase):
    def setUp(self):
        self.preparar()
        self.pendiente()
        self.pesar("100")

    def competir(self, operacion, partidas=None):
        barrera = Barrier(2)
        partidas = partidas or [self.partida, self.partida]

        def ejecutar(indice):
            close_old_connections()
            try:
                partida = PartidaProceso.objects.get(pk=partidas[indice].pk)
                usuario = Usuario.objects.get(pk=[self.usuario.pk, self.jefe.pk][indice])
                barrera.wait(timeout=10)
                try:
                    operacion(indice, partida, usuario)
                except ValidationError:
                    return "rechazada"
                return "aceptada"
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            resultados = list(pool.map(ejecutar, range(2)))
        self.assertCountEqual(resultados, ["aceptada", "rechazada"])

    @skipUnlessDBFeature("has_select_for_update")
    def test_dos_lotes_simultaneos_no_sobreconsumen_100_kg(self):
        self.competir(lambda i, partida, usuario: self.crear_lote(
            partida=partida, usuario=usuario, codigo=f"CONC-{i}", cantidad_kg=Decimal("60"),
        ))
        self.assertEqual(LoteProduccion.objects.count(), 1)
        self.assertEqual(ConsumoLote.objects.count(), 1)
        self.assertEqual(self.disponible(), Decimal("40"))

    @skipUnlessDBFeature("has_select_for_update")
    def test_agregados_simultaneos_no_sobreconsumen(self):
        lotes = [self.crear_lote(partida=self.otra_lista(), codigo=f"BASE-{i}", cantidad_kg=Decimal("1")) for i in range(2)]
        self.competir(lambda i, partida, usuario: agregar_consumo_lote(
            lote=lotes[i], partida=partida, cantidad_kg=Decimal("60"), usuario=usuario,
        ))
        self.assertEqual(self.partida.consumos_lote.aggregate(total=Sum("cantidad_kg_utilizada"))["total"], Decimal("60"))

    @skipUnlessDBFeature("has_select_for_update")
    def test_codigo_unico_entre_seguimientos_concurrentes(self):
        otra = self.otra_lista("100")
        self.competir(lambda i, partida, usuario: self.crear_lote(
            partida=partida, usuario=usuario, codigo="MISMO-CODIGO", cantidad_kg=Decimal("60"),
        ), partidas=[self.partida, otra])
        self.assertEqual(LoteProduccion.objects.count(), 1)
        self.assertEqual(ConsumoLote.objects.count(), 1)
