from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.usuarios.models import Rol, Usuario

from .models import EventoProceso, PartidaProceso, RutaProceso, TipoProceso
from .services_rutas import guardar_operacion, nombre_para_secuencia
from .test_gestion_rutas import DatosGestionRutas


class RutasEncargadaTests(DatosGestionRutas, TestCase):
    def setUp(self):
        self.preparar()
        self.encargada = Usuario.objects.create_user(username="encargada_rutas", first_name="Carolina",
                                                     rol=Rol.objects.get(codigo="ENCARGADA"))
        self.url = reverse("trazabilidad:seleccionar_operaciones", args=[self.partida.pk])
        # Fileteo solo no calza con la ruta de prueba (Descabezado, Fileteo, Emparrillado).
        self.datos = self.secuencia_post(cantidad_kg="100")

    def test_encargada_crea_la_ruta_y_empieza(self):
        self.client.force_login(self.encargada)
        respuesta = self.client.post(self.url, {**self.datos, "accion": "buscar"})
        self.assertContains(respuesta, "Crear ruta y empezar")
        self.assertContains(respuesta, 'placeholder="Fileteo"')
        self.assertNotContains(respuesta, "Avísale a la encargada")
        self.assertNotContains(respuesta, "Ruta autorizada coincidente")
        respuesta = self.client.post(self.url, {**self.datos, "accion": "crear"})
        ruta = RutaProceso.objects.get(nombre="Fileteo")
        self.assertEqual((ruta.creado_por, ruta.activo, ruta.especie), (self.encargada, True, self.especie))
        hija = PartidaProceso.objects.get(partida_padre=self.partida, ruta_proceso=ruta)
        self.assertRedirects(respuesta, reverse("trazabilidad:gestionar_procesamiento", args=[hija.pk]),
                             fetch_redirect_response=False)
        self.assertTrue(EventoProceso.objects.filter(partida=hija).exists())

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
