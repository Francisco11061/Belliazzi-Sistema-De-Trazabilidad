from django.template.loader import render_to_string
from django.test import SimpleTestCase


class BadgeTests(SimpleTestCase):
    def test_mapa_compartido_y_texto_accesible(self):
        grupos = {
            'green': ['DISPONIBLE', 'LISTA_PARA_EMPAQUE', 'ALMACENADA', 'COMPLETADA'],
            'cyan': ['EN_MANTENCION', 'EN_PROCESO', 'EN_CONGELACION', 'EN CURSO'],
            'amber': ['PENDIENTE_PESAJE', 'PENDIENTE', 'REVISION', 'SIN_PESO', 'FALTAN_PESOS'],
            'red': ['NO_OPERABLE', 'BAJA'],
            'slate': ['DIVIDIDA', 'CONSUMIDA', 'CERRADA', 'DESPACHADA', 'DESCONOCIDO'],
        }
        for color, estados in grupos.items():
            for estado in estados:
                with self.subTest(estado=estado):
                    html = render_to_string('components/_badge.html', {'estado': estado, 'etiqueta': 'Etiqueta visible'})
                    self.assertIn(f'bg-{color}-50', html)
                    self.assertIn(f'text-{color}-700', html)
                    self.assertIn(f'border-{color}-200', html)
                    if estado not in ('SIN_PESO', 'FALTAN_PESOS'):
                        self.assertIn('Etiqueta visible', html)
                    self.assertEqual('<svg' in html, color in ('amber', 'red'))

    def test_pendiente_conserva_el_contexto(self):
        for etiqueta in ('Pendiente de almacenar', 'Pendiente'):
            self.assertIn(etiqueta, render_to_string('components/_badge.html', {'estado': 'PENDIENTE', 'etiqueta': etiqueta}))
