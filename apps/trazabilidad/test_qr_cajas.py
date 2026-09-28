from decimal import Decimal
from importlib import import_module
from unittest.mock import patch
from uuid import UUID, uuid4

import png
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.forms import modelform_factory
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.usuarios.models import Rol, Usuario
from .forms import CajaForm
from .models import Caja, ComposicionCaja, ConsumoLote, EstanciaPartida, LoteProduccion, PresentacionBolsa, UnidadFrio
from .qr import construir_url_qr_caja, generar_png_qr_caja
from .services import agregar_consumo_lote
from .test_cajas import DatosCajas


class DatosQR(DatosCajas):
    def preparar_qr(self):
        self.preparar_cajas(total="70")
        self.especie.nombre = "Congrio"
        self.especie.save()
        self.lote.codigo_lote = "TEST-QR-001"
        self.lote.save()
        self.otra = self.otra_lista("40", mismo_origen=False)
        # Fixture histórico mixto: las nuevas operaciones ya no admiten esta mezcla.
        ConsumoLote.objects.create(lote_produccion=self.lote, partida=self.otra, cantidad_kg_utilizada=Decimal("30"))
        for partida, folio in ((self.partida, "ABC123"), (self.otra, "DEF456")):
            origen = partida.detalle_recepcion.origen_sernapesca
            origen.folio_origen = folio
            origen.save()
        unidad = UnidadFrio.objects.create(nombre="Mantención origen A", tipo="MANTENCION")
        EstanciaPartida.objects.create(partida=self.partida, unidad_frio=unidad,
                                      fecha_hora_ingreso=timezone.now(), fecha_hora_salida=timezone.now(), ingresado_por=self.usuario)
        self.registro = self.caja(peso_neto_kg=Decimal("24.82"))

    def consulta(self, caja=None):
        return reverse("producto_terminado:consulta_caja_qr", args=[(caja or self.registro).identificador_qr])

    def png_url(self, caja=None):
        return reverse("producto_terminado:caja_qr_png", args=[(caja or self.registro).pk])


