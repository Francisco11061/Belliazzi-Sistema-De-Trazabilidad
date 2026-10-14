from datetime import date
from io import BytesIO
from openpyxl import load_workbook
from django.test import TestCase, override_settings
from django.urls import reverse
from .excel_sernapesca import construir_csv_sernapesca
from .test_dashboard import DatosDashboard


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class OperacionesReporteTests(DatosDashboard, TestCase):
    def test_revision_y_excel_solo_operacion_seleccionada(self):
        self.client.force_login(self.jefe)
        self.partida()
        for codigo, nombre in (("abastecimiento", "Abastecimiento"), ("produccion", "Producción"), ("destino", "Destino")):
            with self.subTest(operacion=codigo):
                params = dict(operacion=codigo, fecha_inicio="2026-09-01", fecha_fin="2026-09-30")
                respuesta = self.client.get(reverse("reportes:sernapesca"), params)
                self.assertEqual(set(respuesta.context["resumen"]), {codigo})
                self.assertContains(respuesta, 'formato=csv')
                descarga = self.client.get(reverse("reportes:descargar_sernapesca"), params)
                self.assertEqual(descarga.status_code, 200)
                libro = load_workbook(BytesIO(descarga.content))
                self.assertEqual(libro.sheetnames, ["Resumen", nombre])
                if codigo == "abastecimiento":
                    self.assertEqual(libro[nombre].max_row, 2)
                    self.assertContains(respuesta, '>fila</span>')

    def test_csv_mismas_columnas_fecha_chilena_y_texto_seguro(self):
        contenido = construir_csv_sernapesca({"Producción": [dict(lote="=1+1", fecha=date(2026,9,30))]})
        self.assertTrue(contenido.startswith(b'\xef\xbb\xbf'))
        self.assertIn("'=1+1", contenido.decode('utf-8-sig'))
        self.assertIn("2026-09-30", contenido.decode('utf-8-sig'))
        self.client.force_login(self.jefe)
        url = reverse("reportes:descargar_sernapesca")
        respuesta = self.client.get(url, dict(operacion="produccion", formato="csv"))
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn('text/csv', respuesta.headers['Content-Type'])
        self.assertEqual(self.client.get(url, dict(formato="csv")).status_code, 400)
        self.assertEqual(self.client.get(url, dict(operacion="desconocida")).status_code, 400)

    def test_exportaciones_solo_jefe(self):
        for usuario in (self.encargada, self.operaria):
            self.client.force_login(usuario)
            for formato in ('csv', 'xlsx'):
                self.assertEqual(self.client.get(reverse("reportes:descargar_sernapesca"),
                    dict(operacion="destino", formato=formato)).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("reportes:descargar_sernapesca"), dict(operacion="destino")).status_code,302)
