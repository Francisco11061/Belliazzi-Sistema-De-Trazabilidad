from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connection, connections
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.usuarios.models import Rol, Usuario
from .forms import EnviarMantencionForm
from .models import (
    ConsumoLote, Correccion, DetalleRecepcion, Especie, EstanciaPartida,
    EventoProceso, LoteProduccion, MermaProceso, OrigenSernapesca,
    PartidaProceso, Pesaje, Recepcion, TipoProceso, UnidadFrio,
)
from .selectors import partidas_con_situacion, recepcion_con_actividad
from .services import (
    _dividir_partida, corregir_recepcion, enviar_a_mantencion,
    iniciar_procesamiento, registrar_recepcion, retirar_de_mantencion,
)


class PartidasTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.jefe = Usuario.objects.create_user(username="jefe_partidas", rol=Rol.objects.get(codigo="JEFE"))
        cls.encargada = Usuario.objects.create_user(username="encargada_partidas", rol=Rol.objects.get(codigo="ENCARGADA"))
        cls.operaria = Usuario.objects.create_user(username="operaria_partidas", rol=Rol.objects.get(codigo="OPERARIA"))
        cls.superusuario = Usuario.objects.create_superuser(username="admin_partidas")
        cls.sin_rol = Usuario.objects.create_user(
            username="sin_acceso_partidas", rol=Rol.objects.create(codigo="CONSULTA", nombre="Consulta"),
        )
        cls.merluza = Especie.objects.create(nombre="Merluza Austral partidas")
        cls.congrio = Especie.objects.create(nombre="Congrio partidas")
        cls.camara = UnidadFrio.objects.create(nombre="Cámara mantención", tipo="MANTENCION")

    def setUp(self):
        self.recepcion = self.registrar()
        self.partida = PartidaProceso.objects.get(detalle_recepcion__recepcion=self.recepcion)

    def registrar(self, dos=False):
        detalles = [{"especie": self.merluza, "peso_origen_kg": Decimal("510"), "peso_recepcion_kg": Decimal("500")}]
        if dos:
            detalles.append({"especie": self.congrio, "peso_origen_kg": Decimal("35"), "peso_recepcion_kg": Decimal("30")})
        return registrar_recepcion(
            fecha_hora_recepcion=timezone.now(), registrado_por=self.jefe,
            origen={"folio_origen": "FOLIO-PARTIDAS", "proveedor": "Proveedor de prueba"}, detalles=detalles,
        )

    def enviar(self, cantidad="500", partida=None, unidad=None):
        return enviar_a_mantencion(partida=partida or self.partida, cantidad_kg=Decimal(cantidad),
                                  unidad=unidad or self.camara, usuario=self.operaria)

    def retirar(self, cantidad="500", partida=None):
        return retirar_de_mantencion(partida=partida or self.partida, cantidad_kg=Decimal(cantidad), usuario=self.operaria)

    def procesar(self, cantidad="500", partida=None):
        return iniciar_procesamiento(partida=partida or self.partida, cantidad_kg=Decimal(cantidad), usuario=self.operaria)

    def situacion(self, partida):
        return partidas_con_situacion().get(pk=partida.pk).situacion

    def corregir(self, **cambios):
        detalle = self.partida.detalle_recepcion
        datos = {
            "recepcion": self.recepcion, "usuario": self.jefe,
            "fecha_hora_recepcion": self.recepcion.fecha_hora_recepcion, "observaciones": "",
            "origen": {"folio_origen": "FOLIO-PARTIDAS", "proveedor": "Proveedor de prueba"},
            "detalles": [{"detalle_id": detalle.pk, "especie": self.merluza,
                          "peso_origen_kg": Decimal("510"), "peso_recepcion_kg": Decimal("450")}],
            "motivo": "Rectificación de pesaje",
        }
        datos.update(cambios)
        return corregir_recepcion(**datos)

    def test_registrar_recepcion_crea_una_raiz_por_detalle_con_peso_recibido(self):
        recepcion = self.registrar(dos=True)
        raices = PartidaProceso.objects.filter(detalle_recepcion__recepcion=recepcion)
        self.assertEqual(raices.count(), 2)
        for detalle in recepcion.detalles.all():
            raiz = detalle.partidas.get()
            self.assertIsNone(raiz.partida_padre_id)
            self.assertEqual(raiz.cantidad_inicial_kg, detalle.peso_recepcion_kg)
            self.assertEqual(raiz.estado, "ACTIVA")
            self.assertEqual(raiz.creado_por, self.jefe)

    def test_fallo_segunda_partida_revierte_recepcion_completa(self):
        modelos = (Recepcion, OrigenSernapesca, DetalleRecepcion, PartidaProceso)
        antes = [m.objects.count() for m in modelos]
        original = PartidaProceso.save
        llamadas = 0

        def guardar(objeto, *args, **kwargs):
            nonlocal llamadas
            llamadas += 1
            if llamadas == 2:
                raise RuntimeError("Fallo simulado")
            return original(objeto, *args, **kwargs)

        with patch.object(PartidaProceso, "save", guardar), self.assertRaises(RuntimeError):
            self.registrar(dos=True)
        self.assertEqual([m.objects.count() for m in modelos], antes)

    def test_gets_no_crean_partidas_ni_catalogos(self):
        self.client.force_login(self.jefe)
        for _ in range(2):
            self.client.get(reverse("trazabilidad:lista_partidas"))
            self.client.get(reverse("trazabilidad:detalle_recepcion", args=[self.recepcion.pk]))
            self.client.get(reverse("trazabilidad:detalle_partida", args=[self.partida.pk]))
        self.assertEqual(PartidaProceso.objects.count(), 1)
        self.assertFalse(TipoProceso.objects.filter(codigo="PROCESAMIENTO").exists())

    def test_cantidad_total_no_divide(self):
        seleccionada, restante = _dividir_partida(partida=self.partida, cantidad_seleccionada=Decimal("500"), usuario=self.jefe)
        self.assertEqual(seleccionada.pk, self.partida.pk)
        self.assertIsNone(restante)
        self.assertEqual(PartidaProceso.objects.count(), 1)

    def test_parcial_padre_dividida_hijas_activas_suma_exacta(self):
        seleccionada, restante = _dividir_partida(partida=self.partida, cantidad_seleccionada=Decimal("300"), usuario=self.operaria)
        self.partida.refresh_from_db()
        self.assertEqual(self.partida.estado, "DIVIDIDA")
        self.assertEqual(seleccionada.cantidad_inicial_kg, Decimal("300"))
        self.assertEqual(restante.cantidad_inicial_kg, Decimal("200"))
        self.assertEqual(seleccionada.cantidad_inicial_kg + restante.cantidad_inicial_kg, self.partida.cantidad_inicial_kg)
        for hija in (seleccionada, restante):
            self.assertEqual(hija.estado, "ACTIVA")
            self.assertEqual(hija.partida_padre_id, self.partida.pk)
            self.assertEqual(hija.detalle_recepcion_id, self.partida.detalle_recepcion_id)
            self.assertEqual(hija.creado_por, self.operaria)

    def test_cantidades_invalidas_no_escriben(self):
        for cantidad in (Decimal("0"), Decimal("-1"), Decimal("500.01"), Decimal("1.001"),
                         Decimal("NaN"), Decimal("Infinity"), 1.1, "1", None):
            with self.subTest(cantidad=cantidad), self.assertRaises(ValidationError):
                enviar_a_mantencion(partida=self.partida, cantidad_kg=cantidad, unidad=self.camara, usuario=self.jefe)
        self.assertEqual(PartidaProceso.objects.count(), 1)
        self.assertFalse(EstanciaPartida.objects.exists())

    def test_no_operar_estados_no_activos(self):
        for estado in ("DIVIDIDA", "CONSUMIDA", "CERRADA"):
            PartidaProceso.objects.filter(pk=self.partida.pk).update(estado=estado)
            for operacion in (self.enviar, self.procesar, self.retirar):
                with self.subTest(estado=estado, operacion=operacion), self.assertRaises(ValidationError):
                    operacion()

    def test_enviar_total_misma_partida_y_estancia(self):
        seleccionada, restante = self.enviar()
        self.assertEqual(seleccionada.pk, self.partida.pk)
        self.assertIsNone(restante)
        estancia = EstanciaPartida.objects.get()
        self.assertEqual(estancia.unidad_frio, self.camara)
        self.assertEqual(estancia.ingresado_por, self.operaria)
        self.assertIsNone(estancia.fecha_hora_salida)
        self.assertEqual(self.situacion(self.partida), "EN_MANTENCION")

    def test_enviar_parcial_solo_seleccionada_entra_resto_disponible(self):
        seleccionada, restante = self.enviar("300")
        self.assertEqual(EstanciaPartida.objects.get().partida_id, seleccionada.pk)
        self.assertEqual(self.situacion(seleccionada), "EN_MANTENCION")
        self.assertEqual(self.situacion(restante), "DISPONIBLE")
        self.assertFalse(restante.estancias_frio.exists())

    def test_no_permite_doble_estancia_ni_proceso_desde_camara(self):
        self.enviar()
        for operacion in (self.enviar, self.procesar):
            with self.assertRaises(ValidationError):
                operacion()
        self.assertEqual(EstanciaPartida.objects.count(), 1)
        self.assertFalse(EventoProceso.objects.exists())

    def test_relee_unidad_y_rechaza_inactiva_tunel_almacenamiento(self):
        for valores in ({"activo": False}, {"activo": True, "tipo": "TUNEL_CONGELADO"}, {"tipo": "ALMACENAMIENTO"}):
            UnidadFrio.objects.filter(pk=self.camara.pk).update(**valores)
            with self.assertRaises(ValidationError):
                self.enviar("100")
        self.assertEqual(PartidaProceso.objects.count(), 1)

    def test_retirar_total_cierra_estancia_misma_partida(self):
        self.enviar()
        seleccionada, restante = self.retirar()
        self.assertEqual(seleccionada.pk, self.partida.pk)
        self.assertIsNone(restante)
        estancia = EstanciaPartida.objects.get()
        self.assertIsNotNone(estancia.fecha_hora_salida)
        self.assertEqual(estancia.retirado_por, self.operaria)
        self.assertEqual(self.situacion(seleccionada), "DISPONIBLE")

    def test_retirar_parcial_divide_y_resto_en_misma_camara(self):
        self.enviar()
        seleccionada, restante = self.retirar("120")
        original = self.partida.estancias_frio.get()
        nueva = restante.estancias_frio.get()
        self.assertIsNotNone(original.fecha_hora_salida)
        self.assertEqual(nueva.unidad_frio, original.unidad_frio)
        self.assertEqual(nueva.fecha_hora_ingreso, original.fecha_hora_salida)
        self.assertEqual(self.situacion(seleccionada), "DISPONIBLE")
        self.assertEqual(self.situacion(restante), "EN_MANTENCION")
        self.assertEqual(restante.cantidad_inicial_kg, Decimal("380"))
        self.assertFalse(seleccionada.estancias_frio.exists())

    def test_no_retirar_fuera_camara(self):
        with self.assertRaises(ValidationError):
            self.retirar()

    def test_no_retirar_con_dos_estancias_abiertas(self):
        self.enviar()
        EstanciaPartida.objects.create(partida=self.partida, unidad_frio=self.camara,
                                       fecha_hora_ingreso=timezone.now(), ingresado_por=self.jefe)
        with self.assertRaises(ValidationError):
            self.retirar()
        self.assertEqual(EstanciaPartida.objects.filter(fecha_hora_salida__isnull=True).count(), 2)

    def test_retiro_no_requiere_camara_aun_activa(self):
        self.enviar()
        UnidadFrio.objects.filter(pk=self.camara.pk).update(activo=False)
        seleccionada, restante = self.retirar("100")
        self.assertEqual(self.situacion(seleccionada), "DISPONIBLE")
        self.assertEqual(self.situacion(restante), "EN_MANTENCION")

    def test_iniciar_proceso_total_persiste_evento_abierto(self):
        seleccionada, restante = self.procesar()
        self.assertEqual(seleccionada.pk, self.partida.pk)
        self.assertIsNone(restante)
        evento = EventoProceso.objects.get()
        self.assertEqual(evento.tipo_proceso.codigo, "PROCESAMIENTO")
        self.assertEqual(evento.iniciado_por, self.operaria)
        self.assertIsNone(evento.fecha_hora_termino)
        self.assertEqual(self.situacion(seleccionada), "EN_PROCESO")

    def test_iniciar_proceso_parcial_resto_disponible(self):
        seleccionada, restante = self.procesar("300.01")
        self.assertEqual(self.situacion(seleccionada), "EN_PROCESO")
        self.assertEqual(self.situacion(restante), "DISPONIBLE")
        self.assertEqual(restante.cantidad_inicial_kg, Decimal("199.99"))

    def test_no_repetir_proceso_ni_enviar_a_camara(self):
        self.procesar()
        for operacion in (self.procesar, self.enviar):
            with self.assertRaises(ValidationError):
                operacion()
        self.assertEqual(EventoProceso.objects.count(), 1)

    def test_evento_cerrado_no_vuelve_disponible_silenciosamente(self):
        self.procesar()
        EventoProceso.objects.update(fecha_hora_termino=timezone.now())
        self.assertEqual(self.situacion(self.partida), "EN_PROCESO")
        with self.assertRaises(ValidationError):
            self.procesar()

    def test_catalogo_se_reutiliza_y_respeta_inactivo(self):
        tipo = TipoProceso.objects.create(codigo="PROCESAMIENTO", nombre="Procesamiento", activo=False)
        with self.assertRaises(ValidationError):
            self.procesar("300")
        self.assertEqual(PartidaProceso.objects.count(), 1)
        tipo.activo = True
        tipo.save()
        self.procesar()
        self.assertEqual(TipoProceso.objects.filter(codigo="PROCESAMIENTO").count(), 1)

    def test_rollback_envio_fallido_revierte_hijas_y_padre(self):
        with patch.object(EstanciaPartida, "save", side_effect=RuntimeError), self.assertRaises(RuntimeError):
            self.enviar("300")
        self.partida.refresh_from_db()
        self.assertEqual(self.partida.estado, "ACTIVA")
        self.assertEqual(PartidaProceso.objects.count(), 1)

    def test_rollback_retiro_fallido_reabre_estancia_original(self):
        self.enviar()
        with patch.object(PartidaProceso, "save", side_effect=RuntimeError), self.assertRaises(RuntimeError):
            self.retirar("100")
        self.assertIsNone(EstanciaPartida.objects.get().fecha_hora_salida)
        self.assertEqual(PartidaProceso.objects.count(), 1)

    def test_rollback_proceso_fallido_revierte_division_y_catalogo(self):
        with patch.object(EventoProceso, "save", side_effect=RuntimeError), self.assertRaises(RuntimeError):
            self.procesar("300")
        self.partida.refresh_from_db()
        self.assertEqual(self.partida.estado, "ACTIVA")
        self.assertEqual(PartidaProceso.objects.count(), 1)
        self.assertFalse(TipoProceso.objects.filter(codigo="PROCESAMIENTO").exists())

    def test_instancia_obsoleta_no_puede_reutilizar_padre(self):
        self.enviar("300")
        with self.assertRaises(ValidationError):
            self.procesar("300")
        self.assertEqual(PartidaProceso.objects.count(), 3)

    def test_corregir_raiz_intacta_sin_auditoria_adicional(self):
        self.assertFalse(recepcion_con_actividad(self.recepcion))
        self.corregir()
        self.partida.refresh_from_db()
        self.assertEqual(self.partida.cantidad_inicial_kg, Decimal("450"))
        self.assertEqual(self.partida.detalle_recepcion.peso_recepcion_kg, Decimal("450"))
        auditoria = Correccion.objects.get()
        self.assertEqual(auditoria.entidad_afectada, "DetalleRecepcion")
        self.assertEqual(auditoria.campo, "peso_recepcion_kg")

    def test_correccion_raiz_rollback_incluye_auditoria(self):
        with patch.object(Correccion, "save", side_effect=RuntimeError), self.assertRaises(RuntimeError):
            self.corregir()
        self.partida.refresh_from_db()
        self.assertEqual(self.partida.cantidad_inicial_kg, Decimal("500"))
        self.assertEqual(self.partida.detalle_recepcion.peso_recepcion_kg, Decimal("500"))
        self.assertFalse(Correccion.objects.exists())

    def test_camara_bloquea_correccion_incluso_tras_retiro(self):
        self.enviar()
        with self.assertRaises(ValidationError):
            self.corregir()
        self.retirar()
        with self.assertRaises(ValidationError):
            self.corregir()

    def test_division_bloquea_correccion(self):
        _dividir_partida(partida=self.partida, cantidad_seleccionada=Decimal("300"), usuario=self.jefe)
        with self.assertRaises(ValidationError):
            self.corregir()

    def test_proceso_bloquea_especie_permite_descripcion(self):
        self.procesar()
        detalle = {"detalle_id": self.partida.detalle_recepcion_id, "especie": self.congrio,
                   "peso_origen_kg": Decimal("510"), "peso_recepcion_kg": Decimal("500")}
        with self.assertRaises(ValidationError):
            self.corregir(detalles=[detalle])
        detalle["especie"] = self.merluza
        self.corregir(detalles=[detalle], observaciones="Descripción corregida")
        self.assertEqual(Correccion.objects.get().campo, "observaciones")

    def test_pesajes_mermas_consumos_bloquean_correccion(self):
        pesaje = Pesaje.objects.create(partida=self.partida, tipo="Control", peso_kg=Decimal("500"),
                                      fecha_hora_evento=timezone.now(), registrado_por=self.jefe)
        with self.assertRaises(ValidationError):
            self.corregir()
        pesaje.delete()
        merma = MermaProceso.objects.create(partida=self.partida, cantidad_kg=Decimal("1"), motivo="Prueba",
                                           fecha_hora_evento=timezone.now(), registrado_por=self.jefe)
        with self.assertRaises(ValidationError):
            self.corregir()
        merma.delete()
        lote = LoteProduccion.objects.create(codigo_lote="TEST-PARTIDAS", especie=self.merluza,
                                            fecha_elaboracion=timezone.localdate(), registrado_por=self.jefe)
        ConsumoLote.objects.create(partida=self.partida, lote_produccion=lote, cantidad_kg_utilizada=Decimal("1"))
        with self.assertRaises(ValidationError):
            self.corregir()

    def test_form_solo_mantencion_activa_y_preseleccion(self):
        UnidadFrio.objects.create(nombre="Túnel", tipo="TUNEL_CONGELADO")
        UnidadFrio.objects.create(nombre="Inactiva", tipo="MANTENCION", activo=False)
        form = EnviarMantencionForm(partida=self.partida)
        self.assertEqual(list(form.fields["unidad"].queryset), [self.camara])
        self.assertEqual(form.fields["unidad"].initial, self.camara.pk)

    def test_roles_pueden_listar_ver_y_operar(self):
        for usuario in (self.jefe, self.encargada, self.operaria, self.superusuario):
            with self.subTest(usuario=usuario.username):
                recepcion = self.registrar()
                partida = PartidaProceso.objects.get(detalle_recepcion__recepcion=recepcion)
                self.client.force_login(usuario)
                self.assertEqual(self.client.get(reverse("trazabilidad:lista_partidas")).status_code, 200)
                respuesta = self.client.get(reverse("trazabilidad:detalle_partida", args=[partida.pk]))
                self.assertContains(respuesta, reverse("trazabilidad:lista_partidas"))
                self.assertEqual(self.client.post(reverse("trazabilidad:enviar_partida_mantencion", args=[partida.pk]),
                                                 {"cantidad_kg": "500", "unidad": self.camara.pk}).status_code, 302)
                self.assertEqual(self.client.post(reverse("trazabilidad:retirar_partida_mantencion", args=[partida.pk]),
                                                 {"cantidad_kg": "500"}).status_code, 302)
                self.assertEqual(self.client.post(reverse("trazabilidad:procesar_partida", args=[partida.pk]),
                                                 {"cantidad_kg": "500"}).status_code, 302)

    def test_anonimo_y_sin_rol_no_acceden(self):
        urls = [reverse("trazabilidad:lista_partidas")] + [reverse(f"trazabilidad:{nombre}", args=[self.partida.pk])
                for nombre in ("detalle_partida", "enviar_partida_mantencion", "retirar_partida_mantencion", "procesar_partida")]
        for url in urls:
            self.assertRedirects(self.client.get(url), reverse("usuarios:login") + "?next=" + url, fetch_redirect_response=False)
        self.client.force_login(self.sin_rol)
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(url, {"cantidad_kg": "500"}).status_code, 403)
        with self.assertRaises(PermissionDenied):
            iniciar_procesamiento(partida=self.partida, cantidad_kg=Decimal("500"), usuario=self.sin_rol)

    def test_acciones_segun_situacion_y_post_manipulado(self):
        self.client.force_login(self.operaria)
        url = reverse("trazabilidad:detalle_partida", args=[self.partida.pk])
        respuesta = self.client.get(url)
        self.assertContains(respuesta, "Enviar a proceso")
        self.assertContains(respuesta, "Enviar a cámara")
        self.enviar()
        respuesta = self.client.get(url)
        self.assertContains(respuesta, "Retirar de cámara")
        self.assertNotContains(respuesta, "Enviar a proceso")
        self.client.post(reverse("trazabilidad:procesar_partida", args=[self.partida.pk]), {"cantidad_kg": "500"})
        self.assertFalse(EventoProceso.objects.exists())
        self.retirar()
        self.procesar()
        respuesta = self.client.get(url)
        self.assertContains(respuesta, "ya ingresó a procesamiento")
        for texto in ("Enviar a cámara", "Enviar a proceso", "Retirar de cámara"):
            self.assertNotContains(respuesta, texto)

    def test_filtros_paginacion_y_queries_constantes(self):
        self.client.force_login(self.operaria)
        url = reverse("trazabilidad:lista_partidas")
        with CaptureQueriesContext(connection) as pocas:
            self.client.get(url)
        for _ in range(11):
            self.registrar()
        with CaptureQueriesContext(connection) as muchas:
            respuesta = self.client.get(url)
        self.assertEqual(len(muchas), len(pocas))
        self.assertLessEqual(len(muchas), 7)
        self.assertEqual(len(respuesta.context["page_obj"]), 10)
        respuesta = self.client.get(url, {"especie": self.merluza.pk, "situacion": "DISPONIBLE", "page": "2"})
        self.assertEqual(len(respuesta.context["page_obj"]), 2)
        self.assertIn("situacion=DISPONIBLE", respuesta.context["pagina_anterior"])
        self.assertIn(f"especie={self.merluza.pk}", respuesta.context["pagina_anterior"])
        for filtros in ({"partida": self.partida.pk}, {"recepcion": self.recepcion.pk}):
            respuesta = self.client.get(url, filtros)
            self.assertEqual([p.pk for p in respuesta.context["page_obj"]], [self.partida.pk])
        self.enviar()
        respuesta = self.client.get(url, {"situacion": "EN_MANTENCION"})
        self.assertEqual([p.pk for p in respuesta.context["page_obj"]], [self.partida.pk])
        for filtros in ({"partida": "abc"}, {"situacion": "inventada"}, {"recepcion": "-1"}):
            respuesta = self.client.get(url, filtros)
            self.assertEqual(len(respuesta.context["page_obj"]), 0)

    def test_linaje_y_listado_excluye_padre_dividido(self):
        seleccionada, restante = self.enviar("300")
        self.client.force_login(self.operaria)
        respuesta = self.client.get(reverse("trazabilidad:lista_partidas"))
        self.assertEqual({p.pk for p in respuesta.context["page_obj"]}, {seleccionada.pk, restante.pk})
        respuesta = self.client.get(reverse("trazabilidad:detalle_partida", args=[self.partida.pk]))
        self.assertContains(respuesta, reverse("trazabilidad:detalle_partida", args=[seleccionada.pk]))
        self.assertContains(respuesta, "Dividida")
        self.assertNotContains(respuesta, "Enviar a proceso")
        respuesta = self.client.get(reverse("trazabilidad:detalle_partida", args=[seleccionada.pk]))
        self.assertContains(respuesta, "Proviene de")
        self.assertContains(respuesta, "FOLIO-PARTIDAS")

    def test_flujo_completo_dos_especies(self):
        recepcion = self.registrar(dos=True)
        merluza = PartidaProceso.objects.get(detalle_recepcion__recepcion=recepcion, detalle_recepcion__especie=self.merluza)
        congrio = PartidaProceso.objects.get(detalle_recepcion__recepcion=recepcion, detalle_recepcion__especie=self.congrio)
        self.enviar(partida=merluza)
        self.retirar(partida=merluza)
        en_camara, disponible = self.enviar("20", partida=congrio)
        retirada, sigue_en_camara = self.retirar("12", partida=en_camara)
        self.procesar("12", partida=retirada)
        self.procesar(partida=merluza)
        self.assertEqual(self.situacion(disponible), "DISPONIBLE")
        self.assertEqual(sigue_en_camara.cantidad_inicial_kg, Decimal("8"))
        self.assertEqual(self.situacion(sigue_en_camara), "EN_MANTENCION")
        self.assertEqual(self.situacion(retirada), "EN_PROCESO")
        self.assertEqual(retirada.detalle_recepcion.especie, self.congrio)
        self.assertEqual(merluza.detalle_recepcion.especie, self.merluza)
        self.assertEqual(TipoProceso.objects.filter(codigo="PROCESAMIENTO").count(), 1)


