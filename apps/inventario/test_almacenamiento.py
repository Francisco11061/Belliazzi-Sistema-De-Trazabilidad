from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch

from django.contrib import admin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connection, connections
from django.test import RequestFactory, TestCase, TransactionTestCase, skipUnlessDBFeature
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.trazabilidad.models import Caja, LoteProduccion, UnidadFrio
from apps.trazabilidad.test_cajas import DatosCajas
from apps.usuarios.models import Rol, Usuario
from .forms import DestinoForm
from .models import BajaCaja, Despacho, DetalleDespacho, EstanciaCaja
from .selectors import cajas_con_inventario, resumen_inventario
from .services import ingresar_caja, mover_caja


class DatosInventario(DatosCajas):
    def preparar_inventario(self):
        self.preparar_cajas()
        self.registro = self.caja(peso_neto_kg=Decimal('24.50'))
        self.camara1 = UnidadFrio.objects.create(nombre='Almacenamiento A', tipo='ALMACENAMIENTO')
        self.camara2 = UnidadFrio.objects.create(nombre='Almacenamiento B', tipo='ALMACENAMIENTO')

    def ingresar(self, caja=None, unidad=None, usuario=None):
        return ingresar_caja(caja=caja or self.registro, unidad=unidad or self.camara1, usuario=usuario or self.usuario)

    def mover(self, caja=None, unidad=None, usuario=None):
        return mover_caja(caja=caja or self.registro, unidad=unidad or self.camara2, usuario=usuario or self.usuario)

    def estado(self, caja=None):
        return cajas_con_inventario().get(pk=(caja or self.registro).pk)

    def stock(self):
        return resumen_inventario(cajas_con_inventario())

    def url(self, nombre, caja=None):
        return reverse('inventario:'+nombre, args=[(caja or self.registro).pk] if nombre != 'lista' else [])


