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

from apps.trazabilidad.models import Caja, MermaProceso, UnidadFrio
from apps.usuarios.models import Rol, Usuario
from .forms import BajaCajaForm
from .models import BajaCaja, Despacho, DetalleDespacho, EstanciaCaja
from .selectors import cajas_para_despacho
from .services import registrar_baja
from .test_despachos import DatosDespacho


class DatosBaja(DatosDespacho):
    def baja(self, caja=None, **cambios):
        datos=dict(caja=caja or self.registro, tipo='DANO', motivo='Caja dañada durante manipulación.', usuario=self.usuario)
        datos.update(cambios)
        return registrar_baja(**datos)

    def url_baja(self,caja=None):
        return reverse('inventario:crear_baja',args=[(caja or self.registro).pk])

    def url_bajas(self):
        return reverse('inventario:lista_bajas')


class BajaTests(DatosBaja, TestCase):
    def setUp(self):
        self.preparar_inventario()
        self.client.force_login(self.usuario)

    def test_pendiente_baja_sin_estancias_ni_stock_ficticio(self):
        antes=self.stock()['stock']
        baja=self.baja()
        self.assertEqual(baja.caja,self.registro)
        self.assertFalse(EstanciaCaja.objects.exists())
        self.assertEqual(self.estado().estado_inventario,'BAJA')
        self.assertEqual(self.estado().estado_visible,'Baja')
        self.assertEqual(self.stock()['stock'],antes)
        self.assertEqual(self.stock()['pendientes'],0)

    def test_almacenada_cierra_mismo_instante_usuario_conserva_historial(self):
        primera=self.ingresar()
        segunda=self.mover()
        primera.refresh_from_db()
        antes=EstanciaCaja.objects.values().get(pk=primera.pk)
        ahora=timezone.now()
        with patch('apps.inventario.services.timezone.now',return_value=ahora):
            baja=self.baja(usuario=self.jefe)
        segunda.refresh_from_db()
        self.assertEqual(baja.fecha_hora_evento,ahora)
        self.assertTrue(timezone.is_aware(baja.fecha_hora_evento))
        self.assertEqual(segunda.fecha_hora_salida,ahora)
        self.assertEqual(segunda.retirado_por,self.jefe)
        self.assertEqual(baja.registrado_por,self.jefe)
        self.assertEqual(EstanciaCaja.objects.values().get(pk=primera.pk),antes)
        self.assertEqual(EstanciaCaja.objects.count(),2)

    def test_stock_por_especie_lote_camara_disminuye_peso_real(self):
        otra=self.caja(peso_neto_kg=Decimal('24.82'))
        self.ingresar()
        self.ingresar(caja=otra)
        self.assertEqual(self.stock()['stock']['peso_conocido'],Decimal('49.32'))
        self.baja()
        resumen=self.stock()
        self.assertEqual(resumen['stock']['cajas'],1)
        self.assertEqual(resumen['stock']['peso_conocido'],Decimal('24.82'))
        for campo in ('por_especie','por_lote','por_camara'):
            filas=list(resumen[campo])
            self.assertEqual(filas[0]['cajas'],1)
            self.assertEqual(filas[0]['peso_conocido'],Decimal('24.82'))
        self.assertEqual(self.estado(otra).estado_inventario,'ALMACENADA')

    def test_tipo_motivo_y_ninguna_modificacion_caja_lote_uuid_mermas(self):
        antes=Caja.objects.values().get(pk=self.registro.pk)
        lote_antes=self.lote.__class__.objects.values().get(pk=self.lote.pk)
        baja=self.baja(tipo='PERDIDA',motivo='  Extraviada en manipulación  ')
        self.assertEqual(baja.tipo,'PERDIDA')
        self.assertEqual(baja.motivo,'Extraviada en manipulación')
        self.assertEqual(Caja.objects.values().get(pk=self.registro.pk),antes)
        self.assertEqual(self.lote.__class__.objects.values().get(pk=self.lote.pk),lote_antes)
        self.assertFalse(MermaProceso.objects.exists())

    def test_motivo_obligatorio_y_tipo_valido_en_service_y_form(self):
        for motivo in ('','   ',None,'x'*201):
            with self.subTest(motivo=motivo),self.assertRaises(ValidationError):self.baja(motivo=motivo)
        for tipo in ('','INVALIDO',None):
            with self.assertRaises(ValidationError):self.baja(tipo=tipo)
        self.assertFalse(BajaCajaForm({'tipo':'DANO','motivo':'  '}).is_valid())
        self.assertFalse(BajaCajaForm({'tipo':'INVALIDO','motivo':'Motivo'}).is_valid())
        self.assertFalse(BajaCaja.objects.exists())

    def test_todos_los_tipos_conservados(self):
        for tipo in BajaCaja.Tipo.values:
            caja=self.caja()
            self.assertEqual(self.baja(caja=caja,tipo=tipo).tipo,tipo)

    def test_despachada_rechazada_sin_modificar_salida(self):
        self.ingresar()
        despacho=self.despachar()
        antes=list(EstanciaCaja.objects.values())
        with self.assertRaisesRegex(ValidationError,'ya fue despachada'):
            self.baja()
        self.assertFalse(BajaCaja.objects.exists())
        self.assertEqual(list(EstanciaCaja.objects.values()),antes)
        self.assertEqual(DetalleDespacho.objects.get().despacho_id,despacho.pk)

    def test_no_doble_baja_y_unicidad_estructural(self):
        baja=self.baja()
        with self.assertRaisesRegex(ValidationError,'ya posee una baja'):self.baja()
        with self.assertRaises(IntegrityError),transaction.atomic():
            BajaCaja.objects.create(caja=self.registro,motivo='Duplicada',fecha_hora_evento=timezone.now(),registrado_por=self.usuario)
        self.assertEqual(BajaCaja.objects.get().pk,baja.pk)

    def test_baja_no_despacha_no_ingresa_no_mueve(self):
        self.ingresar()
        self.baja()
        for operacion in (self.despachar,self.ingresar,self.mover):
            with self.assertRaises(ValidationError):operacion()
        self.assertNotIn(self.registro,cajas_para_despacho())
        self.assertFalse(DetalleDespacho.objects.exists())
        self.assertFalse(EstanciaCaja.objects.filter(fecha_hora_salida=None).exists())

    def test_fallo_guardado_revierte_cierre_y_usuario_salida(self):
        estancia=self.ingresar()
        with patch('apps.inventario.services.BajaCaja.save',side_effect=IntegrityError('simulado')):
            with self.assertRaisesRegex(ValidationError,'no está disponible'):self.baja()
        estancia.refresh_from_db()
        self.assertIsNone(estancia.fecha_hora_salida)
        self.assertIsNone(estancia.retirado_por)
        self.assertFalse(BajaCaja.objects.exists())
        self.assertEqual(self.stock()['stock']['cajas'],1)

    def test_historial_ambiguo_rechazado_sin_reescritura(self):
        self.ingresar()
        EstanciaCaja.objects.create(caja=self.registro,unidad_frio=self.camara2,fecha_hora_ingreso=timezone.now(),ingresado_por=self.usuario)
        antes=list(EstanciaCaja.objects.values())
        with self.assertRaisesRegex(ValidationError,'historial requiere revisión'):self.baja()
        self.assertFalse(self.estado().puede_registrar_baja)
        self.assertEqual(list(EstanciaCaja.objects.values()),antes)

    def test_unidad_no_almacenamiento_e_ingreso_futuro_rechazados(self):
        estancia=self.ingresar(unidad=self.camara1)
        estancia.unidad_frio=self.tunel
        estancia.save()
        with self.assertRaises(ValidationError):self.baja()
        estancia.unidad_frio=self.camara1
        estancia.fecha_hora_ingreso=timezone.now()+timedelta(days=1)
        estancia.save()
        with self.assertRaises(ValidationError):self.baja()
        self.assertFalse(self.estado().puede_registrar_baja)

    def test_camara_inactiva_permite_baja_del_producto_presente(self):
        self.ingresar()
        UnidadFrio.objects.filter(pk=self.camara1.pk).update(activo=False)
        self.baja()
        self.assertEqual(self.estado().estado_inventario,'BAJA')

    def test_historica_sin_peso_permite_baja_sin_inventar(self):
        Caja.objects.filter(pk=self.registro.pk).update(peso_neto_kg=None,peso_total_kg=Decimal('500'))
        self.ingresar()
        self.baja()
        self.registro.refresh_from_db()
        self.assertIsNone(self.registro.peso_neto_kg)
        self.assertEqual(self.stock()['stock']['cajas'],0)
        respuesta=self.client.get(self.url_lote('detalle_caja',self.registro.pk))
        self.assertContains(respuesta,'Peso no documentado')

    def test_historica_sin_tipo_consultable_y_con_despacho_no_disponible(self):
        BajaCaja.objects.create(caja=self.registro,motivo='Baja histórica',fecha_hora_evento=timezone.now(),registrado_por=self.usuario)
        despacho=Despacho.objects.create(fecha_hora_despacho=timezone.now(),destino='Histórico',registrado_por=self.usuario)
        DetalleDespacho.objects.create(caja=self.registro,despacho=despacho)
        respuesta=self.client.get(self.url_lote('detalle_caja',self.registro.pk))
        self.assertContains(respuesta,'No documentado')
        self.assertContains(respuesta,'también tiene un despacho histórico')
        self.assertEqual(self.estado().estado_inventario,'BAJA')
        self.assertEqual(self.stock()['stock']['cajas'],0)
        self.assertEqual(BajaCaja.objects.count(),1)
        self.assertEqual(DetalleDespacho.objects.count(),1)

    def test_roles_operativos_y_superusuario(self):
        rol,_=Rol.objects.get_or_create(codigo='ENCARGADA',defaults={'nombre':'Encargada'})
        encargada=Usuario.objects.create_user(username='enc_baja',rol=rol)
        tecnico=Usuario.objects.create_superuser(username='tec_baja')
        for usuario in (self.usuario,self.jefe,encargada,tecnico):
            caja=self.caja()
            self.client.force_login(usuario)
            respuesta=self.client.post(self.url_baja(caja),{'tipo':'DANO','motivo':'Daño en manipulación'})
            self.assertEqual(respuesta.status_code,302)
            self.assertEqual(BajaCaja.objects.get(caja=caja).registrado_por,usuario)

    def test_no_permiso_y_anonimo(self):
        rol=Rol.objects.create(codigo='CONSULTA',nombre='Consulta')
        usuario=Usuario.objects.create_user(username='consulta_baja',rol=rol)
        with self.assertRaises(PermissionDenied):self.baja(usuario=usuario)
        self.client.force_login(usuario)
        self.assertEqual(self.client.post(self.url_baja(),{'tipo':'DANO','motivo':'Daño'}).status_code,403)
        self.assertEqual(self.client.get(self.url_bajas()).status_code,403)
        self.client.logout()
        self.assertEqual(self.client.get(self.url_baja()).status_code,302)

    def test_get_no_muta_post_ignora_peso_fecha_usuario_caja(self):
        self.client.get(self.url_baja())
        self.assertFalse(BajaCaja.objects.exists())
        otra=self.caja()
        self.client.post(self.url_baja(),{'tipo':'DANO','motivo':'Daño','caja':otra.pk,'usuario':self.jefe.pk,'peso_neto_kg':'1','fecha_hora_evento':'2000-01-01'})
        baja=BajaCaja.objects.get()
        self.assertEqual(baja.caja_id,self.registro.pk)
        self.assertEqual(baja.registrado_por,self.usuario)
        self.assertEqual(timezone.localdate(baja.fecha_hora_evento),timezone.localdate())
        self.registro.refresh_from_db()
        self.assertEqual(self.registro.peso_neto_kg,Decimal('24.50'))

    def test_post_directo_no_salta_validaciones_y_404(self):
        self.ingresar()
        self.despachar()
        respuesta=self.client.post(self.url_baja(),{'tipo':'DANO','motivo':'Daño'})
        self.assertContains(respuesta,'ya fue despachada')
        self.assertNotContains(respuesta,'type="submit" class="rounded-lg bg-cyan-700')
        self.assertFalse(BajaCaja.objects.exists())
        self.assertEqual(self.client.get(reverse('inventario:crear_baja',args=[999999])).status_code,404)

    def test_detalle_qr_baja_historial_trazabilidad_y_acciones(self):
        self.ingresar()
        detalle=self.url_lote('detalle_caja',self.registro.pk)
        self.assertContains(self.client.get(detalle),'Registrar baja')
        self.baja()
        qr=reverse('producto_terminado:consulta_caja_qr',args=[self.registro.identificador_qr])
        for url in (detalle,qr):
            respuesta=self.client.get(url)
            for texto in ('Baja de producto terminado','Daño','Caja dañada durante manipulación.','24,50 kg',self.camara1.nombre,'Historial de almacenamiento'):
                self.assertContains(respuesta,texto)
            for texto in ('>Ingresar a almacenamiento</a>','>Mover de cámara</a>','>Registrar baja</a>'):
                self.assertNotContains(respuesta,texto)
        self.assertContains(self.client.get(qr),f'Seguimiento #{self.partida.pk}')
        self.assertEqual(self.client.get(self.url_lote('caja_qr_png',self.registro.pk))['Content-Type'],'image/png')

    def test_no_aparece_pendiente_almacenada_no_stock(self):
        self.baja()
        for estado in ('PENDIENTE','ALMACENADA'):
            respuesta=self.client.get(self.url('lista'),{'estado':estado})
            self.assertNotIn(self.registro,respuesta.context['page_obj'])
            self.assertEqual(respuesta.context['stock']['cajas'],0)
        self.assertIn(self.registro,self.client.get(self.url('lista'),{'estado':'BAJA'}).context['page_obj'])

    def test_lista_filtros_fecha_especie_tipo_y_errores(self):
        self.baja()
        for datos in ({'fecha':timezone.localdate().isoformat()},{'especie':self.especie.pk},{'tipo':'DANO'}):
            self.assertEqual(self.client.get(self.url_bajas(),datos).context['page_obj'].paginator.count,1)
        for datos in ({'fecha':'2000-01-01'},{'especie':self.otra_especie.pk},{'tipo':'PERDIDA'},{'fecha':'incorrecta'}):
            self.assertEqual(self.client.get(self.url_bajas(),datos).context['page_obj'].paginator.count,0)
        self.assertContains(self.client.get(self.url_bajas()),'Caja dañada durante manipulación.')

    def test_paginacion_conserva_filtros_y_no_n_mas_uno(self):
        self.baja()
        with CaptureQueriesContext(connection) as antes:self.client.get(self.url_bajas())
        for _ in range(12):
            caja=Caja.objects.create(lote_produccion=self.lote,fecha_armado=timezone.now(),registrado_por=self.usuario)
            self.baja(caja=caja)
        with CaptureQueriesContext(connection) as despues:self.client.get(self.url_bajas())
        self.assertEqual(len(antes),len(despues))
        respuesta=self.client.get(self.url_bajas(),{'tipo':'DANO','page':2})
        self.assertEqual(respuesta.context['page_obj'].number,2)
        self.assertIn('tipo=DANO',respuesta.context['pagina_anterior'])

    def test_admin_solo_consulta_sin_acciones_mutables(self):
        registro=admin.site._registry[BajaCaja]
        request=RequestFactory().get('/admin/')
        request.user=self.jefe
        self.assertFalse(registro.has_add_permission(request))
        self.assertFalse(registro.has_change_permission(request))
        self.assertFalse(registro.has_delete_permission(request))
        self.assertIsNone(registro.actions)


