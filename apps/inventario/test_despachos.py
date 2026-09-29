from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch

from django.contrib import admin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, close_old_connections, connection, connections, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import RequestFactory, TestCase, TransactionTestCase, skipUnlessDBFeature
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.trazabilidad.models import Caja, LoteProduccion, UnidadFrio
from apps.usuarios.models import Rol, Usuario
from .forms import DespachoForm
from .models import BajaCaja, Despacho, DetalleDespacho, EstanciaCaja
from .selectors import cajas_para_despacho, despachos_con_totales
from .services import ingresar_caja, mover_caja, registrar_despacho
from .test_almacenamiento import DatosInventario


class DatosDespacho(DatosInventario):
    def preparar_despacho(self):
        self.preparar_inventario()
        self.ingresar()

    def datos_despacho(self, **cambios):
        datos = dict(cajas=[self.registro], usuario=self.usuario, tipo_destino='NACIONAL',
            destino='Comercializadora de prueba', rut_destinatario='12.345.678-5',
            tipo_documento='Guía de despacho', numero_documento='GD-001', fecha_documento=timezone.localdate())
        datos.update(cambios)
        return datos

    def despachar(self, **cambios):
        return registrar_despacho(**self.datos_despacho(**cambios))

    def datos_post(self, **cambios):
        datos = self.datos_despacho(cajas=[str(self.registro.pk)])
        datos.pop('usuario')
        datos.update(cambios)
        return datos

    def url_despacho(self, nombre='crear_despacho', pk=None):
        return reverse('inventario:'+nombre, args=[pk] if pk else [])