class QRCajasTests(DatosQR, TestCase):
    def setUp(self):
        self.preparar_qr()
        self.client.force_login(self.usuario)

    def test_caja_nueva_recibe_uuid_aleatorio_independiente_pk(self):
        identificador = self.registro.identificador_qr
        self.assertIsInstance(identificador, UUID)
        self.assertEqual(identificador.version, 4)
        self.assertNotEqual(identificador.int, self.registro.pk)
        self.registro.refresh_from_db()
        self.assertEqual(identificador, self.registro.identificador_qr)

    def test_uuid_unico_y_constraint(self):
        otra = self.caja()
        self.assertNotEqual(self.registro.identificador_qr, otra.identificador_qr)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Caja.objects.create(identificador_qr=otra.identificador_qr, lote_produccion=self.lote,
                                registrado_por=self.usuario, fecha_armado=timezone.now())

    def test_uuid_inmutable_save_y_save_parcial(self):
        original = self.registro.identificador_qr
        for kwargs in ({}, {"update_fields": ["identificador_qr"]}):
            self.registro.identificador_qr = uuid4()
            with self.assertRaises(ValidationError):
                self.registro.save(**kwargs)
            self.registro.refresh_from_db()
            self.assertEqual(original, self.registro.identificador_qr)
        self.registro.save(update_fields=["peso_neto_kg"])
        self.assertEqual(original, self.registro.identificador_qr)
        reconstruida = Caja(pk=self.registro.pk, lote_produccion=self.lote, registrado_por=self.usuario, fecha_armado=timezone.now())
        with self.assertRaises(ValidationError):
            reconstruida.save()

    def test_uuid_no_editable_en_forms_ni_post(self):
        self.assertNotIn("identificador_qr", CajaForm().fields)
        self.assertNotIn("identificador_qr", modelform_factory(Caja, fields="__all__")().fields)
        falso = uuid4()
        respuesta = self.client.post(self.url_lote("crear_caja", self.lote.pk), self.datos_caja() | {"identificador_qr": str(falso)})
        self.assertEqual(respuesta.status_code, 302)
        self.assertNotEqual(Caja.objects.latest("pk").identificador_qr, falso)

    def test_uuid_admin_readonly_y_busqueda(self):
        request = RequestFactory().get("/admin/")
        request.user = Usuario.objects.create_superuser(username="admin_qr")
        configuracion = admin.site._registry[Caja]
        self.assertIn("identificador_qr", configuracion.get_readonly_fields(request, self.registro))
        self.assertFalse(configuracion.has_change_permission(request, self.registro))
        resultados, _ = configuracion.get_search_results(request, configuracion.get_queryset(request), str(self.registro.identificador_qr))
        self.assertEqual(list(resultados.values_list("pk", flat=True)), [self.registro.pk])
        self.client.force_login(request.user)
        self.assertContains(self.client.get(reverse("admin:trazabilidad_caja_change", args=[self.registro.pk])), str(self.registro.identificador_qr))

    def test_consulta_resuelve_por_uuid_no_pk_y_desconocido_404(self):
        respuesta = self.client.get(self.consulta())
        self.assertEqual(respuesta.context["caja"].pk, self.registro.pk)
        self.assertEqual(self.client.get(reverse("producto_terminado:consulta_caja_qr", args=[uuid4()])).status_code, 404)
        self.assertEqual(self.client.get(f"/producto-terminado/cajas/qr/{self.registro.pk}/").status_code, 404)

    def test_consulta_y_png_requieren_login_y_preservan_next(self):
        self.client.logout()
        for url in (self.consulta(), self.png_url()):
            respuesta = self.client.get(url)
            self.assertEqual(respuesta.status_code, 302)
            self.assertIn("next=" + url, respuesta.url)

    def test_consulta_y_png_roles_operativos(self):
        enc, _ = Rol.objects.get_or_create(codigo="ENCARGADA", defaults={"nombre": "Encargada"})
        usuarios = [self.jefe, self.usuario, Usuario.objects.create_user(username="enc_qr", rol=enc), Usuario.objects.create_superuser(username="super_qr")]
        for usuario in usuarios:
            self.client.force_login(usuario)
            for url in (self.consulta(), self.png_url()):
                with self.subTest(usuario=usuario.username, url=url):
                    self.assertEqual(self.client.get(url).status_code, 200)

    def test_otros_roles_denegados_y_solo_get(self):
        rol = Rol.objects.create(codigo="LECTOR_QR", nombre="Lector")
        usuario = Usuario.objects.create_user(username="lector_qr", rol=rol)
        self.client.force_login(usuario)
        for url in (self.consulta(), self.png_url()):
            self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.usuario)
        for url in (self.consulta(), self.png_url()):
            self.assertEqual(self.client.post(url).status_code, 405)

    def test_consulta_caja_pesos_composicion_producto_lote_fechas(self):
        respuesta = self.client.get(self.consulta())
        for texto in (f"Trazabilidad de Caja #{self.registro.pk}", "24,82 kg", "25,00 kg", "-0,18 kg", "Bolsa 5 kg", "5 bolsas", "TEST-QR-001", "Congrio", "Filete IQF", "26/09/2026", "26/10/2026", self.usuario.username):
            self.assertContains(respuesta, texto)

    def test_origenes_separados_con_aporte_recepcion_folio(self):
        respuesta = self.client.get(self.consulta())
        self.assertContains(respuesta, "data-aporte=", count=2)
        for texto in (f"Seguimiento #{self.partida.pk}", f"Seguimiento #{self.otra.pk}", "70,00 kg aportados", "30,00 kg aportados", "ABC123", "DEF456", f"#{self.partida.detalle_recepcion.recepcion_id}", f"#{self.otra.detalle_recepcion.recepcion_id}"):
            self.assertContains(respuesta, texto)
        aportes = list(respuesta.context["aportes"])
        self.assertEqual([a.partida.detalle_recepcion.origen_sernapesca.folio_origen for a in aportes], ["ABC123", "DEF456"])
        self.assertEqual([a.cantidad_kg_utilizada for a in aportes], [Decimal("70"), Decimal("30")])

    def test_procesamiento_etapas_frio_postproceso_por_seguimiento(self):
        respuesta = self.client.get(self.consulta())
        for texto in ("Descabezado", "Fileteo", "Emparrillado", "Mantención origen A", "Túnel de congelado 1", "Ingreso:", "Salida:", "125,00 kg", "40,00 kg"):
            self.assertContains(respuesta, texto)
        self.assertEqual([len(a.partida.postprocesos_qr) for a in respuesta.context["aportes"]], [1, 1])

    def test_etapas_configurables_sin_lista_fija(self):
        tipo = self.tipos["DESCABEZADO"]
        tipo.nombre = "Limpieza especial configurada"
        tipo.save()
        self.assertContains(self.client.get(self.consulta()), tipo.nombre)

    def test_navegacion_y_permiso_recepcion_se_conservan(self):
        url_recepcion = reverse("trazabilidad:detalle_recepcion", args=[self.partida.detalle_recepcion.recepcion_id])
        respuesta = self.client.get(self.consulta())
        self.assertContains(respuesta, self.url_lote("detalle_lote", self.lote.pk))
        self.assertContains(respuesta, reverse("trazabilidad:detalle_partida", args=[self.partida.pk]))
        self.assertNotContains(respuesta, url_recepcion)
        self.client.force_login(self.jefe)
        self.assertContains(self.client.get(self.consulta()), url_recepcion)
        self.assertEqual(self.client.get(url_recepcion).status_code, 200)

    def test_detalle_muestra_uuid_qr_y_enlace_sin_regenerar(self):
        respuesta = self.client.get(self.url_lote("detalle_caja", self.registro.pk))
        for texto in (str(self.registro.identificador_qr), self.png_url(), self.consulta(), "Ver trazabilidad"):
            self.assertContains(respuesta, texto)
        self.assertNotContains(respuesta, "Regenerar")
        self.assertContains(self.client.get(self.consulta()), 'aria-current="page"', count=1)

    @override_settings(ALLOWED_HOSTS=["planta.example"])
    def test_url_contenido_qr_absoluta_solo_uuid_no_datos(self):
        request = RequestFactory().get("/", secure=True, HTTP_HOST="planta.example")
        self.assertEqual(construir_url_qr_caja(request, self.registro), "https://planta.example" + self.consulta())
        with patch("apps.trazabilidad.qr.qrcode.make") as generador:
            generar_png_qr_caja(request, self.registro)
        self.assertEqual(generador.call_args.args, ("https://planta.example" + self.consulta(),))

    def test_png_valido_content_type_en_memoria_y_no_cache(self):
        respuesta = self.client.get(self.png_url())
        self.assertEqual(respuesta["Content-Type"], "image/png")
        self.assertTrue(respuesta.content.startswith(b"\x89PNG\r\n\x1a\n"))
        ancho, alto, filas, _ = png.Reader(bytes=respuesta.content).read()
        self.assertEqual(ancho, alto)
        self.assertGreater(ancho, 100)
        self.assertEqual(len(list(filas)), alto)
        self.assertIn("no-store", respuesta["Cache-Control"])
        self.assertIn("no-store", self.client.get(self.consulta())["Cache-Control"])
        self.assertEqual(self.client.get(reverse("producto_terminado:caja_qr_png", args=[999999])).status_code, 404)

    def test_historica_sin_neto_nominal_500_sin_ruta_inactiva(self):
        LoteProduccion.objects.filter(pk=self.lote.pk).update(ruta_proceso=None)
        historica = Caja.objects.create(lote_produccion=self.lote, peso_total_kg=Decimal("500"), fecha_armado=timezone.now(), registrado_por=self.usuario)
        ComposicionCaja.objects.create(caja=historica, presentacion=self.bolsa5, cantidad=100, peso_unitario_kg=Decimal("5"))
        PresentacionBolsa.objects.filter(pk=self.bolsa5.pk).update(activo=False)
        respuesta = self.client.get(self.consulta(historica))
        for texto in ("Peso neto no documentado", "500,00 kg", "Sin ruta documentada", "Bolsa 5 kg"):
            self.assertContains(respuesta, texto)
        self.assertEqual(self.client.get(self.png_url(historica)).status_code, 200)
        historica.refresh_from_db()
        self.assertIsNone(historica.peso_neto_kg)
        self.assertEqual(historica.peso_total_kg, Decimal("500"))

    def test_historicos_sin_aportes_y_sin_actividad(self):
        self.partida.eventos.all().update(fecha_hora_termino=None)
        self.otra.pesajes.all().delete()
        self.assertContains(self.client.get(self.consulta()), "Peso postproceso no documentado")
        self.assertContains(self.client.get(self.consulta()), "En curso")
        self.lote.consumos.all().delete()
        self.assertContains(self.client.get(self.consulta()), "no tiene aportes documentados")

    def test_consulta_sin_n_mas_uno_al_aumentar_origenes(self):
        with CaptureQueriesContext(connection) as antes:
            self.client.get(self.consulta())
        tercera = self.otra_lista("10")
        ConsumoLote.objects.create(lote_produccion=self.lote, partida=tercera, cantidad_kg_utilizada=Decimal("10"))
        with CaptureQueriesContext(connection) as despues:
            respuesta = self.client.get(self.consulta())
        self.assertEqual(len(antes), len(despues))
        self.assertLessEqual(len(despues), 10)
        self.assertContains(respuesta, "data-aporte=", count=3)


