from django.test import TestCase, override_settings
from django.urls import reverse
from apps.inventario.models import Despacho
from apps.usuarios.models import Rol, Usuario
from apps.trazabilidad.test_qr_cajas import DatosQR
from apps.trazabilidad.services_rutas import solicitar_ruta


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class MatrizPermisosTests(DatosQR, TestCase):
    def setUp(self):
        self.preparar_qr()
        self.encargada = Usuario.objects.create_user(username="matriz_enc", rol=Rol.objects.get(codigo="ENCARGADA"))
        self.cuenta = Usuario.objects.create_user(username="matriz_cuenta", rol=Rol.objects.get(codigo="OPERARIA"))
        self.despacho = Despacho.objects.create(fecha_hora_despacho=self.registro.fecha_armado, registrado_por=self.jefe)
        self.solicitud = solicitar_ruta(usuario=self.usuario, especie=self.especie,
                                       operaciones=[self.tipos["FILETEO"].pk])

    def matriz(self):
        p, l, c = self.partida.pk, self.lote.pk, self.registro.pk
        recepcion = self.partida.detalle_recepcion.recepcion_id
        filas = []
        def grupo(namespace, roles, rutas):
            for nombre, args, metodo in rutas:
                filas.append((namespace+":"+nombre, args, metodo, roles))
        grupo("trazabilidad", ("OPERARIA", "ENCARGADA", "JEFE"), [
            ("lista_partidas", [], "get"), ("detalle_partida", [p], "get"),
            *[(n, [p], "get") for n in ("enviar_partida_mantencion", "retirar_partida_mantencion", "procesar_partida", "gestionar_procesamiento", "enviar_partida_tunel", "registrar_peso_postproceso", "registrar_merma_proceso")],
            ("retirar_partida_tunel", [p], "post"),
            *[(n, [p, self.partida.ruta_proceso.etapas.first().pk], "post") for n in ("iniciar_etapa", "finalizar_etapa")]])
        grupo("producto_terminado", ("OPERARIA", "ENCARGADA", "JEFE"), [
            ("lista_cajas", [], "get"), ("seleccionar_lote_caja", [], "get"), ("crear_caja", [l], "get"),
            ("detalle_caja", [c], "get"), ("caja_qr_png", [c], "get"),
            ("consulta_caja_qr", [self.registro.identificador_qr], "get")])
        grupo("inventario", ("OPERARIA", "ENCARGADA", "JEFE"), [
            ("lista", [], "get"), ("lista_bajas", [], "get"),
            *[(n, [c], "get") for n in ("ingresar", "mover", "crear_baja")]])
        grupo("trazabilidad", ("ENCARGADA", "JEFE"), [("lista_recepciones", [], "get"),
            ("nueva_recepcion", [], "get"), ("detalle_recepcion", [recepcion], "get")])
        grupo("producto_terminado", ("ENCARGADA", "JEFE"), [("lista_lotes", [], "get"),
            ("detalle_lote", [l], "get"), ("crear_lote", [p], "get"), ("agregar_producto_lote", [l], "get")])
        grupo("inventario", ("ENCARGADA", "JEFE"), [("lista_despachos", [], "get"),
            ("crear_despacho", [], "get"), ("detalle_despacho", [self.despacho.pk], "get")])
        grupo("trazabilidad", ("JEFE",), [("corregir_recepcion", [recepcion], "get")])
        grupo("reportes", ("JEFE",), [(n, [], "get") for n in ("dashboard", "umbrales", "sernapesca", "descargar_sernapesca")])
        grupo("usuarios", ("JEFE",), [("lista_usuarios", [], "get"), ("crear_usuario", [], "get"),
            *[(n, [self.cuenta.pk], "get") for n in ("detalle_usuario", "editar_rol", "reset_contrasena")],
            *[(n, [self.cuenta.pk], "post") for n in ("desactivar_usuario", "reactivar_usuario")]])
        grupo("usuarios", ("OPERARIA", "ENCARGADA", "JEFE"), [(n, [], "get") for n in ("inicio", "cambiar_contrasena")])
        grupo("trazabilidad", ("OPERARIA", "ENCARGADA", "JEFE"), [
            ("seleccionar_operaciones", [p], "get"), ("solicitudes_ruta", [], "get")])
        grupo("trazabilidad", ("ENCARGADA", "JEFE"), [
            ("consulta_integral", [], "get"), ("ficha_integral", ["caja", c], "get")])
        grupo("trazabilidad", ("JEFE",), [
            ("rutas", [], "get"), ("nueva_ruta", [], "get"), ("operaciones", [], "get"),
            ("editar_ruta", [self.ruta.pk], "get"), ("duplicar_ruta", [self.ruta.pk], "get"),
            ("estado_ruta", [self.ruta.pk], "post"),
            ("editar_operacion", [self.tipos["FILETEO"].pk], "get"),
            ("resolver_solicitud", [self.solicitud.pk], "get")])
        return filas

    def test_matriz_completa_por_perfil_y_anonimo(self):
        for usuario in (None, self.usuario, self.encargada, self.jefe):
            if usuario:
                self.client.force_login(usuario)
            else:
                self.client.logout()
            codigo = usuario.rol.codigo if usuario else "ANONIMO"
            for nombre, args, metodo, roles in self.matriz():
                with self.subTest(vista=nombre, rol=codigo, metodo=metodo):
                    esperado = 302 if not usuario else (403 if codigo not in roles else (302 if metodo == "post" else 200))
                    if usuario and codigo == "JEFE" and nombre == "usuarios:inicio":
                        esperado = 302
                    self.assertEqual(getattr(self.client, metodo)(reverse(nombre, args=args)).status_code, esperado)

    def test_matriz_cubre_todas_las_urls_funcionales(self):
        from importlib import import_module
        esperadas = set()
        for modulo in ('apps.usuarios.urls', 'apps.trazabilidad.urls', 'apps.trazabilidad.urls_lotes', 'apps.inventario.urls', 'apps.reportes.urls'):
            urls = import_module(modulo)
            esperadas.update(urls.app_name+":"+p.name for p in urls.urlpatterns)
        self.assertEqual({f[0] for f in self.matriz()}, esperadas - {'usuarios:login', 'usuarios:logout'})

    def test_login_logout_y_next(self):
        self.client.logout()
        url = reverse('producto_terminado:consulta_caja_qr', args=[self.registro.identificador_qr])
        self.assertIn('next='+url, self.client.get(url).url)
        self.assertEqual(self.client.get(reverse('usuarios:login'), {'next': url}).status_code, 200)
        for usuario in (self.usuario, self.encargada, self.jefe):
            self.client.force_login(usuario)
            self.assertEqual(self.client.post(reverse('usuarios:logout')).status_code, 302)

    def test_nombres_visibles_en_encabezado_y_formulario(self):
        for usuario, nombre in ((self.usuario,'Operario'), (self.encargada,'Encargada de registro'), (self.jefe,'Jefe / Administrador')):
            self.client.force_login(usuario)
            self.assertContains(self.client.get(reverse('usuarios:inicio'), follow=True), nombre)
        respuesta = self.client.get(reverse('usuarios:crear_usuario'))
        for nombre in ('Operario', 'Encargada de registro', 'Jefe / Administrador'):
            self.assertContains(respuesta,nombre)
