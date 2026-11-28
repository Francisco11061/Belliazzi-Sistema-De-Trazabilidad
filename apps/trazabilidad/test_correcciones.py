"""Correcciones auditadas fuera de la recepción."""
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.inventario.models import Despacho, DetalleDespacho
from apps.usuarios.models import Rol, Usuario

from .correcciones import (
    corregir_caja, corregir_despacho, corregir_estancia_tunel, corregir_etapa, corregir_lote, corregir_merma,
    corregir_pesaje,
)
from .models import Correccion, EstanciaPartida, EventoProceso, Pesaje, Recepcion
from .permisos_correccion import puede_corregir
from .test_cajas import DatosCajas
from .test_postproceso import DatosPostproceso


def _encargada(nombre="encargada_correcciones"):
    return Usuario.objects.create_user(username=nombre, password="clave-prueba-123", rol=Rol.objects.get(codigo="ENCARGADA"))


class PermisosCorreccionTests(DatosCajas, TestCase):
    def setUp(self):
        self.preparar_cajas()
        self.encargada = _encargada()

    def test_jefe_siempre_y_encargada_solo_lo_propio_del_mismo_dia(self):
        ahora, ayer = timezone.now(), timezone.now() - timedelta(days=1)
        self.assertTrue(puede_corregir(self.jefe, self.usuario.pk, ayer))
        self.assertTrue(puede_corregir(self.encargada, self.encargada.pk, ahora))
        self.assertFalse(puede_corregir(self.encargada, self.encargada.pk, ayer))
        self.assertFalse(puede_corregir(self.encargada, self.usuario.pk, ahora))
        self.assertFalse(puede_corregir(self.usuario, self.usuario.pk, ahora))

    def test_encargada_corrige_su_recepcion_del_dia_y_no_la_de_ayer(self):
        recepcion = self.partida.detalle_recepcion.recepcion
        Recepcion.objects.filter(pk=recepcion.pk).update(registrado_por=self.encargada)
        self.client.force_login(self.encargada)
        url = reverse("trazabilidad:corregir_recepcion", args=[recepcion.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        Recepcion.objects.filter(pk=recepcion.pk).update(creado_en=timezone.now() - timedelta(days=1))
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_operaria_no_corrige_ni_ve_el_enlace(self):
        caja = self.caja(peso_neto_kg=Decimal("24.80"))
        self.client.force_login(self.usuario)
        self.assertNotContains(self.client.get(reverse("producto_terminado:detalle_caja", args=[caja.pk])), "Corregir peso")
        self.assertEqual(self.client.get(reverse("trazabilidad:corregir_registro", args=["caja", caja.pk])).status_code, 403)
        with self.assertRaises(PermissionDenied):
            corregir_caja(caja=caja, usuario=self.usuario, peso_neto_kg=Decimal("25"), motivo="Error")


class CorreccionCajaYLoteTests(DatosCajas, TestCase):
    def setUp(self):
        self.preparar_cajas(total="30")
        self.primera = self.caja(peso_neto_kg=Decimal("24.80"))

    def test_peso_corregido_queda_en_historial_y_pide_reimprimir(self):
        self.client.force_login(self.jefe)
        url = reverse("trazabilidad:corregir_registro", args=["caja", self.primera.pk])
        respuesta = self.client.post(url, {"peso_neto_kg": "25.00", "motivo": "Se pesó de nuevo"}, follow=True)
        self.primera.refresh_from_db()
        self.assertEqual(self.primera.peso_neto_kg, Decimal("25.00"))
        cambio = Correccion.objects.get(entidad_afectada="Caja", campo="peso_neto_kg")
        self.assertEqual((cambio.valor_anterior, cambio.valor_nuevo, cambio.usuario), ("24.80", "25.00", self.jefe))
        self.assertContains(respuesta, "Reimprime la etiqueta")
        self.assertContains(respuesta, "24,8 kg → <span class=\"font-semibold\">25 kg</span>", html=False)

    def test_no_supera_lo_disponible_del_lote_ni_el_maximo(self):
        segunda = self.caja(peso_neto_kg=Decimal("5.00"))
        with self.assertRaisesMessage(ValidationError, "Supera lo disponible del lote"):
            corregir_caja(caja=segunda, usuario=self.jefe, peso_neto_kg=Decimal("5.30"), motivo="Error")
        with self.assertRaisesMessage(ValidationError, "no superar"):
            corregir_caja(caja=self.primera, usuario=self.jefe, peso_neto_kg=Decimal("99"), motivo="Error")

    def test_caja_despachada_queda_bloqueada(self):
        despacho = Despacho.objects.create(fecha_hora_despacho=timezone.now(), registrado_por=self.jefe)
        DetalleDespacho.objects.create(despacho=despacho, caja=self.primera)
        with self.assertRaisesMessage(ValidationError, "ya fue despachada"):
            corregir_caja(caja=self.primera, usuario=self.jefe, peso_neto_kg=Decimal("24"), motivo="Error")
        self.client.force_login(self.jefe)
        respuesta = self.client.get(reverse("trazabilidad:corregir_registro", args=["caja", self.primera.pk]))
        self.assertContains(respuesta, "ya fue despachada")
        self.assertNotContains(respuesta, "Guardar corrección")

    def test_sin_cambios_no_guarda_nada(self):
        with self.assertRaisesMessage(ValidationError, "No hay cambios"):
            corregir_caja(caja=self.primera, usuario=self.jefe, peso_neto_kg=Decimal("24.80"), motivo="Nada")
        self.assertFalse(Correccion.objects.exists())

    def test_motivo_obligatorio(self):
        with self.assertRaisesMessage(ValidationError, "motivo"):
            corregir_caja(caja=self.primera, usuario=self.jefe, peso_neto_kg=Decimal("24"), motivo="  ")

    def test_lote_corrige_vencimiento_pero_no_elaboracion(self):
        with self.assertRaisesMessage(ValidationError, "anterior a la elaboración"):
            corregir_lote(lote=self.lote, usuario=self.jefe, fecha_vencimiento=date(2026, 9, 1), observaciones="", motivo="Error")
        corregir_lote(lote=self.lote, usuario=self.jefe, fecha_vencimiento=date(2026, 11, 26),
                      observaciones=self.lote.observaciones, motivo="Vencimiento mal digitado")
        self.lote.refresh_from_db()
        self.assertEqual(self.lote.fecha_vencimiento, date(2026, 11, 26))
        self.client.force_login(self.jefe)
        respuesta = self.client.get(reverse("trazabilidad:corregir_registro", args=["lote", self.lote.pk]))
        self.assertNotContains(respuesta, 'name="fecha_elaboracion"')
        self.assertContains(respuesta, "forma parte del código del lote")


class CorreccionSeguimientoTests(DatosPostproceso, TestCase):
    def setUp(self):
        self.preparar()
        self.pendiente()
        ahora = timezone.now()
        Recepcion.objects.filter(pk=self.partida.detalle_recepcion.recepcion_id).update(fecha_hora_recepcion=ahora - timedelta(hours=6))
        self.etapas = list(self.partida.eventos.filter(etapa_ruta__isnull=False).order_by("etapa_ruta__orden"))
        for indice, evento in enumerate(self.etapas):
            EventoProceso.objects.filter(pk=evento.pk).update(
                fecha_hora_inicio=ahora - timedelta(hours=5 - indice), fecha_hora_termino=ahora - timedelta(hours=4 - indice))
        fin_etapas = ahora - timedelta(hours=4 - (len(self.etapas) - 1))
        self.general = self.partida.eventos.get(etapa_ruta__isnull=True)
        self.estancia = self.partida.estancias_frio.get(unidad_frio__tipo="TUNEL_CONGELADO")
        EventoProceso.objects.filter(pk=self.general.pk).update(fecha_hora_inicio=ahora - timedelta(hours=5),
                                                                fecha_hora_termino=fin_etapas)
        EstanciaPartida.objects.filter(pk=self.estancia.pk).update(fecha_hora_ingreso=fin_etapas,
                                                                   fecha_hora_salida=ahora - timedelta(minutes=30))
        for objeto in (*self.etapas, self.general, self.estancia):
            objeto.refresh_from_db()

    def test_etapa_respeta_el_orden(self):
        primera, segunda = self.etapas[0], self.etapas[1]
        with self.assertRaisesMessage(ValidationError, "anterior al inicio"):
            corregir_etapa(evento=primera, usuario=self.jefe, fecha_hora_inicio=primera.fecha_hora_inicio,
                           fecha_hora_termino=primera.fecha_hora_inicio - timedelta(minutes=1), observaciones="", motivo="Error")
        with self.assertRaisesMessage(ValidationError, "después de que empiece la siguiente"):
            corregir_etapa(evento=primera, usuario=self.jefe, fecha_hora_inicio=primera.fecha_hora_inicio,
                           fecha_hora_termino=segunda.fecha_hora_inicio + timedelta(minutes=5), observaciones="", motivo="Error")
        with self.assertRaisesMessage(ValidationError, "futura"):
            corregir_etapa(evento=primera, usuario=self.jefe, fecha_hora_inicio=timezone.now() + timedelta(hours=1),
                           fecha_hora_termino=primera.fecha_hora_termino, observaciones="", motivo="Error")
        nuevo = primera.fecha_hora_termino - timedelta(minutes=10)
        corregir_etapa(evento=primera, usuario=self.jefe, fecha_hora_inicio=primera.fecha_hora_inicio,
                       fecha_hora_termino=nuevo, observaciones="", motivo="Se marcó tarde el término")
        primera.refresh_from_db()
        self.assertEqual(primera.fecha_hora_termino, nuevo)
        self.assertTrue(Correccion.objects.filter(entidad_afectada="EventoProceso", campo="fecha_hora_termino").exists())

    def test_tunel_corrige_horas_y_alinea_el_procesamiento(self):
        nuevo_ingreso = self.estancia.fecha_hora_ingreso + timedelta(minutes=15)
        corregir_estancia_tunel(estancia=self.estancia, usuario=self.jefe, fecha_hora_ingreso=nuevo_ingreso,
                                fecha_hora_salida=self.estancia.fecha_hora_salida, motivo="Se envió tarde en el sistema")
        self.general.refresh_from_db()
        self.assertEqual(self.general.fecha_hora_termino, nuevo_ingreso)
        with self.assertRaisesMessage(ValidationError, "anterior al ingreso"):
            corregir_estancia_tunel(estancia=self.estancia, usuario=self.jefe, fecha_hora_ingreso=nuevo_ingreso,
                                    fecha_hora_salida=nuevo_ingreso - timedelta(minutes=1), motivo="Error")

    def test_mantencion_no_se_corrige(self):
        from .models import UnidadFrio
        camara = UnidadFrio.objects.create(nombre="Mantención prueba", tipo="MANTENCION")
        estancia = EstanciaPartida.objects.create(partida=self.partida, unidad_frio=camara, ingresado_por=self.usuario,
                                                  fecha_hora_ingreso=timezone.now() - timedelta(hours=8),
                                                  fecha_hora_salida=timezone.now() - timedelta(hours=7))
        with self.assertRaisesMessage(ValidationError, "mantención"):
            corregir_estancia_tunel(estancia=estancia, usuario=self.jefe, fecha_hora_ingreso=estancia.fecha_hora_ingreso,
                                    fecha_hora_salida=estancia.fecha_hora_salida, motivo="Error")

    def test_merma_no_supera_la_cantidad_del_seguimiento(self):
        merma = self.merma(cantidad="8")
        with self.assertRaisesMessage(ValidationError, "no pueden superar"):
            corregir_merma(merma=merma, usuario=self.jefe, tipo="MERMA", cantidad_kg=Decimal("600"), detalle="x", motivo="Error")
        corregir_merma(merma=merma, usuario=self.jefe, tipo="DESCARTE", cantidad_kg=Decimal("10"),
                       detalle="Restos de fileteo", motivo="Era descarte")
        merma.refresh_from_db()
        self.assertEqual((merma.tipo, merma.cantidad_kg), ("DESCARTE", Decimal("10.00")))

    def test_pesaje_no_baja_de_lo_asignado_a_lotes(self):
        from .services import crear_lote_produccion
        self.especie.codigo_lote = "01"
        self.especie.save()
        pesaje = self.pesar("125")
        crear_lote_produccion(partida=self.partida, cantidad_kg=Decimal("100"), fecha_elaboracion=date(2026, 11, 26),
                              fecha_vencimiento=None, observaciones="", usuario=self.usuario)
        with self.assertRaisesMessage(ValidationError, "asignado a lotes"):
            corregir_pesaje(pesaje=pesaje, usuario=self.jefe, peso_kg=Decimal("90"), motivo="Error")
        corregir_pesaje(pesaje=pesaje, usuario=self.jefe, peso_kg=Decimal("120.50"), motivo="Balanza descalibrada")
        self.assertEqual(Pesaje.objects.get(pk=pesaje.pk).peso_kg, Decimal("120.50"))

    def test_detalle_muestra_enlaces_e_historial(self):
        pesaje = self.pesar("125")
        corregir_pesaje(pesaje=pesaje, usuario=self.jefe, peso_kg=Decimal("124"), motivo="Balanza descalibrada")
        self.client.force_login(self.jefe)
        respuesta = self.client.get(reverse("trazabilidad:detalle_partida", args=[self.partida.pk]))
        self.assertContains(respuesta, reverse("trazabilidad:corregir_registro", args=["pesaje", pesaje.pk]))
        self.assertContains(respuesta, reverse("trazabilidad:corregir_registro", args=["etapa", self.etapas[0].pk]))
        self.assertContains(respuesta, reverse("trazabilidad:corregir_registro", args=["frio", self.estancia.pk]))
        self.assertContains(respuesta, "Historial de correcciones")
        self.assertContains(respuesta, "Balanza descalibrada")


class CorreccionDespachoTests(DatosCajas, TestCase):
    def setUp(self):
        self.preparar_cajas()
        self.encargada = _encargada()
        self.despacho = Despacho.objects.create(
            tipo_destino="NACIONAL", destino="Distribuidora Sur", rut_destinatario="76.482.913-1",
            tipo_documento="Guía de despacho", numero_documento="4589", fecha_documento=date(2026, 11, 26),
            fecha_hora_despacho=timezone.now(), registrado_por=self.encargada)

    def datos(self, **cambios):
        base = {c: getattr(self.despacho, c) for c in ("tipo_destino", "destino", "rut_destinatario", "pais_destino",
                                                       "tipo_documento", "numero_documento", "fecha_documento", "observaciones")}
        base.update(cambios)
        return base

    def test_valida_igual_que_al_registrar(self):
        with self.assertRaisesMessage(ValidationError, "RUT no es válido"):
            corregir_despacho(despacho=self.despacho, usuario=self.jefe, datos=self.datos(rut_destinatario="11.111.111-2"), motivo="Error")

    def test_encargada_corrige_su_despacho_del_dia(self):
        corregir_despacho(despacho=self.despacho, usuario=self.encargada, datos=self.datos(numero_documento="4590"),
                          motivo="Número de guía mal digitado")
        self.despacho.refresh_from_db()
        self.assertEqual(self.despacho.numero_documento, "4590")
        Despacho.objects.filter(pk=self.despacho.pk).update(creado_en=timezone.now() - timedelta(days=1))
        self.despacho.refresh_from_db()
        with self.assertRaises(PermissionDenied):
            corregir_despacho(despacho=self.despacho, usuario=self.encargada, datos=self.datos(numero_documento="4591"), motivo="Otra vez")

    def test_pagina_de_detalle_y_formulario(self):
        self.client.force_login(self.jefe)
        detalle = reverse("inventario:detalle_despacho", args=[self.despacho.pk])
        self.assertContains(self.client.get(detalle), "Corregir despacho")
        url = reverse("trazabilidad:corregir_registro", args=["despacho", self.despacho.pk])
        datos = {k: ("" if v is None else str(v)) for k, v in self.datos(numero_documento="4600").items()}
        datos["fecha_documento"] = "2026-11-26"
        datos["motivo"] = "Corrección de guía"
        respuesta = self.client.post(url, datos, follow=True)
        self.assertRedirects(respuesta, detalle)
        self.assertContains(respuesta, "Número de documento")
        self.assertContains(respuesta, "4600")
