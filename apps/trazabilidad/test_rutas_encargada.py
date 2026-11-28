from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.usuarios.models import Rol, Usuario

from .models import EventoProceso, PartidaProceso, RutaProceso, SolicitudRuta, TipoProceso
from .services_rutas import guardar_operacion, nombre_para_secuencia, resolver_solicitud
from .test_gestion_rutas import DatosGestionRutas


class RutasEncargadaTests(DatosGestionRutas, TestCase):
    def setUp(self):
        self.preparar()
        self.encargada = Usuario.objects.create_user(username="encargada_rutas", first_name="Carolina",
                                                     rol=Rol.objects.get(codigo="ENCARGADA"))
        self.url = reverse("trazabilidad:seleccionar_operaciones", args=[self.partida.pk])
        # Fileteo solo no calza con la ruta de prueba (Descabezado, Fileteo, Emparrillado).
        self.datos = self.secuencia_post(cantidad_kg="100")

    def test_encargada_crea_la_ruta_y_empieza_sin_solicitar(self):
        self.client.force_login(self.encargada)
        respuesta = self.client.post(self.url, {**self.datos, "accion": "buscar"})
        self.assertContains(respuesta, "Crear ruta y empezar")
        self.assertContains(respuesta, 'placeholder="Fileteo"')
        self.assertNotContains(respuesta, "Solicitar nueva ruta")
        self.assertNotContains(respuesta, "Observación de la solicitud")
        respuesta = self.client.post(self.url, {**self.datos, "accion": "crear"})
        ruta = RutaProceso.objects.get(nombre="Fileteo")
        self.assertEqual((ruta.creado_por, ruta.activo, ruta.especie), (self.encargada, True, self.especie))
        hija = PartidaProceso.objects.get(partida_padre=self.partida, ruta_proceso=ruta)
        self.assertRedirects(respuesta, reverse("trazabilidad:gestionar_procesamiento", args=[hija.pk]),
                             fetch_redirect_response=False)
        self.assertTrue(EventoProceso.objects.filter(partida=hija).exists())
        self.assertFalse(SolicitudRuta.objects.exists())

    def test_nombre_propio_repetido_no_deja_nada_a_medias(self):
        self.client.force_login(self.encargada)
        self.client.post(self.url, {**self.datos, "accion": "crear", "nombre_ruta": "Filete simple"})
        self.assertTrue(RutaProceso.objects.filter(nombre="Filete simple", creado_por=self.encargada).exists())
        otra = self.crear_partida()
        url = reverse("trazabilidad:seleccionar_operaciones", args=[otra.pk])
        datos = self.secuencia_post(cantidad_kg="50", **{"etapas-0-tipo_proceso": self.tipos["DESCABEZADO"].pk})
        respuesta = self.client.post(url, {**datos, "accion": "crear", "nombre_ruta": "filete SIMPLE"})
        self.assertContains(respuesta, "Ya hay una ruta llamada")
        self.assertEqual(RutaProceso.objects.filter(creado_por=self.encargada).count(), 1)
        self.assertFalse(PartidaProceso.objects.filter(partida_padre=otra).exists())

    def test_operaria_sigue_solicitando_y_no_puede_crear(self):
        self.client.force_login(self.usuario)
        respuesta = self.client.post(self.url, {**self.datos, "accion": "buscar"})
        self.assertContains(respuesta, "Solicitar nueva ruta")
        self.assertNotContains(respuesta, "Crear ruta y empezar")
        self.assertEqual(self.client.post(self.url, {**self.datos, "accion": "crear", "nombre_ruta": "X"}).status_code, 200)
        self.assertEqual(RutaProceso.objects.count(), 1)
        self.client.post(self.url, {**self.datos, "accion": "solicitar"})
        self.assertEqual(SolicitudRuta.objects.get().solicitante, self.usuario)

    def test_jefe_y_encargada_no_generan_solicitudes(self):
        for usuario in (self.jefe, self.encargada):
            self.client.force_login(usuario)
            self.assertEqual(self.client.post(self.url, {**self.datos, "accion": "solicitar"}).status_code, 200)
        self.assertFalse(SolicitudRuta.objects.exists())

    def test_encargada_ve_y_resuelve_solicitudes_de_operarias(self):
        solicitud = self.propuesta()
        self.client.force_login(self.encargada)
        respuesta = self.client.get(reverse("trazabilidad:solicitudes_ruta"))
        self.assertContains(respuesta, f"Solicitud #{solicitud.pk}")
        self.assertContains(respuesta, reverse("trazabilidad:resolver_solicitud", args=[solicitud.pk]))
        resolver = reverse("trazabilidad:resolver_solicitud", args=[solicitud.pk])
        self.assertEqual(self.client.post(resolver, {"decision": "aprobar", "nombre": "Aprobada por Carolina"}).status_code, 302)
        solicitud.refresh_from_db()
        self.assertEqual((solicitud.estado, solicitud.resuelto_por), ("APROBADA", self.encargada))
        self.assertEqual(solicitud.ruta_aprobada.creado_por, self.encargada)
        self.client.force_login(self.usuario)
        self.assertNotContains(self.client.get(reverse("trazabilidad:solicitudes_ruta")), "Revisar solicitud")

    def test_encargada_gestiona_rutas_y_operaciones(self):
        self.client.force_login(self.encargada)
        respuesta = self.client.post(reverse("trazabilidad:nueva_ruta"), self.secuencia_post(
            nombre="Desde rutas", especie=self.especie.pk, activo="on"))
        self.assertEqual(respuesta.status_code, 302)
        self.assertContains(self.client.get(reverse("trazabilidad:rutas")), "por Carolina")
        operacion = guardar_operacion(usuario=self.encargada, codigo="DESCONCHADO", nombre="Desconchado", activo=True)
        self.assertTrue(TipoProceso.objects.filter(pk=operacion.pk).exists())
        respuesta = self.client.post(reverse("trazabilidad:estado_ruta", args=[self.ruta.pk]), {"accion": "desactivar"})
        self.assertEqual(respuesta.status_code, 302)
        self.ruta.refresh_from_db()
        self.assertFalse(self.ruta.activo)

    def test_nombre_automatico_legible_y_sin_choques(self):
        ids = [self.tipos["DESCABEZADO"].pk, self.tipos["FILETEO"].pk, self.tipos["EMPARRILLADO"].pk]
        self.assertEqual(nombre_para_secuencia(self.especie, ids), "Descabezado, fileteo y emparrillado")
        self.crear_ruta(nombre="Descabezado, fileteo y emparrillado", activo=False)
        self.assertEqual(nombre_para_secuencia(self.especie, ids), "Descabezado, fileteo y emparrillado (2)")
        self.assertEqual(nombre_para_secuencia(self.otra_especie, ids), "Descabezado, fileteo y emparrillado")

    def test_solicitud_resuelta_por_la_encargada_avisa_a_la_operaria(self):
        from apps.reportes.avisos import avisos_para
        solicitud = self.propuesta()
        self.assertIn(f"pidió una ruta para {self.especie.nombre}",
                      " ".join(a["titulo"] for a in avisos_para(self.encargada)["avisos"]))
        resolver_solicitud(usuario=self.encargada, solicitud=solicitud, aprobar=False, respuesta="Usa la ruta habitual.")
        titulos = [a["titulo"] for a in avisos_para(self.usuario)["avisos"]]
        self.assertEqual(titulos, [f"Rechazaron tu ruta para {self.especie.nombre}"])
        with self.assertRaises(ValidationError):
            resolver_solicitud(usuario=self.jefe, solicitud=solicitud, aprobar=True, nombre="Tarde")
