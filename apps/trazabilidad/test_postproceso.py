from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch

from django.contrib import admin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connection, connections
from django.test import RequestFactory, TestCase, TransactionTestCase, skipUnlessDBFeature
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.usuarios.models import Rol, Usuario
from .forms import MermaProcesoForm, PesoPostprocesoForm
from .models import EstanciaPartida, EventoProceso, MermaProceso, PartidaProceso, Pesaje, UnidadFrio
from .selectors import ADVERTENCIA_PESO_SUPERIOR, partidas_con_situacion, resumen_pesajes_mermas
from .services import enviar_a_mantencion, registrar_merma_proceso, registrar_peso_postproceso, retirar_de_tunel
from .test_procesamiento import DatosProcesamiento


class DatosPostproceso(DatosProcesamiento):
    def pendiente(self, cantidad="500"):
        self.partida, _ = self.iniciar(cantidad=cantidad)
        self.completar()
        self.enviar()
        retirar_de_tunel(partida=self.partida, usuario=self.usuario)

    def pesar(self, peso="125"):
        return registrar_peso_postproceso(partida=self.partida, peso_kg=Decimal(peso), usuario=self.usuario)

    def merma(self, cantidad="8", tipo="MERMA", motivo="Restos de fileteo"):
        return registrar_merma_proceso(partida=self.partida, tipo=tipo, cantidad_kg=Decimal(cantidad),
                                      motivo=motivo, usuario=self.usuario)

    def url(self, nombre):
        return reverse("trazabilidad:" + nombre, args=[self.partida.pk])


