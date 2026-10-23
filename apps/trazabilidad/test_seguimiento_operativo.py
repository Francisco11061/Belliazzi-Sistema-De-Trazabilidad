from datetime import timedelta
from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.usuarios.models import Rol, Usuario
from .models import ConsumoLote, EstanciaPartida, PartidaProceso, Pesaje, UnidadFrio
from .seguimiento_operativo import seguimientos_operativos
from .services import enviar_a_mantencion, retirar_de_mantencion, retirar_de_tunel
from .test_lotes import DatosLotes


class SeguimientoOperativoTests(DatosLotes, TestCase):
    def setUp(self):
        self.preparar()
        self.client.force_login(self.jefe)
        self.listado = reverse("trazabilidad:lista_partidas")

    def fila(self, partida=None):
        return seguimientos_operativos().get(pk=(partida or self.partida).pk)

    def pagina(self, **parametros):
        respuesta = self.client.get(self.listado, parametros)
        self.assertEqual(respuesta.status_code, 200)
        return respuesta

    def test_predeterminada_y_recorrido_productivo(self):
        self.assertEqual(self.pagina().context["categoria"], "atender")
        self.assertContains(self.pagina(partida=self.partida.pk), "Enviar a proceso")
        self.assertEqual(self.fila().categoria_operativa, "atender")
        camara = UnidadFrio.objects.create(nombre="Mantención operativa", tipo="MANTENCION")
        enviar_a_mantencion(partida=self.partida, cantidad_kg=Decimal("500"), unidad=camara, usuario=self.usuario)
        self.assertEqual(self.fila().situacion, "EN_MANTENCION")
        self.assertContains(self.pagina(), "Retirar de cámara")
        retirar_de_mantencion(partida=self.partida, cantidad_kg=Decimal("500"), usuario=self.usuario)
        self.iniciar()
        self.empezar()
        self.terminar()
        self.assertEqual(self.fila().categoria_operativa, "atender")
        self.assertEqual(self.pagina(categoria="empaque").context["page_obj"].paginator.count, 0)
        self.assertContains(self.pagina(), "Gestionar procesamiento")
        for indice in range(1, len(self.etapas)):
            self.empezar(indice)
            self.terminar(indice)
        self.enviar()
        self.assertEqual(self.fila().situacion, "EN_CONGELACION")
        self.assertContains(self.pagina(), "Revisar retiro del túnel")
        retirar_de_tunel(partida=self.partida, usuario=self.usuario)
        self.assertEqual(self.fila().categoria_operativa, "atender")
        self.assertContains(self.pagina(), "Registrar peso postproceso")
        self.pesar()
        self.assertEqual(self.fila().categoria_operativa, "empaque")
        self.assertContains(self.pagina(categoria="empaque"), "Asignar a lote")

    def test_saldo_parcial_y_total_sin_cambiar_estado_persistido(self):
        self.pendiente()
        self.pesar()
        self.crear_lote(cantidad_kg=Decimal("100"))
        fila = self.fila()
        self.assertEqual(fila.disponible_lote_kg, Decimal("25"))
        self.assertEqual(fila.categoria_operativa, "empaque")
        self.assertContains(self.pagina(categoria="empaque", vista="tarjetas"), "25,00")
        self.crear_lote(cantidad_kg=Decimal("25"))
        self.assertEqual(self.fila().categoria_operativa, "historial")
        self.assertEqual(self.fila().estado, "ACTIVA")
        respuesta = self.pagina(categoria="historial")
        self.assertContains(respuesta, "Asignado por completo")
        self.assertNotContains(respuesta, ">Asignar a lote")

    def test_incompletos_y_saldos_invalidos_no_son_historial(self):
        self.pendiente()
        self.pesar()
        original = self.fila()
        PartidaProceso.objects.filter(pk=self.partida.pk).update(ruta_proceso=None)
        self.assertEqual(self.fila().categoria_operativa, "atender")
        self.assertTrue(self.fila().revision_operativa)
        PartidaProceso.objects.filter(pk=self.partida.pk).update(ruta_proceso=self.ruta)
        extra = Pesaje.objects.create(partida=self.partida, tipo=Pesaje.POSTPROCESO,
            peso_kg=Decimal("125"), fecha_hora_evento=timezone.now(), registrado_por=self.usuario)
        self.assertEqual(self.fila().categoria_operativa, "atender")
        extra.delete()
        lote = self.crear_lote(cantidad_kg=Decimal("125"))
        ConsumoLote.objects.filter(lote_produccion=lote).update(cantidad_kg_utilizada=Decimal("126"))
        self.assertEqual(self.fila().categoria_operativa, "atender")
        self.assertContains(self.pagina(), "Revisa la ruta, el pesaje y las asignaciones")
        self.assertEqual(original.situacion, self.fila().situacion)
        # Un peso histórico inválido de cero no demuestra una asignación completa.
        ConsumoLote.objects.filter(lote_produccion=lote).delete()
        Pesaje.objects.filter(partida=self.partida, tipo=Pesaje.POSTPROCESO).update(peso_kg=0)
        self.assertEqual(self.fila().categoria_operativa, "atender")

    def test_no_operable_y_activo_con_hijas_siguen_localizables(self):
        PartidaProceso.objects.filter(pk=self.partida.pk).update(ruta_proceso=self.ruta)
        self.assertEqual(self.fila().situacion, "NO_OPERABLE")
        respuesta = self.pagina(situacion="NO_OPERABLE")
        self.assertContains(respuesta, "Revisar seguimiento")
        self.assertContains(respuesta, "El historial está incompleto")
        self.assertNotContains(respuesta, ">Enviar a proceso")
        PartidaProceso.objects.filter(pk=self.partida.pk).update(ruta_proceso=None)
        PartidaProceso.objects.create(detalle_recepcion=self.partida.detalle_recepcion,
            partida_padre=self.partida, cantidad_inicial_kg=Decimal("20"), creado_por=self.usuario)
        self.assertEqual(self.fila().categoria_operativa, "atender")
        self.assertEqual(self.fila().situacion, "NO_OPERABLE")

    def test_historial_incluye_todos_los_estados_terminales_sin_acciones(self):
        for estado in ("DIVIDIDA", "CONSUMIDA", "CERRADA"):
            with self.subTest(estado=estado):
                PartidaProceso.objects.filter(pk=self.partida.pk).update(estado=estado)
                self.assertEqual(self.fila().categoria_operativa, "historial")
                self.assertEqual(self.pagina().context["page_obj"].paginator.count, 0)
                respuesta = self.pagina(categoria="historial", situacion=estado)
                self.assertEqual(respuesta.context["page_obj"].paginator.count, 1)
                for accion in (">Enviar a proceso", ">Enviar a cámara", ">Asignar a lote", ">Registrar pérdida"):
                    self.assertNotContains(respuesta, accion)
                self.assertEqual(self.client.get(reverse("trazabilidad:detalle_partida", args=[self.partida.pk])).status_code, 200)

    def test_frio_inconsistente_no_ofrece_retiro(self):
        for _ in range(2):
            EstanciaPartida.objects.create(partida=self.partida, unidad_frio=self.tunel,
                fecha_hora_ingreso=timezone.now(), ingresado_por=self.usuario)
        respuesta = self.pagina()
        self.assertEqual(self.fila().categoria_operativa, "atender")
        self.assertContains(respuesta, "Revisar seguimiento")
        self.assertNotContains(respuesta, "Revisar retiro del túnel")
        self.assertNotContains(respuesta, ">Registrar pérdida")

    def test_busqueda_filtros_contadores_y_paginacion_conservan_estado(self):
        for _ in range(12):
            PartidaProceso.objects.create(detalle_recepcion=self.partida.detalle_recepcion,
                cantidad_inicial_kg=Decimal("10"), creado_por=self.usuario)
        ultima = PartidaProceso.objects.latest("pk")
        PartidaProceso.objects.filter(pk=ultima.pk).update(estado="CERRADA")
        parametros = dict(q=self.partida.detalle_recepcion.recepcion_id, especie=self.especie.pk,
                          categoria="atender", vista="tarjetas", page=2)
        respuesta = self.pagina(**parametros)
        self.assertEqual(respuesta.context["page_obj"].paginator.count, 12)
        cantidades = {x["codigo"]: x["cantidad"] for x in respuesta.context["categorias"]}
        self.assertEqual(cantidades, {"atender": 12, "empaque": 0, "historial": 1})
        for item in respuesta.context["categorias"]:
            self.assertIn("q=", item["url"])
            self.assertIn("especie=", item["url"])
            self.assertIn("vista=tarjetas", item["url"])
            self.assertNotIn("page=", item["url"])
        self.assertIn("categoria=atender", respuesta.context["pagina_anterior"])
        self.assertEqual(self.pagina(q=ultima.pk, categoria="historial").context["page_obj"].paginator.count, 1)
        self.assertEqual(self.pagina(recepcion=self.partida.detalle_recepcion.recepcion_id,
            partida=ultima.pk, categoria="historial", situacion="CERRADA").context["page_obj"].paginator.count, 1)
        self.assertContains(self.pagina(q="no-es-numero"), "Revisa los filtros indicados")
        self.assertContains(self.pagina(categoria="empaque"), "No hay seguimientos con producto disponible")
        self.assertEqual(self.pagina(categoria="invalida").context["categoria"], "atender")

    def test_orden_por_operacion_real_y_desempate_pk(self):
        otra = self.crear_partida()
        ahora = timezone.now()
        PartidaProceso.objects.filter(pk=self.partida.pk).update(creado_en=ahora-timedelta(days=10))
        PartidaProceso.objects.filter(pk=otra.pk).update(creado_en=ahora-timedelta(days=5))
        self.iniciar()
        self.assertEqual(list(seguimientos_operativos().values_list("pk", flat=True)), [otra.pk, self.partida.pk])
        PartidaProceso.objects.filter(pk=otra.pk).update(creado_en=self.fila().fecha_prioridad)
        self.assertEqual(list(seguimientos_operativos().values_list("pk", flat=True)), sorted([otra.pk, self.partida.pk]))

    def test_perfiles_y_accion_asignar_solo_autorizada(self):
        self.pendiente()
        self.pesar()
        encargada = Usuario.objects.create_user(username="encargada_operativa", rol=Rol.objects.get(codigo="ENCARGADA"))
        self.crear_partida()
        cerrada = self.crear_partida()
        PartidaProceso.objects.filter(pk=cerrada.pk).update(estado="CERRADA")
        for usuario in (self.jefe, encargada, self.usuario):
            self.client.force_login(usuario)
            for categoria in ("atender", "empaque", "historial"):
                for vista in ("", "tabla", "tarjetas"):
                    respuesta = self.pagina(categoria=categoria, vista=vista)
                    self.assertEqual(respuesta.context["page_obj"].paginator.count, 1)
            respuesta = self.pagina(categoria="empaque")
            self.assertEqual(respuesta.context["page_obj"].paginator.count, 1)
            enlace = 'href="' + reverse("producto_terminado:crear_lote", args=[self.partida.pk]) + '"'
            if usuario == self.usuario:
                self.assertNotContains(respuesta, enlace)
            else:
                self.assertContains(respuesta, enlace)
        self.client.logout()
        self.assertEqual(self.client.get(self.listado).status_code, 302)
        sin_rol = Usuario.objects.create_user(username="sin_rol_operativo",
            rol=Rol.objects.create(codigo="CONSULTA_OPERATIVA", nombre="Consulta"))
        self.client.force_login(sin_rol)
        self.assertEqual(self.client.get(self.listado).status_code, 403)

    def test_consultas_constantes_y_pagina_limitada(self):
        with CaptureQueriesContext(connection) as pocas:
            self.pagina()
        for _ in range(22):
            PartidaProceso.objects.create(detalle_recepcion=self.partida.detalle_recepcion,
                cantidad_inicial_kg=Decimal("10"), creado_por=self.usuario)
        with CaptureQueriesContext(connection) as muchas:
            respuesta = self.pagina()
        self.assertEqual(len(pocas), len(muchas))
        self.assertEqual(len(respuesta.context["page_obj"]), 10)
        self.assertTrue(any("LIMIT 10" in q["sql"] for q in muchas))
