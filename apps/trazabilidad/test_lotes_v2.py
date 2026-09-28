from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier

from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, connection, connections, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import RequestFactory, TestCase, TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse

from .forms import AgregarConsumoLoteForm
from .lotes import presentar_codigo_lote, segmento_folio
from .models import Caja, ComposicionCaja, ConsumoLote, Especie, LoteProduccion, PartidaProceso
from .selectors import origen_unico_lote, partidas_para_lote
from .services import crear_caja
from .test_lotes import DatosLotes
from .test_cajas import DatosCajas


class LotesV2Tests(DatosLotes, TestCase):
    def setUp(self):
        self.preparar()
        self.pendiente()
        self.pesar("130")
        self.origen = self.partida.detalle_recepcion.origen_sernapesca
        self.origen.folio_origen = "38343"
        self.origen.save()

    def nuevo(self, **kwargs):
        return self.crear_lote(fecha_elaboracion=date(2026, 9, 28), **kwargs)

    def test_especie_codigo_exactamente_dos_digitos_y_unico(self):
        for codigo in ("1", "001", "AB", "1A", "１２"):
            with self.subTest(codigo=codigo), self.assertRaises(ValidationError):
                Especie(nombre="Inválida", codigo_lote=codigo).full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Especie.objects.create(nombre="Duplicada", codigo_lote="01")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Especie.objects.create(nombre="Inválida BD", codigo_lote="X1")

    def test_codigo_estable_al_renombrar_y_admin_editable(self):
        self.especie.nombre = "Nombre definitivo"
        self.especie.save()
        self.especie.refresh_from_db()
        self.assertEqual(self.especie.codigo_lote, "01")
        request = RequestFactory().get("/admin/")
        request.user = self.jefe
        self.assertIn("codigo_lote", admin.site._registry[Especie].get_form(request).base_fields)

    def test_especie_sin_codigo_no_genera_lote(self):
        Especie.objects.filter(pk=self.especie.pk).update(codigo_lote=None)
        with self.assertRaisesRegex(ValidationError, "especie necesita"):
            self.nuevo()
        self.assertFalse(LoteProduccion.objects.exists())
        self.assertFalse(ConsumoLote.objects.exists())

    def test_codigo_automatico_17_digitos_ejemplo_exacto(self):
        lote = self.nuevo()
        self.assertEqual(lote.codigo_lote, "38343010128092026")
        self.assertEqual(lote.codigo_visible, "38343 0101 28092026")
        self.assertEqual(len(lote.codigo_lote), 17)
        self.assertTrue(lote.codigo_lote.isascii() and lote.codigo_lote.isdigit())

    def test_presentacion_historica_no_se_reinterpreta(self):
        for codigo in ("TEST-MER-280926-A", "12345", "X8343010128092026", "38343 0101 28092026"):
            self.assertEqual(presentar_codigo_lote(codigo), codigo)

    def test_segmentos_folio_y_no_modifica_original(self):
        for folio, segmento in (("38343", "38343"), ("FOLIO-38343", "38343"), ("123", "00123"), ("ABC123", "00123"), ("1234567", "34567")):
            with self.subTest(folio=folio):
                self.assertEqual(segmento_folio(folio), segmento)
                self.origen.folio_origen = folio
                self.origen.save()
                lote = self.nuevo(cantidad_kg=Decimal("1"))
                self.assertTrue(lote.codigo_lote.startswith(segmento))
                self.origen.refresh_from_db()
                self.assertEqual(self.origen.folio_origen, folio)

    def test_folio_sin_digitos_rechaza_sin_parciales(self):
        self.origen.folio_origen = "ABCXYZ"
        self.origen.save()
        with self.assertRaisesRegex(ValidationError, "folio de origen no contiene números"):
            self.nuevo()
        self.assertFalse(LoteProduccion.objects.exists())
        self.assertFalse(ConsumoLote.objects.exists())

    def test_correlativos_01_02_03_misma_combinacion(self):
        lotes = [self.nuevo(cantidad_kg=Decimal("10")) for _ in range(3)]
        self.assertEqual([l.codigo_lote for l in lotes], [f"3834301{n:02d}28092026" for n in (1, 2, 3)])
        self.assertEqual(self.disponible(), Decimal("100"))

    def test_correlativo_por_prefijo_visible_no_por_entidad_origen(self):
        self.nuevo(cantidad_kg=Decimal("10"))
        otra = self.otra_lista(mismo_origen=False)
        origen = otra.detalle_recepcion.origen_sernapesca
        origen.folio_origen = "OTRO-9938343"
        origen.save()
        lote = self.nuevo(partida=otra, cantidad_kg=Decimal("10"))
        self.assertEqual(lote.codigo_lote, "38343010228092026")

    def test_fecha_y_especie_separan_correlativo(self):
        self.nuevo(cantidad_kg=Decimal("10"))
        otro_dia = self.crear_lote(fecha_elaboracion=date(2026, 9, 29), cantidad_kg=Decimal("10"))
        self.assertEqual(otro_dia.codigo_lote, "38343010129092026")
        self.otra_especie.codigo_lote = "02"
        self.otra_especie.save()
        otra = self.otra_lista(especie=self.otra_especie, ruta=self.crear_ruta(especie=self.otra_especie))
        self.assertEqual(self.nuevo(partida=otra, cantidad_kg=Decimal("10")).codigo_lote, "38343020128092026")

    def test_correlativo_99_agotado_error_controlado(self):
        LoteProduccion.objects.bulk_create([
            LoteProduccion(codigo_lote=f"3834301{n:02d}28092026", especie=self.especie, ruta_proceso=self.ruta,
                           fecha_elaboracion=date(2026, 9, 28), registrado_por=self.usuario)
            for n in range(1, 100)
        ])
        with self.assertRaisesRegex(ValidationError, "No hay más correlativos"):
            self.nuevo()
        self.assertEqual(LoteProduccion.objects.count(), 99)
        self.assertFalse(ConsumoLote.objects.exists())

    def test_codigo_historico_ocupado_cuenta_aunque_no_tenga_origen(self):
        LoteProduccion.objects.create(codigo_lote="38343010128092026", especie=self.especie,
                                     fecha_elaboracion=date(2020, 1, 1), registrado_por=self.usuario)
        self.assertEqual(self.nuevo().codigo_lote, "38343010228092026")

    def test_varios_seguimientos_misma_entidad_origen_permitidos(self):
        lote = self.nuevo(cantidad_kg=Decimal("70"))
        otra = self.otra_lista("50")
        self.agregar(lote, otra, "50")
        self.assertEqual(lote.consumos.count(), 2)
        self.assertEqual(origen_unico_lote(lote), self.origen.pk)

    def test_otra_entidad_origen_mismo_folio_se_rechaza_service_selector_post(self):
        lote = self.nuevo()
        otra = self.otra_lista(mismo_origen=False)
        origen = otra.detalle_recepcion.origen_sernapesca
        origen.folio_origen = self.origen.folio_origen
        origen.save()
        self.assertNotEqual(origen.pk, self.origen.pk)
        with self.assertRaisesRegex(ValidationError, "origen diferente"):
            self.agregar(lote, otra, "10")
        self.assertNotIn(otra, partidas_para_lote(lote))
        self.client.force_login(self.usuario)
        respuesta = self.client.post(self.url_lote("agregar_producto_lote", lote.pk), {"partida": otra.pk, "cantidad_kg": "10"})
        self.assertIn("partida", respuesta.context["form"].errors)
        self.assertEqual(lote.consumos.count(), 1)
        self.assertEqual(self.disponible(otra), Decimal("40"))

    def test_historico_mixto_consultable_bloquea_nuevos_aportes(self):
        lote = self.nuevo()
        LoteProduccion.objects.filter(pk=lote.pk).update(codigo_lote="TEST-MER-280926-A")
        lote.refresh_from_db()
        otra = self.otra_lista(mismo_origen=False)
        ConsumoLote.objects.create(lote_produccion=lote, partida=otra, cantidad_kg_utilizada=Decimal("10"))
        tercera = self.otra_lista()
        with self.assertRaisesRegex(ValidationError, "requiere revisión"):
            self.agregar(lote, tercera)
        self.assertFalse(partidas_para_lote(lote).exists())
        self.client.force_login(self.usuario)
        respuesta = self.client.get(self.url_lote("detalle_lote", lote.pk))
        self.assertContains(respuesta, "TEST-MER-280926-A")
        self.assertContains(respuesta, "requiere revisión")
        self.assertNotContains(respuesta, ">Agregar producto</a>")
        self.assertEqual(lote.consumos.count(), 2)

    def test_historico_sin_aportes_no_inventa_origen(self):
        lote = LoteProduccion.objects.create(codigo_lote="TEST-SIN-APORTES", especie=self.especie, ruta_proceso=self.ruta,
                                            fecha_elaboracion=date(2020, 1, 1), registrado_por=self.usuario)
        with self.assertRaisesRegex(ValidationError, "requiere revisión"):
            self.agregar(lote)
        self.assertFalse(AgregarConsumoLoteForm(lote=lote).fields["partida"].queryset.exists())

    def test_form_precargado_total_editable_parcial_y_post_codigo_ignorado(self):
        self.client.force_login(self.usuario)
        url = self.url_lote("crear_lote", self.partida.pk)
        respuesta = self.client.get(url)
        self.assertEqual(respuesta.context["form"]["cantidad_kg"].value(), Decimal("130"))
        self.assertNotContains(respuesta, 'name="codigo"')
        self.assertContains(respuesta, "38343")
        self.assertContains(respuesta, "Se generará automáticamente")
        datos = self.datos_form(cantidad="100") | {"fecha_elaboracion": "2026-09-28", "codigo": "MANIPULADO"}
        self.assertEqual(self.client.post(url, datos).status_code, 302)
        self.assertEqual(LoteProduccion.objects.get().codigo_lote, "38343010128092026")
        self.assertEqual(self.client.get(url).context["form"]["cantidad_kg"].value(), Decimal("30"))
        datos.pop("codigo")
        datos["cantidad_kg"] = "30"
        self.assertEqual(self.client.post(url, datos).status_code, 302)
        self.assertEqual(self.disponible(), Decimal("0"))

    def test_caja_qr_nuevo_codigo_y_origen_real(self):
        lote = self.nuevo()
        from .models import PresentacionBolsa
        bolsa = PresentacionBolsa.objects.create(nombre="Bolsa v2", peso_nominal_kg=Decimal("5"))
        caja = crear_caja(lote=lote, composiciones=[{"presentacion": bolsa, "cantidad": 5}], peso_neto_kg=Decimal("24.82"), usuario=self.usuario)
        identificador = caja.identificador_qr
        self.client.force_login(self.usuario)
        for url in (self.url_lote("detalle_caja", caja.pk), reverse("producto_terminado:consulta_caja_qr", args=[identificador])):
            self.assertContains(self.client.get(url), "38343 0101 28092026")
        respuesta = self.client.get(reverse("producto_terminado:consulta_caja_qr", args=[identificador]))
        self.assertContains(respuesta, "38343")
        self.assertContains(respuesta, f"Seguimiento #{self.partida.pk}")
        self.assertEqual(self.client.get(self.url_lote("caja_qr_png", caja.pk))["Content-Type"], "image/png")
        caja.refresh_from_db()
        self.assertEqual(caja.identificador_qr, identificador)


