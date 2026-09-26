from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from importlib import import_module
from threading import Barrier
from unittest.mock import patch

from django.apps import apps
from django.contrib import admin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, close_old_connections, connection, connections, transaction
from django.db.models.deletion import ProtectedError
from django.test import RequestFactory, TestCase, TransactionTestCase, skipUnlessDBFeature
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.usuarios.models import Rol, Usuario
from .forms import ComposicionCajaFormSet, CajaForm
from .models import Caja, ComposicionCaja, LoteProduccion, PresentacionBolsa
from .selectors import cajas_con_peso, lotes_con_empaque
from .services import crear_caja, agregar_consumo_lote
from .test_lotes import DatosLotes


class DatosCajas(DatosLotes):
    def preparar_cajas(self, total="125"):
        self.preparar()
        self.pendiente()
        self.pesar("125")
        self.lote = self.crear_lote(codigo="TEST-CAJAS-001", cantidad_kg=Decimal(total))
        self.bolsa1, _ = PresentacionBolsa.objects.get_or_create(nombre="Bolsa 1 kg", defaults={"peso_nominal_kg": Decimal("1")})
        self.bolsa5, _ = PresentacionBolsa.objects.get_or_create(nombre="Bolsa 5 kg", defaults={"peso_nominal_kg": Decimal("5")})

    def caja(self, filas=None, **kwargs):
        # Datos de prueba: por defecto una medición de 25 kg; nunca inferida en producción.
        peso_neto_kg = kwargs.pop("peso_neto_kg", Decimal("25"))
        return crear_caja(lote=kwargs.pop("lote", self.lote), usuario=kwargs.pop("usuario", self.usuario),
                          composiciones=filas if filas is not None else [{"presentacion": self.bolsa5, "cantidad": 5}], peso_neto_kg=peso_neto_kg, **kwargs)

    def resumen(self):
        return lotes_con_empaque().get(pk=self.lote.pk)

    def peso(self, caja):
        return cajas_con_peso().get(pk=caja.pk).peso_teorico_kg

    def datos_caja(self, filas=None, peso="25.00"):
        filas = filas if filas is not None else [(self.bolsa5.pk, "3"), (self.bolsa1.pk, "10")]
        datos = {"peso_neto_kg": peso, "composiciones-TOTAL_FORMS": str(len(filas)), "composiciones-INITIAL_FORMS": "0",
                 "composiciones-MIN_NUM_FORMS": "0", "composiciones-MAX_NUM_FORMS": "50"}
        for i, (presentacion, cantidad) in enumerate(filas):
            datos[f"composiciones-{i}-presentacion"] = str(presentacion)
            datos[f"composiciones-{i}-cantidad"] = cantidad
        return datos


