import re
from types import SimpleNamespace

from django.contrib.auth.models import AnonymousUser
from django.template import Context, Template
from django.test import RequestFactory, TestCase
from django.urls import resolve, reverse

from apps.trazabilidad.models import Especie, SolicitudRuta
from .models import Rol, Usuario
from .templatetags.navegacion import ENLACES, menu_lateral


class SidebarTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.jefe = Usuario.objects.create_user(username='menu_jefe', rol=Rol.objects.get(codigo='JEFE'))
        cls.encargada = Usuario.objects.create_user(username='menu_encargada', rol=Rol.objects.get(codigo='ENCARGADA'))
        cls.operaria = Usuario.objects.create_user(username='menu_operaria', rol=Rol.objects.get(codigo='OPERARIA'))
        cls.superusuario = Usuario.objects.create_superuser(username='menu_super')
        cls.sin_rol = Usuario(username='menu_sin_rol')
        especie = Especie.objects.create(nombre='Especie menú')
        for usuario in (cls.encargada, cls.operaria, cls.operaria):
            SolicitudRuta.objects.create(solicitante=usuario, especie=especie)
        SolicitudRuta.objects.create(solicitante=cls.operaria, especie=especie, estado='RECHAZADA')

    def request(self, user, actual='usuarios:inicio'):
        request = RequestFactory().get('/')
        request.user = user
        request.resolver_match = SimpleNamespace(view_name=actual)
        return request

    def enlaces(self, datos):
        return [e for g in datos['grupos'] for e in g['enlaces']]

    def test_orden_y_visibilidad_por_rol(self):
        for usuario, esperado in (
            (self.jefe, [e[0] for e in ENLACES]),
            (self.superusuario, [e[0] for e in ENLACES]),
            (self.encargada, ['consulta', 'solicitudes', 'recepciones', 'seguimiento', 'lotes', 'cajas', 'inventario', 'despachos']),
            (self.operaria, ['solicitudes', 'seguimiento', 'cajas', 'inventario']),
            (self.sin_rol, []), (AnonymousUser(), []),
        ):
            with self.subTest(usuario=usuario):
                datos = menu_lateral({'request': self.request(usuario)})
                self.assertEqual([e['clave'] for e in self.enlaces(datos)], esperado)
                self.assertEqual(datos['mostrar_inicio'], usuario in (self.encargada, self.operaria, self.sin_rol))

    def test_activo_exacto_y_unico_en_paginas_y_subpaginas(self):
        nombres = []
        for clave, _, _, paginas, _ in ENLACES:
            for pagina in paginas:
                nombres.append(pagina)
                datos = menu_lateral({'request': self.request(self.jefe, pagina)})
                self.assertEqual([e['clave'] for e in self.enlaces(datos) if e['activo']], [clave])
        self.assertEqual(len(nombres), len(set(nombres)))
        for pagina in ('usuarios:cambiar_contrasena', 'otra:lista', 'trazabilidad:ruta_inexistente'):
            self.assertFalse(any(e['activo'] for e in self.enlaces(menu_lateral({'request': self.request(self.jefe, pagina)}))))

    def test_contador_respeta_propietario_y_una_consulta_por_request(self):
        for usuario, esperado in ((self.jefe, 3), (self.superusuario, 3), (self.encargada, 1), (self.operaria, 2)):
            request = self.request(usuario)
            with self.assertNumQueries(1):
                datos = menu_lateral({'request': request})
                menu_lateral({'request': request})
            solicitud = next(e for e in self.enlaces(datos) if e['clave'] == 'solicitudes')
            self.assertEqual(solicitud['pendientes'], esperado)
        with self.assertNumQueries(0):
            menu_lateral({'request': self.request(AnonymousUser())})

    def test_html_iconos_activo_y_contador_cero_oculto(self):
        template = Template('{% load navegacion %}{% menu_lateral %}')
        html = template.render(Context({'request': self.request(self.jefe, 'trazabilidad:resolver_solicitud')}))
        self.assertEqual(html.count('aria-current="page"'), 1)
        self.assertEqual(html.count('<svg '), 12)
        self.assertEqual(html.count('width="20" height="20"'), 12)
        self.assertIn('3<span class="sr-only"> solicitudes pendientes', html)
        SolicitudRuta.objects.all().delete()
        html = template.render(Context({'request': self.request(self.jefe)}))
        self.assertNotIn('solicitudes pendientes', html)

    def test_destinos_visibles_resuelven_y_permiten_get(self):
        for usuario in (self.jefe, self.superusuario, self.encargada, self.operaria):
            self.client.force_login(usuario)
            datos = menu_lateral({'request': self.request(usuario)})
            for enlace in self.enlaces(datos):
                with self.subTest(usuario=usuario.username, destino=enlace['destino']):
                    url = reverse(enlace['destino'])
                    self.assertEqual(resolve(url).view_name, enlace['destino'])
                    self.assertEqual(self.client.get(url).status_code, 200)

    def test_base_conserva_logout_post_y_drawer(self):
        self.client.force_login(self.operaria)
        response = self.client.get(reverse('usuarios:inicio'))
        self.assertContains(response, 'id="sidebar-overlay"')
        self.assertContains(response, 'aria-controls="sidebar"')
        self.assertContains(response, f'method="post" action="{reverse("usuarios:logout")}"')
        nav = re.search(r'<nav.*?</nav>', response.content.decode(), re.S)[0]
        self.assertEqual(nav.count('aria-current="page"'), 1)