class PostprocesoTests(DatosPostproceso, TestCase):
    def setUp(self):
        self.preparar()

    def test_salida_tunel_queda_pendiente_pesaje(self):
        self.iniciar()
        self.completar()
        self.enviar()
        self.client.force_login(self.usuario)
        respuesta = self.client.post(self.url("retirar_partida_tunel"), follow=True)
        self.assertContains(respuesta, "Producto retirado del túnel. Pendiente de pesaje postproceso.")
        self.assertContains(respuesta, "Registrar peso postproceso")
        self.assertEqual(self.situacion(), "PENDIENTE_PESAJE")

    def test_pendiente_pesaje_sin_postproceso(self):
        self.pendiente()
        self.assertFalse(Pesaje.objects.exists())
        self.assertFalse(MermaProceso.objects.exists())
        self.assertEqual(self.situacion(), "PENDIENTE_PESAJE")

    def test_dato_historico_sin_pesaje_queda_pendiente(self):
        ahora = timezone.now()
        EventoProceso.objects.create(partida=self.partida, tipo_proceso=self.tipos["PROCESAMIENTO"],
                                     fecha_hora_inicio=ahora, fecha_hora_termino=ahora,
                                     iniciado_por=self.usuario, finalizado_por=self.usuario)
        EstanciaPartida.objects.create(partida=self.partida, unidad_frio=self.tunel,
                                       fecha_hora_ingreso=ahora, fecha_hora_salida=ahora,
                                       ingresado_por=self.usuario, retirado_por=self.usuario)
        self.assertEqual(self.situacion(), "PENDIENTE_PESAJE")
        self.assertFalse(Pesaje.objects.exists())

    def test_lista_para_empaque_requiere_postproceso(self):
        self.pendiente()
        Pesaje.objects.create(partida=self.partida, tipo="CONTROL", peso_kg=500,
                              fecha_hora_evento=timezone.now(), registrado_por=self.usuario)
        self.assertEqual(self.situacion(), "PENDIENTE_PESAJE")
        self.pesar()
        self.assertEqual(self.situacion(), "LISTA_PARA_EMPAQUE")

    def test_registrar_peso_postproceso_y_cambiar_a_lista_para_empaque(self):
        self.pendiente()
        antes = timezone.now()
        pesaje = self.pesar()
        self.assertEqual(pesaje.peso_kg, Decimal("125"))
        self.assertEqual(pesaje.tipo, Pesaje.POSTPROCESO)
        self.assertEqual(pesaje.registrado_por, self.usuario)
        self.assertGreaterEqual(pesaje.fecha_hora_evento, antes)
        self.assertIsNone(pesaje.evento_proceso_id)
        self.assertEqual(self.situacion(), "LISTA_PARA_EMPAQUE")

    def test_peso_no_sobrescribe_recepcion_ni_cantidad_inicial(self):
        self.pendiente()
        self.pesar()
        self.partida.refresh_from_db()
        self.assertEqual(self.partida.cantidad_inicial_kg, Decimal("500"))
        self.assertEqual(self.partida.detalle_recepcion.peso_recepcion_kg, Decimal("500"))

    def test_no_pesaje_antes_de_tunel(self):
        with self.assertRaises(ValidationError):
            self.pesar()
        self.iniciar()
        with self.assertRaises(ValidationError):
            self.pesar()
        self.completar()
        with self.assertRaises(ValidationError):
            self.pesar()
        self.assertFalse(Pesaje.objects.exists())

    def test_no_pesaje_dentro_del_tunel(self):
        self.iniciar()
        self.completar()
        self.enviar()
        with self.assertRaises(ValidationError):
            self.pesar()
        self.assertFalse(Pesaje.objects.exists())

    def test_no_segundo_pesaje_y_rollback(self):
        self.pendiente()
        original = self.pesar()
        with self.assertRaises(ValidationError):
            self.pesar("200")
        self.assertEqual(list(Pesaje.objects.values_list("pk", "peso_kg")), [(original.pk, Decimal("125"))])
        self.assertFalse(MermaProceso.objects.exists())

    def test_peso_cero_negativo_precision_y_valores_invalidos(self):
        self.pendiente()
        for valor in (Decimal("0"), Decimal("-1"), Decimal("1.001"), Decimal("100000000"),
                      Decimal("NaN"), Decimal("Infinity"), "125", 125.0):
            with self.subTest(valor=valor), self.assertRaises(ValidationError):
                registrar_peso_postproceso(partida=self.partida, peso_kg=valor, usuario=self.usuario)
        self.assertFalse(Pesaje.objects.exists())

    def test_peso_superior_permitido_diferencia_negativa_y_advertencia(self):
        self.pendiente("190")
        self.client.force_login(self.usuario)
        respuesta = self.client.post(self.url("registrar_peso_postproceso"), {"peso_kg": "193"}, follow=True)
        self.assertContains(respuesta, "Peso postproceso registrado correctamente.")
        self.assertContains(respuesta, ADVERTENCIA_PESO_SUPERIOR)
        self.assertContains(respuesta, "-3,00 kg")
        self.assertContains(respuesta, "101,58 %")
        self.assertEqual(self.situacion(), "LISTA_PARA_EMPAQUE")
        self.assertNotContains(respuesta, "Registrar peso postproceso</a>")
        self.assertContains(self.client.get(self.url("detalle_partida")), ADVERTENCIA_PESO_SUPERIOR)

    def test_diferencia_y_rendimiento_correctos(self):
        self.pendiente("190")
        self.pesar()
        resumen = resumen_pesajes_mermas(self.partida)
        self.assertEqual(resumen["diferencia"], Decimal("65"))
        self.assertEqual(resumen["rendimiento"], Decimal("65.79"))
        self.assertIsInstance(resumen["rendimiento"], Decimal)
        self.assertFalse(MermaProceso.objects.exists())

    def test_division_usa_cantidad_del_seguimiento(self):
        raiz = self.partida
        self.pendiente("300")
        self.pesar("205")
        resumen = resumen_pesajes_mermas(self.partida)
        self.assertEqual(resumen["diferencia"], Decimal("95"))
        self.assertEqual(resumen["rendimiento"], Decimal("68.33"))
        self.assertEqual(raiz.subpartidas.exclude(pk=self.partida.pk).get().cantidad_inicial_kg, Decimal("200"))
        self.assertEqual(self.partida.detalle_recepcion.peso_recepcion_kg, Decimal("500"))

    def test_no_pesaje_sin_proceso_completado_o_con_estancia_abierta(self):
        self.pendiente()
        general = self.partida.eventos.get(etapa_ruta__isnull=True)
        general.fecha_hora_termino = None
        general.save()
        with self.assertRaises(ValidationError):
            self.pesar()
        general.fecha_hora_termino = timezone.now()
        general.save()
        EstanciaPartida.objects.create(partida=self.partida, unidad_frio=self.tunel,
                                       fecha_hora_ingreso=timezone.now(), ingresado_por=self.usuario)
        with self.assertRaises(ValidationError):
            self.pesar()

    def test_registrar_merma_en_proceso(self):
        self.iniciar()
        antes = timezone.now()
        merma = self.merma(motivo="  Restos de fileteo  ")
        self.assertEqual(merma.tipo, "MERMA")
        self.assertEqual(merma.cantidad_kg, Decimal("8"))
        self.assertEqual(merma.motivo, "Restos de fileteo")
        self.assertEqual(merma.registrado_por, self.usuario)
        self.assertGreaterEqual(merma.fecha_hora_evento, antes)

    def test_registrar_descarte(self):
        self.iniciar()
        self.assertEqual(self.merma(tipo="DESCARTE").tipo, "DESCARTE")

    def test_registrar_perdida(self):
        self.iniciar()
        self.assertEqual(self.merma(tipo="PERDIDA").tipo, "PERDIDA")

    def test_motivo_obligatorio_longitud_y_tipo_validos(self):
        self.iniciar()
        for motivo in ("", "  \t\n", "a" * 201, None):
            with self.subTest(motivo=motivo), self.assertRaises(ValidationError):
                self.merma(motivo=motivo)
        for tipo in ("", "OTRO", None):
            with self.subTest(tipo=tipo), self.assertRaises(ValidationError):
                self.merma(tipo=tipo)
        self.assertFalse(MermaProceso.objects.exists())
        self.merma(motivo="a" * 200)

    def test_cantidad_merma_positiva_precision_y_limite(self):
        self.iniciar()
        for valor in ("0", "-1", "1.001", "100000000", "NaN", "Infinity"):
            with self.subTest(valor=valor), self.assertRaises(ValidationError):
                self.merma(cantidad=valor)
        self.assertFalse(MermaProceso.objects.exists())

    def test_merma_se_asocia_a_etapa_abierta(self):
        self.iniciar()
        self.empezar()
        self.terminar()
        evento = self.empezar(1)
        self.assertEqual(self.merma().evento_proceso_id, evento.pk)
        self.terminar(1)
        self.empezar(2)
        self.terminar(2)
        self.enviar()
        self.assertEqual(self.situacion(), "EN_CONGELACION")

    def test_merma_sin_etapa_abierta_deja_evento_null(self):
        self.iniciar()
        self.assertIsNone(self.merma().evento_proceso_id)

    def test_merma_con_etapas_ambiguas_no_crea_registro(self):
        self.iniciar()
        self.empezar()
        EventoProceso.objects.create(partida=self.partida, etapa_ruta=self.etapas[1],
                                     tipo_proceso=self.tipos["FILETEO"], fecha_hora_inicio=timezone.now(), iniciado_por=self.usuario)
        with self.assertRaises(ValidationError):
            self.merma()
        self.assertFalse(MermaProceso.objects.exists())

    def test_total_mermas_no_supera_cantidad_y_rollback(self):
        self.iniciar()
        primera = self.merma("480")
        with self.assertRaises(ValidationError):
            self.merma("21", tipo="DESCARTE")
        self.assertEqual(list(MermaProceso.objects.values_list("pk", flat=True)), [primera.pk])
        self.merma("20", tipo="PERDIDA")
        with self.assertRaises(ValidationError):
            self.merma("0.01")
        self.assertEqual(resumen_pesajes_mermas(self.partida)["total_mermas"], Decimal("500"))

    def test_merma_no_modifica_cantidad_inicial_ni_peso_recepcion(self):
        self.partida, _ = self.iniciar(cantidad="300")
        self.merma()
        self.partida.refresh_from_db()
        self.assertEqual(self.partida.cantidad_inicial_kg, Decimal("300"))
        self.assertEqual(self.partida.detalle_recepcion.peso_recepcion_kg, Decimal("500"))
        with self.assertRaises(ValidationError):
            self.merma("300")

    def test_varias_mermas_conservan_historial(self):
        self.iniciar()
        registros = [self.merma(tipo=t) for t in MermaProceso.Tipo.values]
        self.assertEqual(resumen_pesajes_mermas(self.partida)["mermas"], registros)
        self.client.force_login(self.usuario)
        respuesta = self.client.get(self.url("detalle_partida"))
        for texto in ("Merma", "Descarte", "Pérdida", "Restos de fileteo", self.usuario.username, "24,00 kg"):
            self.assertContains(respuesta, texto)

    def test_no_merma_disponible_ni_mantencion(self):
        with self.assertRaises(ValidationError):
            self.merma()
        camara = UnidadFrio.objects.create(nombre="Cámara", tipo="MANTENCION")
        enviar_a_mantencion(partida=self.partida, cantidad_kg=Decimal("500"), unidad=camara, usuario=self.usuario)
        with self.assertRaises(ValidationError):
            self.merma()
        self.assertFalse(MermaProceso.objects.exists())

    def test_merma_permitida_en_congelacion_y_pendiente_pesaje(self):
        self.iniciar()
        self.completar()
        self.enviar()
        self.assertIsNone(self.merma().evento_proceso_id)
        retirar_de_tunel(partida=self.partida, usuario=self.usuario)
        self.assertIsNone(self.merma().evento_proceso_id)
        self.assertEqual(MermaProceso.objects.count(), 2)

    def test_no_merma_despues_de_lista_para_empaque(self):
        self.pendiente()
        self.pesar()
        with self.assertRaises(ValidationError):
            self.merma()
        self.assertFalse(MermaProceso.objects.exists())

    def test_se_relee_estado_y_rechaza_dividida_consumida_cerrada(self):
        self.pendiente()
        for estado in ("DIVIDIDA", "CONSUMIDA", "CERRADA"):
            PartidaProceso.objects.filter(pk=self.partida.pk).update(estado=estado)
            for operacion in (self.pesar, self.merma):
                with self.subTest(estado=estado), self.assertRaises(ValidationError):
                    operacion()
        self.assertFalse(Pesaje.objects.exists())
        self.assertFalse(MermaProceso.objects.exists())

    def test_rollback_si_falla_despues_de_guardar(self):
        self.pendiente()
        for modelo, operacion in ((Pesaje, self.pesar), (MermaProceso, self.merma)):
            original = modelo.save

            def guardar_y_fallar(objeto, *args, **kwargs):
                original(objeto, *args, **kwargs)
                raise RuntimeError("Fallo posterior a INSERT")

            with patch.object(modelo, "save", guardar_y_fallar), self.assertRaises(RuntimeError):
                operacion()
            self.assertFalse(modelo.objects.exists())
        self.assertEqual(self.situacion(), "PENDIENTE_PESAJE")

    def test_formularios_precision_y_campos(self):
        self.assertEqual(list(PesoPostprocesoForm().fields), ["peso_kg"])
        self.assertEqual(list(MermaProcesoForm().fields), ["tipo", "cantidad_kg", "motivo"])
        for valor in ("", "0", "-1", "1.001", "100000000", "NaN"):
            self.assertFalse(PesoPostprocesoForm({"peso_kg": valor}).is_valid())
            self.assertFalse(MermaProcesoForm({"tipo": "MERMA", "cantidad_kg": valor, "motivo": "Prueba"}).is_valid())
        self.assertTrue(PesoPostprocesoForm({"peso_kg": "99999999.99"}).is_valid())
        for motivo in ("  ", "x" * 201):
            self.assertFalse(MermaProcesoForm({"tipo": "MERMA", "cantidad_kg": "1", "motivo": motivo}).is_valid())

    def test_permisos_operativos_y_get_sin_escrituras(self):
        encargada, _ = Rol.objects.get_or_create(codigo="ENCARGADA", defaults={"nombre": "Encargada"})
        usuarios = [self.usuario, self.jefe, Usuario.objects.create_user(username="enc_peso", rol=encargada),
                    Usuario.objects.create_superuser(username="super_peso")]
        for usuario in usuarios:
            self.partida = self.crear_partida()
            self.pendiente()
            self.client.force_login(usuario)
            for nombre in ("registrar_peso_postproceso", "registrar_merma_proceso"):
                self.assertEqual(self.client.get(self.url(nombre)).status_code, 200)
            self.assertFalse(self.partida.pesajes.exists())
            self.assertFalse(self.partida.mermas.exists())
            self.assertEqual(self.client.post(self.url("registrar_merma_proceso"),
                                             {"tipo": "MERMA", "cantidad_kg": "1", "motivo": "Prueba"}).status_code, 302)
            self.assertEqual(self.client.post(self.url("registrar_peso_postproceso"), {"peso_kg": "125"}).status_code, 302)
            self.assertEqual(self.partida.pesajes.get().registrado_por, usuario)
            self.assertEqual(self.partida.mermas.get().registrado_por, usuario)

    def test_anonimos_y_sin_rol_rechazados(self):
        nombres = ("registrar_peso_postproceso", "registrar_merma_proceso")
        for nombre in nombres:
            self.assertEqual(self.client.get(self.url(nombre)).status_code, 302)
            self.assertEqual(self.client.post(self.url(nombre)).status_code, 302)
        rol = Rol.objects.create(codigo="CONSULTOR", nombre="Consultor")
        usuario = Usuario.objects.create_user(username="sin_rol_operativo_pesaje", rol=rol)
        self.client.force_login(usuario)
        for nombre in nombres:
            self.assertEqual(self.client.get(self.url(nombre)).status_code, 403)
            self.assertEqual(self.client.post(self.url(nombre)).status_code, 403)
        with self.assertRaises(PermissionDenied):
            registrar_peso_postproceso(partida=self.partida, peso_kg=Decimal("1"), usuario=usuario)
        with self.assertRaises(PermissionDenied):
            registrar_merma_proceso(partida=self.partida, tipo="MERMA", cantidad_kg=Decimal("1"), motivo="Prueba", usuario=usuario)

    def test_posts_manipulados_no_eluden_situacion(self):
        self.client.force_login(self.usuario)
        self.client.post(self.url("registrar_peso_postproceso"), {"peso_kg": "125"})
        self.client.post(self.url("registrar_merma_proceso"), {"tipo": "MERMA", "cantidad_kg": "1", "motivo": "Prueba"})
        self.assertFalse(Pesaje.objects.exists())
        self.assertFalse(MermaProceso.objects.exists())

    def test_acciones_visibles_segun_situacion(self):
        self.client.force_login(self.usuario)
        self.assertNotContains(self.client.get(self.url("detalle_partida")), "Registrar pérdida o merma")
        self.iniciar()
        self.assertContains(self.client.get(self.url("detalle_partida")), "Registrar pérdida o merma")
        self.assertContains(self.client.get(self.url("gestionar_procesamiento")), "Registrar pérdida o merma")
        self.completar()
        self.enviar()
        self.assertContains(self.client.get(self.url("detalle_partida")), "Registrar pérdida o merma")
        retirar_de_tunel(partida=self.partida, usuario=self.usuario)
        self.assertContains(self.client.get(self.url("detalle_partida")), "Registrar peso postproceso")
        self.pesar()
        respuesta = self.client.get(self.url("detalle_partida"))
        self.assertNotContains(respuesta, "Registrar pérdida o merma")
        self.assertNotContains(respuesta, "Registrar peso postproceso")

    def test_filtros_paginacion_y_consultas_constantes(self):
        self.pendiente()
        self.client.force_login(self.usuario)
        url = reverse("trazabilidad:lista_partidas")
        respuesta = self.client.get(url, {"situacion": "PENDIENTE_PESAJE"})
        self.assertContains(respuesta, "Registrar peso</a>")
        self.assertEqual([p.pk for p in respuesta.context["page_obj"]], [self.partida.pk])
        self.pesar()
        self.assertFalse(self.client.get(url, {"situacion": "PENDIENTE_PESAJE"}).context["page_obj"])
        self.assertEqual(len(self.client.get(url, {"situacion": "LISTA_PARA_EMPAQUE"}).context["page_obj"]), 1)
        with CaptureQueriesContext(connection) as pocas:
            self.client.get(url)
        for _ in range(11):
            self.crear_partida()
        with CaptureQueriesContext(connection) as muchas:
            respuesta = self.client.get(url, {"situacion": "DISPONIBLE"})
        self.assertEqual(len(pocas), len(muchas))
        self.assertEqual(len(respuesta.context["page_obj"]), 10)
        self.assertIn("situacion=DISPONIBLE", respuesta.context["pagina_siguiente"])

    def test_admin_historial_sin_edicion_ni_borrado(self):
        self.pendiente()
        merma = self.merma()
        pesaje = self.pesar()
        usuario = Usuario.objects.create_superuser(username="admin_historia")
        request = RequestFactory().get("/admin/")
        request.user = usuario
        self.client.force_login(usuario)
        for registro in (merma, pesaje):
            configuracion = admin.site._registry[type(registro)]
            self.assertFalse(configuracion.has_add_permission(request))
            self.assertFalse(configuracion.has_change_permission(request, registro))
            self.assertFalse(configuracion.has_delete_permission(request, registro))
            self.assertFalse(configuracion.get_actions(request))
            prefijo = "admin:trazabilidad_" + registro._meta.model_name
            self.assertEqual(self.client.get(reverse(prefijo + "_change", args=[registro.pk])).status_code, 200)
            self.assertEqual(self.client.post(reverse(prefijo + "_delete", args=[registro.pk]), {"post": "yes"}).status_code, 403)

    def test_flujo_190_mermas_11_peso_125_sin_reconciliacion(self):
        self.partida, _ = self.iniciar(cantidad="190")
        self.empezar()
        self.terminar()
        fileteo = self.empezar(1)
        self.assertEqual(self.merma("8").evento_proceso_id, fileteo.pk)
        self.terminar(1)
        self.empezar(2)
        self.terminar(2)
        self.enviar()
        retirar_de_tunel(partida=self.partida, usuario=self.usuario)
        self.merma("3", tipo="DESCARTE", motivo="Producto no apto")
        self.pesar("125")
        resumen = resumen_pesajes_mermas(self.partida)
        self.assertEqual(resumen["total_mermas"], Decimal("11"))
        self.assertEqual(resumen["diferencia"], Decimal("65"))
        self.assertEqual(resumen["rendimiento"], Decimal("65.79"))
        self.assertEqual(self.situacion(), "LISTA_PARA_EMPAQUE")
        self.assertEqual(MermaProceso.objects.count(), 2)
        self.client.force_login(self.usuario)
        respuesta = self.client.get(self.url("detalle_partida"))
        for texto in ("190,00 kg", "125,00 kg", "65,00 kg", "65,79 %", "11,00 kg", "Fileteo", "Producto no apto"):
            self.assertContains(respuesta, texto)