class DespachoTests(DatosDespacho, TestCase):
    def setUp(self):
        self.preparar_despacho()
        self.client.force_login(self.usuario)

    def test_una_caja_cierre_mismo_instante_usuario_estado_stock(self):
        antes = Caja.objects.values().get(pk=self.registro.pk)
        ahora = timezone.now()
        with patch('apps.inventario.services.timezone.now', return_value=ahora):
            despacho = self.despachar()
        estancia = EstanciaCaja.objects.get(caja=self.registro)
        self.assertEqual(estancia.fecha_hora_salida, ahora)
        self.assertEqual(despacho.fecha_hora_despacho, ahora)
        self.assertTrue(timezone.is_aware(ahora))
        self.assertEqual(estancia.retirado_por, self.usuario)
        self.assertEqual(despacho.registrado_por, self.usuario)
        self.assertEqual(despacho.detalles.get().caja, self.registro)
        self.assertEqual(self.estado().estado_inventario, 'DESPACHADA')
        self.assertEqual(self.stock()['stock']['cajas'], 0)
        self.assertEqual(self.stock()['pendientes'], 0)
        self.assertEqual(Caja.objects.values().get(pk=self.registro.pk), antes)

    def test_varias_cajas_distintos_lotes_especies_camaras(self):
        segunda = self.caja(peso_neto_kg=Decimal('24.82'))
        self.ingresar(caja=segunda, unidad=self.camara2)
        lote = LoteProduccion.objects.create(codigo_lote='HIST-CONGRIO', especie=self.otra_especie, fecha_elaboracion=timezone.localdate(), registrado_por=self.usuario)
        tercera = Caja.objects.create(lote_produccion=lote, peso_neto_kg=Decimal('20.10'), fecha_armado=timezone.now(), registrado_por=self.usuario)
        self.ingresar(caja=tercera)
        despacho = self.despachar(cajas=[self.registro, segunda, tercera])
        total = despachos_con_totales().get(pk=despacho.pk)
        self.assertEqual(total.cantidad_cajas, 3)
        self.assertEqual(total.peso_conocido, Decimal('69.42'))
        self.assertEqual(EstanciaCaja.objects.filter(fecha_hora_salida=None).count(), 0)
        self.assertEqual(set(EstanciaCaja.objects.values_list('fecha_hora_salida',flat=True)), {despacho.fecha_hora_despacho})
        tercera.refresh_from_db()
        self.assertEqual(tercera.lote_produccion_id, lote.pk)

    def test_salida_parcial_lote_con_cajas_completas_y_stock_por_grupo(self):
        segunda = self.caja(peso_neto_kg=Decimal('24.82'))
        tercera = self.caja(peso_neto_kg=Decimal('24.60'))
        self.ingresar(caja=segunda, unidad=self.camara2)
        self.ingresar(caja=tercera, unidad=self.camara2)
        despacho = self.despachar(cajas=[self.registro,segunda])
        self.assertEqual(despachos_con_totales().get(pk=despacho.pk).peso_conocido, Decimal('49.32'))
        resumen = self.stock()
        for key in ('por_especie','por_lote','por_camara'):
            filas = list(resumen[key])
            self.assertEqual(len(filas), 1)
            self.assertEqual(filas[0]['peso_conocido'], Decimal('24.60'))
            self.assertEqual(filas[0]['cajas'], 1)
        self.assertEqual(self.estado(tercera).estado_inventario, 'ALMACENADA')

    def test_total_exclusivamente_real_decimal(self):
        Caja.objects.filter(pk=self.registro.pk).update(peso_total_kg=Decimal('999'))
        despacho = self.despachar()
        total = despachos_con_totales().get(pk=despacho.pk).peso_conocido
        self.assertEqual(total, Decimal('24.50'))
        self.assertIsInstance(total, Decimal)

    def test_pendiente_rechazada_service_form_post(self):
        otra = self.caja()
        with self.assertRaisesRegex(ValidationError, 'no están disponibles'):
            self.despachar(cajas=[otra])
        form = DespachoForm(self.datos_post(cajas=[otra.pk]))
        self.assertFalse(form.is_valid())
        self.assertIn('cajas', form.errors)
        self.assertEqual(self.client.post(self.url_despacho(), self.datos_post(cajas=[otra.pk])).status_code, 200)
        self.assertFalse(Despacho.objects.exists())

    def test_doble_despacho_rechazado_y_unique_estructural(self):
        primero = self.despachar()
        with self.assertRaisesRegex(ValidationError, 'no están disponibles'):
            self.despachar()
        with self.assertRaises(IntegrityError), transaction.atomic():
            DetalleDespacho.objects.create(despacho=primero, caja=self.registro)
        self.assertEqual(Despacho.objects.count(), 1)
        self.assertEqual(DetalleDespacho.objects.count(), 1)

    def test_baja_historica_rechazada(self):
        BajaCaja.objects.create(caja=self.registro,motivo='Histórica',fecha_hora_evento=timezone.now(),registrado_por=self.usuario)
        with self.assertRaises(ValidationError):
            self.despachar()
        self.assertNotIn(self.registro,cajas_para_despacho())

    def test_sin_peso_no_sustituye_nominal(self):
        Caja.objects.filter(pk=self.registro.pk).update(peso_neto_kg=None,peso_total_kg=Decimal('25'))
        with self.assertRaisesRegex(ValidationError,'peso neto real documentado'):
            self.despachar()
        self.assertNotIn(self.registro,cajas_para_despacho())
        self.assertIsNone(EstanciaCaja.objects.get(caja=self.registro).fecha_hora_salida)

    def test_no_cajas_o_duplicadas(self):
        for cajas in ([], [self.registro,self.registro], [self.registro.pk,self.registro.pk], [999999], ['x']):
            with self.subTest(cajas=cajas), self.assertRaises(ValidationError):
                self.despachar(cajas=cajas)
        for cajas in ([], [self.registro.pk,self.registro.pk], [str(self.registro.pk),f'0{self.registro.pk}']):
            respuesta=self.client.post(self.url_despacho(),self.datos_post(cajas=cajas))
            self.assertIn('cajas',respuesta.context['form'].errors)
        self.assertFalse(Despacho.objects.exists())

    def test_nacional_destinatario_y_rut_obligatorios(self):
        for campo in ('destino','rut_destinatario'):
            with self.subTest(campo=campo), self.assertRaises(ValidationError) as error:
                self.despachar(**{campo:'  '})
            self.assertIn(campo,error.exception.message_dict)
            form=DespachoForm(self.datos_post(**{campo:''}))
            self.assertFalse(form.is_valid())
            self.assertIn(campo,form.errors)

    def test_exportacion_pais_obligatorio_destinatario_opcional(self):
        with self.assertRaises(ValidationError) as error:
            self.despachar(tipo_destino='EXPORTACION',destino='',rut_destinatario='')
        self.assertIn('pais_destino',error.exception.message_dict)
        despacho=self.despachar(tipo_destino='EXPORTACION',destino='',rut_destinatario='',pais_destino='Perú')
        self.assertEqual(despacho.pais_destino,'Perú')
        self.assertEqual(despacho.destino,'')

    def test_documento_requerido_y_tipo_abierto(self):
        for campo,valor in [('tipo_documento',''),('numero_documento',''),('fecha_documento',None)]:
            with self.subTest(campo=campo), self.assertRaises(ValidationError):
                self.despachar(**{campo:valor})
        despacho=self.despachar(tipo_documento='Documento de transporte',numero_documento=' ABC-100 ',observaciones=' Observación ',rut_destinatario=' 12.345.678-k ')
        self.assertEqual(despacho.numero_documento,'ABC-100')
        self.assertEqual(despacho.rut_destinatario,'12.345.678-K')
        self.assertEqual(despacho.fecha_documento,timezone.localdate())
        self.assertEqual(despacho.observaciones,'Observación')

    def test_tipos_y_longitudes_invalidos_service(self):
        for cambios in ({'tipo_destino':'OTRO'},{'destino':'X'*201},{'fecha_documento':'no-fecha'},{'numero_documento':None}):
            with self.subTest(cambios=cambios), self.assertRaises(ValidationError):
                self.despachar(**cambios)
        self.assertFalse(Despacho.objects.exists())

    def test_una_invalida_revierte_todo(self):
        pendiente=self.caja()
        antes=list(EstanciaCaja.objects.values())
        with self.assertRaises(ValidationError):
            self.despachar(cajas=[self.registro,pendiente])
        self.assertFalse(Despacho.objects.exists())
        self.assertFalse(DetalleDespacho.objects.exists())
        self.assertEqual(list(EstanciaCaja.objects.values()),antes)

    def test_fallo_detalles_revierte_encabezado_y_cierres(self):
        with patch('apps.inventario.services.DetalleDespacho.objects.bulk_create',side_effect=IntegrityError('colisión')):
            with self.assertRaisesRegex(ValidationError,'no están disponibles'):
                self.despachar()
        self.assertFalse(Despacho.objects.exists())
        self.assertIsNone(EstanciaCaja.objects.get(caja=self.registro).fecha_hora_salida)

    def test_ubicacion_ambigua_o_no_almacenamiento_rechazada(self):
        estancia=EstanciaCaja.objects.get(caja=self.registro)
        estancia.unidad_frio=self.tunel
        estancia.save()
        with self.assertRaises(ValidationError):self.despachar()
        estancia.unidad_frio=self.camara1
        estancia.save()
        EstanciaCaja.objects.create(caja=self.registro,unidad_frio=self.camara2,fecha_hora_ingreso=timezone.now(),ingresado_por=self.usuario)
        with self.assertRaises(ValidationError):self.despachar()
        self.assertFalse(Despacho.objects.exists())

    def test_ingreso_futuro_rechazado_sin_cierres(self):
        EstanciaCaja.objects.filter(caja=self.registro).update(fecha_hora_ingreso=timezone.now()+timedelta(days=1))
        with self.assertRaisesRegex(ValidationError,'fecha de ingreso'):
            self.despachar()
        self.assertFalse(Despacho.objects.exists())

    def test_camara_inactiva_no_impide_salida_de_stock_existente(self):
        UnidadFrio.objects.filter(pk=self.camara1.pk).update(activo=False)
        self.despachar()
        self.assertEqual(self.estado().estado_inventario,'DESPACHADA')

    def test_roles_operativos_y_superusuario_post(self):
        rol,_=Rol.objects.get_or_create(codigo='ENCARGADA',defaults={'nombre':'Encargada'})
        encargada=Usuario.objects.create_user(username='enc_despacho',rol=rol)
        tecnico=Usuario.objects.create_superuser(username='tec_despacho')
        for usuario in (self.jefe,encargada,self.usuario,tecnico):
            caja=self.caja()
            self.ingresar(caja=caja)
            self.client.force_login(usuario)
            respuesta=self.client.post(self.url_despacho(),self.datos_post(cajas=[caja.pk]))
            self.assertEqual(respuesta.status_code,302)
            self.assertEqual(caja.detalle_despacho.despacho.registrado_por,usuario)

    def test_no_permiso_anonimo_get_y_post(self):
        rol=Rol.objects.create(codigo='CONSULTA',nombre='Consulta')
        usuario=Usuario.objects.create_user(username='consulta_despacho',rol=rol)
        with self.assertRaises(PermissionDenied):self.despachar(usuario=usuario)
        self.client.force_login(usuario)
        for nombre in ('crear_despacho','lista_despachos'):
            self.assertEqual(self.client.get(self.url_despacho(nombre)).status_code,403)
        self.assertEqual(self.client.post(self.url_despacho(),self.datos_post()).status_code,403)
        self.client.logout()
        self.assertEqual(self.client.get(self.url_despacho()).status_code,302)

    def test_get_no_muta_post_no_acepta_peso_fecha_usuario_manipulados(self):
        self.assertEqual(self.client.get(self.url_despacho()).status_code,200)
        self.assertFalse(Despacho.objects.exists())
        self.client.post(self.url_despacho(),self.datos_post(peso_total='1',fecha_hora_despacho='2000-01-01',registrado_por=self.jefe.pk))
        despacho=Despacho.objects.get()
        self.assertEqual(despacho.registrado_por,self.usuario)
        self.assertEqual(timezone.localdate(despacho.fecha_hora_despacho),timezone.localdate())
        self.assertEqual(despachos_con_totales().get().peso_conocido,Decimal('24.50'))

    def test_detalle_caja_y_qr_despacho_documento_historial_y_traza(self):
        despacho=self.despachar()
        urls=[self.url_lote('detalle_caja',self.registro.pk),reverse('producto_terminado:consulta_caja_qr',args=[self.registro.identificador_qr])]
        for url in urls:
            respuesta=self.client.get(url)
            for texto in ('Despachada',f'Despacho #{despacho.pk}','GD-001','Comercializadora de prueba',self.camara1.nombre,'Historial de almacenamiento'):
                self.assertContains(respuesta,texto)
            self.assertNotContains(respuesta,'>Ingresar a almacenamiento</a>')
            self.assertNotContains(respuesta,'>Mover de cámara</a>')
        self.assertContains(self.client.get(urls[1]),f'Seguimiento #{self.partida.pk}')
        self.assertEqual(self.client.get(self.url_lote('caja_qr_png',self.registro.pk))['Content-Type'],'image/png')

    def test_despachada_no_reingresa_no_mueve_no_elegible(self):
        self.despachar()
        self.assertNotIn(self.registro,cajas_para_despacho())
        with self.assertRaises(ValidationError):self.ingresar()
        with self.assertRaises(ValidationError):self.mover()
        for estado in ('PENDIENTE','ALMACENADA'):
            self.assertNotIn(self.registro,self.client.get(self.url('lista'),{'estado':estado}).context['page_obj'])

    def test_seleccion_resumen_preservados_con_error(self):
        respuesta=self.client.post(self.url_despacho(),self.datos_post(destino=''))
        self.assertEqual(respuesta.context['cantidad_seleccionada'],1)
        self.assertEqual(respuesta.context['peso_seleccionado'],Decimal('24.50'))
        self.assertTrue(respuesta.context['cajas_disponibles'][0].seleccionada)

    def test_listado_filtros_y_detalle(self):
        despacho=self.despachar()
        url=self.url_despacho('lista_despachos')
        for filtro in ({'destinatario':'Comercializadora'},{'tipo_destino':'NACIONAL'},{'fecha':timezone.localdate().isoformat()}):
            self.assertEqual(self.client.get(url,filtro).context['page_obj'].paginator.count,1)
        for filtro in ({'destinatario':'Inexistente'},{'tipo_destino':'EXPORTACION'},{'fecha':'inválida'}):
            self.assertEqual(self.client.get(url,filtro).context['page_obj'].paginator.count,0)
        respuesta=self.client.get(self.url_despacho('detalle_despacho',despacho.pk))
        for texto in ('Registrado','24,50 kg','GD-001','Guía de despacho','12.345.678-5'):
            self.assertContains(respuesta,texto)

    def test_historicos_incompletos_accesibles_sin_inventar_peso(self):
        Caja.objects.filter(pk=self.registro.pk).update(peso_neto_kg=None)
        historico=Despacho.objects.create(fecha_hora_despacho=timezone.now(),destino='Destino histórico',registrado_por=self.usuario)
        DetalleDespacho.objects.create(despacho=historico,caja=self.registro)
        respuesta=self.client.get(self.url_despacho('detalle_despacho',historico.pk))
        self.assertContains(respuesta,'No documentado')
        self.assertContains(respuesta,'total completo no está documentado')
        self.assertEqual(self.stock()['stock']['cajas'],0)
        self.assertEqual(self.estado().estado_inventario,'DESPACHADA')

    def test_paginacion_y_consultas_constantes(self):
        self.despachar()
        url=self.url_despacho('lista_despachos')
        with CaptureQueriesContext(connection) as antes:self.client.get(url)
        Despacho.objects.bulk_create([Despacho(fecha_hora_despacho=timezone.now(),destino='Histórico',registrado_por=self.usuario) for _ in range(12)])
        with CaptureQueriesContext(connection) as despues:self.client.get(url)
        self.assertEqual(len(antes),len(despues))
        respuesta=self.client.get(url,{'destinatario':'Histórico','page':2})
        self.assertEqual(respuesta.context['page_obj'].number,2)
        self.assertIn('destinatario=',respuesta.context['pagina_anterior'])

    def test_admin_solo_consulta(self):
        request=RequestFactory().get('/admin/')
        request.user=self.jefe
        for modelo in (Despacho,DetalleDespacho):
            registro=admin.site._registry[modelo]
            self.assertFalse(registro.has_add_permission(request))
            self.assertFalse(registro.has_change_permission(request))
            self.assertFalse(registro.has_delete_permission(request))


