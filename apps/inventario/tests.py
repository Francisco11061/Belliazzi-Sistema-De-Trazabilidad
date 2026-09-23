from datetime import date, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.trazabilidad.models import Caja, Especie, LoteProduccion, UnidadFrio
from apps.usuarios.models import Usuario

from .models import EstanciaCaja


class InventarioIntegridadTests(TestCase):
    def test_estancia_caja_no_permite_salida_anterior_al_ingreso(self):
        usuario = Usuario.objects.create_superuser(
            username="tecnico_inventario",
            password="clave-solo-para-pruebas",
        )
        especie = Especie.objects.create(nombre="Especie de prueba")
        lote = LoteProduccion.objects.create(
            codigo_lote="LOTE-PRUEBA",
            especie=especie,
            fecha_elaboracion=date(2026, 9, 22),
            registrado_por=usuario,
        )
        ingreso = timezone.now()
        caja = Caja.objects.create(
            lote_produccion=lote,
            codigo_caja="CAJA-PRUEBA",
            peso_total_kg=Decimal("25.00"),
            fecha_armado=ingreso,
            registrado_por=usuario,
        )
        unidad = UnidadFrio.objects.create(
            nombre="Almacenamiento de prueba",
            tipo=UnidadFrio.TipoUnidad.ALMACENAMIENTO,
        )

        with self.assertRaisesRegex(
            IntegrityError, "estancia_caja_salida_mayor_igual_ingreso"
        ):
            with transaction.atomic():
                EstanciaCaja.objects.create(
                    caja=caja,
                    unidad_frio=unidad,
                    fecha_hora_ingreso=ingreso,
                    fecha_hora_salida=ingreso - timedelta(hours=1),
                    ingresado_por=usuario,
                )