class InventarioTests(DatosInventario, TestCase):
    def setUp(self):
        self.preparar_inventario()
        self.client.force_login(self.usuario)

    def test_nueva_pendiente_sin_stock_ni_estancias_ficticias(self):
        self.assertEqual(self.estado().estado_inventario, 'PENDIENTE')
        self.assertEqual(self.estado().ubicacion_actual, '—')
        self.assertFalse(EstanciaCaja.objects.exists())
        self.assertEqual(self.stock()['stock']['cajas'], 0)
        self.assertEqual(self.stock()['pendientes'], 1)

    def test_ingreso_aware_usuario_y_ubicacion(self):
        ahora = timezone.now()
        with patch('apps.inventario.services.timezone.now', return_value=ahora):
            estancia = self.ingresar()
        self.assertEqual(estancia.fecha_hora_ingreso, ahora)
        self.assertTrue(timezone.is_aware(estancia.fecha_hora_ingreso))
        self.assertEqual(estancia.ingresado_por, self.usuario)
        self.assertIsNone(estancia.fecha_hora_salida)
        self.assertEqual(self.estado().estado_inventario, 'ALMACENADA')
        self.assertEqual(self.estado().ubicacion_actual, self.camara1.nombre)

    def test_destinos_rechazados_service_y_form(self):
        for tipo, activo in [('MANTENCION', True), ('TUNEL_CONGELADO', True), ('ALMACENAMIENTO', False)]:
            unidad = UnidadFrio.objects.create(nombre=tipo+str(activo), tipo=tipo, activo=activo)
            with self.subTest(tipo=tipo), self.assertRaisesRegex(ValidationError, 'no está disponible'):
                self.ingresar(unidad=unidad)
            self.assertFalse(DestinoForm({'unidad': unidad.pk}).is_valid())
        self.assertCountEqual(DestinoForm().fields['unidad'].queryset, [self.camara1,self.camara2])
        self.assertEqual(EstanciaCaja.objects.count(), 0)

    def test_destino_se_revalida_aunque_objeto_este_desactualizado(self):
        UnidadFrio.objects.filter(pk=self.camara1.pk).update(activo=False)
        with self.assertRaises(ValidationError):
            self.ingresar()

    def test_no_doble_ingreso(self):
        self.ingresar()
        with self.assertRaisesRegex(ValidationError, 'ya se encuentra almacenada'):
            self.ingresar(unidad=self.camara2)
        self.assertEqual(EstanciaCaja.objects.filter(fecha_hora_salida=None).count(), 1)

    def test_movimiento_cierra_abre_conserva_historial_y_stock(self):
        anterior = self.ingresar()
        antes = self.stock()['stock']
        nueva = self.mover(usuario=self.jefe)
        anterior.refresh_from_db()
        self.assertEqual(anterior.fecha_hora_salida, nueva.fecha_hora_ingreso)
        self.assertEqual(anterior.retirado_por, self.jefe)
        self.assertEqual(anterior.ingresado_por, self.usuario)
        self.assertEqual(nueva.ingresado_por, self.jefe)
        self.assertEqual(EstanciaCaja.objects.count(), 2)
        self.assertEqual(EstanciaCaja.objects.filter(fecha_hora_salida=None).count(), 1)
        self.assertEqual(self.estado().ubicacion_actual, self.camara2.nombre)
        self.assertEqual(self.stock()['stock'], antes)
        self.assertEqual(list(self.stock()['por_camara'])[0]['unidad_abierta_id'], self.camara2.pk)

    def test_movimiento_misma_camara_rechazado(self):
        self.ingresar()
        with self.assertRaisesRegex(ValidationError, 'ya se encuentra en esa cámara'):
            self.mover(unidad=self.camara1)
        self.assertEqual(EstanciaCaja.objects.count(), 1)

    def test_pendiente_no_puede_moverse(self):
        with self.assertRaisesRegex(ValidationError, 'no posee una ubicación'):
            self.mover()
        self.assertFalse(EstanciaCaja.objects.exists())

    def test_movimiento_destinos_invalidos_no_cierra_anterior(self):
        anterior = self.ingresar()
        for tipo, activo in [('MANTENCION', True), ('TUNEL_CONGELADO', True), ('ALMACENAMIENTO', False)]:
            unidad = UnidadFrio.objects.create(nombre=tipo+str(activo), tipo=tipo, activo=activo)
            with self.assertRaises(ValidationError):
                self.mover(unidad=unidad)
        anterior.refresh_from_db()
        self.assertIsNone(anterior.fecha_hora_salida)
        self.assertEqual(EstanciaCaja.objects.count(), 1)

    def test_fallo_al_abrir_nueva_revierte_cierre(self):
        anterior = self.ingresar()
        with patch('apps.inventario.services.EstanciaCaja.objects.create', side_effect=ValidationError('Fallo simulado')):
            with self.assertRaises(ValidationError):
                self.mover()
        anterior.refresh_from_db()
        self.assertIsNone(anterior.fecha_hora_salida)
        self.assertIsNone(anterior.retirado_por)

    def test_post_manipulado_no_acepta_destinos_invalidos_o_caja_almacenada(self):
        unidad = UnidadFrio.objects.create(nombre='Otro túnel', tipo='TUNEL_CONGELADO')
        for pk in (unidad.pk, 999999):
            response = self.client.post(self.url('ingresar'), {'unidad':pk})
            self.assertEqual(response.status_code, 200)
            self.assertIn('unidad', response.context['form'].errors)
        self.assertEqual(self.client.post(self.url('ingresar'), {'unidad':self.camara1.pk}).status_code, 302)
        self.assertContains(self.client.post(self.url('ingresar'), {'unidad':self.camara2.pk}), 'ya se encuentra almacenada')
        self.assertContains(self.client.post(self.url('mover'), {'unidad':self.camara1.pk}), 'ya se encuentra en esa cámara')
        self.assertEqual(EstanciaCaja.objects.count(), 1)

    def test_get_no_muta_y_fechas_post_no_se_usan(self):
        self.client.get(self.url('ingresar'))
        self.assertEqual(EstanciaCaja.objects.count(), 0)
        self.client.post(self.url('ingresar'), {'unidad':self.camara1.pk,'fecha_hora_ingreso':'2000-01-01','ingresado_por':self.jefe.pk})
        estancia = EstanciaCaja.objects.get()
        self.assertEqual(estancia.ingresado_por, self.usuario)
        self.assertGreater(estancia.fecha_hora_ingreso.year, 2000)

    def test_stock_real_decimal_por_especie_lote_y_camara(self):
        segunda = self.caja(peso_neto_kg=Decimal('24.82'))
        otra_lote = LoteProduccion.objects.create(codigo_lote='HIST-CONGRIO', especie=self.otra_especie, fecha_elaboracion=timezone.localdate(), registrado_por=self.usuario)
        tercera = Caja.objects.create(lote_produccion=otra_lote, peso_neto_kg=Decimal('18.30'), peso_total_kg=Decimal('999'), fecha_armado=timezone.now(), registrado_por=self.usuario)
        self.caja(peso_neto_kg=Decimal('20'))  # Pendiente: nunca se suma.
        self.ingresar()
        self.ingresar(caja=segunda)
        self.ingresar(caja=tercera, unidad=self.camara2)
        resumen = self.stock()
        self.assertEqual(resumen['stock'], {'cajas':3,'peso_conocido':Decimal('67.62'),'sin_peso':0})
        especie = {f['lote_produccion__especie_id']:f for f in resumen['por_especie']}
        self.assertEqual(especie[self.especie.pk]['peso_conocido'], Decimal('49.32'))
        self.assertEqual(especie[self.otra_especie.pk]['peso_conocido'], Decimal('18.30'))
        lotes = {f['lote_produccion_id']:f for f in resumen['por_lote']}
        self.assertEqual(lotes[self.lote.pk]['peso_conocido'], Decimal('49.32'))
        camaras = {f['unidad_abierta_id']:f for f in resumen['por_camara']}
        self.assertEqual(camaras[self.camara1.pk]['peso_conocido'], Decimal('49.32'))
        self.assertEqual(camaras[self.camara2.pk]['peso_conocido'], Decimal('18.30'))
        self.assertIsInstance(resumen['stock']['peso_conocido'], Decimal)

    def test_historica_sin_peso_cuenta_caja_no_nominal_y_no_altera_uuid(self):
        Caja.objects.filter(pk=self.registro.pk).update(peso_neto_kg=None, peso_total_kg=Decimal('500'))
        antes = Caja.objects.values().get(pk=self.registro.pk)
        self.ingresar()
        self.mover()
        self.assertEqual(self.stock()['stock'], {'cajas':1,'peso_conocido':Decimal('0'),'sin_peso':1})
        self.assertEqual(Caja.objects.values().get(pk=self.registro.pk), antes)
        self.assertContains(self.client.get(self.url('lista')), 'Peso neto no documentado')

    def test_camara_inactiva_conserva_stock_historico_permite_salida(self):
        self.ingresar()
        UnidadFrio.objects.filter(pk=self.camara1.pk).update(activo=False)
        self.assertEqual(self.stock()['stock']['cajas'], 1)
        self.mover()
        self.assertEqual(self.estado().ubicacion_actual, self.camara2.nombre)

    def test_salida_cerrada_sin_otra_estancia_queda_pendiente(self):
        estancia = self.ingresar()
        estancia.fecha_hora_salida = timezone.now()
        estancia.save()
        self.assertEqual(self.estado().estado_inventario, 'PENDIENTE')
        self.assertEqual(self.stock()['stock']['cajas'], 0)

    def test_historicas_despachadas_y_baja_excluidas_y_no_operables(self):
        self.ingresar()
        despacho = Despacho.objects.create(fecha_hora_despacho=timezone.now(), registrado_por=self.usuario)
        DetalleDespacho.objects.create(despacho=despacho, caja=self.registro)
        self.assertEqual(self.estado().estado_inventario, 'DESPACHADA')
        self.assertEqual(self.stock()['stock']['cajas'], 0)
        with self.assertRaises(ValidationError):
            self.mover()
        otra = self.caja()
        BajaCaja.objects.create(caja=otra, motivo='Histórico', fecha_hora_evento=timezone.now(), registrado_por=self.usuario)
        self.assertEqual(self.estado(otra).estado_inventario, 'BAJA')
        with self.assertRaises(ValidationError):
            self.ingresar(caja=otra)
        self.assertEqual(self.stock()['pendientes'], 0)

    def test_historico_ambiguo_no_se_reescribe_ni_duplica_stock(self):
        self.ingresar()
        EstanciaCaja.objects.create(caja=self.registro, unidad_frio=self.camara2, fecha_hora_ingreso=timezone.now(), ingresado_por=self.usuario)
        antes = list(EstanciaCaja.objects.values())
        self.assertEqual(self.estado().estado_inventario, 'REVISION')
        self.assertEqual(self.stock()['stock']['cajas'], 0)
        self.assertEqual(self.stock()['revision'], 1)
        with self.assertRaisesRegex(ValidationError, 'requiere revisión'):
            self.mover()
        self.assertEqual(list(EstanciaCaja.objects.values()), antes)

    def test_historico_en_tunel_no_se_inventa_almacenamiento(self):
        EstanciaCaja.objects.create(caja=self.registro, unidad_frio=self.tunel, fecha_hora_ingreso=timezone.now(), ingresado_por=self.usuario)
        self.assertEqual(self.estado().estado_inventario, 'REVISION')
        self.assertEqual(self.stock()['stock']['cajas'], 0)
        with self.assertRaises(ValidationError):
            self.ingresar()

    def test_roles_operativos_ingreso_y_movimiento(self):
        rol, _ = Rol.objects.get_or_create(codigo='ENCARGADA', defaults={'nombre':'Encargada'})
        encargada = Usuario.objects.create_user(username='encargada_inventario', rol=rol)
        for usuario in (self.jefe, encargada, self.usuario):
            caja = self.caja()
            self.client.force_login(usuario)
            self.assertEqual(self.client.post(self.url('ingresar', caja), {'unidad':self.camara1.pk}).status_code, 302)
            self.assertEqual(self.client.post(self.url('mover', caja), {'unidad':self.camara2.pk}).status_code, 302)

    def test_superuser_bypass(self):
        tecnico = Usuario.objects.create_superuser(username='tecnico', password='clave', email='tecnico@example.com')
        self.ingresar(usuario=tecnico)
        self.client.force_login(tecnico)
        self.assertEqual(self.client.post(self.url('mover'), {'unidad':self.camara2.pk}).status_code, 302)

    def test_sin_rol_y_anonimo_rechazados(self):
        rol = Rol.objects.create(codigo='CONSULTA', nombre='Consulta')
        usuario = Usuario.objects.create_user(username='sinrol', password='clave', rol=rol)
        with self.assertRaises(PermissionDenied):
            self.ingresar(usuario=usuario)
        self.client.force_login(usuario)
        for nombre in ('lista','ingresar','mover'):
            self.assertEqual(self.client.get(self.url(nombre)).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(self.url('lista')).status_code, 302)

    def test_detalle_y_qr_estado_historial_trazabilidad(self):
        detalle = self.url_lote('detalle_caja', self.registro.pk)
        qr = reverse('producto_terminado:consulta_caja_qr', args=[self.registro.identificador_qr])
        self.assertContains(self.client.get(detalle), 'Pendiente de almacenar')
        self.ingresar()
        self.mover()
        for url in (detalle, qr):
            respuesta = self.client.get(url)
            for texto in ('Almacenada', self.camara1.nombre, self.camara2.nombre, 'Historial de almacenamiento', self.lote.codigo_lote):
                self.assertContains(respuesta, texto)
        self.assertContains(self.client.get(qr), f'Seguimiento #{self.partida.pk}')
        self.assertEqual(self.client.get(self.url_lote('caja_qr_png', self.registro.pk))['Content-Type'], 'image/png')

    def test_filtros_especie_lote_camara_estado(self):
        self.ingresar()
        self.caja()  # Pendiente, misma especie/lote.
        for filtro in ({'especie':self.especie.pk}, {'lote':self.lote.pk}, {'camara':self.camara1.pk}, {'estado':'ALMACENADA'}):
            respuesta = self.client.get(self.url('lista'), filtro)
            self.assertIn(self.registro, respuesta.context['page_obj'])
        for filtro in ({'especie':self.otra_especie.pk}, {'camara':self.camara2.pk}, {'estado':'PENDIENTE'}):
            self.assertNotIn(self.registro, self.client.get(self.url('lista'), filtro).context['page_obj'])
        self.assertEqual(self.client.get(self.url('lista'), {'estado':'PENDIENTE'}).context['stock']['cajas'], 0)
        self.assertEqual(self.client.get(self.url('lista'), {'lote':999999}).context['page_obj'].paginator.count, 0)

    def test_paginacion_conserva_filtros(self):
        Caja.objects.bulk_create([Caja(lote_produccion=self.lote, fecha_armado=timezone.now(), registrado_por=self.usuario) for _ in range(12)])
        respuesta = self.client.get(self.url('lista'), {'estado':'PENDIENTE','page':2})
        self.assertEqual(respuesta.context['page_obj'].number, 2)
        self.assertIn('estado=PENDIENTE', respuesta.context['pagina_anterior'])
        self.assertEqual(respuesta.context['pendientes'], 13)

    def test_listado_sin_n_mas_uno(self):
        self.ingresar()
        with CaptureQueriesContext(connection) as antes:
            self.client.get(self.url('lista'))
        for _ in range(3):
            self.ingresar(caja=self.caja())
        with CaptureQueriesContext(connection) as despues:
            self.client.get(self.url('lista'))
        self.assertEqual(len(antes), len(despues))

    def test_admin_estancias_solo_consulta(self):
        request = RequestFactory().get('/admin/')
        request.user = self.jefe
        registro = admin.site._registry[EstanciaCaja]
        self.assertFalse(registro.has_add_permission(request))
        self.assertFalse(registro.has_change_permission(request))
        self.assertFalse(registro.has_delete_permission(request))