class CajasTests(DatosCajas, TestCase):
    def setUp(self):
        self.preparar_cajas()

    def test_caja_requiere_peso_neto_real(self):
        self.assertFalse(CajaForm({}).is_valid())
        with self.assertRaises(ValidationError):
            self.caja(peso_neto_kg=None)
        self.client.force_login(self.usuario)
        datos = self.datos_caja()
        del datos["peso_neto_kg"]
        respuesta = self.client.post(self.url_lote("crear_caja", self.lote.pk), datos)
        self.assertIn("peso_neto_kg", respuesta.context["form"].errors)
        self.assertFalse(Caja.objects.exists())

    def test_peso_neto_real_mayor_cero_decimal_finito_y_precision(self):
        for peso in ("0", "-1", "NaN", "Infinity", "24.821"):
            with self.subTest(peso=peso), self.assertRaises(ValidationError):
                self.caja(peso_neto_kg=Decimal(peso))
            self.assertFalse(CajaForm({"peso_neto_kg": peso}).is_valid())
        self.assertFalse(Caja.objects.exists())
        self.assertFalse(ComposicionCaja.objects.exists())

    def test_caja_real_superior_25kg_incluso_500_se_rechaza_sin_parciales(self):
        for peso in ("25.01", "50", "500"):
            with self.subTest(peso=peso), self.assertRaises(ValidationError):
                self.caja(peso_neto_kg=Decimal(peso))
            self.assertFalse(CajaForm({"peso_neto_kg": peso}).is_valid())
        self.assertFalse(Caja.objects.exists())
        self.assertFalse(ComposicionCaja.objects.exists())

    def test_peso_teorico_superior_25kg_rechaza_real_24_sin_parciales(self):
        diez = PresentacionBolsa.objects.create(nombre="Bolsa 10 kg", peso_nominal_kg=Decimal("10"))
        with self.assertRaises(ValidationError):
            self.caja([{"presentacion": diez, "cantidad": 3}], peso_neto_kg=Decimal("24"))
        self.assertFalse(ComposicionCajaFormSet(self.datos_caja([(diez.pk, "3")], peso="24"), prefix="composiciones").is_valid())
        self.assertFalse(Caja.objects.exists())
        self.assertFalse(ComposicionCaja.objects.exists())

    def test_peso_real_y_teorico_exactamente_25kg_se_permite(self):
        caja = self.caja(peso_neto_kg=Decimal("25"))
        self.assertEqual(caja.peso_neto_kg, Decimal("25"))
        self.assertEqual(self.peso(caja), Decimal("25"))

    def test_total_empacado_y_disponible_usan_real_no_teorico(self):
        self.caja(peso_neto_kg=Decimal("24.82"))
        self.assertEqual(self.resumen().disponible_empacar, Decimal("100.18"))
        self.caja(peso_neto_kg=Decimal("24.91"))
        self.assertEqual(self.resumen().total_empacado, Decimal("49.73"))
        self.assertEqual(self.resumen().disponible_empacar, Decimal("75.27"))

    def test_real_puede_diferir_sin_tolerancias_y_diferencia_correcta(self):
        for cantidad, real, diferencia in ((5, "24.82", "-0.18"), (4, "20.15", "0.15"), (5, "1", "-24")):
            caja = self.caja([{"presentacion": self.bolsa5, "cantidad": cantidad}], peso_neto_kg=Decimal(real))
            self.assertEqual(cajas_con_peso().get(pk=caja.pk).diferencia_kg, Decimal(diferencia))

    def test_caja_parcial_menor_25kg_permite_saldo_final(self):
        self.lote.consumos.update(cantidad_kg_utilizada=Decimal("10.12"))
        caja = self.caja([{"presentacion": self.bolsa1, "cantidad": 10}], peso_neto_kg=Decimal("10.08"))
        self.assertEqual(caja.peso_neto_kg, Decimal("10.08"))
        self.assertEqual(self.resumen().disponible_empacar, Decimal("0.04"))

    def test_sobreempaque_se_valida_con_real_y_rollback(self):
        self.lote.consumos.update(cantidad_kg_utilizada=Decimal("10"))
        filas = [{"presentacion": self.bolsa5, "cantidad": 2}]
        with self.assertRaises(ValidationError):
            self.caja(filas, peso_neto_kg=Decimal("10.15"))
        self.assertFalse(Caja.objects.exists())
        self.assertFalse(ComposicionCaja.objects.exists())
        self.caja(filas, peso_neto_kg=Decimal("9.92"))
        self.assertEqual(self.resumen().disponible_empacar, Decimal("0.08"))

    def test_teorico_no_limita_saldo_si_real_cabe(self):
        self.lote.consumos.update(cantidad_kg_utilizada=Decimal("10"))
        self.caja(peso_neto_kg=Decimal("9.92"))
        self.assertEqual(self.resumen().disponible_empacar, Decimal("0.08"))

    def test_historica_500kg_visible_sin_inventar_neto_ni_saldo(self):
        caja = Caja.objects.create(lote_produccion=self.lote, peso_total_kg=Decimal("500"),
                                  registrado_por=self.usuario, fecha_armado=timezone.now())
        ComposicionCaja.objects.create(caja=caja, presentacion=self.bolsa5, cantidad=100, peso_unitario_kg=Decimal("5"))
        leida = cajas_con_peso().get(pk=caja.pk)
        self.assertEqual(leida.peso_teorico_kg, Decimal("500"))
        self.assertIsNone(leida.peso_neto_kg)
        self.assertIsNone(leida.diferencia_kg)
        self.assertIsNone(self.resumen().total_empacado)
        self.assertIsNone(self.resumen().disponible_empacar)
        with self.assertRaises(ValidationError):
            self.caja()
        self.client.force_login(self.usuario)
        for url in (self.url_lote("detalle_caja", caja.pk), self.url_lote("lista_cajas")):
            self.assertContains(self.client.get(url), "Peso neto no documentado")
        self.assertContains(self.client.get(self.url_lote("detalle_caja", caja.pk)), "500,00 kg")
        for url in (self.url_lote("lista_lotes"), self.url_lote("detalle_lote", self.lote.pk), self.url_lote("crear_caja", self.lote.pk)):
            self.assertContains(self.client.get(url), "Requiere revisión")
        caja.refresh_from_db()
        self.assertEqual(caja.peso_total_kg, Decimal("500"))

    def test_peso_real_documentado_historico_superior_25_no_rompe_lectura(self):
        caja = Caja.objects.create(lote_produccion=self.lote, peso_neto_kg=Decimal("500"),
                                  registrado_por=self.usuario, fecha_armado=timezone.now())
        self.client.force_login(self.usuario)
        self.assertContains(self.client.get(self.url_lote("detalle_caja", caja.pk)), "500,00 kg")
        self.assertEqual(self.resumen().total_empacado, Decimal("500"))
        with self.assertRaises(ValidationError):
            self.caja()

    def test_vistas_muestran_real_teorico_y_diferencia(self):
        caja = self.caja(peso_neto_kg=Decimal("24.82"))
        self.client.force_login(self.usuario)
        detalle = self.client.get(self.url_lote("detalle_caja", caja.pk))
        for texto in ("24,82 kg", "25,00 kg", "-0,18 kg", "Subtotal nominal"):
            self.assertContains(detalle, texto)
        self.assertContains(self.client.get(self.url_lote("lista_cajas")), "24,82 kg")
        self.assertContains(self.client.get(self.url_lote("detalle_lote", self.lote.pk)), "100,18 kg")

    def test_presentacion_peso_mayor_cero(self):
        for peso in ("0", "-1", "0.001"):
            with self.subTest(peso=peso), self.assertRaises(ValidationError):
                PresentacionBolsa(nombre="Inválida", peso_nominal_kg=Decimal(peso)).full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            PresentacionBolsa.objects.create(nombre="Inválida BD", peso_nominal_kg=0)

    def test_presentacion_inactiva_no_aparece_en_formulario(self):
        PresentacionBolsa.objects.filter(pk=self.bolsa5.pk).update(activo=False)
        formset = ComposicionCajaFormSet(prefix="composiciones")
        self.assertNotIn(self.bolsa5, formset.forms[0].fields["presentacion"].queryset)
        formset = ComposicionCajaFormSet(self.datos_caja(), prefix="composiciones")
        self.assertFalse(formset.is_valid())

    def test_presentacion_inactiva_historica_sigue_visible(self):
        caja = self.caja()
        PresentacionBolsa.objects.filter(pk=self.bolsa5.pk).update(activo=False)
        self.client.force_login(self.usuario)
        respuesta = self.client.get(self.url_lote("detalle_caja", caja.pk))
        self.assertContains(respuesta, "Bolsa 5 kg")
        self.assertContains(respuesta, "25,00 kg")
        self.assertEqual(self.peso(caja), Decimal("25"))

    def test_peso_decimal_presentacion_funciona(self):
        media = PresentacionBolsa.objects.create(nombre="Bolsa 0.5 kg", peso_nominal_kg=Decimal("0.50"))
        caja = self.caja([{"presentacion": media, "cantidad": 10}])
        self.assertEqual(self.peso(caja), Decimal("5.00"))
        self.assertEqual(caja.composiciones.get().subtotal_kg, Decimal("5.00"))

    def test_presentacion_arbitraria_sin_reglas_1_o_5(self):
        presentacion = PresentacionBolsa.objects.create(nombre="Bolsa 2.5 kg", peso_nominal_kg=Decimal("2.50"))
        self.assertEqual(self.peso(self.caja([{"presentacion": presentacion, "cantidad": 3}])), Decimal("7.50"))

    def test_crear_caja_con_una_presentacion(self):
        caja = self.caja()
        self.assertEqual(caja.composiciones.count(), 1)
        self.assertEqual(self.peso(caja), Decimal("25"))
        self.assertEqual(str(caja), f"Caja #{caja.pk}")
        self.assertIsNone(caja.codigo_caja)
        self.assertIsNone(caja.peso_total_kg)

    def test_crear_caja_con_varias_presentaciones_subtotales_y_total(self):
        caja = self.caja([{"presentacion": self.bolsa5, "cantidad": 3}, {"presentacion": self.bolsa1, "cantidad": 10}])
        self.assertEqual(caja.composiciones.count(), 2)
        self.assertEqual(caja.composiciones.get(presentacion=self.bolsa5).subtotal_kg, Decimal("15"))
        self.assertEqual(caja.composiciones.get(presentacion=self.bolsa1).subtotal_kg, Decimal("10"))
        self.assertEqual(self.peso(caja), Decimal("25"))

    def test_caja_pertenece_a_un_lote_y_usuario_fecha_se_conservan(self):
        antes = timezone.now()
        caja = self.caja(usuario=self.jefe)
        self.assertEqual(caja.lote_produccion_id, self.lote.pk)
        self.assertEqual(caja.registrado_por, self.jefe)
        self.assertGreaterEqual(caja.fecha_armado, antes)

    def test_caja_no_puede_quedar_vacia(self):
        with self.assertRaises(ValidationError):
            self.caja([])
        self.assertFalse(Caja.objects.exists())
        self.assertFalse(ComposicionCaja.objects.exists())

    def test_cantidad_bolsas_mayor_cero(self):
        for cantidad in (0, -2):
            with self.subTest(cantidad=cantidad), self.assertRaises(ValidationError):
                self.caja([{"presentacion": self.bolsa1, "cantidad": cantidad}])
        self.assertFalse(Caja.objects.exists())

    def test_cantidad_bolsas_entera_sin_conversion_silenciosa(self):
        for cantidad in (1.5, Decimal("1.5"), "2", True, None, 4294967296):
            with self.subTest(cantidad=cantidad), self.assertRaises(ValidationError):
                self.caja([{"presentacion": self.bolsa1, "cantidad": cantidad}])
        self.assertFalse(ComposicionCaja.objects.exists())

    def test_no_repetir_presentacion_en_misma_caja(self):
        with self.assertRaises(ValidationError):
            self.caja([{"presentacion": self.bolsa5, "cantidad": 2}, {"presentacion": self.bolsa5, "cantidad": 3}])
        self.assertFalse(Caja.objects.exists())
        self.assertFalse(ComposicionCaja.objects.exists())

    def test_constraint_presentacion_unica_y_cantidad_positiva(self):
        caja = self.caja()
        for presentacion, cantidad in ((self.bolsa5, 1), (self.bolsa1, 0)):
            with self.assertRaises(IntegrityError), transaction.atomic():
                ComposicionCaja.objects.create(caja=caja, presentacion=presentacion, cantidad=cantidad, peso_unitario_kg=1)

    def test_fechas_caja_provienen_del_lote(self):
        caja = self.caja()
        self.client.force_login(self.usuario)
        respuesta = self.client.get(self.url_lote("detalle_caja", caja.pk))
        self.assertContains(respuesta, "26/09/2026")
        self.assertContains(respuesta, "26/10/2026")
        form = self.client.get(self.url_lote("crear_caja", self.lote.pk))
        self.assertNotContains(form, 'name="fecha_elaboracion"')
        self.assertNotContains(form, 'name="fecha_vencimiento"')

    def test_total_asignado_empacado_disponible_no_multiplica_joins(self):
        otra = self.otra_lista("40")
        agregar_consumo_lote(lote=self.lote, partida=otra, cantidad_kg=Decimal("25"), usuario=self.usuario)
        self.caja([{"presentacion": self.bolsa5, "cantidad": 3}, {"presentacion": self.bolsa1, "cantidad": 10}])
        self.caja()
        lote = self.resumen()
        self.assertEqual(lote.total_asignado, Decimal("150"))
        self.assertEqual(lote.total_empacado, Decimal("50"))
        self.assertEqual(lote.disponible_empacar, Decimal("100"))
        self.assertEqual(lote.numero_fuentes, 2)
        self.assertEqual(lote.numero_cajas, 2)

    def test_creacion_caja_reduce_disponible(self):
        self.caja()
        self.assertEqual(self.resumen().disponible_empacar, Decimal("100"))

    def test_varias_cajas_reducen_disponible_y_no_sobreempacar(self):
        self.caja([{"presentacion": self.bolsa5, "cantidad": 3}, {"presentacion": self.bolsa1, "cantidad": 10}])
        self.caja()
        self.assertEqual(self.resumen().disponible_empacar, Decimal("75"))
        with self.assertRaises(ValidationError):
            self.caja([{"presentacion": self.bolsa5, "cantidad": 16}])
        self.assertEqual(Caja.objects.count(), 2)
        self.assertEqual(ComposicionCaja.objects.count(), 3)
        self.assertEqual(self.resumen().total_empacado, Decimal("50"))

    def test_exactamente_todo_lote_se_puede_empacar_y_oculta_accion(self):
        for _ in range(5):
            self.caja()
        self.assertEqual(self.resumen().disponible_empacar, Decimal("0"))
        with self.assertRaises(ValidationError):
            self.caja([{"presentacion": self.bolsa1, "cantidad": 1}])
        self.client.force_login(self.usuario)
        respuesta = self.client.get(self.url_lote("detalle_lote", self.lote.pk))
        self.assertContains(respuesta, "Todo el producto asignado a este lote ya fue empacado.")
        self.assertNotContains(respuesta, ">Crear caja</a>")

    def test_no_se_usa_postproceso_directo_ni_mermas_para_caja(self):
        # Aporte del lote = 25; postproceso = 125; merma = 100.
        self.partida = self.crear_partida()
        self.pendiente()
        self.merma("100")
        self.pesar("125")
        self.lote = self.crear_lote(codigo="CON-MERMA", cantidad_kg=Decimal("25"))
        with self.assertRaises(ValidationError):
            self.caja([{"presentacion": self.bolsa5, "cantidad": 6}])
        self.caja()
        self.assertEqual(self.resumen().total_asignado, Decimal("25"))
        self.assertEqual(self.resumen().disponible_empacar, Decimal("0"))
        self.assertEqual(self.partida.pesajes.get().peso_kg, Decimal("125"))
        self.assertEqual(self.partida.mermas.get().cantidad_kg, Decimal("100"))

    def test_lote_historico_sin_ruta_visible_pero_no_nueva_caja(self):
        LoteProduccion.objects.filter(pk=self.lote.pk).update(ruta_proceso=None)
        with self.assertRaises(ValidationError):
            self.caja()
        self.client.force_login(self.usuario)
        self.assertContains(self.client.get(self.url_lote("lista_lotes")), "Sin ruta documentada")
        respuesta = self.client.get(self.url_lote("detalle_lote", self.lote.pk))
        self.assertContains(respuesta, "Lote histórico sin ruta documentada")
        self.assertNotContains(respuesta, ">Crear caja</a>")

    def test_caja_historica_sin_composicion_se_conserva_y_bloquea_empaque(self):
        caja = Caja.objects.create(lote_produccion=self.lote, codigo_caja="HIST-01", peso_total_kg=Decimal("20"),
                                   fecha_armado=timezone.now(), registrado_por=self.usuario)
        with self.assertRaises(ValidationError):
            self.caja()
        self.client.force_login(self.usuario)
        respuesta = self.client.get(self.url_lote("detalle_caja", caja.pk))
        self.assertContains(respuesta, "HIST-01")
        self.assertContains(respuesta, "20,00 kg")
        self.assertContains(respuesta, "Sin composición documentada")
        self.assertTrue(self.resumen().historial_empaque_incompleto)

    def test_cambio_peso_catalogo_no_altera_historia_y_nuevas_cajas_releen_peso(self):
        caja = self.caja()
        PresentacionBolsa.objects.filter(pk=self.bolsa5.pk).update(peso_nominal_kg=Decimal("2.50"))
        self.assertEqual(self.peso(caja), Decimal("25"))
        nueva = self.caja(peso_neto_kg=Decimal("12.50"))  # Service debe releer el catálogo.
        self.assertEqual(self.peso(nueva), Decimal("12.50"))
        self.assertEqual(self.resumen().total_empacado, Decimal("37.50"))

    def test_presentacion_inactiva_se_relee_y_rollback(self):
        PresentacionBolsa.objects.filter(pk=self.bolsa5.pk).update(activo=False)
        with self.assertRaises(ValidationError):
            self.caja()
        self.assertFalse(Caja.objects.exists())
        self.assertFalse(ComposicionCaja.objects.exists())

    def test_presentacion_inexistente_o_invalida_no_crea_caja(self):
        for presentacion in (None, PresentacionBolsa(pk=99999)):
            with self.assertRaises(ValidationError):
                self.caja([{"presentacion": presentacion, "cantidad": 1}])
        self.assertFalse(Caja.objects.exists())

    def test_falla_segunda_composicion_revierte_caja_y_todas_las_lineas(self):
        guardar = ComposicionCaja.save
        contador = 0

        def guardar_y_fallar(objeto, *args, **kwargs):
            nonlocal contador
            guardar(objeto, *args, **kwargs)
            contador += 1
            if contador == 2:
                raise RuntimeError("Falla segunda composición tras INSERT")

        with patch.object(ComposicionCaja, "save", guardar_y_fallar), self.assertRaises(RuntimeError):
            self.caja([{"presentacion": self.bolsa5, "cantidad": 3}, {"presentacion": self.bolsa1, "cantidad": 10}])
        self.assertFalse(Caja.objects.exists())
        self.assertFalse(ComposicionCaja.objects.exists())
        self.assertEqual(self.resumen().disponible_empacar, Decimal("125"))

    def test_formset_rechaza_vacio_management_invalido_y_sin_presentacion(self):
        for datos in ({}, self.datos_caja([]), self.datos_caja([("", "2")]), self.datos_caja([("", "")])):
            self.assertFalse(ComposicionCajaFormSet(datos, prefix="composiciones").is_valid())

    def test_formset_rechaza_fraccion_cero_duplicados(self):
        for filas in ([(self.bolsa1.pk, "1.5")], [(self.bolsa1.pk, "0")], [(self.bolsa1.pk, "-2")],
                      [(self.bolsa1.pk, "1"), (self.bolsa1.pk, "2")]):
            self.assertFalse(ComposicionCajaFormSet(self.datos_caja(filas), prefix="composiciones").is_valid())

    def test_formset_fila_quitada_no_cuenta_y_no_bloquea_por_campos_ausentes(self):
        datos = self.datos_caja([(self.bolsa5.pk, "5"), ("", "")])
        datos["composiciones-1-DELETE"] = "on"
        self.assertTrue(ComposicionCajaFormSet(datos, prefix="composiciones").is_valid())
        datos["composiciones-0-DELETE"] = "on"
        self.assertFalse(ComposicionCajaFormSet(datos, prefix="composiciones").is_valid())

    def test_formset_limite_de_filas(self):
        datos = self.datos_caja() | {"composiciones-TOTAL_FORMS": "10000"}
        formset = ComposicionCajaFormSet(datos, prefix="composiciones")
        self.assertFalse(formset.is_valid())
        self.assertEqual(len(formset.forms), 100)

    def test_flujo_web_crea_y_conserva_formulario_al_sobreempacar(self):
        self.lote.consumos.update(cantidad_kg_utilizada=Decimal("60"))
        self.client.force_login(self.usuario)
        url = self.url_lote("crear_caja", self.lote.pk)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertFalse(Caja.objects.exists())
        respuesta = self.client.post(url, self.datos_caja())
        self.assertEqual(respuesta.status_code, 302)
        caja = Caja.objects.get()
        self.assertEqual(respuesta.url, self.url_lote("detalle_caja", caja.pk))
        self.client.post(url, self.datos_caja([(self.bolsa5.pk, "5")]))
        respuesta = self.client.post(url, self.datos_caja([(self.bolsa5.pk, "2")], peso="10.15"))
        self.assertContains(respuesta, "no superar el disponible")
        self.assertEqual(respuesta.context["composiciones"].forms[0]["cantidad"].value(), "2")
        self.assertEqual(Caja.objects.count(), 2)
        self.assertEqual(self.resumen().disponible_empacar, Decimal("10"))

    def test_fechas_codigo_total_y_lote_post_manipulados_se_ignoran(self):
        self.client.force_login(self.usuario)
        self.client.post(self.url_lote("crear_caja", self.lote.pk), self.datos_caja() | {
            "peso_total_kg": "1", "codigo_caja": "INVENTADO", "lote_produccion": "999999",
            "fecha_elaboracion": "2000-01-01", "composiciones-0-peso_unitario_kg": "0.01",
        })
        caja = Caja.objects.get()
        self.assertEqual(caja.lote_produccion_id, self.lote.pk)
        self.assertEqual(self.peso(caja), Decimal("25"))
        self.assertIsNone(caja.codigo_caja)
        self.assertIsNone(caja.peso_total_kg)

    def test_permisos_operativos_creacion_y_consulta(self):
        rol, _ = Rol.objects.get_or_create(codigo="ENCARGADA", defaults={"nombre": "Encargada"})
        usuarios = [self.usuario, self.jefe, Usuario.objects.create_user(username="enc_cajas", rol=rol),
                    Usuario.objects.create_superuser(username="super_cajas")]
        for usuario in usuarios:
            self.client.force_login(usuario)
            respuesta = self.client.post(self.url_lote("crear_caja", self.lote.pk), self.datos_caja([(self.bolsa1.pk, "1")]))
            self.assertEqual(respuesta.status_code, 302)
            caja = Caja.objects.latest("pk")
            self.assertEqual(caja.registrado_por, usuario)
            self.assertEqual(self.client.get(respuesta.url).status_code, 200)
            self.assertEqual(self.client.get(self.url_lote("lista_cajas")).status_code, 200)

    def test_anonimo_login_otros_roles_403_y_service_protegido(self):
        caja = self.caja()
        urls = [self.url_lote("lista_cajas"), self.url_lote("detalle_caja", caja.pk), self.url_lote("crear_caja", self.lote.pk)]
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 302)
            self.assertEqual(self.client.post(url).status_code, 302)
        rol = Rol.objects.create(codigo="LECTOR", nombre="Lector")
        usuario = Usuario.objects.create_user(username="lector_cajas", rol=rol)
        self.client.force_login(usuario)
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(url).status_code, 403)
        with self.assertRaises(PermissionDenied):
            self.caja(usuario=usuario)

    def test_listado_busqueda_id_lote_especie_y_paginacion(self):
        caja = self.caja()
        self.client.force_login(self.usuario)
        url = self.url_lote("lista_cajas")
        for q in (str(caja.pk), "TEST-CAJAS", self.especie.nombre):
            self.assertEqual([c.pk for c in self.client.get(url, {"q": q}).context["page_obj"]], [caja.pk])
        for _ in range(10):
            self.caja([{"presentacion": self.bolsa1, "cantidad": 1}], peso_neto_kg=Decimal("1"))
        respuesta = self.client.get(url, {"q": "TEST-CAJAS"})
        self.assertEqual(len(respuesta.context["page_obj"]), 10)
        self.assertIn("q=TEST-CAJAS", respuesta.context["pagina_siguiente"])
        self.assertEqual(len(self.client.get(url, {"q": "TEST-CAJAS", "page": 2}).context["page_obj"]), 1)
        self.assertContains(self.client.get(url, {"q": "9" * 200}), "No hay cajas")

    def test_listados_y_detalle_lote_sin_n_mas_uno(self):
        self.caja()
        self.client.force_login(self.usuario)
        urls = [self.url_lote("lista_cajas"), self.url_lote("lista_lotes"), self.url_lote("detalle_lote", self.lote.pk)]
        antes = []
        for url in urls:
            with CaptureQueriesContext(connection) as consultas:
                self.client.get(url)
            antes.append(len(consultas))
        for _ in range(3):
            self.caja()
        for url, cantidad in zip(urls, antes):
            with CaptureQueriesContext(connection) as consultas:
                self.client.get(url)
            self.assertEqual(len(consultas), cantidad)

    def test_navegacion_bidireccional_y_sidebar_un_activo(self):
        caja = self.caja()
        self.client.force_login(self.usuario)
        respuesta = self.client.get(self.url_lote("detalle_lote", self.lote.pk))
        self.assertContains(respuesta, self.url_lote("detalle_caja", caja.pk))
        respuesta = self.client.get(self.url_lote("detalle_caja", caja.pk))
        self.assertContains(respuesta, self.url_lote("detalle_lote", self.lote.pk))
        self.assertContains(respuesta, 'aria-current="page"', count=1)
        self.assertContains(respuesta, "Producto terminado")
        self.assertContains(respuesta, "/producto-terminado/cajas/")

    def test_admin_cajas_historico_catalogo_editable_y_protect(self):
        caja = self.caja()
        request = RequestFactory().get("/admin/")
        request.user = Usuario.objects.create_superuser(username="admin_cajas")
        self.client.force_login(request.user)
        for objeto in (caja, caja.composiciones.get()):
            configuracion = admin.site._registry[type(objeto)]
            self.assertFalse(configuracion.has_add_permission(request))
            self.assertFalse(configuracion.has_change_permission(request, objeto))
            self.assertFalse(configuracion.has_delete_permission(request, objeto))
            url = reverse(f"admin:trazabilidad_{objeto._meta.model_name}_change", args=[objeto.pk])
            self.assertEqual(self.client.get(url).status_code, 200)
        self.assertTrue(admin.site._registry[PresentacionBolsa].has_change_permission(request, self.bolsa5))
        with self.assertRaises(ProtectedError):
            self.bolsa5.delete()

    def test_migracion_presentaciones_repetible_preserva_peso_e_inactivo(self):
        self.bolsa1.peso_nominal_kg = Decimal("0.50")
        self.bolsa1.activo = False
        self.bolsa1.save()
        migracion = import_module("apps.trazabilidad.migrations.0009_presentaciones_iniciales")
        with connection.schema_editor() as editor:
            migracion.crear_presentaciones(apps, editor)
            migracion.crear_presentaciones(apps, editor)
        self.bolsa1.refresh_from_db()
        self.assertEqual(self.bolsa1.peso_nominal_kg, Decimal("0.50"))
        self.assertFalse(self.bolsa1.activo)
        self.assertEqual(PresentacionBolsa.objects.filter(nombre="Bolsa 1 kg").count(), 1)