class ConcurrenciaCodigoV2Tests(DatosLotes, TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_origenes_distintos_mismo_prefijo_generan_correlativos_distintos(self):
        self.preparar()
        self.pendiente()
        self.pesar("100")
        otra = self.otra_lista("100", mismo_origen=False)
        for partida, folio in ((self.partida, "38343"), (otra, "9938343")):
            origen = partida.detalle_recepcion.origen_sernapesca
            origen.folio_origen = folio
            origen.save()
        barrera = Barrier(2)
        def ejecutar(pk):
            close_old_connections()
            try:
                partida = PartidaProceso.objects.get(pk=pk)
                barrera.wait(timeout=10)
                return self.crear_lote(partida=partida, cantidad_kg=Decimal("60"), fecha_elaboracion=date(2026, 9, 28)).codigo_lote
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            codigos = list(pool.map(ejecutar, [self.partida.pk, otra.pk]))
        self.assertCountEqual(codigos, ["38343010128092026", "38343010228092026"])
        self.assertEqual(ConsumoLote.objects.count(), 2)


class MigracionEspeciesV2Tests(DatosCajas, TransactionTestCase):
    def test_migracion_asigna_por_orden_pk_preserva_negocio_y_qr(self):
        self.preparar_cajas()
        self.caja()
        modelos = [LoteProduccion, ConsumoLote, Caja, ComposicionCaja]
        antes = {m: list(m.objects.order_by("pk").values()) for m in modelos}
        ids = list(Especie.objects.order_by("pk").values_list("pk", flat=True))
        actual = MigrationExecutor(connection).loader.graph.leaf_nodes()
        try:
            MigrationExecutor(connection).migrate([("trazabilidad", "0011_caja_identificador_qr")])
            MigrationExecutor(connection).migrate(actual)
            self.assertEqual(list(Especie.objects.order_by("pk").values_list("pk", flat=True)), ids)
            self.assertEqual(list(Especie.objects.order_by("pk").values_list("codigo_lote", flat=True)), [f"{n:02d}" for n in range(1,len(ids)+1)])
            for m in modelos:
                self.assertEqual(list(m.objects.order_by("pk").values()), antes[m])
        finally:
            MigrationExecutor(connection).migrate(actual)
