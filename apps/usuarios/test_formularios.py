from datetime import date
from decimal import Decimal

from django import forms
from django.forms import formset_factory
from django.template.loader import render_to_string
from django.test import SimpleTestCase
from django.utils.translation import override

from apps.inventario.forms import DespachoForm
from apps.trazabilidad.forms import RecepcionForm, PesoPostprocesoForm
from .templatetags.formularios import resumen_errores


class FeedbackFormulariosTests(SimpleTestCase):
    def test_resumen_enlaza_campos_y_no_repite_mensajes(self):
        with override('es-cl'):
            form = RecepcionForm({'fecha_hora_recepcion': '', 'observaciones': 'Dato conservado'})
            context = resumen_errores({'form': form})
            html = render_to_string('components/_form_errors.html', context)
            self.assertIn('href="#id_fecha_hora_recepcion"', html)
            self.assertIn('role="alert"', html)
            self.assertNotIn('This field', str(form.errors))
            self.assertEqual(form['observaciones'].value(), 'Dato conservado')

    def test_formulario_inicial_no_muestra_alerta(self):
        self.assertEqual(resumen_errores({'form': RecepcionForm()}), {'enlaces': [], 'generales': False})

    def test_filas_conservadas_e_identificadas_sin_incluir_eliminadas(self):
        class Fila(forms.Form):
            cantidad = forms.IntegerField(label='Cantidad')
        factory = formset_factory(Fila, can_delete=True)
        data = {'composiciones-TOTAL_FORMS': '2', 'composiciones-INITIAL_FORMS': '0',
                'composiciones-0-cantidad': 'incorrecta', 'composiciones-1-cantidad': '',
                'composiciones-1-DELETE': 'on'}
        formset = factory(data, prefix='composiciones')
        context = resumen_errores({'composiciones': formset})
        self.assertEqual(context['enlaces'], [{'id': 'id_composiciones-0-cantidad', 'nombre': 'Presentación 1: Cantidad'}])
        self.assertEqual(formset.forms[0]['cantidad'].value(), 'incorrecta')
        self.assertEqual(len(formset.forms), 2)

    def test_error_general_tambien_muestra_resumen(self):
        self.assertTrue(resumen_errores({'errores': ['La operación ya no está disponible.']})['generales'])

    def test_localizacion_conserva_fechas_iso_y_pesos_decimales(self):
        with override('es-cl'):
            form = RecepcionForm({'fecha_hora_recepcion': '2020-01-15T12:30', 'observaciones': ''})
            self.assertTrue(form.is_valid(), form.errors)
            self.assertIn('value="2020-01-15T12:30"', str(form['fecha_hora_recepcion']))
            widget = DespachoForm.base_fields['fecha_documento'].widget
            self.assertIn('value="2020-01-15"', widget.render('fecha_documento', date(2020, 1, 15)))
            field = next(field for field in PesoPostprocesoForm.base_fields.values() if isinstance(field, forms.DecimalField))
            self.assertEqual(field.clean('24.50'), Decimal('24.50'))
            self.assertIn('value="24.50"', field.widget.render('peso', Decimal('24.50')))