class ConcurrenciaCajasTests(DatosCajas, TransactionTestCase):
    def setUp(self):
        self.preparar_cajas(total="40")

    @skipUnlessDBFeature("has_select_for_update")
    def test_dos_cajas_reales_25_kg_no_sobreempacan_lote_40(self):
        barrera = Barrier(2)

        def ejecutar(usuario_id):
            close_old_connections()
            try:
                usuario = Usuario.objects.get(pk=usuario_id)
                lote = LoteProduccion.objects.get(pk=self.lote.pk)
                presentacion = PresentacionBolsa.objects.get(pk=self.bolsa5.pk)
                barrera.wait(timeout=10)
                try:
                    crear_caja(lote=lote, usuario=usuario, peso_neto_kg=Decimal("25"), composiciones=[{"presentacion": presentacion, "cantidad": 5}])
                except ValidationError:
                    return "rechazada"
                return "aceptada"
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            resultados = list(pool.map(ejecutar, [self.usuario.pk, self.jefe.pk]))
        self.assertCountEqual(resultados, ["aceptada", "rechazada"])
        self.assertEqual(Caja.objects.count(), 1)
        self.assertEqual(ComposicionCaja.objects.count(), 1)
        self.assertEqual(self.resumen().total_empacado, Decimal("25"))
        self.assertEqual(self.resumen().disponible_empacar, Decimal("15"))
