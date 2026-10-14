from django.test import TestCase
from django.urls import reverse
from apps.usuarios.models import Rol, Usuario
from .test_almacenamiento import DatosInventario


class FiltroInicialTests(DatosInventario, TestCase):
    def setUp(self):
        self.preparar_inventario()

    def test_default_por_disponibilidad_sin_alterar_totales_y_todo_explicito(self):
        otra = self.caja()
        self.ingresar(caja=otra)
        for codigo in ('OPERARIA', 'ENCARGADA', 'JEFE'):
            usuario = Usuario.objects.create_user(username='inventario_'+codigo, rol=Rol.objects.get(codigo=codigo))
            self.client.force_login(usuario)
            respuesta = self.client.get(reverse('inventario:lista'))
            self.assertEqual(respuesta.context['estado_actual'], 'PENDIENTE')
            self.assertEqual([c.pk for c in respuesta.context['page_obj']], [self.registro.pk])
            self.assertEqual(respuesta.context['stock']['cajas'], 1)
            todo = self.client.get(reverse('inventario:lista'), {'estado': ''})
            self.assertEqual(todo.context['page_obj'].paginator.count, 2)
        self.ingresar()
        respuesta = self.client.get(reverse('inventario:lista'))
        self.assertEqual(respuesta.context['estado_actual'], 'ALMACENADA')
        self.assertEqual(respuesta.context['page_obj'].paginator.count, 2)
