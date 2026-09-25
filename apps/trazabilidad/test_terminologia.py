import re

from django.test import TestCase
from django.urls import reverse
from django.utils.html import strip_tags

from .test_procesamiento import DatosProcesamiento


class TerminologiaSeguimientoTests(DatosProcesamiento, TestCase):
    def setUp(self):
        self.preparar()
        self.client.force_login(self.usuario)

    def pagina(self, nombre):
        args = [] if nombre == "lista_partidas" else [self.partida.pk]
        return self.client.get(reverse(f"trazabilidad:{nombre}", args=args))

    def titulo(self, respuesta):
        return strip_tags(re.search(r"<h1\b[^>]*>(.*?)</h1>", respuesta.content.decode(), re.S).group(1)).strip()

    def test_sidebar_conserva_enlace_y_muestra_seguimiento(self):
        respuesta = self.pagina("lista_partidas")
        nav = re.search(r'<nav[^>]*aria-label="Navegación principal".*?</nav>', respuesta.content.decode(), re.S).group(0)
        self.assertIn("Seguimiento de proceso", strip_tags(nav))
        self.assertIn(reverse("trazabilidad:lista_partidas"), nav)
        self.assertNotIn("Partidas", strip_tags(nav))

    def test_listado_y_filtro_utilizan_nueva_terminologia(self):
        respuesta = self.pagina("lista_partidas")
        self.assertEqual(self.titulo(respuesta), "Seguimiento de proceso")
        self.assertContains(respuesta, "ID de seguimiento")
        self.assertContains(respuesta, "Ver seguimiento")
        self.assertNotContains(respuesta, "Partidas")
        self.assertNotContains(respuesta, "ID de partida")

    def test_detalle_prioriza_especie_y_mantiene_identificadores(self):
        respuesta = self.pagina("detalle_partida")
        self.assertEqual(self.titulo(respuesta), self.especie.nombre)
        self.assertContains(respuesta, f"Seguimiento #{self.partida.pk}")
        self.assertContains(respuesta, f"Recepción #{self.partida.detalle_recepcion.recepcion_id}")
        self.assertContains(respuesta, "Origen del seguimiento")
        self.assertContains(respuesta, "Registro inicial de la especie.")
        self.assertNotContains(respuesta, "Partida #")

    def test_formulario_camara_muestra_especie_y_seguimiento(self):
        respuesta = self.pagina("enviar_partida_mantencion")
        self.assertContains(respuesta, self.especie.nombre)
        self.assertContains(respuesta, f"Seguimiento #{self.partida.pk}")
        self.assertNotContains(respuesta, "Partida #")
        self.assertContains(respuesta, "Volver al seguimiento")

    def test_procesamiento_prioriza_especie_y_mantiene_seguimiento(self):
        self.iniciar()
        respuesta = self.pagina("gestionar_procesamiento")
        self.assertEqual(self.titulo(respuesta), f"Procesamiento de {self.especie.nombre}")
        self.assertContains(respuesta, f"Seguimiento #{self.partida.pk}")
        self.assertContains(respuesta, "Ruta de procesamiento")
        self.assertNotContains(respuesta, "Procesamiento de partida")

    def test_tunel_muestra_especie_y_seguimiento(self):
        self.iniciar()
        self.completar()
        respuesta = self.pagina("enviar_partida_tunel")
        self.assertContains(respuesta, self.especie.nombre)
        self.assertContains(respuesta, f"Seguimiento #{self.partida.pk}")
        self.assertNotContains(respuesta, "Partida #")

    def test_mensaje_parcial_describe_cantidades_sin_parentesco_tecnico(self):
        respuesta = self.client.post(reverse("trazabilidad:procesar_partida", args=[self.partida.pk]),
                                     {"cantidad_kg": "300", "ruta": self.ruta.pk}, follow=True)
        self.assertContains(respuesta, "Se enviaron 300,00 kg a procesamiento y quedaron 200,00 kg disponibles.")
        self.assertNotContains(respuesta, "partida hija")