class ConcurrenciaInventarioTests(DatosInventario, TransactionTestCase):
    def setUp(self):
        self.preparar_inventario()

    def competir(self, operacion):
        barrera = Barrier(2)
        def ejecutar(unidad):
            close_old_connections()
            try:
                barrera.wait(timeout=10)
                try:
                    operacion(caja=self.registro, unidad=unidad, usuario=self.usuario)
                    return 'ok'
                except ValidationError:
                    return 'rechazada'
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(ejecutar, [self.camara1,self.camara2]))

    @skipUnlessDBFeature('has_select_for_update')
    def test_dos_ingresos_simultaneos_unica_estancia(self):
        self.assertCountEqual(self.competir(ingresar_caja), ['ok','rechazada'])
        self.assertEqual(EstanciaCaja.objects.count(), 1)

    @skipUnlessDBFeature('has_select_for_update')
    def test_dos_movimientos_simultaneos_unica_ubicacion(self):
        tercera = UnidadFrio.objects.create(nombre='Almacenamiento C', tipo='ALMACENAMIENTO')
        self.ingresar(unidad=tercera)
        self.assertEqual(self.competir(mover_caja), ['ok','ok'])
        self.assertEqual(EstanciaCaja.objects.count(), 3)
        self.assertEqual(EstanciaCaja.objects.filter(fecha_hora_salida=None).count(), 1)
        historial = list(EstanciaCaja.objects.order_by('fecha_hora_ingreso','pk'))
        self.assertEqual(historial[0].fecha_hora_salida, historial[1].fecha_hora_ingreso)
        self.assertEqual(historial[1].fecha_hora_salida, historial[2].fecha_hora_ingreso)
