from datetime import date, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.usuarios.models import Usuario

from .models import (
    DetalleRecepcion,
    Especie,
    EventoProceso,
    LoteProduccion,
    OrigenSernapesca,
    PartidaProceso,
    Recepcion,
    TipoProceso,
)


class TrazabilidadIntegridadTests(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.create_superuser(
            username="tecnico_trazabilidad",
            password="clave-solo-para-pruebas",
        )
        self.especie = Especie.objects.create(nombre="Especie de prueba")

    def test_evento_no_permite_termino_anterior_al_inicio(self):
        inicio = timezone.now()
        origen = OrigenSernapesca.objects.create(folio_origen="ORIGEN-PRUEBA")
        recepcion = Recepcion.objects.create(
            fecha_hora_recepcion=inicio,
            registrado_por=self.usuario,
        )
        detalle = DetalleRecepcion.objects.create(
            recepcion=recepcion,
            origen_sernapesca=origen,
            especie=self.especie,
            peso_origen_kg=Decimal("25.00"),
            peso_recepcion_kg=Decimal("25.00"),
        )
        partida = PartidaProceso.objects.create(
            detalle_recepcion=detalle,
            cantidad_inicial_kg=Decimal("25.00"),
            creado_por=self.usuario,
        )
        tipo = TipoProceso.objects.create(codigo="PRUEBA", nombre="Proceso de prueba")

        with self.assertRaisesRegex(IntegrityError, "evento_fin_mayor_igual_inicio"):
            with transaction.atomic():
                EventoProceso.objects.create(
                    partida=partida,
                    tipo_proceso=tipo,
                    fecha_hora_inicio=inicio,
                    fecha_hora_termino=inicio - timedelta(hours=1),
                    iniciado_por=self.usuario,
                )

    def test_lote_no_permite_vencimiento_anterior_a_elaboracion(self):
        elaboracion = date(2026, 9, 22)

        with self.assertRaisesRegex(
            IntegrityError, "lote_vencimiento_mayor_igual_elaboracion"
        ):
            with transaction.atomic():
                LoteProduccion.objects.create(
                    codigo_lote="LOTE-PRUEBA",
                    especie=self.especie,
                    fecha_elaboracion=elaboracion,
                    fecha_vencimiento=elaboracion - timedelta(days=1),
                    registrado_por=self.usuario,
                )
