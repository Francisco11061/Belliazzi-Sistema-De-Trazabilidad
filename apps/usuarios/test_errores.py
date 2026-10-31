from unittest.mock import patch
from django.core.exceptions import PermissionDenied, SuspiciousOperation
from django.http import Http404, HttpResponse
from django.test import TestCase, RequestFactory, override_settings
from django.urls import path
from config import errores

def error_view(request, codigo):
    if codigo==400:raise SuspiciousOperation('detalle interno secreto')
    if codigo==403:raise PermissionDenied('detalle interno secreto')
    if codigo==404:raise Http404('detalle interno secreto')
    if codigo==429:return errores.respuesta_error(429)
    raise RuntimeError('detalle interno secreto')

urlpatterns=[path('error/<int:codigo>/',error_view)]
handler400=errores.bad_request
handler403=errores.permission_denied
handler404=errores.page_not_found
handler500=errores.server_error

@override_settings(DEBUG=False,ROOT_URLCONF=__name__)
class PaginasErrorTests(TestCase):
    def test_codigos_reales_sin_informacion_interna(self):
        self.client.raise_request_exception=False
        for codigo in (400,403,404,429,500):
            with self.subTest(codigo=codigo):
                respuesta=self.client.get(f'/error/{codigo}/')
                self.assertEqual(respuesta.status_code,codigo)
                self.assertContains(respuesta,errores.MENSAJES[codigo][0],status_code=codigo)
                for texto in ('detalle interno secreto','Traceback','SQL','request.user'):
                    self.assertNotContains(respuesta,texto,status_code=codigo)
                self.assertIn('no-store',respuesta['Cache-Control'])

    def test_500_no_usa_sesion_ni_base_de_datos(self):
        request=RequestFactory().get('/error/500/')
        with self.assertNumQueries(0),patch('django.contrib.auth.get_user',side_effect=AssertionError('No user')):
            response=errores.server_error(request)
        self.assertEqual(response.status_code,500)

    def test_debug_conserva_diagnostico_y_excepcion_se_registra(self):
        self.client.raise_request_exception=False
        with self.assertLogs('django.request',level='ERROR') as logs:
            self.client.get('/error/500/')
        self.assertTrue(any('Internal Server Error' in line for line in logs.output))
        with override_settings(DEBUG=True):
            self.assertContains(self.client.get('/error/500/'),'detalle interno secreto',status_code=500)

    def test_csrf_amigable(self):
        response=errores.csrf_failure(RequestFactory().post('/'),reason='detalle interno secreto')
        self.assertEqual(response.status_code,403)
        self.assertContains(response,'Vuelve a cargar el formulario',status_code=403)
        self.assertNotContains(response,'detalle interno secreto',status_code=403)