class ConcurrenciaPartidasTests(TransactionTestCase):
    def setUp(self):
        rol, _ = Rol.objects.get_or_create(codigo="OPERARIA", defaults={"nombre": "Operaria"})
        self.usuario = Usuario.objects.create_user(username="concurrencia_partidas", rol=rol)
        especie = Especie.objects.create(nombre="Especie concurrencia")
        recepcion = registrar_recepcion(
            fecha_hora_recepcion=timezone.now(), registrado_por=self.usuario,
            origen={"folio_origen": "CONCURRENCIA"},
            detalles=[{"especie": especie, "peso_origen_kg": Decimal("500"), "peso_recepcion_kg": Decimal("500")}],
        )
        self.partida = PartidaProceso.objects.get(detalle_recepcion__recepcion=recepcion)
        self.camara = UnidadFrio.objects.create(nombre="Cámara concurrencia", tipo="MANTENCION")

    def competir(self, operaciones):
        barrera = Barrier(2)
        partida_id, usuario_id, unidad_id = self.partida.pk, self.usuario.pk, self.camara.pk

        def ejecutar(operacion):
            close_old_connections()
            try:
                partida = PartidaProceso.objects.get(pk=partida_id)
                usuario = Usuario.objects.get(pk=usuario_id)
                unidad = UnidadFrio.objects.get(pk=unidad_id)
                barrera.wait(timeout=10)
                try:
                    if operacion == "camara":
                        enviar_a_mantencion(partida=partida, cantidad_kg=Decimal("300"), unidad=unidad, usuario=usuario)
                    else:
                        iniciar_procesamiento(partida=partida, cantidad_kg=Decimal("300"), usuario=usuario)
                except ValidationError:
                    return "rechazada"
                return "aceptada"
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            resultados = list(pool.map(ejecutar, operaciones))
        self.assertCountEqual(resultados, ["aceptada", "rechazada"])
        self.partida.refresh_from_db()
        self.assertEqual(self.partida.estado, "DIVIDIDA")
        self.assertEqual(self.partida.subpartidas.count(), 2)
        self.assertEqual(sum(p.cantidad_inicial_kg for p in self.partida.subpartidas.all()), Decimal("500"))
        self.assertEqual(EstanciaPartida.objects.count() + EventoProceso.objects.count(), 1)

    @skipUnlessDBFeature("has_select_for_update")
    def test_dos_envios_simultaneos_solo_usan_kilos_una_vez(self):
        self.competir(["camara", "camara"])

    @skipUnlessDBFeature("has_select_for_update")
    def test_envio_y_proceso_simultaneos_solo_usan_kilos_una_vez(self):
        self.competir(["camara", "proceso"])