class MigracionQRCajasTests(DatosCajas, TransactionTestCase):
    def test_migracion_historicos_uuid_distintos_sin_modificar_negocio(self):
        self.preparar_cajas()
        self.caja(peso_neto_kg=Decimal("24.82"))
        self.caja(peso_neto_kg=Decimal("24.91"))
        Caja.objects.create(lote_produccion=self.lote, peso_total_kg=Decimal("500"), registrado_por=self.usuario, fecha_armado=timezone.now())
        campos = [f.name for f in Caja._meta.fields if f.name != "identificador_qr"]
        cajas_antes = list(Caja.objects.order_by("pk").values(*campos))
        composiciones_antes = list(ComposicionCaja.objects.order_by("pk").values())
        anterior = [("trazabilidad", "0010_caja_peso_neto_kg")]
        actual = [("trazabilidad", "0011_caja_identificador_qr")]
        ultimas = MigrationExecutor(connection).loader.graph.leaf_nodes()
        try:
            MigrationExecutor(connection).migrate(anterior)
            MigrationExecutor(connection).migrate(actual)
            ids = list(Caja.objects.values_list("identificador_qr", flat=True))
            self.assertEqual(len(set(ids)), 3)
            self.assertTrue(all(isinstance(i, UUID) and i.version == 4 for i in ids))
            self.assertEqual(cajas_antes, list(Caja.objects.order_by("pk").values(*campos)))
            self.assertEqual(composiciones_antes, list(ComposicionCaja.objects.order_by("pk").values()))
            # Ejecutar de nuevo el llenado no reemplaza identificadores existentes.
            estado = MigrationExecutor(connection).loader.project_state(actual).apps
            with connection.schema_editor() as editor:
                import_module("apps.trazabilidad.migrations.0011_caja_identificador_qr").asignar_identificadores(estado, editor)
            self.assertEqual(ids, list(Caja.objects.values_list("identificador_qr", flat=True)))
        finally:
            MigrationExecutor(connection).migrate(ultimas)
