from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.trazabilidad.services import corregir_recepcion, registrar_recepcion
from apps.usuarios.models import Rol, Usuario

from .forms import EspecieRecibidaForm, EspecieRecibidaFormSet, OrigenRecepcionForm, RecepcionForm

from .models import (
    Correccion,
    DetalleRecepcion,
    Especie,
    EventoProceso,
    LoteProduccion,
    OrigenSernapesca,
    PartidaProceso,
    Recepcion,
    TipoProceso,
)


class ZonaHorariaTests(SimpleTestCase):
    def test_hora_local_utiliza_zona_de_santiago(self):
        self.assertEqual(settings.TIME_ZONE, "America/Santiago")
        self.assertTrue(settings.USE_TZ)
        self.assertEqual(str(timezone.localtime().tzinfo), "America/Santiago")


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
        self.datos_origen = {
            "folio_origen": "FOLIO-001", "tipo_origen": "Desembarque",
            "codigo_agente": "AG-01", "proveedor": "Proveedor prueba",
        }
        self.merluza = Especie.objects.create(nombre="Merluza del sur")
        self.sierra = Especie.objects.create(nombre="Sierra")

    def test_registrar_recepcion_con_dos_especies_y_un_origen(self):
        recepcion = registrar_recepcion(
            fecha_hora_recepcion=timezone.now(),
            registrado_por=self.usuario,
            origen=self.datos_origen,
            detalles=[
                {
                    "especie": self.merluza,
                    "peso_origen_kg": Decimal("500.00"),
                    "peso_recepcion_kg": Decimal("493.00"),
                },
                {
                    "especie": self.sierra,
                    "peso_origen_kg": Decimal("200.00"),
                    "peso_recepcion_kg": Decimal("198.00"),
                },
            ],
        )

        self.assertEqual(OrigenSernapesca.objects.count(), 1)
        self.assertEqual(Recepcion.objects.count(), 1)
        self.assertEqual(DetalleRecepcion.objects.count(), 2)
        origen = OrigenSernapesca.objects.get()
        for campo, valor in self.datos_origen.items():
            self.assertEqual(getattr(origen, campo), valor)
        self.assertEqual(set(recepcion.detalles.values_list("origen_sernapesca_id", flat=True)), {origen.pk})
        recepcion.refresh_from_db()
        self.assertEqual(recepcion.registrado_por, self.usuario)
        self.assertEqual(recepcion.detalles.count(), 2)
        merluza = recepcion.detalles.get(especie=self.merluza)
        sierra = recepcion.detalles.get(especie=self.sierra)
        self.assertEqual(merluza.peso_origen_kg, Decimal("500.00"))
        self.assertEqual(merluza.peso_recepcion_kg, Decimal("493.00"))
        self.assertEqual(sierra.peso_origen_kg, Decimal("200.00"))
        self.assertEqual(sierra.peso_recepcion_kg, Decimal("198.00"))

    def test_recepcion_sin_especies_falla(self):
        with self.assertRaises(ValidationError):
            registrar_recepcion(
                fecha_hora_recepcion=timezone.now(),
                registrado_por=self.usuario,
                origen=self.datos_origen,
                detalles=[],
            )

        self.assertEqual(OrigenSernapesca.objects.count(), 0)
        self.assertEqual(Recepcion.objects.count(), 0)
        self.assertEqual(DetalleRecepcion.objects.count(), 0)

    def test_error_en_segunda_especie_revierte_todo(self):
        with self.assertRaises(ValidationError) as error:
            registrar_recepcion(
                fecha_hora_recepcion=timezone.now(),
                registrado_por=self.usuario,
                origen=self.datos_origen,
                detalles=[
                    {
                        "especie": self.merluza,
                        "peso_origen_kg": Decimal("500.00"),
                        "peso_recepcion_kg": Decimal("493.00"),
                    },
                    {
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
        self.assertEqual(OrigenSernapesca.objects.count(), 0)
        self.assertEqual(Recepcion.objects.count(), 0)
        self.assertEqual(DetalleRecepcion.objects.count(), 0)


    def test_origen_invalido_revierte_toda_la_recepcion(self):
        with self.assertRaises(ValidationError):
            registrar_recepcion(
                fecha_hora_recepcion=timezone.now(), registrado_por=self.usuario,
                origen={**self.datos_origen, "folio_origen": ""},
                detalles=[{"especie": self.merluza, "peso_origen_kg": Decimal("10"),
                           "peso_recepcion_kg": Decimal("10")}],
            )
        self.assertEqual(Recepcion.objects.count(), 0)
        self.assertEqual(OrigenSernapesca.objects.count(), 0)
        self.assertEqual(DetalleRecepcion.objects.count(), 0)

    def test_recepcion_invalida_no_crea_origen(self):
        with self.assertRaises(ValidationError):
            registrar_recepcion(
                fecha_hora_recepcion=None, registrado_por=self.usuario,
                origen=self.datos_origen,
                detalles=[{"especie": self.merluza, "peso_origen_kg": Decimal("10"),
                           "peso_recepcion_kg": Decimal("10")}],
            )
        self.assertEqual(Recepcion.objects.count(), 0)
        self.assertEqual(OrigenSernapesca.objects.count(), 0)
        self.assertEqual(DetalleRecepcion.objects.count(), 0)

    def test_folio_repetido_crea_origen_independiente_y_acepta_iterable(self):
        for _ in range(2):
            registrar_recepcion(
                fecha_hora_recepcion=timezone.now(), registrado_por=self.usuario,
                origen=self.datos_origen,
                detalles=iter([{"especie": self.merluza, "peso_origen_kg": Decimal("10"),
                                "peso_recepcion_kg": Decimal("10")}]),
            )
        self.assertEqual(Recepcion.objects.count(), 2)
        self.assertEqual(OrigenSernapesca.objects.count(), 2)
        self.assertEqual(DetalleRecepcion.objects.count(), 2)
        self.assertEqual(PartidaProceso.objects.count(), 2)


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
        self.datos_origen = {
            "folio_origen": "FOLIO-WEB", "tipo_origen": "Desembarque",
            "codigo_agente": "AG-WEB", "proveedor": "Proveedor web",
        }
        self.especie = Especie.objects.create(nombre="Merluza del sur", activo=True)

    def datos_validos(self):
        return {
            "fecha_hora_recepcion": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
            "observaciones": "Recepción de prueba",
            "especies-TOTAL_FORMS": "1",
            "especies-INITIAL_FORMS": "0",
            "especies-MIN_NUM_FORMS": "0",
            "especies-MAX_NUM_FORMS": "1000",
            **self.datos_origen,
            "especies-0-especie": str(self.especie.pk),
            "especies-0-peso_origen_kg": "500.00",
            "especies-0-peso_recepcion_kg": "493.00",
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
        self.assertEqual(OrigenSernapesca.objects.count(), 0)
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
        self.assertEqual(OrigenSernapesca.objects.count(), 1)
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
        datos["especies-0-peso_origen_kg"] = "0"
        response = self.client.post(reverse("trazabilidad:nueva_recepcion"), datos)
        self.assertEqual(response.status_code, 200)
        self.assertIn("peso_origen_kg", response.context["especies"].forms[0].errors)
        self.assertEqual(OrigenSernapesca.objects.count(), 0)
        self.assertEqual(Recepcion.objects.count(), 0)
        self.assertEqual(DetalleRecepcion.objects.count(), 0)

    def test_detalle_recepcion_muestra_datos(self):
        recepcion = registrar_recepcion(
            fecha_hora_recepcion=timezone.now(),
            registrado_por=self.jefe,
            origen=self.datos_origen,
            detalles=[{
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
        self.assertContains(response, self.datos_origen["folio_origen"])
        self.assertContains(response, self.especie.nombre)
        self.assertContains(response, self.datos_origen["proveedor"])

    def test_formset_vacio_o_eliminado_no_guarda_datos(self):
        self.client.force_login(self.jefe)
        for eliminado in (False, True):
            with self.subTest(eliminado=eliminado):
                datos = self.datos_validos()
                if eliminado:
                    datos["especies-0-DELETE"] = "on"
                else:
                    for campo in ("especie", "peso_origen_kg", "peso_recepcion_kg"):
                        datos[f"especies-0-{campo}"] = ""
                response = self.client.post(reverse("trazabilidad:nueva_recepcion"), datos)
                self.assertContains(response, "Debe ingresar al menos una especie recibida.")
                self.assertEqual(OrigenSernapesca.objects.count(), 0)
                self.assertEqual(Recepcion.objects.count(), 0)
                self.assertEqual(DetalleRecepcion.objects.count(), 0)

    def test_varios_detalles_ignoran_eliminados_y_vacios(self):
        self.client.force_login(self.jefe)
        datos = self.datos_validos()
        datos["especies-TOTAL_FORMS"] = "4"
        for indice in (1, 2):
            for campo in ("especie", "peso_origen_kg", "peso_recepcion_kg"):
                datos[f"especies-{indice}-{campo}"] = datos[f"especies-0-{campo}"]
        segunda = Especie.objects.create(nombre="Sierra adicional")
        datos["especies-1-especie"] = str(segunda.pk)
        datos["especies-2-DELETE"] = "on"
        response = self.client.post(reverse("trazabilidad:nueva_recepcion"), datos)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(OrigenSernapesca.objects.count(), 1)
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
        self.assertEqual(response.context["origen"]["folio_origen"].value(), "FOLIO-WEB")
        self.assertEqual(response.context["origen"]["proveedor"].value(), "Proveedor web")
        self.assertEqual(response.context["especies"].forms[0]["peso_origen_kg"].value(), "500.00")
        self.assertEqual(OrigenSernapesca.objects.count(), 0)
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


    def test_encargada_registra_dos_especies_con_un_origen(self):
        self.client.force_login(self.encargada)
        segunda = Especie.objects.create(nombre="Sierra")
        datos = self.datos_validos()
        datos.update({"especies-TOTAL_FORMS": "2", "especies-1-especie": str(segunda.pk),
                      "especies-1-peso_origen_kg": "200", "especies-1-peso_recepcion_kg": "198",
                      "registrado_por": str(self.jefe.pk)})
        response = self.client.post(reverse("trazabilidad:nueva_recepcion"), datos)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Recepcion.objects.count(), 1)
        self.assertEqual(OrigenSernapesca.objects.count(), 1)
        self.assertEqual(DetalleRecepcion.objects.count(), 2)
        self.assertEqual(Recepcion.objects.get().registrado_por, self.encargada)
        self.assertEqual(set(DetalleRecepcion.objects.values_list("origen_sernapesca_id", flat=True)),
                         {OrigenSernapesca.objects.get().pk})

    def test_estructura_formset_dinamico(self):
        self.client.force_login(self.jefe)
        response = self.client.get(reverse("trazabilidad:nueva_recepcion"))
        self.assertContains(response, 'name="especies-TOTAL_FORMS"')
        self.assertContains(response, "+ Agregar otra especie")
        self.assertContains(response, '<template id="especie-vacia">')
        self.assertContains(response, 'name="especies-__prefix__-especie"')
        self.assertContains(response, '<div hidden><input type="checkbox" name="especies-0-DELETE"')
        self.assertNotContains(response, "No incluir este detalle al guardar")
        self.assertNotContains(response, 'name="origen_sernapesca"')

    def test_origen_invalido_conserva_especies_sin_guardar(self):
        self.client.force_login(self.jefe)
        datos = self.datos_validos()
        datos["folio_origen"] = ""
        response = self.client.post(reverse("trazabilidad:nueva_recepcion"), datos)
        self.assertEqual(response.status_code, 200)
        self.assertIn("folio_origen", response.context["origen"].errors)
        self.assertEqual(response.context["especies"][0]["peso_origen_kg"].value(), "500.00")
        self.assertEqual(response.context["origen"]["proveedor"].value(), "Proveedor web")
        self.assertEqual(Recepcion.objects.count(), 0)
        self.assertEqual(OrigenSernapesca.objects.count(), 0)
        self.assertEqual(DetalleRecepcion.objects.count(), 0)

    def test_management_ausente_o_cero_especies_no_guarda(self):
        self.client.force_login(self.jefe)
        for total in (None, "0"):
            datos = self.datos_validos()
            if total is None:
                del datos["especies-TOTAL_FORMS"]
            else:
                datos["especies-TOTAL_FORMS"] = total
            response = self.client.post(reverse("trazabilidad:nueva_recepcion"), datos)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["especies"].non_form_errors())
            self.assertEqual(Recepcion.objects.count(), 0)
            self.assertEqual(OrigenSernapesca.objects.count(), 0)
            self.assertEqual(DetalleRecepcion.objects.count(), 0)

    def test_delete_ignora_fila_incompleta_y_conserva_estado_tras_error(self):
        self.client.force_login(self.jefe)
        datos = self.datos_validos()
        datos.update({"especies-TOTAL_FORMS": "2", "especies-1-DELETE": "on",
                      "especies-1-peso_origen_kg": "-1", "folio_origen": ""})
        response = self.client.post(reverse("trazabilidad:nueva_recepcion"), datos)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["especies"][1]["DELETE"].value())
        self.assertEqual(Recepcion.objects.count(), 0)
        datos["folio_origen"] = "FOLIO-WEB"
        response = self.client.post(reverse("trazabilidad:nueva_recepcion"), datos)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(DetalleRecepcion.objects.count(), 1)
        self.assertEqual(OrigenSernapesca.objects.count(), 1)

    def test_especie_inactiva_no_se_puede_registrar(self):
        self.client.force_login(self.jefe)
        self.especie.activo = False
        self.especie.save()
        response = self.client.post(reverse("trazabilidad:nueva_recepcion"), self.datos_validos())
        self.assertIn("especie", response.context["especies"][0].errors)
        self.assertEqual(Recepcion.objects.count(), 0)
        self.assertEqual(OrigenSernapesca.objects.count(), 0)

    def test_historica_sin_detalles_no_falla(self):
        recepcion = Recepcion.objects.create(fecha_hora_recepcion=timezone.now(), registrado_por=self.jefe)
        self.client.force_login(self.jefe)
        response = self.client.get(reverse("trazabilidad:detalle_recepcion", args=[recepcion.pk]))
        self.assertContains(response, "No informado")
        self.assertContains(response, "No hay especies registradas.")
        self.assertEqual(response.context["origenes"], [])

    def test_historica_varios_origenes_sin_consultas_n_mas_uno(self):
        from .views import detalle_recepcion

        recepcion = Recepcion.objects.create(fecha_hora_recepcion=timezone.now(), registrado_por=self.jefe)
        for indice in range(3):
            origen = OrigenSernapesca.objects.create(folio_origen=f"HISTORICO-{indice}")
            DetalleRecepcion.objects.create(recepcion=recepcion, origen_sernapesca=origen,
                especie=self.especie, peso_origen_kg=10, peso_recepcion_kg=11)
        request = RequestFactory().get(reverse("trazabilidad:detalle_recepcion", args=[recepcion.pk]))
        request.user = self.jefe
        with self.assertNumQueries(5):  # Incluye una consulta para el historial.
            response = detalle_recepcion(request, recepcion.pk)
        for indice in range(3):
            self.assertContains(response, f"HISTORICO-{indice}")


class ValidacionesRecepcionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.usuario = Usuario.objects.create_user(
            username="validaciones", rol=Rol.objects.get(codigo="JEFE"),
        )
        cls.merluza = Especie.objects.create(nombre="Merluza validaciones")
        cls.sierra = Especie.objects.create(nombre="Sierra validaciones")

    def datos_especie(self, especie=None, origen="500.00", recepcion="493.00"):
        return {
            "especie": especie or self.merluza,
            "peso_origen_kg": Decimal(origen),
            "peso_recepcion_kg": Decimal(recepcion),
        }

    def registrar(self, **cambios):
        datos = {
            "fecha_hora_recepcion": timezone.now(),
            "registrado_por": self.usuario,
            "origen": {"folio_origen": "FOLIO-123"},
            "detalles": [self.datos_especie()],
        }
        datos.update(cambios)
        return registrar_recepcion(**datos)

    def assert_sin_registros(self):
        self.assertEqual(Recepcion.objects.count(), 0)
        self.assertEqual(OrigenSernapesca.objects.count(), 0)
        self.assertEqual(DetalleRecepcion.objects.count(), 0)

    def datos_web(self, especies=None):
        datos = {
            "fecha_hora_recepcion": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
            "folio_origen": "FOLIO-123",
            "observaciones": "Conservar observaciones",
            "proveedor": "Proveedor prueba",
            "especies-INITIAL_FORMS": "0",
        }
        filas = especies if especies is not None else [self.datos_especie()]
        datos["especies-TOTAL_FORMS"] = str(len(filas))
        for indice, fila in enumerate(filas):
            for campo, valor in fila.items():
                datos[f"especies-{indice}-{campo}"] = str(valor.pk if campo == "especie" else valor)
        return datos

    def test_form_fecha_actual_y_pasada_validas_futura_invalida(self):
        ahora = timezone.now()
        with patch("apps.trazabilidad.forms.timezone.now", return_value=ahora):
            for delta in (timedelta(), timedelta(days=-10)):
                form = RecepcionForm({"fecha_hora_recepcion": ahora + delta})
                self.assertTrue(form.is_valid(), form.errors)
            form = RecepcionForm({"fecha_hora_recepcion": ahora + timedelta(seconds=1)})
            self.assertFalse(form.is_valid())
            self.assertIn("La recepción no puede registrarse con una fecha y hora posterior a la actual.",
                          form.errors["fecha_hora_recepcion"])

    def test_forms_normalizan_textos_y_folio_obligatorio(self):
        form = OrigenRecepcionForm({
            "folio_origen": "   FOLIO-123   ", "tipo_origen": "  Desembarque ",
            "codigo_agente": "   ", "proveedor": "  Proveedor prueba  ",
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data, {
            "folio_origen": "FOLIO-123", "tipo_origen": "Desembarque",
            "codigo_agente": "", "proveedor": "Proveedor prueba",
        })
        for folio in ("", "    "):
            form = OrigenRecepcionForm({"folio_origen": folio})
            self.assertFalse(form.is_valid())
            self.assertIn("El folio de origen Sernapesca es obligatorio.", form.errors["folio_origen"])
        form = OrigenRecepcionForm({"folio_origen": "X" * 51})
        self.assertFalse(form.is_valid())
        for texto, esperado in (("  Observación prueba  ", "Observación prueba"), ("   ", "")):
            form = RecepcionForm({"fecha_hora_recepcion": timezone.now(), "observaciones": texto})
            self.assertTrue(form.is_valid(), form.errors)
            self.assertEqual(form.cleaned_data["observaciones"], esperado)

    def test_formset_distintas_validas_duplicadas_un_solo_error(self):
        datos = self.datos_web([self.datos_especie(), self.datos_especie(self.sierra)])
        formset = EspecieRecibidaFormSet(datos, prefix="especies")
        self.assertTrue(formset.is_valid(), formset.errors)
        datos = self.datos_web([self.datos_especie()] * 3)
        formset = EspecieRecibidaFormSet(datos, prefix="especies")
        self.assertFalse(formset.is_valid())
        self.assertEqual(len(formset.non_form_errors()), 1)
        self.assertIn("ya fue agregada", formset.non_form_errors()[0])
        self.assertIn(self.merluza.nombre, formset.non_form_errors()[0])

    def test_formset_ignora_duplicados_eliminados_y_filas_vacias(self):
        datos = self.datos_web([self.datos_especie()] * 2)
        datos.update({"especies-1-DELETE": "on", "especies-TOTAL_FORMS": "3"})
        formset = EspecieRecibidaFormSet(datos, prefix="especies")
        self.assertTrue(formset.is_valid(), formset.errors)

    def test_form_pesos_menor_igual_validos_mayor_invalido(self):
        for recibido in ("493", "500", "503"):
            form = EspecieRecibidaForm({
                "especie": self.merluza.pk, "peso_origen_kg": "500",
                "peso_recepcion_kg": recibido,
            })
            self.assertEqual(form.is_valid(), recibido != "503")
            if recibido == "503":
                self.assertIn("El peso en recepción no puede ser mayor que el peso de origen.",
                              form.errors["peso_recepcion_kg"])

    def test_form_pesos_cero_negativos_precision_y_digitos_invalidos(self):
        for campo in ("peso_origen_kg", "peso_recepcion_kg"):
            for valor in ("0", "-1", "1.001", "100000000.00"):
                with self.subTest(campo=campo, valor=valor):
                    datos = {"especie": self.merluza.pk, "peso_origen_kg": "500",
                             "peso_recepcion_kg": "493", campo: valor}
                    form = EspecieRecibidaForm(datos)
                    self.assertFalse(form.is_valid())
                    self.assertIn(campo, form.errors)

    def test_fecha_futura_no_crea_recepcion(self):
        with self.assertRaisesMessage(ValidationError, "La recepción no puede registrarse con una fecha y hora posterior a la actual."):
            self.registrar(fecha_hora_recepcion=timezone.now() + timedelta(days=1))
        self.assert_sin_registros()

    def test_service_fecha_actual_y_pasada_validas(self):
        ahora = timezone.now()
        with patch("apps.trazabilidad.services.timezone.now", return_value=ahora):
            for delta in (timedelta(), timedelta(days=-10)):
                self.registrar(fecha_hora_recepcion=ahora + delta)
        self.assertEqual(Recepcion.objects.count(), 2)

    def test_especie_duplicada_revierte_todo(self):
        with self.assertRaises(ValidationError) as error:
            self.registrar(detalles=[self.datos_especie()] * 3)
        self.assertEqual(len(error.exception.messages), 1)
        self.assertIn("ya fue agregada", error.exception.messages[0])
        self.assert_sin_registros()

    def test_especie_inactiva_no_se_puede_registrar(self):
        # The caller still holds an active instance; the database is authoritative.
        Especie.objects.filter(pk=self.merluza.pk).update(activo=False)
        self.assertTrue(self.merluza.activo)
        with self.assertRaisesMessage(ValidationError,
                                     f"La especie '{self.merluza.nombre}' no se encuentra activa."):
            self.registrar()
        self.assert_sin_registros()
        self.assertTrue(self.merluza.activo)  # The service did not mutate the caller's object.

    def test_peso_recepcion_mayor_que_origen_revierte_todo(self):
        with self.assertRaisesMessage(ValidationError, "no puede ser mayor"):
            self.registrar(detalles=[self.datos_especie(),
                                    self.datos_especie(self.sierra, recepcion="503")])
        self.assert_sin_registros()

    def test_peso_recepcion_igual_origen_es_valido(self):
        recepcion = self.registrar(detalles=[self.datos_especie(recepcion="500")])
        detalle = recepcion.detalles.get()
        self.assertEqual(detalle.peso_origen_kg, detalle.peso_recepcion_kg)
        self.assertEqual(OrigenSernapesca.objects.count(), 1)

    def test_service_pesos_invalidos_no_guardan(self):
        for campo in ("peso_origen_kg", "peso_recepcion_kg"):
            for valor in ("0", "-1", "1.001", "100000000.00", "NaN"):
                with self.subTest(campo=campo, valor=valor):
                    datos = self.datos_especie()
                    datos[campo] = Decimal(valor)
                    with self.assertRaises(ValidationError):
                        self.registrar(detalles=[datos])
                    self.assert_sin_registros()

    def test_textos_se_normalizan(self):
        recepcion = self.registrar(
            origen={"folio_origen": "  FOLIO-123  ", "proveedor": "  Proveedor prueba  ",
                    "tipo_origen": "  Desembarque ", "codigo_agente": "  "},
            observaciones="  Observación prueba  ",
        )
        origen = recepcion.detalles.get().origen_sernapesca
        self.assertEqual(origen.folio_origen, "FOLIO-123")
        self.assertEqual(origen.proveedor, "Proveedor prueba")
        self.assertEqual(origen.tipo_origen, "Desembarque")
        self.assertEqual(origen.codigo_agente, "")
        self.assertEqual(recepcion.observaciones, "Observación prueba")
        recepcion = self.registrar(observaciones="   ")
        self.assertEqual(recepcion.observaciones, "")

    def test_folio_solo_espacios_falla(self):
        with self.assertRaisesMessage(ValidationError, "El folio de origen Sernapesca es obligatorio."):
            self.registrar(origen={"folio_origen": "   "})
        self.assert_sin_registros()

    def test_service_rechaza_textos_no_string_y_longitudes_invalidas(self):
        for campo in ("folio_origen", "tipo_origen", "codigo_agente", "proveedor"):
            with self.subTest(campo=campo):
                with self.assertRaises(ValidationError):
                    self.registrar(origen={"folio_origen": "FOLIO", campo: 123})
                self.assert_sin_registros()
        with self.assertRaises(ValidationError):
            self.registrar(observaciones=123)
        with self.assertRaises(ValidationError):
            self.registrar(origen={"folio_origen": "X" * 51})
        self.assert_sin_registros()

    def test_error_tardio_revierte_recepcion_origen_y_detalles(self):
        with patch.object(DetalleRecepcion, "full_clean",
                          side_effect=[None, ValidationError("Error al validar segunda especie")]):
            with self.assertRaisesMessage(ValidationError, "segunda especie"):
                self.registrar(detalles=[self.datos_especie(), self.datos_especie(self.sierra)])
        self.assert_sin_registros()

    def test_web_fecha_futura_duplicado_y_peso_mayor_no_guardan(self):
        self.client.force_login(self.usuario)
        casos = [
            ({"fecha_hora_recepcion": (timezone.localtime() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")},
             "La recepción no puede registrarse con una fecha y hora posterior a la actual."),
            ({"especies-TOTAL_FORMS": "2", "especies-1-especie": str(self.merluza.pk),
              "especies-1-peso_origen_kg": "500", "especies-1-peso_recepcion_kg": "493"},
             "ya fue agregada"),
            ({"especies-0-peso_recepcion_kg": "503"},
             "El peso en recepción no puede ser mayor que el peso de origen."),
        ]
        for cambios, mensaje in casos:
            with self.subTest(cambios=cambios):
                datos = self.datos_web()
                datos.update(cambios)
                response = self.client.post(reverse("trazabilidad:nueva_recepcion"), datos)
                self.assertContains(response, mensaje, status_code=200)
                self.assertEqual(response.context["form"]["observaciones"].value(), "Conservar observaciones")
                self.assertEqual(response.context["origen"]["proveedor"].value(), "Proveedor prueba")
                self.assert_sin_registros()


class ListadoRecepcionesFiltrosTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.jefe = Usuario.objects.create_user(username="filtro_jefe", rol=Rol.objects.get(codigo="JEFE"))
        cls.encargada = Usuario.objects.create_user(username="filtro_encargada", rol=Rol.objects.get(codigo="ENCARGADA"))
        cls.operaria = Usuario.objects.create_user(username="filtro_operaria", rol=Rol.objects.get(codigo="OPERARIA"))
        cls.merluza = Especie.objects.create(nombre="Merluza filtro")
        cls.sierra = Especie.objects.create(nombre="Sierra filtro")
        cls.primera = cls.crear("2025-09-01T00:00:00", "FOLIO-ALFA", "Pesquera Austral", cls.jefe, [cls.merluza, cls.sierra])
        cls.ultima = cls.crear("2025-09-30T23:59:59.999999", "FOLIO-BETA", "Pacifico", cls.encargada, [cls.sierra])
        cls.fuera = cls.crear("2025-08-31T23:59:59", "FOLIO-GAMMA", "Pesquera Austral", cls.jefe, [cls.merluza])

    @classmethod
    def crear(cls, fecha, folio, proveedor, usuario, especies):
        from datetime import datetime

        recepcion = Recepcion.objects.create(
            fecha_hora_recepcion=timezone.make_aware(datetime.fromisoformat(fecha)),
            registrado_por=usuario,
        )
        origen = OrigenSernapesca.objects.create(folio_origen=folio, proveedor=proveedor)
        for especie in especies:
            DetalleRecepcion.objects.create(
                recepcion=recepcion, origen_sernapesca=origen, especie=especie,
                peso_origen_kg=500, peso_recepcion_kg=493,
            )
        return recepcion

    def setUp(self):
        self.client.force_login(self.jefe)
        self.url = reverse("trazabilidad:lista_recepciones")

    def ids(self, response):
        return [r.pk for r in response.context["page_obj"]]

    def test_buscar_recepcion_por_id(self):
        response = self.client.get(self.url, {"q": str(self.primera.pk)})
        self.assertEqual(self.ids(response), [self.primera.pk])

    def test_buscar_por_folio(self):
        response = self.client.get(self.url, {"q": "  folio-al  "})
        self.assertEqual(self.ids(response), [self.primera.pk])
        self.assertEqual(response.context["page_obj"].paginator.count, 1)

    def test_buscar_por_proveedor(self):
        response = self.client.get(self.url, {"q": "austral"})
        self.assertEqual(self.ids(response), [self.primera.pk, self.fuera.pk])

    def test_filtrar_por_especie(self):
        response = self.client.get(self.url, {"especie": self.merluza.pk})
        self.assertEqual(self.ids(response), [self.primera.pk, self.fuera.pk])

    def test_filtrar_por_rango_de_fechas(self):
        self.crear("2025-10-01T00:00:00", "POSTERIOR", "Otro", self.jefe, [self.merluza])
        response = self.client.get(self.url, {"fecha_desde": "2025-09-01", "fecha_hasta": "2025-09-30"})
        self.assertEqual(self.ids(response), [self.ultima.pk, self.primera.pk])
        # Local late evening falls on the following UTC date and must be included.
        response = self.client.get(self.url, {"fecha_desde": "2025-09-30", "fecha_hasta": "2025-09-30"})
        self.assertEqual(self.ids(response), [self.ultima.pk])

    def test_fechas_individuales_y_filtros_futuros_permitidos(self):
        response = self.client.get(self.url, {"fecha_hasta": "2025-08-31"})
        self.assertEqual(self.ids(response), [self.fuera.pk])
        response = self.client.get(self.url, {"fecha_desde": "2025-09-30"})
        self.assertEqual(self.ids(response), [self.ultima.pk])
        response = self.client.get(self.url, {"fecha_desde": "2090-01-01"})
        self.assertFalse(response.context["filtro"].errors)
        self.assertEqual(self.ids(response), [])

    def test_rango_de_fechas_invalido(self):
        response = self.client.get(self.url, {"fecha_desde": "2025-10-01", "fecha_hasta": "2025-09-01"})
        self.assertContains(response, "La fecha inicial no puede ser posterior a la fecha final.", status_code=200)

    def test_parametros_invalidos_no_producen_excepcion(self):
        for parametros in ({"fecha_desde": "no-fecha"}, {"especie": "99999999"}, {"registrado_por": "no-usuario"}):
            with self.subTest(parametros=parametros):
                response = self.client.get(self.url, parametros)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["filtro"].errors)
                self.assertEqual(self.ids(response), [])

    def test_filtrar_por_usuario(self):
        response = self.client.get(self.url, {"registrado_por": self.jefe.pk})
        self.assertEqual(self.ids(response), [self.primera.pk, self.fuera.pk])

    def test_combinar_filtros(self):
        response = self.client.get(self.url, {
            "q": "Austral", "especie": self.merluza.pk,
            "fecha_desde": "2025-09-01", "fecha_hasta": "2025-09-30", "registrado_por": self.jefe.pk,
        })
        self.assertEqual(self.ids(response), [self.primera.pk])

    def test_busqueda_sin_resultados(self):
        response = self.client.get(self.url, {"q": "SIN-COINCIDENCIAS"})
        self.assertContains(response, "No se encontraron recepciones con los criterios seleccionados.")
        self.assertContains(response, "Limpiar filtros")
        self.assertContains(response, "0 recepciones encontradas")

    def test_paginacion(self):
        nuevas = [self.crear("2025-09-15T12:00:00", f"PAG-{i}", "Otro", self.jefe, [self.merluza])
                  for i in range(8)]
        primera = self.client.get(self.url)
        segunda = self.client.get(self.url, {"page": 2})
        self.assertEqual(len(self.ids(primera)), 10)
        self.assertEqual(len(self.ids(segunda)), 1)
        self.assertEqual(primera.context["page_obj"].paginator.count, 11)
        self.assertEqual(self.ids(primera)[1:9], [r.pk for r in reversed(nuevas)])
        self.assertEqual(self.ids(segunda), [self.fuera.pk])
        self.assertIsNone(primera.context["pagina_anterior"])
        self.assertIsNone(segunda.context["pagina_siguiente"])

    def test_paginacion_conserva_filtros(self):
        from urllib.parse import parse_qs, urlsplit
        from html import escape

        for i in range(11):
            self.crear("2025-09-15T12:00:00", f"ENCODE-{i}", "Austral & Sur", self.jefe, [self.merluza])
        parametros = {"q": "Austral & Sur", "especie": str(self.merluza.pk),
                      "fecha_desde": "2025-09-01", "fecha_hasta": "2025-09-30",
                      "registrado_por": str(self.jefe.pk)}
        response = self.client.get(self.url, parametros)
        enlace = response.context["pagina_siguiente"]
        self.assertEqual(parse_qs(urlsplit(enlace).query), {**{k: [v] for k, v in parametros.items()}, "page": ["2"]})
        self.assertContains(response, escape(enlace, quote=True))
        response = self.client.get(self.url + enlace)
        self.assertEqual(len(self.ids(response)), 1)
        self.assertEqual(parse_qs(urlsplit(response.context["pagina_anterior"]).query)["q"], ["Austral & Sur"])

    def test_paginas_invalidas_y_busqueda_numerica_grande(self):
        for pagina in ("abc", "-1", "99999"):
            response = self.client.get(self.url, {"page": pagina})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context["page_obj"].number, 1)
        response = self.client.get(self.url, {"q": "9" * 100})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.ids(response), [])

    def test_listado_no_genera_n_mas_uno(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from .views import lista_recepciones

        def consultas():
            request = RequestFactory().get(self.url)
            request.user = self.jefe
            with CaptureQueriesContext(connection) as capturadas:
                response = lista_recepciones(request)
            self.assertEqual(response.status_code, 200)
            return len(capturadas)

        pocas = consultas()
        for i in range(8):
            self.crear("2025-09-15T12:00:00", f"PERF-{i}", "Otro", self.jefe, [self.merluza, self.sierra])
        muchas = consultas()
        self.assertEqual(pocas, muchas)
        self.assertLessEqual(muchas, 8)

    def test_operaria_sigue_sin_acceso(self):
        self.client.force_login(self.operaria)
        self.assertEqual(self.client.get(self.url, {"q": "Austral"}).status_code, 403)

    def test_anonimo_sigue_redirigiendo_al_login(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertRedirects(response, f"{reverse('usuarios:login')}?next={self.url}")

    def test_listado_vacio_sin_filtros(self):
        DetalleRecepcion.objects.all().delete()
        Recepcion.objects.all().delete()
        response = self.client.get(self.url)
        self.assertContains(response, "No hay recepciones registradas.")
        self.assertContains(response, "Registrar primera recepción")


class CorreccionRecepcionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.jefe = Usuario.objects.create_user(username="jefe_correccion", first_name="Jano",
                                               last_name="Belliazzi", rol=Rol.objects.get(codigo="JEFE"))
        cls.encargada = Usuario.objects.create_user(username="encargada_correccion", rol=Rol.objects.get(codigo="ENCARGADA"))
        cls.operaria = Usuario.objects.create_user(username="operaria_correccion", rol=Rol.objects.get(codigo="OPERARIA"))
        cls.tecnico = Usuario.objects.create_superuser(username="tecnico_correccion")
        cls.merluza = Especie.objects.create(nombre="Merluza correccion")
        cls.sierra = Especie.objects.create(nombre="Sierra correccion")

    def setUp(self):
        self.recepcion = registrar_recepcion(
            fecha_hora_recepcion=timezone.now() - timedelta(days=1), registrado_por=self.jefe,
            origen={"folio_origen": "FOLIO-CORREGIR", "proveedor": "Proveedor anterior"},
            observaciones="Original",
            detalles=[{"especie": self.merluza, "peso_origen_kg": Decimal("500"), "peso_recepcion_kg": Decimal("493")}],
        )
        self.detalle = self.recepcion.detalles.get()
        self.origen = self.detalle.origen_sernapesca
        self.url = reverse("trazabilidad:corregir_recepcion", args=[self.recepcion.pk])

    def datos(self, **cambios):
        datos = {
            "recepcion": self.recepcion, "usuario": self.jefe,
            "fecha_hora_recepcion": self.recepcion.fecha_hora_recepcion,
            "observaciones": "Original",
            "origen": {"folio_origen": "FOLIO-CORREGIR", "proveedor": "Proveedor anterior"},
            "detalles": [{"detalle_id": self.detalle.pk, "especie": self.merluza,
                          "peso_origen_kg": Decimal("500"), "peso_recepcion_kg": Decimal("493")}],
            "motivo": "  Error de tipeo  ",
        }
        datos.update(cambios)
        return datos

    def web(self, **cambios):
        datos = {
            "fecha_hora_recepcion": timezone.localtime(self.recepcion.fecha_hora_recepcion).isoformat(),
            "observaciones": "Original", "folio_origen": "FOLIO-CORREGIR",
            "tipo_origen": "", "codigo_agente": "", "proveedor": "Proveedor anterior",
            "especies-TOTAL_FORMS": "1", "especies-INITIAL_FORMS": "1",
            "especies-0-detalle_id": str(self.detalle.pk), "especies-0-especie": str(self.merluza.pk),
            "especies-0-peso_origen_kg": "500.00", "especies-0-peso_recepcion_kg": "493.00",
            "motivo": "Error de tipeo",
        }
        datos.update(cambios)
        return datos

    def procesar(self):
        partida = self.detalle.partidas.get()
        EventoProceso.objects.create(
            partida=partida, tipo_proceso=TipoProceso.objects.get(codigo="PROCESAMIENTO"),
            fecha_hora_inicio=timezone.now(), iniciado_por=self.jefe,
        )
        return partida

    def assert_sin_cambios(self):
        self.recepcion.refresh_from_db()
        self.origen.refresh_from_db()
        self.detalle.refresh_from_db()
        self.assertEqual(self.recepcion.observaciones, "Original")
        self.assertEqual(self.origen.proveedor, "Proveedor anterior")
        self.assertEqual(self.detalle.especie_id, self.merluza.pk)
        self.assertEqual(self.detalle.peso_recepcion_kg, Decimal("493"))
        self.assertEqual(Correccion.objects.count(), 0)

    def test_corregir_observaciones_crea_auditoria(self):
        corregir_recepcion(**self.datos(observaciones="  Observación nueva  "))
        self.recepcion.refresh_from_db()
        self.assertEqual(self.recepcion.observaciones, "Observación nueva")
        auditoria = Correccion.objects.get()
        self.assertEqual(auditoria.entidad_afectada, "Recepcion")
        self.assertEqual(auditoria.identificador_registro, str(self.recepcion.pk))
        self.assertEqual(auditoria.campo, "observaciones")
        self.assertEqual(auditoria.valor_anterior, "Original")
        self.assertEqual(auditoria.valor_nuevo, "Observación nueva")
        self.assertEqual(auditoria.usuario, self.jefe)
        self.assertEqual(auditoria.motivo, "Error de tipeo")
        self.assertIsNotNone(auditoria.fecha_hora)

    def test_corregir_proveedor_crea_auditoria(self):
        datos = self.datos()
        datos["origen"]["proveedor"] = "Proveedor nuevo"
        corregir_recepcion(**datos)
        self.origen.refresh_from_db()
        self.assertEqual(self.origen.proveedor, "Proveedor nuevo")
        auditoria = Correccion.objects.get()
        self.assertEqual(auditoria.entidad_afectada, "OrigenSernapesca")
        self.assertEqual(auditoria.identificador_registro, str(self.origen.pk))
        self.assertEqual(auditoria.valor_anterior, "Proveedor anterior")
        self.assertEqual(auditoria.valor_nuevo, "Proveedor nuevo")

    def test_corregir_varios_campos_crea_una_auditoria_por_campo(self):
        datos = self.datos(observaciones="Nueva")
        datos["origen"]["proveedor"] = "Nuevo"
        corregir_recepcion(**datos)
        self.assertEqual(set(Correccion.objects.values_list("campo", flat=True)), {"observaciones", "proveedor"})
        self.assertEqual(Correccion.objects.count(), 2)

    def test_corregir_especie_sin_procesamiento(self):
        datos = self.datos()
        datos["detalles"][0]["especie"] = self.sierra
        corregir_recepcion(**datos)
        self.detalle.refresh_from_db()
        self.assertEqual(self.detalle.especie, self.sierra)
        auditoria = Correccion.objects.get()
        self.assertEqual(auditoria.valor_anterior, self.merluza.nombre)
        self.assertEqual(auditoria.valor_nuevo, self.sierra.nombre)
        self.assertEqual(auditoria.identificador_registro, str(self.detalle.pk))

    def test_corregir_pesos_sin_procesamiento(self):
        datos = self.datos()
        datos["detalles"][0]["peso_recepcion_kg"] = Decimal("490")
        corregir_recepcion(**datos)
        auditoria = Correccion.objects.get()
        self.assertEqual(auditoria.valor_anterior, "493.00")
        self.assertEqual(auditoria.valor_nuevo, "490.00")

    def test_correccion_invalida_hace_rollback(self):
        datos = self.datos(observaciones="Nueva")
        datos["origen"]["proveedor"] = "Nuevo"
        datos["detalles"][0]["peso_recepcion_kg"] = Decimal("503")
        with self.assertRaises(ValidationError):
            corregir_recepcion(**datos)
        self.assert_sin_cambios()

    def test_fallo_de_auditoria_revierte_cambios_y_auditorias_parciales(self):
        original = Correccion.save
        llamadas = []
        def guardar(objeto, *args, **kwargs):
            llamadas.append(objeto)
            if len(llamadas) == 2:
                raise ValidationError("Fallo de auditoría")
            return original(objeto, *args, **kwargs)
        datos = self.datos(observaciones="Nueva")
        datos["origen"]["proveedor"] = "Nuevo"
        with patch.object(Correccion, "save", guardar):
            with self.assertRaisesMessage(ValidationError, "Fallo de auditoría"):
                corregir_recepcion(**datos)
        self.assert_sin_cambios()

    def test_sin_cambios_no_crea_auditoria(self):
        with patch.object(Recepcion, "save") as guardar:
            with self.assertRaisesMessage(ValidationError, "No se detectaron cambios para registrar."):
                corregir_recepcion(**self.datos())
        guardar.assert_not_called()
        self.assert_sin_cambios()

    def test_motivo_vacio_falla(self):
        with self.assertRaisesMessage(ValidationError, "Indica el motivo"):
            corregir_recepcion(**self.datos(observaciones="Nueva", motivo="   "))
        self.assert_sin_cambios()

    def test_detalle_id_de_otra_recepcion_falla(self):
        otra = Recepcion.objects.create(fecha_hora_recepcion=timezone.now(), registrado_por=self.jefe)
        ajeno = DetalleRecepcion.objects.create(recepcion=otra, origen_sernapesca=self.origen,
                  especie=self.sierra, peso_origen_kg=10, peso_recepcion_kg=10)
        datos = self.datos()
        datos["detalles"][0]["detalle_id"] = ajeno.pk
        with self.assertRaises(ValidationError):
            corregir_recepcion(**datos)
        self.assert_sin_cambios()

    def test_no_se_puede_omitir_un_detalle(self):
        with self.assertRaises(ValidationError):
            corregir_recepcion(**self.datos(detalles=[]))
        self.assert_sin_cambios()

    def test_no_se_puede_agregar_detalle_desconocido(self):
        datos = self.datos()
        datos["detalles"].append({**datos["detalles"][0], "detalle_id": 999999})
        with self.assertRaises(ValidationError):
            corregir_recepcion(**datos)
        self.assert_sin_cambios()

    def test_no_se_pueden_repetir_ids(self):
        datos = self.datos()
        datos["detalles"] *= 2
        with self.assertRaises(ValidationError):
            corregir_recepcion(**datos)
        self.assert_sin_cambios()

    def test_procesamiento_bloquea_cambio_de_especie(self):
        self.procesar()
        datos = self.datos()
        datos["detalles"][0]["especie"] = self.sierra
        with self.assertRaisesMessage(ValidationError, "procesamiento"):
            corregir_recepcion(**datos)
        self.assert_sin_cambios()

    def test_procesamiento_bloquea_cambio_de_pesos(self):
        self.procesar()
        for campo, valor in (("peso_origen_kg", "501"), ("peso_recepcion_kg", "490")):
            datos = self.datos()
            datos["detalles"][0][campo] = Decimal(valor)
            with self.assertRaisesMessage(ValidationError, "procesamiento"):
                corregir_recepcion(**datos)
            self.assert_sin_cambios()

    def test_procesamiento_permite_corregir_proveedor(self):
        self.procesar()
        datos = self.datos()
        datos["origen"]["proveedor"] = "Nuevo"
        corregir_recepcion(**datos)
        self.assertEqual(Correccion.objects.get().campo, "proveedor")

    def test_recepcion_historica_con_multiples_origenes_no_se_fusiona(self):
        otro = OrigenSernapesca.objects.create(folio_origen="HISTORICO")
        DetalleRecepcion.objects.create(recepcion=self.recepcion, origen_sernapesca=otro,
                especie=self.sierra, peso_origen_kg=10, peso_recepcion_kg=10)
        with self.assertRaisesMessage(ValidationError, "estructura histórica"):
            corregir_recepcion(**self.datos(observaciones="Nueva"))
        self.assert_sin_cambios()
        self.client.force_login(self.jefe)
        self.assertContains(self.client.get(self.url), "estructura histórica")
        self.assertContains(self.client.post(self.url, self.web()), "estructura histórica")

    def test_origen_compartido_no_modifica_otra_recepcion(self):
        otra = Recepcion.objects.create(fecha_hora_recepcion=timezone.now(), registrado_por=self.jefe)
        DetalleRecepcion.objects.create(recepcion=otra, origen_sernapesca=self.origen,
                                       especie=self.sierra, peso_origen_kg=10, peso_recepcion_kg=10)
        datos = self.datos()
        datos["origen"]["proveedor"] = "Nuevo"
        with self.assertRaisesMessage(ValidationError, "compartido"):
            corregir_recepcion(**datos)
        self.assert_sin_cambios()

    def test_jefe_puede_ver_corregir_recepcion(self):
        self.client.force_login(self.jefe)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="especies-0-detalle_id"')
        self.assertNotContains(response, "Agregar otra especie")
        self.assertNotContains(response, "Quitar especie")
        self.assertNotContains(response, "-DELETE")
        valor = timezone.localtime(self.recepcion.fecha_hora_recepcion).strftime("%Y-%m-%dT%H:%M:%S")
        self.assertContains(response, valor)

    def test_superusuario_puede_corregir(self):
        self.client.force_login(self.tecnico)
        response = self.client.post(self.url, self.web(observaciones="Nueva"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Correccion.objects.get().usuario, self.tecnico)

    def test_encargada_no_puede_corregir(self):
        self.client.force_login(self.encargada)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, self.web()).status_code, 403)
        self.assert_sin_cambios()

    def test_operaria_no_puede_corregir(self):
        self.client.force_login(self.operaria)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, self.web()).status_code, 403)
        self.assert_sin_cambios()

    def test_service_tambien_protege_permiso(self):
        from django.core.exceptions import PermissionDenied
        with self.assertRaises(PermissionDenied):
            corregir_recepcion(**self.datos(usuario=self.encargada, observaciones="Nueva"))
        self.assert_sin_cambios()

    def test_anonimo_es_redirigido_al_login(self):
        self.assertRedirects(self.client.get(self.url), f"{reverse('usuarios:login')}?next={self.url}")

    def test_correccion_desde_vista_redirige_al_detalle(self):
        self.client.force_login(self.jefe)
        response = self.client.post(self.url, self.web(observaciones="Nueva"))
        self.assertRedirects(response, reverse("trazabilidad:detalle_recepcion", args=[self.recepcion.pk]))
        self.assertEqual(Correccion.objects.count(), 1)

    def test_motivo_es_obligatorio(self):
        self.client.force_login(self.jefe)
        self.assertContains(self.client.post(self.url, self.web(motivo="  ")), "Indica el motivo")
        self.assert_sin_cambios()

    def test_error_conserva_formulario(self):
        self.client.force_login(self.jefe)
        response = self.client.post(self.url, self.web(**{"especies-0-peso_recepcion_kg": "503",
                                                        "proveedor": "Nuevo", "motivo": "Revisar peso"}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["origen"]["proveedor"].value(), "Nuevo")
        self.assertEqual(response.context["motivo"]["motivo"].value(), "Revisar peso")
        self.assert_sin_cambios()

    def test_sin_cambios_en_vista_conserva_formulario(self):
        self.client.force_login(self.jefe)
        self.assertContains(self.client.post(self.url, self.web()), "No se detectaron cambios para registrar.")
        self.assert_sin_cambios()

    def test_boton_corregir_visible_solo_para_jefe_y_tecnico(self):
        detalle_url = reverse("trazabilidad:detalle_recepcion", args=[self.recepcion.pk])
        for usuario in (self.jefe, self.tecnico, self.encargada):
            self.client.force_login(usuario)
            response = self.client.get(detalle_url)
            if usuario == self.encargada:
                self.assertNotContains(response, self.url)
            else:
                self.assertContains(response, self.url)

    def test_historial_aparece_en_detalle_y_ordenado(self):
        corregir_recepcion(**self.datos(observaciones="Primera nueva"))
        corregir_recepcion(**self.datos(observaciones="Segunda nueva"))
        self.client.force_login(self.jefe)
        response = self.client.get(reverse("trazabilidad:detalle_recepcion", args=[self.recepcion.pk]))
        for texto in ("Observaciones", "Original", "Primera nueva", "Segunda nueva", "Error de tipeo", "Jano Belliazzi"):
            self.assertContains(response, texto)
        self.assertEqual([h.valor_nuevo for h in response.context["historial"]], ["Segunda nueva", "Primera nueva"])
        self.assertEqual(Correccion.objects.count(), 2)

    def test_aviso_procesamiento_y_post_manipulado(self):
        self.procesar()
        self.client.force_login(self.jefe)
        response = self.client.get(self.url)
        self.assertContains(response, "ya inició su procesamiento")
        self.assertIn("disabled", response.context["especies"][0].fields["especie"].widget.attrs)
        datos = self.web(**{"especies-0-especie": str(self.sierra.pk)})
        self.assertContains(self.client.post(self.url, datos), "procesamiento")
        self.assert_sin_cambios()

    def test_post_procesado_sin_campos_disabled_corrige_descripcion(self):
        self.procesar()
        self.client.force_login(self.jefe)
        datos = self.web(proveedor="Nuevo")
        for campo in ("especie", "peso_origen_kg", "peso_recepcion_kg"):
            del datos[f"especies-0-{campo}"]
        self.assertEqual(self.client.post(self.url, datos).status_code, 302)
        self.assertEqual(Correccion.objects.get().campo, "proveedor")

    def test_post_no_permite_omitir_o_reemplazar_ids(self):
        self.client.force_login(self.jefe)
        for cambios in ({"especies-TOTAL_FORMS": "0"}, {"especies-0-detalle_id": "999999"}):
            self.assertEqual(self.client.post(self.url, self.web(**cambios)).status_code, 200)
            self.assert_sin_cambios()

    def test_service_reutiliza_validaciones_fecha_folio_y_especie_activa(self):
        for cambios in ({"fecha_hora_recepcion": timezone.now() + timedelta(days=1)},
                        {"origen": {"folio_origen": "   "}}):
            with self.assertRaises(ValidationError):
                corregir_recepcion(**self.datos(**cambios))
            self.assert_sin_cambios()
        Especie.objects.filter(pk=self.merluza.pk).update(activo=False)
        with self.assertRaisesMessage(ValidationError, "no se encuentra activa"):
            corregir_recepcion(**self.datos(observaciones="Nueva"))
        self.assert_sin_cambios()


    def test_fecha_visible_sin_microsegundos_no_crea_cambio_involuntario(self):
        self.client.force_login(self.jefe)
        fecha = timezone.localtime(self.recepcion.fecha_hora_recepcion).strftime("%Y-%m-%dT%H:%M:%S")
        response = self.client.post(self.url, self.web(fecha_hora_recepcion=fecha, observaciones="Nueva"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Correccion.objects.get().campo, "observaciones")


    def test_listado_muestra_corregir_para_jefe(self):
        self.client.force_login(self.jefe)
        response = self.client.get(reverse("trazabilidad:lista_recepciones"))
        self.assertContains(response, f'href="{self.url}"')
        self.assertContains(response, f'href="{reverse("trazabilidad:detalle_recepcion", args=[self.recepcion.pk])}"')

    def test_listado_muestra_corregir_para_superusuario(self):
        self.client.force_login(self.tecnico)
        response = self.client.get(reverse("trazabilidad:lista_recepciones"))
        self.assertContains(response, f'href="{self.url}"')

    def test_listado_no_muestra_corregir_para_encargada(self):
        self.client.force_login(self.encargada)
        response = self.client.get(reverse("trazabilidad:lista_recepciones"))
        self.assertNotContains(response, f'href="{self.url}"')
        self.assertContains(response, f'href="{reverse("trazabilidad:detalle_recepcion", args=[self.recepcion.pk])}"')

    def test_detalle_sigue_mostrando_corregir_para_jefe(self):
        self.client.force_login(self.jefe)
        response = self.client.get(reverse("trazabilidad:detalle_recepcion", args=[self.recepcion.pk]))
        self.assertContains(response, f'href="{self.url}"')
        html = response.content.decode()
        volver = html.index('>Volver</a>')
        corregir = html.index('>Corregir recepción</a>')
        nueva = html.index('>Nueva recepción</a>')
        self.assertLess(volver, corregir)
        self.assertLess(corregir, nueva)

    def test_detalle_no_muestra_corregir_para_encargada(self):
        self.client.force_login(self.encargada)
        response = self.client.get(reverse("trazabilidad:detalle_recepcion", args=[self.recepcion.pk]))
        self.assertNotContains(response, f'href="{self.url}"')