class ConcurrenciaBajaTests(DatosBaja, TransactionTestCase):
    def setUp(self):self.preparar_despacho()

    def competir(self,acciones):
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
    def test_baja_vs_baja_solo_una(self):
        self.assertCountEqual(self.competir([self.baja,self.baja]),['ok','rechazada'])
        self.assertEqual(BajaCaja.objects.count(),1)
        self.assertFalse(EstanciaCaja.objects.filter(fecha_hora_salida=None).exists())

    @skipUnlessDBFeature('has_select_for_update')
    def test_baja_vs_despacho_solo_una_salida(self):
        self.assertCountEqual(self.competir([self.baja,self.despachar]),['ok','rechazada'])
        self.assertEqual(BajaCaja.objects.count()+DetalleDespacho.objects.count(),1)
        self.assertFalse(EstanciaCaja.objects.filter(fecha_hora_salida=None).exists())

    @skipUnlessDBFeature('has_select_for_update')
    def test_baja_vs_movimiento_no_reabre_stock(self):
        resultados=self.competir([self.baja,self.mover])
        self.assertEqual(resultados[0],'ok')
        self.assertEqual(BajaCaja.objects.count(),1)
        self.assertFalse(EstanciaCaja.objects.filter(fecha_hora_salida=None).exists())


