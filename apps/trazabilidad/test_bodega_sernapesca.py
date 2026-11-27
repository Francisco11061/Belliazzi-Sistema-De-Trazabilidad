"""Bodega Sernapesca calculada para la materia prima recibida."""
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.reportes.selectors import obtener_abastecimiento_reporte
from apps.usuarios.models import Rol, Usuario

from .bodegas import bodega_de_detalle, bodega_por_defecto
from .forms import RecepcionForm
from .models import BodegaSernapesca, DetalleRecepcion, Especie, EstanciaPartida, UnidadFrio
from .services import registrar_recepcion


class BodegaSernapescaBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        # La migración deja "Almacenamiento" por defecto; estas pruebas parten sin bodegas.
        BodegaSernapesca.objects.all().delete()
        cls.jefe = Usuario.objects.create_user(username="jefe_bodega", password="clave-prueba-123",
                                               rol=Rol.objects.get(codigo="JEFE"))
        cls.merluza = Especie.objects.create(nombre="Merluza bodega", codigo_sernapesca="MER-01")
        cls.mantencion = UnidadFrio.objects.create(nombre="Mantención bodega",
                                                   tipo=UnidadFrio.TipoUnidad.MANTENCION)

    def registrar(self):
        return registrar_recepcion(
            fecha_hora_recepcion=timezone.now() - timedelta(hours=2), registrado_por=self.jefe,
            origen={"folio_origen": "FOLIO-BODEGA", "tipo_origen": "Embarcación artesanal",
                    "fecha_desembarque": timezone.localdate() - timedelta(days=1)},
            detalles=[{"especie": self.merluza, "peso_origen_kg": Decimal("500.00"),
                       "peso_recepcion_kg": Decimal("487.40")}],
        )

    def esperar_en_mantencion(self, recepcion):
        partida = recepcion.detalles.get().partidas.get()
        EstanciaPartida.objects.create(partida=partida, unidad_frio=self.mantencion,
                                       fecha_hora_ingreso=timezone.now() - timedelta(hours=1),
                                       ingresado_por=self.jefe)

    def calcular(self, recepcion):
        detalle = DetalleRecepcion.objects.prefetch_related(
            "partidas", "partidas__estancias_frio__unidad_frio__bodega_sernapesca",
        ).get(recepcion=recepcion)
        return bodega_de_detalle(detalle, bodega_por_defecto())


class BodegaModeloTests(BodegaSernapescaBase):
    def test_solo_una_bodega_por_defecto(self):
        BodegaSernapesca.objects.create(nombre="Bodega planta", por_defecto=True)
        with self.assertRaises(IntegrityError), transaction.atomic():
            BodegaSernapesca.objects.create(nombre="Bodega 2", por_defecto=True)

    def test_bodega_por_defecto_debe_estar_activa(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            BodegaSernapesca.objects.create(nombre="Bodega inactiva", por_defecto=True, activo=False)

    def test_validacion_muestra_mensaje_claro(self):
        BodegaSernapesca.objects.create(nombre="Bodega planta", por_defecto=True)
        with self.assertRaisesMessage(ValidationError, "Ya existe una bodega por defecto"):
            BodegaSernapesca(nombre="Otra", por_defecto=True).full_clean()

    def test_la_recepcion_no_pregunta_bodega(self):
        self.assertNotIn("bodega_sernapesca", RecepcionForm.base_fields)


class BodegaCalculoTests(BodegaSernapescaBase):
    def test_sin_bodegas_queda_vacia(self):
        resultado = self.calcular(self.registrar())
        self.assertEqual((resultado.bodega, resultado.motivo), (None, "Sin bodega configurada"))

    def test_sin_mantencion_usa_la_bodega_por_defecto(self):
        defecto = BodegaSernapesca.objects.create(nombre="Bodega planta", por_defecto=True)
        resultado = self.calcular(self.registrar())
        self.assertEqual((resultado.bodega, resultado.motivo), (defecto, "Bodega por defecto"))

    def test_usa_la_bodega_de_la_camara_de_mantencion(self):
        BodegaSernapesca.objects.create(nombre="Bodega planta", por_defecto=True)
        camara = BodegaSernapesca.objects.create(nombre="Bodega cámara 1")
        UnidadFrio.objects.filter(pk=self.mantencion.pk).update(bodega_sernapesca=camara)
        recepcion = self.registrar()
        self.esperar_en_mantencion(recepcion)
        resultado = self.calcular(recepcion)
        self.assertEqual(resultado.bodega, camara)
        self.assertEqual(resultado.motivo, "Según la cámara Mantención bodega")

    def test_camara_sin_bodega_asignada_usa_la_por_defecto(self):
        defecto = BodegaSernapesca.objects.create(nombre="Bodega planta", por_defecto=True)
        recepcion = self.registrar()
        self.esperar_en_mantencion(recepcion)
        self.assertEqual(self.calcular(recepcion).bodega, defecto)

    def test_por_defecto_inactiva_no_se_usa(self):
        defecto = BodegaSernapesca.objects.create(nombre="Bodega planta", por_defecto=True)
        BodegaSernapesca.objects.filter(pk=defecto.pk).update(por_defecto=False, activo=False)
        self.assertIsNone(self.calcular(self.registrar()).bodega)


class BodegaVistaYReporteTests(BodegaSernapescaBase):
    def test_detalle_de_recepcion_muestra_la_bodega_calculada(self):
        BodegaSernapesca.objects.create(nombre="Bodega planta", por_defecto=True)
        recepcion = self.registrar()
        self.client.force_login(self.jefe)
        respuesta = self.client.get(reverse("trazabilidad:detalle_recepcion", args=[recepcion.pk]))
        self.assertContains(respuesta, "Bodega Sernapesca")
        self.assertContains(respuesta, "Bodega planta")
        self.assertContains(respuesta, "Bodega por defecto")

    def test_abastecimiento_trae_los_datos_del_formulario(self):
        camara = BodegaSernapesca.objects.create(nombre="Bodega cámara 1")
        UnidadFrio.objects.filter(pk=self.mantencion.pk).update(bodega_sernapesca=camara)
        self.esperar_en_mantencion(self.registrar())
        fila = obtener_abastecimiento_reporte(None, timezone.localdate())[0]
        self.assertEqual(fila["codigo_recurso"], "MER-01")
        self.assertEqual(fila["fecha_desembarque"], timezone.localdate() - timedelta(days=1))
        self.assertEqual(fila["toneladas_origen"], Decimal("0.500"))
        self.assertEqual(fila["toneladas_aceptadas"], Decimal("0.487"))
        self.assertEqual(fila["porcentaje_diferencia"], Decimal("-2.5"))
        self.assertEqual(fila["bodega"], "Bodega cámara 1")

    def test_sin_bodega_ni_codigo_quedan_vacios(self):
        Especie.objects.filter(pk=self.merluza.pk).update(codigo_sernapesca="")
        self.registrar()
        fila = obtener_abastecimiento_reporte(None, timezone.localdate())[0]
        self.assertEqual((fila["bodega"], fila["codigo_recurso"]), ("", ""))


class BodegaMigracionTests(TestCase):
    def test_la_migracion_deja_almacenamiento_por_defecto(self):
        self.assertEqual(bodega_por_defecto().nombre, "Almacenamiento")