class ConcurrenciaPostprocesoTests(DatosPostproceso, TransactionTestCase):
    def setUp(self):
        self.preparar()

    def competir(self, servicio, **kwargs):
        barrera = Barrier(2)

        def ejecutar(usuario_id):
            close_old_connections()
            try:
                partida = PartidaProceso.objects.get(pk=self.partida.pk)
                usuario = Usuario.objects.get(pk=usuario_id)
                barrera.wait(timeout=10)
                try:
                    servicio(partida=partida, usuario=usuario, **kwargs)
                except ValidationError:
                    return "rechazada"
                return "aceptada"
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            resultados = list(pool.map(ejecutar, [self.usuario.pk, self.jefe.pk]))
        self.assertCountEqual(resultados, ["aceptada", "rechazada"])

    @skipUnlessDBFeature("has_select_for_update")
    def test_dos_pesajes_simultaneos_solo_un_registro(self):
        self.pendiente()
        self.competir(registrar_peso_postproceso, peso_kg=Decimal("125"))
        self.assertEqual(Pesaje.objects.count(), 1)
        self.assertEqual(self.situacion(), "LISTA_PARA_EMPAQUE")

    @skipUnlessDBFeature("has_select_for_update")
    def test_mermas_simultaneas_no_superan_cantidad(self):
        self.partida, _ = self.iniciar(cantidad="100")
        self.competir(registrar_merma_proceso, tipo="MERMA", cantidad_kg=Decimal("60"), motivo="Prueba concurrente")
        self.assertEqual(MermaProceso.objects.count(), 1)
        self.assertEqual(resumen_pesajes_mermas(self.partida)["total_mermas"], Decimal("60"))
