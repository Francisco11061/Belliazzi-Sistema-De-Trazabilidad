from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import authenticate
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, transaction
from django.test import Client, RequestFactory, TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone

from apps.trazabilidad.models import Correccion, Recepcion
from . import services
from .forms import CrearUsuarioForm
from .models import EventoUsuario, Rol, Usuario


CLAVE = "Temporal-Prueba-48!segura"
NUEVA = "Distinta-Prueba-73!nueva"


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class GestionUsuariosTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.jefe_rol = Rol.objects.get(codigo="JEFE")
        cls.encargada_rol = Rol.objects.get(codigo="ENCARGADA")
        cls.operaria_rol = Rol.objects.get(codigo="OPERARIA")
        cls.jefe = Usuario.objects.create_user(username="jefe", password=CLAVE, rol=cls.jefe_rol)
        cls.encargada = Usuario.objects.create_user(username="encargada", password=CLAVE, rol=cls.encargada_rol)
        cls.operaria = Usuario.objects.create_user(username="operaria", password=CLAVE, rol=cls.operaria_rol)
        cls.tecnico = Usuario.objects.create_superuser(username="tecnico", password=CLAVE)

    def setUp(self):
        self.client.force_login(self.jefe)

    def url(self, nombre, usuario=None):
        return reverse("usuarios:" + nombre, args=[usuario.pk] if usuario else [])

    def datos(self, **extra):
        return dict(username="operaria02", first_name="Ana", last_name="Pérez",
                    rol=self.operaria_rol.pk, password1=CLAVE, password2=CLAVE, **extra)

    def crear(self, **extra):
        data = dict(actor=self.jefe, username="operaria02", first_name="Ana", last_name="Pérez",
                    rol=self.operaria_rol, password=CLAVE)
        data.update(extra)
        return services.crear_usuario(**data)

    def test_acceso_y_sidebar_por_rol(self):
        for usuario, permitido in [(self.jefe, True), (self.tecnico, True),
                                   (self.encargada, False), (self.operaria, False)]:
            with self.subTest(usuario=usuario.username):
                self.client.force_login(usuario)
                response = self.client.get(self.url("lista_usuarios"))
                self.assertEqual(response.status_code, 200 if permitido else 403)
                inicio = self.client.get(self.url("inicio"))
                enlace = f'href="{self.url("lista_usuarios")}"'
                if permitido:
                    self.assertContains(inicio, enlace)
                else:
                    self.assertNotContains(inicio, enlace)

    def test_todas_las_urls_directas_protegidas(self):
        urls = [self.url("lista_usuarios"), self.url("crear_usuario")]
        urls += [self.url(nombre, self.operaria) for nombre in
                 ("detalle_usuario", "editar_rol", "reset_contrasena", "desactivar_usuario", "reactivar_usuario")]
        for usuario in (self.encargada, self.operaria):
            self.client.force_login(usuario)
            for url in urls:
                for metodo in (self.client.get, self.client.post):
                    with self.subTest(usuario=usuario.pk, url=url, metodo=metodo.__name__):
                        self.assertEqual(metodo(url).status_code, 403)

    def test_anonimo_no_tiene_registro_publico(self):
        self.client.logout()
        self.assertEqual(self.client.post(self.url("crear_usuario"), self.datos()).status_code, 302)
        self.assertFalse(Usuario.objects.filter(username="operaria02").exists())

    def test_servicios_rechazan_actores_no_autorizados(self):
        for actor in (self.encargada, self.operaria, AnonymousUser()):
            operaciones = [lambda: self.crear(actor=actor),
                          lambda: services.cambiar_rol(actor=actor, usuario=self.operaria, rol=self.encargada_rol),
                          lambda: services.cambiar_estado(actor=actor, usuario=self.operaria, activo=False),
                          lambda: services.restablecer_contrasena(actor=actor, usuario=self.operaria, password=NUEVA)]
            for operacion in operaciones:
                with self.assertRaises(PermissionDenied):
                    operacion()
        self.assertEqual(EventoUsuario.objects.count(), 0)

    def test_servicio_relee_permisos_del_actor(self):
        Usuario.objects.filter(pk=self.jefe.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            self.crear()

    def test_servicio_rechaza_jefe_pendiente(self):
        Usuario.objects.filter(pk=self.jefe.pk).update(cambio_contrasena_pendiente=True)
        with self.assertRaises(PermissionDenied):
            self.crear()

    def test_crear_desde_ui_ignora_flags_y_no_expone_clave(self):
        response = self.client.post(self.url("crear_usuario"), self.datos(is_staff="on", is_superuser="on"), follow=True)
        usuario = Usuario.objects.get(username="operaria02")
        self.assertContains(response, "Usuario creado correctamente. Debe cambiar su contraseña al iniciar sesión.")
        self.assertNotContains(response, CLAVE)
        self.assertNotContains(response, usuario.password)
        self.assertTrue(usuario.check_password(CLAVE))
        self.assertNotEqual(usuario.password, CLAVE)
        self.assertFalse(usuario.is_staff)
        self.assertFalse(usuario.is_superuser)
        self.assertTrue(usuario.is_active)
        self.assertTrue(usuario.cambio_contrasena_pendiente)
        self.assertEqual(usuario.rol, self.operaria_rol)
        self.assertEqual(usuario.creado_por, self.jefe)
        self.assertEqual(usuario.get_full_name(), "Ana Pérez")
        evento = usuario.eventos.get()
        self.assertEqual(evento.tipo, EventoUsuario.Tipo.CREACION)
        self.assertEqual(evento.actor, self.jefe)

    def test_username_duplicado_y_normalizado(self):
        self.crear()
        response = self.client.post(self.url("crear_usuario"), self.datos())
        self.assertContains(response, "El nombre de usuario ya está en uso.")
        with self.assertRaises(ValidationError):
            self.crear(username="ｏｐｅｒａｒｉａ０２")
        self.assertEqual(Usuario.objects.filter(username="operaria02").count(), 1)

    def test_password_debil_rechazado_formulario_y_servicio(self):
        for clave in ("123", "password", "123456789012"):
            data = self.datos()
            data.update(password1=clave, password2=clave)
            response = self.client.post(self.url("crear_usuario"), data)
            self.assertTrue(response.context["form"].errors)
            with self.assertRaises(ValidationError):
                self.crear(password=clave)
        self.assertFalse(Usuario.objects.filter(username="operaria02").exists())

    def test_password_confirmacion_y_no_rellenar_en_error(self):
        data = self.datos()
        data["password2"] = NUEVA
        response = self.client.post(self.url("crear_usuario"), data)
        self.assertContains(response, "Las contraseñas no coinciden.")
        self.assertNotContains(response, CLAVE)
        self.assertNotContains(response, NUEVA)

    def test_rol_inactivo_rechazado_en_alta_y_cambio(self):
        self.operaria_rol.activo = False
        self.operaria_rol.save()
        self.assertFalse(CrearUsuarioForm(self.datos()).is_valid())
        with self.assertRaisesRegex(ValidationError, "no está disponible"):
            self.crear()
        with self.assertRaises(ValidationError):
            services.cambiar_rol(actor=self.jefe, usuario=self.encargada, rol=self.operaria_rol)

    def test_rol_ajeno_no_se_asigna(self):
        rol = Rol.objects.create(codigo="CONSULTA", nombre="Consulta")
        with self.assertRaises(ValidationError):
            self.crear(rol=rol)
        with self.assertRaises(ValidationError):
            services.cambiar_rol(actor=self.jefe, usuario=self.operaria, rol=rol)

    def test_login_temporal_fuerza_cambio_y_descarta_next(self):
        usuario = self.crear()
        self.client.logout()
        response = self.client.post(self.url("login") + "?next=/usuarios/",
                                    {"username": usuario.username, "password": CLAVE})
        self.assertRedirects(response, self.url("cambiar_contrasena"))
        for url in (self.url("inicio"), "/trazabilidad/recepciones/", "/admin/", self.url("crear_usuario")):
            self.assertRedirects(self.client.get(url), self.url("cambiar_contrasena"))
        self.assertRedirects(self.client.post(self.url("crear_usuario"), self.datos()), self.url("cambiar_contrasena"))
        self.assertRedirects(self.client.post(self.url("logout")), self.url("login"))

    def test_cambio_obligatorio_completo_conserva_sesion(self):
        usuario = self.crear()
        self.client.force_login(usuario)
        response = self.client.post(self.url("cambiar_contrasena"),
                                   dict(old_password=CLAVE, new_password1=NUEVA, new_password2=NUEVA), follow=True)
        self.assertContains(response, "Contraseña actualizada correctamente.")
        usuario.refresh_from_db()
        self.assertFalse(usuario.cambio_contrasena_pendiente)
        self.assertTrue(usuario.check_password(NUEVA))
        self.assertFalse(usuario.check_password(CLAVE))
        self.assertEqual(self.client.session["_auth_user_id"], str(usuario.pk))
        self.assertEqual(self.client.get(self.url("inicio")).status_code, 200)

    def test_cambio_propio_valida_actual_nueva_y_diferente(self):
        usuario = self.crear()
        self.client.force_login(usuario)
        for actual, nueva in [("incorrecta", NUEVA), (CLAVE, "123"), (CLAVE, CLAVE)]:
            response = self.client.post(self.url("cambiar_contrasena"),
                                       dict(old_password=actual, new_password1=nueva, new_password2=nueva))
            self.assertTrue(response.context["form"].errors)
        usuario.refresh_from_db()
        self.assertTrue(usuario.cambio_contrasena_pendiente)

    def test_cambio_rol_y_auditoria(self):
        response = self.client.post(self.url("editar_rol", self.operaria), {"rol": self.encargada_rol.pk}, follow=True)
        self.assertContains(response, "Rol actualizado correctamente.")
        self.operaria.refresh_from_db()
        self.assertEqual(self.operaria.rol, self.encargada_rol)
        evento = self.operaria.eventos.get()
        self.assertEqual(evento.actor, self.jefe)
        self.assertIn(self.operaria_rol.nombre, evento.rol_anterior)
        self.assertIn(self.encargada_rol.nombre, evento.rol_nuevo)

    def test_historico_operativo_y_creador_conservados(self):
        recepcion = Recepcion.objects.create(registrado_por=self.operaria, fecha_hora_recepcion=timezone.now())
        correccion = Correccion.objects.create(usuario=self.operaria, entidad_afectada="Recepcion",
                                              identificador_registro=str(recepcion.pk), campo="observaciones",
                                              valor_anterior="", valor_nuevo="Histórico", motivo="Prueba")
        creado = self.crear()
        snapshot = list(Recepcion.objects.values())
        services.cambiar_rol(actor=self.jefe, usuario=self.operaria, rol=self.encargada_rol)
        services.cambiar_estado(actor=self.jefe, usuario=self.operaria, activo=False)
        services.cambiar_estado(actor=self.jefe, usuario=self.operaria, activo=True)
        self.assertEqual(snapshot, list(Recepcion.objects.values()))
        correccion.refresh_from_db()
        creado.refresh_from_db()
        self.assertEqual(correccion.usuario_id, self.operaria.pk)
        self.assertEqual(creado.creado_por_id, self.jefe.pk)
        response = self.client.get(reverse("trazabilidad:detalle_recepcion", args=[recepcion.pk]))
        self.assertEqual(response.status_code, 200)

    def test_desactivar_bloquea_login_y_sesion_existente(self):
        otro = Client()
        otro.force_login(self.operaria)
        response = self.client.post(self.url("desactivar_usuario", self.operaria), follow=True)
        self.assertContains(response, "Usuario desactivado correctamente.")
        self.operaria.refresh_from_db()
        self.assertFalse(self.operaria.is_active)
        self.assertIsNone(authenticate(username=self.operaria.username, password=CLAVE))
        self.assertEqual(otro.get(self.url("inicio")).status_code, 302)
        self.assertContains(self.client.get(self.url("lista_usuarios") + "?estado=inactivo"), self.operaria.username)

    def test_reactivar_mismo_pk_rol_e_historial(self):
        services.cambiar_estado(actor=self.jefe, usuario=self.operaria, activo=False)
        self.client.post(self.url("reactivar_usuario", self.operaria))
        self.operaria.refresh_from_db()
        self.assertTrue(self.operaria.is_active)
        self.assertEqual(self.operaria.rol, self.operaria_rol)
        self.assertIsNotNone(authenticate(username=self.operaria.username, password=CLAVE))
        self.assertEqual(list(self.operaria.eventos.values_list("tipo", flat=True)),
                         [EventoUsuario.Tipo.REACTIVACION, EventoUsuario.Tipo.DESACTIVACION])

    def test_no_desactivarse_a_si_mismo(self):
        with self.assertRaisesRegex(ValidationError, "No puede desactivar su propia cuenta"):
            services.cambiar_estado(actor=self.jefe, usuario=self.jefe, activo=False)
        self.assertContains(self.client.post(self.url("desactivar_usuario", self.jefe), follow=True),
                            "No puede desactivar su propia cuenta.")

    def test_ultimo_jefe_no_se_desactiva_ni_pierde_rol(self):
        with self.assertRaisesRegex(ValidationError, "Debe existir al menos un usuario Jefe activo"):
            services.cambiar_estado(actor=self.tecnico, usuario=self.jefe, activo=False)
        for actor in (self.jefe, self.tecnico):
            with self.assertRaisesRegex(ValidationError, "Debe existir al menos un usuario Jefe activo"):
                services.cambiar_rol(actor=actor, usuario=self.jefe, rol=self.operaria_rol)

    def test_jefes_inactivos_y_superusuarios_no_cuentan(self):
        Usuario.objects.create_user(username="jefe_inactivo", rol=self.jefe_rol, is_active=False)
        self.tecnico.rol = self.jefe_rol
        self.tecnico.save()
        with self.assertRaises(ValidationError):
            services.cambiar_rol(actor=self.tecnico, usuario=self.jefe, rol=self.encargada_rol)

    def test_dos_jefes_permiten_desactivar_uno(self):
        segundo = Usuario.objects.create_user(username="jefe2", rol=self.jefe_rol)
        services.cambiar_estado(actor=self.jefe, usuario=segundo, activo=False)
        segundo.refresh_from_db()
        self.assertFalse(segundo.is_active)

    def test_autocambio_con_otro_jefe_preserva_sesion_y_retira_acceso(self):
        Usuario.objects.create_user(username="jefe2", rol=self.jefe_rol)
        response = self.client.post(self.url("editar_rol", self.jefe), {"rol": self.operaria_rol.pk})
        self.assertRedirects(response, self.url("inicio"))
        self.assertEqual(self.client.session["_auth_user_id"], str(self.jefe.pk))
        self.assertEqual(self.client.get(self.url("lista_usuarios")).status_code, 403)

    def test_reset_hash_pendiente_y_revoca_sesion_anterior(self):
        otro = Client()
        otro.force_login(self.operaria)
        response = self.client.post(self.url("reset_contrasena", self.operaria),
                                    dict(password1=NUEVA, password2=NUEVA), follow=True)
        self.assertContains(response, "Contraseña restablecida correctamente.")
        self.assertNotContains(response, NUEVA)
        self.operaria.refresh_from_db()
        self.assertFalse(self.operaria.check_password(CLAVE))
        self.assertTrue(self.operaria.check_password(NUEVA))
        self.assertTrue(self.operaria.cambio_contrasena_pendiente)
        self.assertNotEqual(self.operaria.password, NUEVA)
        self.assertEqual(otro.get(self.url("inicio")).status_code, 302)
        self.assertIsNotNone(authenticate(username=self.operaria.username, password=NUEVA))
        self.assertEqual(self.operaria.eventos.get().tipo, EventoUsuario.Tipo.RESET)

    def test_reset_inactivo_no_reactiva(self):
        services.cambiar_estado(actor=self.jefe, usuario=self.operaria, activo=False)
        services.restablecer_contrasena(actor=self.jefe, usuario=self.operaria, password=NUEVA)
        self.operaria.refresh_from_db()
        self.assertFalse(self.operaria.is_active)
        self.assertTrue(self.operaria.cambio_contrasena_pendiente)
        self.assertIsNone(authenticate(username=self.operaria.username, password=NUEVA))

    def test_reset_propio_no_se_permite(self):
        with self.assertRaises(ValidationError):
            services.restablecer_contrasena(actor=self.jefe, usuario=self.jefe, password=NUEVA)
        self.assertRedirects(self.client.get(self.url("reset_contrasena", self.jefe)), self.url("cambiar_contrasena"))

    def test_reset_debil_rechazado(self):
        with self.assertRaises(ValidationError):
            services.restablecer_contrasena(actor=self.jefe, usuario=self.operaria, password="123")
        self.operaria.refresh_from_db()
        self.assertTrue(self.operaria.check_password(CLAVE))

    def test_reset_rechaza_reutilizar_la_contrasena_actual(self):
        with self.assertRaisesRegex(ValidationError, "distinta de la actual"):
            services.restablecer_contrasena(actor=self.jefe, usuario=self.operaria, password=CLAVE)
        self.assertEqual(self.operaria.eventos.count(), 0)

    def test_cuentas_tecnicas_no_gestionables_por_operacion(self):
        self.operaria.is_staff = True
        self.operaria.save()
        for cuenta in (self.tecnico, self.operaria):
            for actor in (self.jefe, self.tecnico):
                with self.assertRaises(ValidationError):
                    services.restablecer_contrasena(actor=actor, usuario=cuenta, password=NUEVA)
                with self.assertRaises(ValidationError):
                    services.cambiar_rol(actor=actor, usuario=cuenta, rol=self.jefe_rol)
                with self.assertRaises(ValidationError):
                    services.cambiar_estado(actor=actor, usuario=cuenta, activo=False)
            self.assertContains(self.client.get(self.url("detalle_usuario", cuenta)), "Cuenta técnica")

    def test_superuser_sin_rol_funciona_y_puede_crear_primer_jefe(self):
        self.tecnico.cambio_contrasena_pendiente = True
        self.tecnico.save()
        self.client.force_login(self.tecnico)
        self.assertContains(self.client.get(self.url("lista_usuarios")), "Administrador técnico")
        usuario = self.crear(actor=self.tecnico, rol=self.jefe_rol)
        self.assertEqual(usuario.rol, self.jefe_rol)
        self.tecnico.refresh_from_db()
        self.assertIsNone(self.tecnico.rol)
        self.assertTrue(self.tecnico.is_staff)
        self.assertTrue(self.tecnico.is_superuser)

    def test_post_y_csrf_en_cambios_de_estado(self):
        for nombre in ("desactivar_usuario", "reactivar_usuario"):
            self.assertEqual(self.client.get(self.url(nombre, self.operaria)).status_code, 405)
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.jefe)
        for nombre in ("desactivar_usuario", "reactivar_usuario", "editar_rol", "reset_contrasena"):
            self.assertEqual(cliente.post(self.url(nombre, self.operaria)).status_code, 403)
        self.assertEqual(cliente.post(self.url("crear_usuario"), self.datos()).status_code, 403)

    def test_sin_borrado_ni_edicion_username(self):
        detalle = self.client.get(self.url("detalle_usuario", self.operaria))
        self.assertNotContains(detalle, "Eliminar")
        self.assertEqual(self.client.post(f"/usuarios/{self.operaria.pk}/eliminar/").status_code, 404)
        self.client.post(self.url("editar_rol", self.operaria), {"rol": self.encargada_rol.pk, "username": "cambiado"})
        self.operaria.refresh_from_db()
        self.assertEqual(self.operaria.username, "operaria")

    def test_listado_filtros_paginacion_y_roles_historicos(self):
        for numero in range(22):
            Usuario.objects.create_user(username=f"filtro{numero:02}", first_name="Persona",
                                        rol=self.operaria_rol, is_active=False)
        self.operaria_rol.activo = False
        self.operaria_rol.save()
        response = self.client.get(self.url("lista_usuarios"), dict(q="Persona", rol="OPERARIA", estado="inactivo"))
        self.assertEqual(response.context["pagina"].paginator.count, 22)
        self.assertEqual(len(response.context["pagina"]), 20)
        self.assertContains(response, "rol inactivo")
        self.assertContains(response, "q=Persona&amp;rol=OPERARIA&amp;estado=inactivo&amp;page=2")
        response = self.client.get(self.url("lista_usuarios"), dict(q="Persona", page="2"))
        self.assertEqual(len(response.context["pagina"]), 2)

    def test_auditoria_completa_sin_credenciales(self):
        usuario = self.crear()
        services.cambiar_rol(actor=self.jefe, usuario=usuario, rol=self.encargada_rol)
        services.restablecer_contrasena(actor=self.jefe, usuario=usuario, password=NUEVA)
        services.cambiar_estado(actor=self.jefe, usuario=usuario, activo=False)
        services.cambiar_estado(actor=self.jefe, usuario=usuario, activo=True)
        eventos = list(usuario.eventos.all())
        self.assertEqual(len(eventos), 5)
        self.assertTrue(all(e.actor_id == self.jefe.pk and e.fecha_hora for e in eventos))
        usuario.refresh_from_db()
        contenido = str(list(usuario.eventos.values()))
        for secreto in (CLAVE, NUEVA, usuario.password):
            self.assertNotIn(secreto, contenido)
        response = self.client.get(self.url("detalle_usuario", usuario))
        for evento in eventos:
            self.assertContains(response, evento.get_tipo_display())

    def test_auditoria_append_only(self):
        evento = self.crear().eventos.get()
        with self.assertRaises(ValueError):
            evento.save()
        with self.assertRaises(ValueError):
            evento.delete()
        with self.assertRaises(ValueError):
            EventoUsuario.objects.filter(pk=evento.pk).update(tipo="RESET")
        with self.assertRaises(ValueError):
            EventoUsuario.objects.all().delete()

    def test_fallo_de_auditoria_revierte_mutacion(self):
        with patch("apps.usuarios.services.EventoUsuario.objects.create", side_effect=RuntimeError("fallo")):
            with self.assertRaises(RuntimeError):
                services.cambiar_estado(actor=self.jefe, usuario=self.operaria, activo=False)
        self.operaria.refresh_from_db()
        self.assertTrue(self.operaria.is_active)

    def test_no_eventos_para_acciones_sin_cambio(self):
        services.cambiar_rol(actor=self.jefe, usuario=self.operaria, rol=self.operaria_rol)
        services.cambiar_estado(actor=self.jefe, usuario=self.operaria, activo=True)
        self.assertEqual(EventoUsuario.objects.count(), 0)

    def test_admin_tecnico_sin_eliminacion_y_historial_solo_lectura(self):
        request = RequestFactory().get("/admin/")
        request.user = self.tecnico
        for modelo in (Rol, Usuario, EventoUsuario):
            model_admin = admin.site._registry[modelo]
            self.assertTrue(model_admin.has_view_permission(request))
            self.assertFalse(model_admin.has_delete_permission(request))
        eventos_admin = admin.site._registry[EventoUsuario]
        self.assertFalse(eventos_admin.has_add_permission(request))
        self.assertFalse(eventos_admin.has_change_permission(request))
        request.user = self.jefe
        self.assertFalse(admin.site._registry[Usuario].has_change_permission(request))


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class UltimoJefeConcurrenciaTests(TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_dos_autocambios_simultaneos_conservan_un_jefe(self):
        jefe_rol, _ = Rol.objects.get_or_create(codigo="JEFE", defaults={"nombre": "Jefe"})
        operaria_rol, _ = Rol.objects.get_or_create(codigo="OPERARIA", defaults={"nombre": "Operaria"})
        jefes = [Usuario.objects.create_user(username=f"concurrente{i}", rol=jefe_rol) for i in range(2)]
        barrera = Barrier(2)

        def cambiar(pk):
            close_old_connections()
            try:
                usuario = Usuario.objects.get(pk=pk)
                barrera.wait(timeout=10)
                try:
                    services.cambiar_rol(actor=usuario, usuario=usuario, rol=operaria_rol)
                    return "cambiado"
                except ValidationError:
                    return "protegido"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            resultados = list(pool.map(cambiar, [u.pk for u in jefes]))
        self.assertCountEqual(resultados, ["cambiado", "protegido"])
        self.assertEqual(Usuario.objects.filter(is_active=True, rol__codigo="JEFE").count(), 1)
