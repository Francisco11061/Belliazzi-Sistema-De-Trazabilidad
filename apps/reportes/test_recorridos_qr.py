from decimal import Decimal
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from apps.trazabilidad.models import ConsumoLote
from apps.trazabilidad.test_qr_cajas import DatosQR
from apps.usuarios.models import Usuario, Rol


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class RecorridosQRTests(DatosQR, TestCase):
    def setUp(self):
        self.preparar_qr()
        self.encargada = Usuario.objects.create_user(username="qr_encargada", rol=Rol.objects.get(codigo="ENCARGADA"))

    def test_qr_consultas_acotadas_segun_perfil_sin_n_mas_uno(self):
        cantidades = []
        for usuario in (self.usuario, self.encargada, self.jefe):
            self.client.force_login(usuario)
            with CaptureQueriesContext(connection) as antes:
                respuesta = self.client.get(self.consulta())
            self.assertEqual(respuesta.status_code, 200)
            # Operario conserva la consulta operativa; gestión abre el historial completo.
            self.assertLessEqual(len(antes), 11 if usuario == self.usuario else 40)
            cantidades.append(len(antes))
        tercera = self.otra_lista("10")
        ConsumoLote.objects.create(lote_produccion=self.lote, partida=tercera, cantidad_kg_utilizada=Decimal("10"))
        with self.assertNumQueries(cantidades[-1]):
            self.client.get(self.consulta())

    def test_inicio_con_acceso_directo_a_registro_y_rutas_por_perfil(self):
        for usuario in (self.usuario,self.encargada,self.jefe):
            self.client.force_login(usuario)
            respuesta=self.client.get(reverse('usuarios:inicio'), follow=True)
            if usuario == self.jefe:
                self.assertEqual(respuesta.redirect_chain, [(reverse('reportes:dashboard'), 302)])
                self.assertContains(respuesta, reverse('producto_terminado:lista_cajas'))
            else:
                self.assertContains(respuesta, reverse('producto_terminado:seleccionar_lote_caja'))
            self.assertContains(respuesta, reverse('trazabilidad:lista_partidas'))
            self.assertContains(respuesta, reverse('inventario:lista'))
            if usuario != self.usuario:
                self.assertContains(respuesta, reverse('inventario:crear_despacho'))

    def test_historico_cerrado_sin_responsable_no_rompe_detalle(self):
        self.client.force_login(self.usuario)
        respuesta=self.client.get(reverse('trazabilidad:detalle_partida',args=[self.partida.pk]))
        self.assertContains(respuesta,'Responsable no documentado')
