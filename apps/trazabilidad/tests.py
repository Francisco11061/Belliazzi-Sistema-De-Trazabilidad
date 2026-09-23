from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.trazabilidad.services import registrar_recepcion
from apps.usuarios.models import Rol, Usuario

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


class RegistroRecepcionServiceTests(TestCase):
    def setUp(self):
        rol = Rol.objects.get(codigo="JEFE")
        self.usuario = Usuario.objects.create_user(
            username="jefe_recepciones",
            password="clave-prueba-123",
            rol=rol,
        )
        self.origen = OrigenSernapesca.objects.create(folio_origen="ORIGEN-PRUEBA")
        self.merluza = Especie.objects.create(nombre="Merluza del sur")
        self.sierra = Especie.objects.create(nombre="Sierra")

    def test_registrar_recepcion_con_dos_detalles(self):
        recepcion = registrar_recepcion(
            fecha_hora_recepcion=timezone.now(),
            registrado_por=self.usuario,
            detalles=[
                {
                    "origen_sernapesca": self.origen,
                    "especie": self.merluza,
                    "peso_origen_kg": Decimal("500.00"),
                    "peso_recepcion_kg": Decimal("493.00"),
                },
                {
                    "origen_sernapesca": self.origen,
                    "especie": self.sierra,
                    "peso_origen_kg": Decimal("200.00"),
                    "peso_recepcion_kg": Decimal("198.00"),
                },
            ],
        )

        self.assertEqual(Recepcion.objects.count(), 1)
        self.assertEqual(DetalleRecepcion.objects.count(), 2)
        recepcion.refresh_from_db()
        self.assertEqual(recepcion.registrado_por, self.usuario)
        self.assertEqual(recepcion.detalles.count(), 2)
        merluza = recepcion.detalles.get(especie=self.merluza)
        sierra = recepcion.detalles.get(especie=self.sierra)
        self.assertEqual(merluza.peso_origen_kg, Decimal("500.00"))
        self.assertEqual(merluza.peso_recepcion_kg, Decimal("493.00"))
        self.assertEqual(sierra.peso_origen_kg, Decimal("200.00"))
        self.assertEqual(sierra.peso_recepcion_kg, Decimal("198.00"))

    def test_recepcion_sin_detalles_falla(self):
        with self.assertRaises(ValidationError):
            registrar_recepcion(
                fecha_hora_recepcion=timezone.now(),
                registrado_por=self.usuario,
                detalles=[],
            )

        self.assertEqual(Recepcion.objects.count(), 0)
        self.assertEqual(DetalleRecepcion.objects.count(), 0)

    def test_error_en_un_detalle_revierte_toda_la_recepcion(self):
        with self.assertRaises(ValidationError) as error:
            registrar_recepcion(
                fecha_hora_recepcion=timezone.now(),
                registrado_por=self.usuario,
                detalles=[
                    {
                        "origen_sernapesca": self.origen,
                        "especie": self.merluza,
                        "peso_origen_kg": Decimal("500.00"),
                        "peso_recepcion_kg": Decimal("493.00"),
                    },
                    {
                        "origen_sernapesca": self.origen,
                        "especie": self.sierra,
                        "peso_origen_kg": Decimal("0.00"),
                        "peso_recepcion_kg": Decimal("198.00"),
                    },
                ],
            )

        self.assertEqual(
            error.exception.error_dict["peso_origen_kg"][0].code,
            "min_value",
        )
        self.assertEqual(Recepcion.objects.count(), 0)
        self.assertEqual(DetalleRecepcion.objects.count(), 0)
