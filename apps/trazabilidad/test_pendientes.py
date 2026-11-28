from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.usuarios.models import Rol, Usuario

from .formato import hace
from .pendientes import pendientes_de_planta
from .test_procesamiento import DatosProcesamiento


class HaceTests(TestCase):
    def test_redondea_a_lo_que_sirve_para_priorizar(self):
        ahora = timezone.now()
        self.assertEqual(hace(ahora, ahora), "recién")
        self.assertEqual(hace(ahora - timedelta(minutes=12), ahora), "hace 12 min")
        self.assertEqual(hace(ahora - timedelta(hours=5, minutes=40), ahora), "hace 5 h")
        self.assertEqual(hace(ahora - timedelta(days=1, hours=3), ahora), "hace 1 día")
        self.assertEqual(hace(ahora - timedelta(days=4), ahora), "hace 4 días")
        self.assertEqual(hace(None, ahora), "")


class PendientesDePlantaTests(DatosProcesamiento, TestCase):
    def setUp(self):
        self.preparar()
        encargada, _ = Rol.objects.get_or_create(codigo="ENCARGADA", defaults={"nombre": "Encargada"})
        self.encargada = Usuario.objects.create_user(username="encargada_pendientes", first_name="Carolina", rol=encargada)

    def grupos(self, usuario=None):
        return {g["clave"]: g for g in pendientes_de_planta(usuario or self.encargada)["grupos"]}

    def test_cada_etapa_aparece_en_su_grupo_con_su_siguiente_paso(self):
        self.assertEqual(list(self.grupos()), ["DISPONIBLE"])
        self.iniciar()
        self.assertEqual(list(self.grupos()), ["EN_PROCESO"])
        self.completar()
        self.enviar()
        grupo = self.grupos()["EN_CONGELACION"]
        self.assertEqual(grupo["total"], 1)
        self.assertTrue(grupo["urgente"])
        self.assertIn("Túnel de congelado 1", grupo["items"][0].contexto)

        self.client.force_login(self.encargada)
        respuesta = self.client.get(reverse("usuarios:inicio"))
        self.assertContains(respuesta, "1 pendiente en planta")
        self.assertContains(respuesta, "En el túnel")
        self.assertContains(respuesta, "Sacar del túnel")
        self.assertContains(respuesta, f"{self.partida.proveedor} · <time")
        self.assertNotContains(respuesta, f"Seguimiento #{self.partida.pk}")

    def test_avisa_cuando_se_pasa_el_tiempo_recomendado_en_el_tunel(self):
        self.tunel.umbral_alerta_horas = Decimal("1")
        self.tunel.save()
        self.iniciar()
        self.completar()
        self.enviar()
        item = pendientes_de_planta(self.encargada, ahora=timezone.now() + timedelta(hours=3))["grupos"][0]["items"][0]
        self.assertTrue(item.alerta)
        self.assertIn("Ya pasó el tiempo recomendado", item.contexto)

    def test_la_operaria_no_ve_lo_que_solo_puede_asignar_la_encargada(self):
        self.iniciar()
        self.completar()
        self.enviar()
        from .services import registrar_peso_postproceso, retirar_de_tunel
        retirar_de_tunel(partida=self.partida, usuario=self.usuario)
        self.assertIn("PENDIENTE_PESAJE", self.grupos())
        registrar_peso_postproceso(partida=self.partida, peso_kg=Decimal("300"), usuario=self.usuario)
        self.assertIn("empaque", self.grupos(self.encargada))
        self.assertNotIn("empaque", self.grupos(self.usuario))

    def test_muestra_como_maximo_cinco_por_grupo_y_enlaza_al_resto(self):
        for _ in range(6):
            self.crear_partida()
        grupo = self.grupos()["DISPONIBLE"]
        self.assertEqual(grupo["total"], 7)
        self.assertEqual(len(grupo["items"]), 5)
        self.assertEqual(grupo["enlace_todos"], reverse("trazabilidad:lista_partidas") + "?categoria=todos&situacion=DISPONIBLE")

    def test_consultas_no_crecen_con_la_cantidad_de_pendientes(self):
        self.crear_partida()
        self.client.force_login(self.encargada)
        antes = self._contar()
        for _ in range(4):
            self.crear_partida()
        self.assertEqual(self._contar(), antes)

    def _contar(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as consultas:
            self.client.get(reverse("usuarios:inicio"))
        return len(consultas)
