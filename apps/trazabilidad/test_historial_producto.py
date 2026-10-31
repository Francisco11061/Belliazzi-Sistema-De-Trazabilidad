from decimal import Decimal
from uuid import uuid4

from django.apps import apps
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.inventario.test_despachos import DatosDespacho
from apps.inventario.services import ingresar_caja, registrar_baja
from apps.usuarios.models import Usuario, Rol
from .consulta_integral import ficha_integral
from .models import Caja, ConsumoLote
from .qr import construir_url_qr_caja


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class HistorialProductoTests(DatosDespacho, TestCase):
    def setUp(self):
        self.preparar_despacho()
        self.encargada = Usuario.objects.create_user(username='historial_enc', rol=Rol.objects.get(codigo='ENCARGADA'))
        self.client.force_login(self.jefe)

    def qr(self, caja=None):
        return reverse('producto_terminado:consulta_caja_qr', args=[(caja or self.registro).identificador_qr])

    def test_uuid_url_y_ficha_compartida_sin_redireccion(self):
        original = self.registro.identificador_qr
        for usuario in (self.jefe, self.encargada):
            self.client.force_login(usuario)
            respuesta = self.client.get(self.qr(), follow=True)
            self.assertEqual(respuesta.redirect_chain, [])
            self.assertTemplateUsed(respuesta, 'trazabilidad/consulta/ficha.html')
            self.assertEqual(respuesta.context['caja'].pk, self.registro.pk)
            self.assertEqual(respuesta.context['raiz'].pk, self.registro.pk)
            self.assertContains(respuesta, 'Historial del producto')
            self.assertIn('no-store', respuesta['Cache-Control'])
        self.registro.refresh_from_db()
        self.assertEqual(self.registro.identificador_qr, original)

    def test_login_next_conserva_caja_para_tres_perfiles(self):
        for usuario in (self.jefe, self.encargada, self.usuario):
            usuario.set_password('Clave-segura-test-42')
            usuario.save(update_fields=['password'])
            self.client.logout()
            anonima = self.client.get(self.qr())
            self.assertEqual(anonima.status_code, 302)
            self.assertIn('next='+self.qr(), anonima.url)
            self.assertNotIn(self.lote.codigo_lote, anonima.content.decode())
            respuesta = self.client.post(anonima.url, {'username':usuario.username, 'password':'Clave-segura-test-42'}, follow=True)
            self.assertEqual(respuesta.status_code, 200)
            self.assertEqual(respuesta.redirect_chain, [(self.qr(), 302)])
            self.assertEqual(respuesta.context['caja'].pk, self.registro.pk)

    def test_operaria_no_amplia_permisos_y_consulta_solo_lectura(self):
        self.client.force_login(self.usuario)
        respuesta = self.client.get(self.qr())
        self.assertTemplateUsed(respuesta, 'trazabilidad/cajas/consulta_qr.html')
        self.assertNotContains(respuesta, 'Cambios registrados')
        self.assertNotContains(respuesta, reverse('trazabilidad:consulta_integral'))
        self.assertNotContains(respuesta, reverse('inventario:mover', args=[self.registro.pk]))
        self.assertNotContains(respuesta, reverse('inventario:crear_baja', args=[self.registro.pk]))
        for tipo, pk in (('caja',self.registro.pk), ('lote',self.lote.pk), ('recepcion',self.partida.detalle_recepcion.recepcion_id)):
            self.assertEqual(self.client.get(reverse('trazabilidad:ficha_integral', args=[tipo,pk])).status_code, 403)
        self.assertEqual(self.client.post(self.qr()).status_code, 405)

    def test_uuid_inexistente_404_todos_roles(self):
        for usuario in (self.jefe, self.encargada, self.usuario):
            self.client.force_login(usuario)
            self.assertEqual(self.client.get(reverse('producto_terminado:consulta_caja_qr', args=[uuid4()])).status_code, 404)

    def test_caja_no_mezcla_movimientos_de_sus_hermanas(self):
        otra = self.caja()
        ingresar_caja(caja=otra, unidad=self.camara1, usuario=self.usuario)
        despacho = self.despachar(cajas=[otra])
        tercera = self.caja()
        registrar_baja(caja=tercera, usuario=self.usuario, tipo='DANO', motivo='Baja exclusiva de otra caja')
        respuesta = self.client.get(self.qr())
        datos = respuesta.context
        self.assertEqual([c.pk for c in datos['cajas']], [self.registro.pk])
        self.assertEqual({e.caja_id for e in datos['almacenamiento']}, {self.registro.pk})
        self.assertEqual(datos['despachos'], [])
        self.assertEqual(datos['bajas'], [])
        self.assertEqual(datos['otras_cajas'], 2)
        self.assertNotIn(despacho.pk, [e['identificador'] for e in datos['timeline'] if e['clase']=='Despacho'])
        self.assertEqual([e['identificador'] for e in datos['timeline'] if e['clase']=='Armado de caja'], [self.registro.pk])
        self.assertContains(respuesta, 'Ver otras cajas del lote')
        self.assertNotContains(respuesta, 'Baja exclusiva de otra caja')
        lote = ficha_integral(self.jefe, 'lote', self.lote.pk)
        self.assertEqual(len(lote['cajas']), 3)
        self.assertEqual(len(lote['despachos']), 1)
        self.assertEqual(len(lote['bajas']), 1)
        # El control de pesos mantiene el alcance del lote, no finge el peso de una caja.
        self.assertEqual(datos['conciliacion'], lote['conciliacion'])

    def test_aviso_solo_con_varios_aportes_y_sin_proporciones(self):
        aviso = 'Esta caja pertenece a un lote elaborado con producto de varios seguimientos.'
        self.assertNotContains(self.client.get(self.qr()), aviso)
        otra = self.otra_lista(peso='40')
        self.agregar(self.lote, partida=otra, cantidad='20', usuario=self.jefe)
        respuesta = self.client.get(self.qr())
        self.assertContains(respuesta, aviso)
        self.assertContains(respuesta, 'No se registró qué cantidad de cada seguimiento')
        self.assertEqual({a.partida_id for a in respuesta.context['aportes']}, {self.partida.pk, otra.pk})
        self.assertNotContains(self.client.get(reverse('trazabilidad:ficha_integral', args=['lote',self.lote.pk])), aviso)

    def test_textos_y_calculos_de_empaque_sin_cambios(self):
        respuesta = self.client.get(self.qr())
        for texto in ('Origen y recepción', 'Historial de procesamiento', 'Control de pesos', 'Historial de movimientos', 'Cambios registrados', 'Producto ingresado al lote', 'Diferencia entre peso asignado y empacado'):
            self.assertContains(respuesta, texto)
        for texto in ('Conciliación documental', 'Disponible para empacar', 'Familias de seguimientos', 'Origen documentado del lote:', 'Las etapas no se suman entre sí.'):
            self.assertNotContains(respuesta, texto)
        lote = respuesta.context['lotes'][0]
        self.assertEqual(lote.disponible_empacar, lote.total_asignado-lote.total_empacado)
        self.assertTrue(any(e['titulo'].startswith('Inicio de ') and 'Seguimiento #' in e['titulo'] for e in respuesta.context['timeline']))

    def test_get_no_modifica_registros_ni_identificadores(self):
        modelos = [m for m in apps.get_models() if m._meta.app_label in ('trazabilidad','inventario')]
        antes = {m._meta.label:list(m.objects.order_by('pk').values()) for m in modelos}
        self.client.get(self.qr())
        despues = {m._meta.label:list(m.objects.order_by('pk').values()) for m in modelos}
        self.assertEqual(antes, despues)

    def test_qr_integral_sin_n_mas_uno_y_faltantes(self):
        with CaptureQueriesContext(connection) as antes:
            self.client.get(self.qr())
        for _ in range(12):
            Caja.objects.create(lote_produccion=self.lote, fecha_armado=timezone.now(), registrado_por=self.usuario)
        with CaptureQueriesContext(connection) as despues:
            respuesta = self.client.get(self.qr())
        self.assertEqual(len(antes), len(despues))
        self.assertIsNone(respuesta.context['conciliacion']['cajas'])
        self.assertIsNone(respuesta.context['lotes'][0].disponible_empacar)
        self.assertContains(respuesta, 'Sin peso documentado')

    def test_despacho_y_baja_de_caja_consultada_si_aparecen(self):
        despacho = self.despachar()
        datos = self.client.get(self.qr()).context
        self.assertEqual([d['registro'].pk for d in datos['despachos']], [despacho.pk])
        otra = self.caja()
        registrar_baja(caja=otra, usuario=self.usuario, tipo='DANO', motivo='Envase roto')
        datos = self.client.get(self.qr(otra)).context
        self.assertEqual([b.caja_id for b in datos['bajas']], [otra.pk])
