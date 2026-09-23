from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
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


class RecepcionViewsTests(TestCase):
    def setUp(self):
        self.jefe = Usuario.objects.create_user(
            username="jefe_web", password="clave-prueba-123",
            rol=Rol.objects.get(codigo="JEFE"),
        )
        self.encargada = Usuario.objects.create_user(
            username="encargada_web", password="clave-prueba-123",
            rol=Rol.objects.get(codigo="ENCARGADA"),
        )
        self.operaria = Usuario.objects.create_user(
            username="operaria_web", password="clave-prueba-123",
            rol=Rol.objects.get(codigo="OPERARIA"),
        )
        self.origen = OrigenSernapesca.objects.create(folio_origen="FOLIO-WEB")
        self.especie = Especie.objects.create(nombre="Merluza del sur", activo=True)

    def datos_validos(self):
        return {
            "fecha_hora_recepcion": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
            "observaciones": "Recepción de prueba",
            "form-TOTAL_FORMS": "1",
            "form-INITIAL_FORMS": "0",
            "form-MIN_NUM_FORMS": "0",
            "form-MAX_NUM_FORMS": "1000",
            "form-0-origen_sernapesca": str(self.origen.pk),
            "form-0-especie": str(self.especie.pk),
            "form-0-peso_origen_kg": "500.00",
            "form-0-peso_recepcion_kg": "493.00",
        }

    def test_jefe_puede_ver_lista_recepciones(self):
        self.client.force_login(self.jefe)
        response = self.client.get(reverse("trazabilidad:lista_recepciones"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "trazabilidad/recepciones/lista.html")

    def test_encargada_puede_ver_formulario_recepcion(self):
        self.client.force_login(self.encargada)
        response = self.client.get(reverse("trazabilidad:nueva_recepcion"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "trazabilidad/recepciones/nueva.html")

    def test_operaria_no_puede_acceder_a_recepciones(self):
        self.client.force_login(self.operaria)
        urls = [
            reverse("trazabilidad:lista_recepciones"),
            reverse("trazabilidad:nueva_recepcion"),
            reverse("trazabilidad:detalle_recepcion", args=[1]),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
        response = self.client.post(
            reverse("trazabilidad:nueva_recepcion"), self.datos_validos()
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Recepcion.objects.count(), 0)

    def test_usuario_anonimo_es_redirigido_al_login(self):
        url = reverse("trazabilidad:lista_recepciones")
        response = self.client.get(url)
        self.assertRedirects(response, f"{reverse('usuarios:login')}?next={url}")

    def test_crear_recepcion_desde_vista(self):
        self.client.force_login(self.jefe)
        response = self.client.post(
            reverse("trazabilidad:nueva_recepcion"), self.datos_validos()
        )
        self.assertEqual(Recepcion.objects.count(), 1)
        self.assertEqual(DetalleRecepcion.objects.count(), 1)
        recepcion = Recepcion.objects.get()
        self.assertEqual(recepcion.registrado_por, self.jefe)
        self.assertRedirects(
            response, reverse("trazabilidad:detalle_recepcion", args=[recepcion.pk])
        )

    def test_recepcion_invalida_no_guarda_datos(self):
        self.client.force_login(self.jefe)
        datos = self.datos_validos()
        datos["form-0-peso_origen_kg"] = "0"
        response = self.client.post(reverse("trazabilidad:nueva_recepcion"), datos)
        self.assertEqual(response.status_code, 200)
        self.assertIn("peso_origen_kg", response.context["detalles"].forms[0].errors)
        self.assertEqual(Recepcion.objects.count(), 0)
        self.assertEqual(DetalleRecepcion.objects.count(), 0)

    def test_detalle_recepcion_muestra_datos(self):
        recepcion = registrar_recepcion(
            fecha_hora_recepcion=timezone.now(),
            registrado_por=self.jefe,
            detalles=[{
                "origen_sernapesca": self.origen,
                "especie": self.especie,
                "peso_origen_kg": Decimal("500.00"),
                "peso_recepcion_kg": Decimal("493.00"),
            }],
        )
        self.client.force_login(self.jefe)
        response = self.client.get(
            reverse("trazabilidad:detalle_recepcion", args=[recepcion.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.origen.folio_origen)
        self.assertContains(response, self.especie.nombre)

    def test_formset_vacio_o_eliminado_no_guarda_datos(self):
        self.client.force_login(self.jefe)
        for eliminado in (False, True):
            with self.subTest(eliminado=eliminado):
                datos = self.datos_validos()
                if eliminado:
                    datos["form-0-DELETE"] = "on"
                else:
                    for campo in ("origen_sernapesca", "especie", "peso_origen_kg", "peso_recepcion_kg"):
                        datos[f"form-0-{campo}"] = ""
                response = self.client.post(reverse("trazabilidad:nueva_recepcion"), datos)
                self.assertContains(response, "Debe ingresar al menos un detalle de recepción.")
                self.assertEqual(Recepcion.objects.count(), 0)
                self.assertEqual(DetalleRecepcion.objects.count(), 0)

    def test_varios_detalles_ignoran_eliminados_y_vacios(self):
        self.client.force_login(self.jefe)
        datos = self.datos_validos()
        datos["form-TOTAL_FORMS"] = "4"
        for indice in (1, 2):
            for campo in ("origen_sernapesca", "especie", "peso_origen_kg", "peso_recepcion_kg"):
                datos[f"form-{indice}-{campo}"] = datos[f"form-0-{campo}"]
        datos["form-2-DELETE"] = "on"
        response = self.client.post(reverse("trazabilidad:nueva_recepcion"), datos)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Recepcion.objects.count(), 1)
        self.assertEqual(DetalleRecepcion.objects.count(), 2)

    def test_error_del_servicio_conserva_formulario(self):
        self.client.force_login(self.jefe)
        with patch(
            "apps.trazabilidad.views.registrar_recepcion",
            side_effect=ValidationError({"peso_origen_kg": ["Error de validación de prueba."]}),
        ):
            response = self.client.post(
                reverse("trazabilidad:nueva_recepcion"), self.datos_validos()
            )
        self.assertContains(response, "Error de validación de prueba.")
        self.assertEqual(
            response.context["form"].non_field_errors(), ["Error de validación de prueba."]
        )
        self.assertEqual(response.context["form"]["observaciones"].value(), "Recepción de prueba")
        self.assertEqual(response.context["detalles"].forms[0]["peso_origen_kg"].value(), "500.00")
        self.assertEqual(Recepcion.objects.count(), 0)

    def test_enlace_inicio_segun_rol_y_acceso_superusuario(self):
        tecnico = Usuario.objects.create_superuser(
            username="tecnico_web", password="clave-prueba-123"
        )
        enlace = reverse("trazabilidad:lista_recepciones")
        for usuario in (self.jefe, self.encargada, tecnico):
            with self.subTest(usuario=usuario.username):
                self.client.force_login(usuario)
                self.assertContains(self.client.get(reverse("usuarios:inicio")), enlace)
                self.assertEqual(self.client.get(enlace).status_code, 200)
        self.client.force_login(self.operaria)
        self.assertNotContains(self.client.get(reverse("usuarios:inicio")), enlace)