class MigracionBajaTests(DatosBaja, TransactionTestCase):
    def test_migracion_conserva_baja_historica_sin_inventar_tipo(self):
        self.preparar_despacho()
        actual=MigrationExecutor(connection).loader.graph.leaf_nodes()
        anterior=[('inventario','0002_despacho_fecha_documento_despacho_numero_documento_and_more')]
        try:
            executor=MigrationExecutor(connection)
            executor.migrate(anterior)
            apps=executor.loader.project_state(anterior).apps
            antigua=apps.get_model('inventario','BajaCaja').objects.create(caja_id=self.registro.pk,motivo='Histórica sin tipo',fecha_hora_evento=timezone.now(),registrado_por_id=self.usuario.pk)
            antes=list(EstanciaCaja.objects.values())
            caja_antes=Caja.objects.values().get(pk=self.registro.pk)
            MigrationExecutor(connection).migrate(actual)
            baja=BajaCaja.objects.get(pk=antigua.pk)
            self.assertEqual(baja.tipo,'')
            self.assertEqual(baja.motivo,'Histórica sin tipo')
            self.assertEqual(baja.fecha_hora_evento,antigua.fecha_hora_evento)
            self.assertEqual(baja.caja_id,self.registro.pk)
            self.assertEqual(list(EstanciaCaja.objects.values()),antes)
            self.assertEqual(Caja.objects.values().get(pk=self.registro.pk),caja_antes)
        finally:MigrationExecutor(connection).migrate(actual)
