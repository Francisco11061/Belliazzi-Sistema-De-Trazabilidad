from django.test import TestCase
from django.urls import reverse
from apps.usuarios.models import Rol, Usuario
from .test_cajas import DatosCajas


class SiguientePasoTests(DatosCajas, TestCase):
    def setUp(self):
        self.preparar_cajas(total="100")

    def test_asignacion_disponible_segun_rol_en_ambas_vistas_y_detalle(self):
        destino = reverse("producto_terminado:crear_lote", args=[self.partida.pk])
        for codigo in ("OPERARIA", "ENCARGADA", "JEFE"):
            cuenta = Usuario.objects.create_user(username=f"paso_{codigo}", rol=Rol.objects.get(codigo=codigo))
            self.client.force_login(cuenta)
            urls = [reverse("trazabilidad:lista_partidas") + "?vista=" + vista for vista in ("tabla", "tarjetas")]
            urls.append(reverse("trazabilidad:detalle_partida", args=[self.partida.pk]))
            for url in urls:
                with self.subTest(rol=codigo, url=url):
                    respuesta = self.client.get(url)
                    if codigo == "OPERARIA":
                        self.assertNotContains(respuesta, f'href="{destino}"')
                    else:
                        self.assertContains(respuesta, f'href="{destino}"', count=1)
