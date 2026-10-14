from django.contrib.messages.storage.base import Message
from django.core.paginator import Paginator
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase


class ComponentesCompartidosTests(SimpleTestCase):
    def test_paginacion_conserva_busqueda_y_vista_y_no_inventa_preferencia(self):
        pagina = Paginator(range(30), 10).page(1)
        for consulta in ('?q=Congrio&vista=tabla', '?q=Congrio'):
            request = RequestFactory().get('/cajas/' + consulta)
            html = render_to_string('components/_paginacion.html', {'page': pagina}, request=request)
            self.assertIn('q=Congrio', html)
            self.assertIn('page=2', html)
            self.assertIn('aria-disabled="true"', html)
            self.assertEqual('vista=' in html, 'vista=' in consulta)

    def test_solo_exito_se_oculta_y_alertas_conservan_semantica(self):
        mensajes = [Message(25, 'Guardado'), Message(30, 'Revisa'), Message(40, 'Error')]
        html = render_to_string('components/_messages.html', {'messages': mensajes})
        self.assertEqual(html.count('data-auto-dismiss'), 1)
        self.assertEqual(html.count('role="alert"'), 2)
        self.assertEqual(html.count('role="status"'), 1)
        self.assertEqual(html.count('aria-label="Cerrar mensaje"'), 3)
