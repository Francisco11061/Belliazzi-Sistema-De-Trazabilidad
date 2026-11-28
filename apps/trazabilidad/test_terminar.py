"""«Dar por terminado»: el resto de un producto o de un lote se registra como merma y deja de quedar pendiente."""
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse

from .correcciones import corregir_caja, corregir_pesaje
from .models import MermaProceso, PartidaProceso, Pesaje
from .pendientes import pendientes_de_planta, restos_por_cerrar
from .selectors import lotes_con_empaque, partidas_para_lote, puede_crear_caja
from .services import MOTIVO_RESTO_EMPAQUE, dar_por_terminada_partida, dar_por_terminado_lote
from .test_cajas import DatosCajas
from .test_correcciones import _encargada


class DarPorTerminadoTests(DatosCajas, TestCase):
    def setUp(self):
        # Peso final 125 kg; al lote van 124,6 kg → quedan 0,4 kg del producto.
        self.preparar_cajas(total="124.6")
        self.encargada = _encargada("encargada_restos")

    def llenar_lote(self):
        """Cajas por 124,15 kg → quedan 0,45 kg del lote."""
        for _ in range(4):
            self.caja(peso_neto_kg=Decimal("25"))
        self.caja(peso_neto_kg=Decimal("24.15"))

    def test_producto_registra_el_resto_como_merma_y_sale_de_pendientes(self):
        self.assertIn(self.partida.pk, [p.pk for p in restos_por_cerrar()[0]])
        resto = dar_por_terminada_partida(partida=self.partida, usuario=self.encargada)
        self.assertEqual(resto, Decimal("0.40"))
        self.partida.refresh_from_db()
        self.assertEqual(self.partida.estado, PartidaProceso.Estado.CERRADA)
        merma = MermaProceso.objects.get(partida=self.partida, motivo=MOTIVO_RESTO_EMPAQUE)
        self.assertEqual((merma.cantidad_kg, merma.tipo, merma.registrado_por), (Decimal("0.40"), "MERMA", self.encargada))
        self.assertFalse(partidas_para_lote().filter(pk=self.partida.pk).exists())
        self.assertNotIn(self.partida.pk, [p.pk for p in restos_por_cerrar()[0]])
        with self.assertRaises(ValidationError):  # Dos clics seguidos no registran dos mermas.
            dar_por_terminada_partida(partida=self.partida, usuario=self.encargada)
        self.assertEqual(MermaProceso.objects.filter(motivo=MOTIVO_RESTO_EMPAQUE).count(), 1)

    def test_producto_sin_lote_no_se_puede_dar_por_terminado(self):
        self.lote.consumos.all().delete()
        with self.assertRaisesMessage(ValidationError, "ya asignado a lote"):
            dar_por_terminada_partida(partida=self.partida, usuario=self.encargada)

    def test_lote_registra_el_resto_y_no_admite_mas_cajas(self):
        self.llenar_lote()
        resto = dar_por_terminado_lote(lote=self.lote, usuario=self.encargada)
        self.assertEqual(resto, Decimal("0.45"))
        lote = lotes_con_empaque().get(pk=self.lote.pk)
        self.assertEqual((lote.merma_cierre_kg, lote.terminado_por, lote.disponible_empacar), (Decimal("0.45"), self.encargada, Decimal("0.00")))
        self.assertFalse(puede_crear_caja(lote))
        with self.assertRaises(ValidationError):
            self.caja(peso_neto_kg=Decimal("0.2"))
        with self.assertRaises(ValidationError):
            dar_por_terminado_lote(lote=self.lote, usuario=self.encargada)
        self.assertEqual(pendientes_de_planta(self.encargada)["restos"], 1)  # Queda el resto del producto, no el del lote.

    def test_lote_sin_cajas_no_se_puede_dar_por_terminado(self):
        with self.assertRaisesMessage(ValidationError, "con cajas"):
            dar_por_terminado_lote(lote=self.lote, usuario=self.encargada)

    def test_solo_jefe_y_encargada(self):
        self.llenar_lote()
        with self.assertRaises(PermissionDenied):
            dar_por_terminada_partida(partida=self.partida, usuario=self.usuario)
        with self.assertRaises(PermissionDenied):
            dar_por_terminado_lote(lote=self.lote, usuario=self.usuario)
        self.client.force_login(self.usuario)
        self.assertEqual(self.client.post(reverse("trazabilidad:terminar_partida", args=[self.partida.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("trazabilidad:restos")).status_code, 403)

    def test_cerrado_no_se_corrigen_los_kilos(self):
        self.llenar_lote()
        caja = self.lote.cajas.order_by("pk").first()
        dar_por_terminada_partida(partida=self.partida, usuario=self.encargada)
        dar_por_terminado_lote(lote=self.lote, usuario=self.encargada)
        pesaje = Pesaje.objects.get(partida=self.partida, tipo=Pesaje.POSTPROCESO)
        with self.assertRaisesMessage(ValidationError, "Ya se dio por terminado"):
            corregir_pesaje(pesaje=pesaje, usuario=self.jefe, peso_kg=Decimal("126"), motivo="Error")
        with self.assertRaisesMessage(ValidationError, "Ya se dio por terminado"):
            corregir_caja(caja=caja, usuario=self.jefe, peso_neto_kg=Decimal("24"), motivo="Error")

    def test_botones_y_pagina_de_restos(self):
        self.llenar_lote()
        self.client.force_login(self.encargada)
        detalle = self.client.get(reverse("trazabilidad:detalle_partida", args=[self.partida.pk]))
        self.assertContains(detalle, "Dar por terminado · 0,4 kg a merma")
        restos = reverse("trazabilidad:restos")
        pagina = self.client.get(restos)
        self.assertContains(pagina, "0,4 kg sin asignar")
        self.assertContains(pagina, "0,45 kg sin encajar")
        respuesta = self.client.post(reverse("producto_terminado:terminar_lote", args=[self.lote.pk]), {"volver": restos})
        self.assertRedirects(respuesta, restos)
        self.assertNotContains(self.client.get(restos), "0,45 kg sin encajar")
        # Un destino externo no se sigue.
        respuesta = self.client.post(reverse("trazabilidad:terminar_partida", args=[self.partida.pk]), {"volver": "https://otro.example/"})
        self.assertRedirects(respuesta, reverse("trazabilidad:detalle_partida", args=[self.partida.pk]))
        detalle = self.client.get(reverse("trazabilidad:detalle_partida", args=[self.partida.pk]))
        self.assertContains(detalle, "Terminado")
        self.assertNotContains(detalle, "Dar por terminado")
        lote = self.client.get(reverse("producto_terminado:detalle_lote", args=[self.lote.pk]))
        self.assertContains(lote, "0,45 kg a merma.")