class ConcurrenciaDespachoTests(DatosDespacho, TransactionTestCase):
    def setUp(self):self.preparar_despacho()

    def competir(self, acciones):
        barrera=Barrier(2)
        def ejecutar(accion):
            close_old_connections()
            try:
                barrera.wait(timeout=10)
                try:
                    accion()
                    return 'ok'
                except ValidationError:return 'rechazada'
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:return list(pool.map(ejecutar,acciones))

    @skipUnlessDBFeature('has_select_for_update')
    def test_misma_caja_solo_un_despacho(self):
        self.assertCountEqual(self.competir([self.despachar,self.despachar]),['ok','rechazada'])
        self.assertEqual(Despacho.objects.count(),1)
        self.assertEqual(DetalleDespacho.objects.count(),1)
        self.assertEqual(EstanciaCaja.objects.filter(fecha_hora_salida=None).count(),0)

    @skipUnlessDBFeature('has_select_for_update')
    def test_orden_inverso_selecciones_sin_despachos_parciales(self):
        otra=self.caja()
        self.ingresar(caja=otra)
        acciones=[lambda:self.despachar(cajas=[self.registro,otra]),lambda:self.despachar(cajas=[otra,self.registro])]
        self.assertCountEqual(self.competir(acciones),['ok','rechazada'])
        self.assertEqual(Despacho.objects.count(),1)
        self.assertEqual(DetalleDespacho.objects.count(),2)

    @skipUnlessDBFeature('has_select_for_update')
    def test_movimiento_y_despacho_no_dejan_ubicacion_abierta(self):
        resultados=self.competir([self.despachar,self.mover])
        self.assertEqual(resultados[0],'ok')
        self.assertEqual(Despacho.objects.count(),1)
        self.assertEqual(EstanciaCaja.objects.filter(fecha_hora_salida=None).count(),0)


