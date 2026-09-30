from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.inventario.models import Despacho, DetalleDespacho, EstanciaCaja
from apps.trazabilidad.models import Caja, Especie, LoteProduccion, UnidadFrio
from apps.usuarios.models import Usuario

from .selectors import resumen_stock


class ResumenStockTests(TestCase):
    def test_caja_disponible_aparece_y_despachada_desaparece_del_stock(self):
        usuario = Usuario.objects.create_superuser(
            username="tecnico_reportes",
            password="clave-solo-para-pruebas",
        )
        especie = Especie.objects.create(nombre="Especie de prueba")
        lote = LoteProduccion.objects.create(
            codigo_lote="LOTE-PRUEBA",
            especie=especie,
            fecha_elaboracion=date(2026, 9, 22),
            registrado_por=usuario,
        )
        caja = Caja.objects.create(
            lote_produccion=lote,
            codigo_caja="CAJA-PRUEBA",
            peso_total_kg=Decimal("25.00"),
            peso_neto_kg=Decimal("24.50"),
            fecha_armado=timezone.now(),
            registrado_por=usuario,
        )
        unidad = UnidadFrio.objects.create(
            nombre="Almacenamiento de prueba",
            tipo=UnidadFrio.TipoUnidad.ALMACENAMIENTO,
        )
        EstanciaCaja.objects.create(
            caja=caja,
            unidad_frio=unidad,
            fecha_hora_ingreso=timezone.now(),
            fecha_hora_salida=None,
            ingresado_por=usuario,
        )

        resumen = resumen_stock()
        self.assertEqual(resumen["total_cajas"], 1)
        self.assertEqual(resumen["peso_total_kg"], Decimal("24.50"))

        despacho = Despacho.objects.create(
            fecha_hora_despacho=timezone.now(),
            registrado_por=usuario,
        )
        DetalleDespacho.objects.create(despacho=despacho, caja=caja)

        resumen = resumen_stock()
        self.assertEqual(resumen["total_cajas"], 0)
        self.assertEqual(resumen["peso_total_kg"], Decimal("0.00"))
