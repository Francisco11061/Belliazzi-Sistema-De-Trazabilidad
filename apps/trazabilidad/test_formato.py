"""Formato de números y tiempos que ve el usuario."""
from datetime import timedelta
from decimal import Decimal

from django.template import Context, Template
from django.test import SimpleTestCase

from .formato import formatear_duracion, formatear_horas, formatear_kg, formatear_numero, formatear_valor_correccion


class FormatoNumerosTests(SimpleTestCase):
    def test_sin_ceros_sobrantes_y_formato_chileno(self):
        casos = {"15.00": "15", "15.50": "15,5", "15.25": "15,25", "0.05": "0,05", "0.00": "0",
                 "1234.50": "1.234,5", "-3.00": "-3", "12.345": "12,35"}
        for valor, esperado in casos.items():
            with self.subTest(valor=valor):
                self.assertEqual(formatear_numero(Decimal(valor)), esperado)

    def test_vacios_y_texto(self):
        self.assertEqual(formatear_numero(None), "")
        self.assertEqual(formatear_kg(None), "")
        self.assertEqual(formatear_kg(Decimal("8.30")), "8,3 kg")

    def test_filtro_disponible_en_todas_las_plantillas(self):
        texto = Template("{{ a|num }} kg · {{ b|num:1 }} %").render(Context({"a": Decimal("101.00"), "b": Decimal("12.0")}))
        self.assertEqual(texto, "101 kg · 12 %")


class FormatoTiemposTests(SimpleTestCase):
    def test_duraciones_largas_en_dias(self):
        casos = {30: "menos de 1 min", 45 * 60: "45 min", 5 * 3600 + 30 * 60: "5 h 30 min",
                 24 * 3600: "1 día", 561 * 3600 + 57 * 60: "23 días 9 h"}
        for segundos, esperado in casos.items():
            with self.subTest(segundos=segundos):
                self.assertEqual(formatear_duracion(timedelta(seconds=segundos)), esperado)

    def test_umbral_en_horas_o_dias(self):
        casos = {"48.00": "2 días", "20.00": "20 h", "1.50": "1 h 30 min", "36": "1 día 12 h"}
        for horas, esperado in casos.items():
            with self.subTest(horas=horas):
                self.assertEqual(formatear_horas(Decimal(horas)), esperado)

    def test_valores_de_correccion_legibles(self):
        self.assertEqual(formatear_valor_correccion("peso_recepcion_kg", "74.80"), "74,8 kg")
        self.assertEqual(formatear_valor_correccion("fecha_desembarque", "2026-11-25"), "25/11/2026")
        self.assertEqual(formatear_valor_correccion("folio_origen", "1402587"), "1402587")