class MigracionDespachoTests(DatosDespacho, TransactionTestCase):
    def test_migracion_conserva_historicos_sin_completar_datos(self):
        self.preparar_despacho()
        actual=MigrationExecutor(connection).loader.graph.leaf_nodes()
        anterior=[('inventario','0001_initial')]
        try:
            executor=MigrationExecutor(connection)
            executor.migrate(anterior)
            apps=executor.loader.project_state(anterior).apps
            antiguo=apps.get_model('inventario','Despacho').objects.create(fecha_hora_despacho=timezone.now(),destino='Histórico',registrado_por_id=self.usuario.pk)
            apps.get_model('inventario','DetalleDespacho').objects.create(despacho_id=antiguo.pk,caja_id=self.registro.pk)
            antes=list(EstanciaCaja.objects.values())
            caja_antes=Caja.objects.values().get(pk=self.registro.pk)
            MigrationExecutor(connection).migrate(actual)
            despacho=Despacho.objects.get(pk=antiguo.pk)
            self.assertEqual(despacho.destino,'Histórico')
            self.assertEqual(despacho.tipo_destino,'')
            self.assertEqual(despacho.numero_documento,'')
            self.assertIsNone(despacho.fecha_documento)
            self.assertEqual(despacho.detalles.get().caja_id,self.registro.pk)
            self.assertEqual(list(EstanciaCaja.objects.values()),antes)
            self.assertEqual(Caja.objects.values().get(pk=self.registro.pk),caja_antes)
        finally:MigrationExecutor(connection).migrate(actual)
