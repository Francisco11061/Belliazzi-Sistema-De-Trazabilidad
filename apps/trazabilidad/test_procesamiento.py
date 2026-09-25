from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from importlib import import_module
from threading import Barrier
from unittest.mock import patch

from django.apps import apps
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, connection, connections, transaction
from django.db.models.deletion import ProtectedError
from django.test import RequestFactory, TestCase, TransactionTestCase, skipUnlessDBFeature
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.usuarios.models import Rol, Usuario
from .forms import IniciarProcesamientoForm, RutaPartidaForm, TunelPartidaForm
from .models import (
    Especie, EstanciaPartida, EtapaRutaProceso, EventoProceso, PartidaProceso,
    RutaProceso, TipoProceso, UnidadFrio,
)
from .selectors import partidas_con_situacion, presentar_partida, resumen_procesamiento
from .services import (
    asignar_ruta_proceso, enviar_a_mantencion, enviar_a_tunel, finalizar_etapa_proceso,
    iniciar_etapa_proceso, iniciar_procesamiento, registrar_recepcion, retirar_de_tunel,
)


class DatosProcesamiento:
    def preparar(self):
        rol, _ = Rol.objects.get_or_create(codigo="OPERARIA", defaults={"nombre": "Operaria"})
        self.usuario = Usuario.objects.create_user(username="operaria_proceso", rol=rol)
        jefe, _ = Rol.objects.get_or_create(codigo="JEFE", defaults={"nombre": "Jefe"})
        self.jefe = Usuario.objects.create_user(username="jefe_proceso", rol=jefe)
        self.especie = Especie.objects.create(nombre="Especie rutas")
        self.otra_especie = Especie.objects.create(nombre="Otra especie rutas")
        self.tipos = {}
        for codigo, nombre in (("PROCESAMIENTO", "Procesamiento"), ("DESCABEZADO", "Descabezado"),
                               ("FILETEO", "Fileteo"), ("EMPARRILLADO", "Emparrillado")):
            self.tipos[codigo], _ = TipoProceso.objects.get_or_create(codigo=codigo, defaults={"nombre": nombre})
        self.ruta = self.crear_ruta()
        self.etapas = list(self.ruta.etapas.order_by("orden"))
        self.partida = self.crear_partida()
        self.tunel = UnidadFrio.objects.create(nombre="Túnel de congelado 1", tipo="TUNEL_CONGELADO")

    def crear_ruta(self, especie=None, nombre="Filete IQF", codigos=None, **kwargs):
        ruta = RutaProceso.objects.create(especie=especie or self.especie, nombre=nombre, **kwargs)
        codigos = codigos if codigos is not None else ["DESCABEZADO", "FILETEO", "EMPARRILLADO"]
        for orden, codigo in enumerate(codigos, 1):
            EtapaRutaProceso.objects.create(ruta=ruta, tipo_proceso=self.tipos[codigo], orden=orden)
        return ruta

    def crear_partida(self):
        recepcion = registrar_recepcion(
            fecha_hora_recepcion=timezone.now(), registrado_por=self.jefe,
            origen={"folio_origen": "PROCESO-001", "proveedor": "Proveedor de prueba"},
            detalles=[{"especie": self.especie, "peso_origen_kg": Decimal("510"), "peso_recepcion_kg": Decimal("500")}],
        )
        return PartidaProceso.objects.get(detalle_recepcion__recepcion=recepcion)

    def iniciar(self, partida=None, cantidad="500", ruta=None):
        return iniciar_procesamiento(partida=partida or self.partida, cantidad_kg=Decimal(cantidad),
                                     ruta=ruta or self.ruta, usuario=self.usuario)

    def empezar(self, indice=0, partida=None):
        return iniciar_etapa_proceso(partida=partida or self.partida, etapa=self.etapas[indice], usuario=self.usuario)

    def terminar(self, indice=0, partida=None):
        return finalizar_etapa_proceso(partida=partida or self.partida, etapa=self.etapas[indice], usuario=self.jefe)

    def completar(self, partida=None):
        for indice in range(len(self.etapas)):
            self.empezar(indice, partida)
            self.terminar(indice, partida)

    def enviar(self):
        return enviar_a_tunel(partida=self.partida, unidad=self.tunel, usuario=self.usuario)

    def situacion(self, partida=None):
        return partidas_con_situacion().get(pk=(partida or self.partida).pk).situacion


class ProcesamientoTests(DatosProcesamiento, TestCase):
    def setUp(self):
        self.preparar()

    def test_especie_admite_varias_rutas_y_nombre_no_es_global(self):
        segunda = self.crear_ruta(nombre="Otro formato")
        otra = self.crear_ruta(especie=self.otra_especie)
        self.assertEqual(self.especie.rutas_proceso.count(), 2)
        self.assertEqual(otra.nombre, self.ruta.nombre)
        self.assertEqual(segunda.especie, self.especie)
        with self.assertRaises(IntegrityError), transaction.atomic():
            RutaProceso.objects.create(especie=self.especie, nombre=self.ruta.nombre)

    def test_orden_unico_y_positivo_en_base_de_datos(self):
        for orden in (1, 0):
            with self.subTest(orden=orden), self.assertRaises(IntegrityError), transaction.atomic():
                EtapaRutaProceso.objects.bulk_create([
                    EtapaRutaProceso(ruta=self.ruta, tipo_proceso=self.tipos["FILETEO"], orden=orden),
                ])

    def test_ruta_sin_etapas_no_se_puede_usar(self):
        ruta = self.crear_ruta(nombre="Sin etapas", codigos=[])
        with self.assertRaisesRegex(ValidationError, "al menos una etapa"):
            self.iniciar(ruta=ruta)
        self.assertFalse(EventoProceso.objects.exists())

    def test_ruta_debe_terminar_en_emparrillado(self):
        ruta = self.crear_ruta(nombre="Final incorrecto", codigos=["EMPARRILLADO", "FILETEO"])
        with self.assertRaisesRegex(ValidationError, "última etapa"):
            self.iniciar(ruta=ruta)

    def test_procesamiento_no_es_etapa_incluso_si_se_elude_admin(self):
        with self.assertRaises(ValidationError):
            EtapaRutaProceso.objects.create(ruta=self.ruta, tipo_proceso=self.tipos["PROCESAMIENTO"], orden=4)
        EtapaRutaProceso.objects.filter(pk=self.etapas[0].pk).update(tipo_proceso=self.tipos["PROCESAMIENTO"])
        with self.assertRaisesRegex(ValidationError, "no puede utilizarse"):
            self.iniciar()

    def test_no_asignar_ruta_otra_especie_inactiva_o_tipo_inactivo(self):
        otra = self.crear_ruta(especie=self.otra_especie)
        with self.assertRaises(ValidationError):
            self.iniciar(ruta=otra)
        RutaProceso.objects.filter(pk=self.ruta.pk).update(activo=False)
        with self.assertRaises(ValidationError):
            self.iniciar()
        RutaProceso.objects.filter(pk=self.ruta.pk).update(activo=True)
        TipoProceso.objects.filter(pk=self.tipos["FILETEO"].pk).update(activo=False)
        with self.assertRaises(ValidationError):
            self.iniciar()
        self.assertFalse(EventoProceso.objects.exists())

    def test_inicio_total_con_ruta_y_evento_general_sin_etapa(self):
        seleccionada, resto = self.iniciar()
        self.assertIsNone(resto)
        self.assertEqual(seleccionada.pk, self.partida.pk)
        self.assertEqual(seleccionada.ruta_proceso_id, self.ruta.pk)
        evento = EventoProceso.objects.get()
        self.assertIsNone(evento.etapa_ruta_id)
        self.assertEqual(evento.tipo_proceso.codigo, "PROCESAMIENTO")
        self.assertEqual(self.situacion(), "EN_PROCESO")

    def test_inicio_parcial_solo_hija_procesada_tiene_ruta(self):
        seleccionada, resto = self.iniciar(cantidad="300")
        self.partida.refresh_from_db()
        self.assertEqual(seleccionada.ruta_proceso_id, self.ruta.pk)
        self.assertIsNone(resto.ruta_proceso_id)
        self.assertIsNone(self.partida.ruta_proceso_id)
        self.assertEqual(resto.cantidad_inicial_kg, Decimal("200"))
        self.assertEqual(self.situacion(resto), "DISPONIBLE")
        self.assertEqual(self.partida.estado, "DIVIDIDA")

    def test_ruta_predeterminada_y_unica_se_preseleccionan(self):
        self.assertEqual(IniciarProcesamientoForm(partida=self.partida).fields["ruta"].initial, self.ruta.pk)
        otra = self.crear_ruta(nombre="Alternativa", predeterminada=True)
        self.assertEqual(IniciarProcesamientoForm(partida=self.partida).fields["ruta"].initial, otra.pk)

    def test_varias_rutas_y_varias_predeterminadas_exigen_seleccion(self):
        otra = self.crear_ruta(nombre="Alternativa")
        self.assertIsNone(IniciarProcesamientoForm(partida=self.partida).fields["ruta"].initial)
        RutaProceso.objects.filter(pk__in=[self.ruta.pk, otra.pk]).update(predeterminada=True)
        form = IniciarProcesamientoForm(partida=self.partida)
        self.assertIsNone(form.fields["ruta"].initial)
        self.assertIn("ambigua", form.fields["ruta"].help_text)
        self.assertFalse(IniciarProcesamientoForm({"cantidad_kg": "500"}, partida=self.partida).is_valid())

    def test_formulario_rutas_solo_activas_misma_especie(self):
        self.crear_ruta(especie=self.otra_especie)
        self.crear_ruta(nombre="Inactiva", activo=False)
        self.assertEqual(list(RutaPartidaForm(partida=self.partida).fields["ruta"].queryset), [self.ruta])

    def test_partida_historica_sin_ruta_puede_asignarla_sin_duplicar_evento(self):
        evento = EventoProceso.objects.create(partida=self.partida, tipo_proceso=self.tipos["PROCESAMIENTO"],
                                              fecha_hora_inicio=timezone.now(), iniciado_por=self.usuario)
        asignar_ruta_proceso(partida=self.partida, ruta=self.ruta, usuario=self.usuario)
        self.partida.refresh_from_db()
        self.assertEqual(self.partida.ruta_proceso_id, self.ruta.pk)
        self.assertEqual(EventoProceso.objects.get().pk, evento.pk)

    def test_no_asignar_ruta_a_disponible_ni_cambiar_ruta_con_etapas(self):
        with self.assertRaises(ValidationError):
            asignar_ruta_proceso(partida=self.partida, ruta=self.ruta, usuario=self.usuario)
        self.iniciar()
        self.empezar()
        otra = self.crear_ruta(nombre="Alternativa")
        with self.assertRaises(ValidationError):
            asignar_ruta_proceso(partida=self.partida, ruta=otra, usuario=self.usuario)

    def test_evento_especifico_tiene_etapa_tipo_y_usuarios_correctos(self):
        self.iniciar()
        evento = self.empezar()
        self.assertEqual(evento.etapa_ruta_id, self.etapas[0].pk)
        self.assertEqual(evento.tipo_proceso_id, self.etapas[0].tipo_proceso_id)
        self.assertEqual(evento.iniciado_por, self.usuario)
        terminado = self.terminar()
        self.assertEqual(terminado.pk, evento.pk)
        self.assertEqual(terminado.finalizado_por, self.jefe)
        self.assertIsNotNone(terminado.fecha_hora_termino)
        self.assertEqual(EventoProceso.objects.count(), 2)

    def test_no_iniciar_segunda_ni_saltar_etapas(self):
        self.iniciar()
        for indice in (1, 2):
            with self.assertRaises(ValidationError):
                self.empezar(indice)
        self.empezar()
        with self.assertRaises(ValidationError):
            self.empezar(1)
        self.terminar()
        with self.assertRaises(ValidationError):
            self.empezar(2)
        self.empezar(1)
        self.assertEqual(EventoProceso.objects.filter(etapa_ruta__isnull=False, fecha_hora_termino__isnull=True).count(), 1)

    def test_no_repetir_inicio_ni_finalizacion(self):
        self.iniciar()
        with self.assertRaises(ValidationError):
            self.terminar()
        self.empezar()
        with self.assertRaises(ValidationError):
            self.empezar()
        self.terminar()
        for operacion in (self.empezar, self.terminar):
            with self.assertRaises(ValidationError):
                operacion()

    def test_etapa_ajena_no_se_puede_operar(self):
        otra = self.crear_ruta(nombre="Alternativa")
        etapa = otra.etapas.first()
        self.iniciar()
        for servicio in (iniciar_etapa_proceso, finalizar_etapa_proceso):
            with self.assertRaises(ValidationError):
                servicio(partida=self.partida, etapa=etapa, usuario=self.usuario)

    def test_etapas_no_operan_partida_inactiva_ni_en_camara(self):
        with self.assertRaises(ValidationError):
            self.empezar()
        camara = UnidadFrio.objects.create(nombre="Mantención", tipo="MANTENCION")
        enviar_a_mantencion(partida=self.partida, cantidad_kg=Decimal("500"), unidad=camara, usuario=self.usuario)
        with self.assertRaises(ValidationError):
            self.empezar()
        for estado in ("DIVIDIDA", "CERRADA", "CONSUMIDA"):
            PartidaProceso.objects.filter(pk=self.partida.pk).update(estado=estado)
            with self.assertRaises(ValidationError):
                self.empezar()

    def test_dos_partidas_pueden_tener_etapas_abiertas(self):
        otra = self.crear_partida()
        self.iniciar()
        self.iniciar(partida=otra)
        self.empezar()
        self.empezar(partida=otra)
        self.assertEqual(EventoProceso.objects.filter(etapa_ruta__isnull=False, fecha_hora_termino__isnull=True).count(), 2)

    def test_tipo_repetido_se_distingue_por_etapa_exacta(self):
        ruta = self.crear_ruta(nombre="Dos pasadas", codigos=["FILETEO", "FILETEO", "EMPARRILLADO"])
        self.iniciar(ruta=ruta)
        for etapa in ruta.etapas.all():
            iniciar_etapa_proceso(partida=self.partida, etapa=etapa, usuario=self.usuario)
            finalizar_etapa_proceso(partida=self.partida, etapa=etapa, usuario=self.usuario)
        self.assertEqual(EventoProceso.objects.filter(tipo_proceso=self.tipos["FILETEO"]).count(), 2)

    def test_ruta_y_tipos_desactivados_no_rompen_ejecucion_existente(self):
        self.iniciar()
        RutaProceso.objects.filter(pk=self.ruta.pk).update(activo=False)
        TipoProceso.objects.filter(pk=self.tipos["FILETEO"].pk).update(activo=False)
        self.completar()
        self.enviar()
        self.assertEqual(self.situacion(), "EN_CONGELACION")

    def test_emparrillado_completado_mantiene_general_abierto_hasta_tunel(self):
        self.iniciar()
        self.completar()
        general = EventoProceso.objects.get(etapa_ruta__isnull=True)
        self.assertIsNone(general.fecha_hora_termino)
        actual = presentar_partida(partidas_con_situacion().get(pk=self.partida.pk))
        resumen = resumen_procesamiento(actual)
        self.assertTrue(resumen["ruta_completa"])
        self.assertTrue(resumen["puede_enviar_tunel"])

    def test_no_enviar_tunel_sin_completar_ruta(self):
        self.iniciar()
        with self.assertRaises(ValidationError):
            self.enviar()
        self.empezar()
        self.terminar()
        with self.assertRaises(ValidationError):
            self.enviar()
        self.assertFalse(EstanciaPartida.objects.exists())

    def test_no_enviar_tunel_sin_emparrillado_aunque_haya_datos_invalidos(self):
        self.iniciar()
        self.completar()
        EtapaRutaProceso.objects.filter(pk=self.etapas[-1].pk).update(tipo_proceso=self.tipos["FILETEO"])
        with self.assertRaises(ValidationError):
            self.enviar()

    def test_tunel_relee_y_rechaza_inactivo_mantencion_almacenamiento(self):
        self.iniciar()
        self.completar()
        for valores in ({"activo": False}, {"activo": True, "tipo": "MANTENCION"}, {"tipo": "ALMACENAMIENTO"}):
            UnidadFrio.objects.filter(pk=self.tunel.pk).update(**valores)
            with self.assertRaises(ValidationError):
                self.enviar()
        self.assertIsNone(EventoProceso.objects.get(etapa_ruta__isnull=True).fecha_hora_termino)

    def test_tunel_usa_partida_completa_y_cierra_general(self):
        self.iniciar()
        self.completar()
        estancia = self.enviar()
        general = EventoProceso.objects.get(etapa_ruta__isnull=True)
        self.assertEqual(general.fecha_hora_termino, estancia.fecha_hora_ingreso)
        self.assertEqual(general.finalizado_por, self.usuario)
        self.assertEqual(estancia.partida_id, self.partida.pk)
        self.assertEqual(estancia.ingresado_por, self.usuario)
        self.assertEqual(self.situacion(), "EN_CONGELACION")
        self.partida.refresh_from_db()
        self.assertEqual(self.partida.estado, "ACTIVA")
        self.assertEqual(PartidaProceso.objects.count(), 1)
        with self.assertRaises(ValidationError):
            self.enviar()

    def test_retiro_tunel_cierra_estancia_y_deja_pendiente_pesaje(self):
        self.iniciar()
        self.completar()
        estancia = self.enviar()
        UnidadFrio.objects.filter(pk=self.tunel.pk).update(activo=False)
        retirada = retirar_de_tunel(partida=self.partida, usuario=self.jefe)
        self.assertEqual(retirada.pk, estancia.pk)
        self.assertEqual(retirada.retirado_por, self.jefe)
        self.assertIsNotNone(retirada.fecha_hora_salida)
        self.assertEqual(self.situacion(), "PENDIENTE_PESAJE")
        with self.assertRaises(ValidationError):
            retirar_de_tunel(partida=self.partida, usuario=self.jefe)

    def test_no_retirar_sin_tunel_o_con_estancias_ambiguas(self):
        with self.assertRaises(ValidationError):
            retirar_de_tunel(partida=self.partida, usuario=self.jefe)
        self.iniciar()
        self.completar()
        self.enviar()
        EstanciaPartida.objects.create(partida=self.partida, unidad_frio=self.tunel,
                                       fecha_hora_ingreso=timezone.now(), ingresado_por=self.usuario)
        with self.assertRaises(ValidationError):
            retirar_de_tunel(partida=self.partida, usuario=self.jefe)

    def test_no_retirar_mantencion_como_tunel(self):
        camara = UnidadFrio.objects.create(nombre="Mantención", tipo="MANTENCION")
        enviar_a_mantencion(partida=self.partida, cantidad_kg=Decimal("500"), unidad=camara, usuario=self.usuario)
        with self.assertRaises(ValidationError):
            retirar_de_tunel(partida=self.partida, usuario=self.jefe)
        self.assertIsNone(EstanciaPartida.objects.get().fecha_hora_salida)

    def test_rollback_tunel_reabre_general(self):
        self.iniciar()
        self.completar()
        with patch.object(EstanciaPartida, "save", side_effect=RuntimeError), self.assertRaises(RuntimeError):
            self.enviar()
        self.assertIsNone(EventoProceso.objects.get(etapa_ruta__isnull=True).fecha_hora_termino)
        self.assertEqual(self.situacion(), "EN_PROCESO")

    def test_rollback_inicio_no_asigna_ruta_ni_divide(self):
        with patch.object(EventoProceso, "save", side_effect=RuntimeError), self.assertRaises(RuntimeError):
            self.iniciar(cantidad="300")
        self.partida.refresh_from_db()
        self.assertIsNone(self.partida.ruta_proceso_id)
        self.assertEqual(self.partida.estado, "ACTIVA")
        self.assertEqual(PartidaProceso.objects.count(), 1)

    def test_form_tunel_solo_unidades_activas_correctas_sin_cantidad(self):
        UnidadFrio.objects.create(nombre="Mantención", tipo="MANTENCION")
        UnidadFrio.objects.create(nombre="Almacenamiento", tipo="ALMACENAMIENTO")
        UnidadFrio.objects.create(nombre="Túnel inactivo", tipo="TUNEL_CONGELADO", activo=False)
        form = TunelPartidaForm()
        self.assertEqual(list(form.fields), ["unidad"])
        self.assertEqual(list(form.fields["unidad"].queryset), [self.tunel])

    def test_data_migration_repetible_no_sobrescribe_catalogo(self):
        tipo = self.tipos["PROCESAMIENTO"]
        tipo.nombre = "Nombre existente"
        tipo.activo = False
        tipo.save()
        migracion = import_module("apps.trazabilidad.migrations.0005_catalogo_procesamiento")
        with connection.schema_editor() as editor:
            migracion.asegurar_tipos(apps, editor)
            migracion.asegurar_tipos(apps, editor)
        tipo.refresh_from_db()
        self.assertEqual(tipo.nombre, "Nombre existente")
        self.assertFalse(tipo.activo)
        self.assertEqual(TipoProceso.objects.filter(codigo__in=self.tipos).count(), 4)

    def test_ruta_usada_no_admite_cambios_estructurales_ni_borrado(self):
        self.iniciar()
        for campo, valor in (("nombre", "Otro"), ("especie", self.otra_especie)):
            ruta = RutaProceso.objects.get(pk=self.ruta.pk)
            setattr(ruta, campo, valor)
            with self.assertRaises(ValidationError):
                ruta.save()
        with self.assertRaises(ProtectedError):
            self.ruta.delete()
        etapa = self.etapas[0]
        etapa.orden = 9
        with self.assertRaises(ValidationError):
            etapa.save()
        with self.assertRaises(ValidationError):
            etapa.delete()
        with self.assertRaises(ValidationError):
            EtapaRutaProceso.objects.create(ruta=self.ruta, tipo_proceso=self.tipos["FILETEO"], orden=9)

    def test_admin_ruta_usada_readonly_y_etapas_no_editables(self):
        self.iniciar()
        request = RequestFactory().get("/admin/")
        request.user = Usuario.objects.create_superuser(username="admin_catalogo")
        ruta_admin = admin.site._registry[RutaProceso]
        etapa_admin = admin.site._registry[EtapaRutaProceso]
        self.assertIn("especie", ruta_admin.get_readonly_fields(request, self.ruta))
        self.assertFalse(ruta_admin.has_delete_permission(request, self.ruta))
        self.assertFalse(etapa_admin.has_change_permission(request, self.etapas[0]))
        self.assertFalse(etapa_admin.has_delete_permission(request, self.etapas[0]))
        self.assertFalse(etapa_admin.get_actions(request))
        self.client.force_login(request.user)
        respuesta = self.client.get(reverse("admin:trazabilidad_rutaproceso_change", args=[self.ruta.pk]))
        self.assertContains(respuesta, "Filete IQF")
        respuesta = self.client.post(reverse("admin:trazabilidad_etaparutaproceso_delete", args=[self.etapas[0].pk]), {"post": "yes"})
        self.assertEqual(respuesta.status_code, 403)

    def test_admin_configura_ruta_con_inline_y_rechaza_final_invalido(self):
        administrador = Usuario.objects.create_superuser(username="admin_ruta")
        self.client.force_login(administrador)
        url = reverse("admin:trazabilidad_rutaproceso_add")
        datos = {
            "especie": self.especie.pk, "nombre": "Ruta desde Admin", "activo": "on",
            "etapas-TOTAL_FORMS": "2", "etapas-INITIAL_FORMS": "0",
            "etapas-MIN_NUM_FORMS": "0", "etapas-MAX_NUM_FORMS": "1000",
            "etapas-0-orden": "1", "etapas-0-tipo_proceso": self.tipos["FILETEO"].pk,
            "etapas-1-orden": "2", "etapas-1-tipo_proceso": self.tipos["EMPARRILLADO"].pk,
            "_save": "Guardar",
        }
        self.assertEqual(self.client.post(url, datos).status_code, 302)
        self.assertEqual(RutaProceso.objects.get(nombre="Ruta desde Admin").etapas.count(), 2)
        datos["nombre"] = "Ruta inválida"
        datos["etapas-1-tipo_proceso"] = self.tipos["FILETEO"].pk
        respuesta = self.client.post(url, datos)
        self.assertContains(respuesta, "última etapa")
        self.assertFalse(RutaProceso.objects.filter(nombre="Ruta inválida").exists())

    def test_admin_puede_desactivar_ruta_usada_sin_cambiar_historia(self):
        self.iniciar()
        administrador = Usuario.objects.create_superuser(username="admin_desactivar")
        self.client.force_login(administrador)
        datos = {"predeterminada": "on", "etapas-TOTAL_FORMS": "3", "etapas-INITIAL_FORMS": "3",
                 "etapas-MIN_NUM_FORMS": "0", "etapas-MAX_NUM_FORMS": "1000", "_save": "Guardar"}
        for i, etapa in enumerate(self.etapas):
            datos[f"etapas-{i}-id"] = etapa.pk
            datos[f"etapas-{i}-ruta"] = self.ruta.pk
        respuesta = self.client.post(reverse("admin:trazabilidad_rutaproceso_change", args=[self.ruta.pk]), datos)
        self.assertEqual(respuesta.status_code, 302)
        self.ruta.refresh_from_db()
        self.assertFalse(self.ruta.activo)
        self.assertEqual(self.ruta.etapas.count(), 3)

    def test_vistas_roles_y_flujo_completo(self):
        encargada, _ = Rol.objects.get_or_create(codigo="ENCARGADA", defaults={"nombre": "Encargada"})
        usuarios = [self.jefe, self.usuario, Usuario.objects.create_user(username="encargada_proc", rol=encargada),
                    Usuario.objects.create_superuser(username="super_proc")]
        for usuario in usuarios:
            with self.subTest(usuario=usuario.username):
                partida = self.crear_partida()
                self.client.force_login(usuario)
                respuesta = self.client.post(reverse("trazabilidad:procesar_partida", args=[partida.pk]),
                                             {"cantidad_kg": "300", "ruta": self.ruta.pk})
                self.assertEqual(respuesta.status_code, 302)
                seleccionada = partida.subpartidas.get(cantidad_inicial_kg=300)
                url = reverse("trazabilidad:gestionar_procesamiento", args=[seleccionada.pk])
                self.assertContains(self.client.get(url), f"Procesamiento de {self.especie.nombre}")
                for etapa in self.etapas:
                    for accion in ("iniciar_etapa", "finalizar_etapa"):
                        self.assertEqual(self.client.post(reverse(f"trazabilidad:{accion}", args=[seleccionada.pk, etapa.pk])).status_code, 302)
                self.assertContains(self.client.get(url), "Enviar a túnel de congelado")
                self.assertEqual(self.client.post(reverse("trazabilidad:enviar_partida_tunel", args=[seleccionada.pk]), {"unidad": self.tunel.pk}).status_code, 302)
                self.assertEqual(self.situacion(seleccionada), "EN_CONGELACION")
                self.assertEqual(self.client.post(reverse("trazabilidad:retirar_partida_tunel", args=[seleccionada.pk])).status_code, 302)
                self.assertEqual(self.situacion(seleccionada), "PENDIENTE_PESAJE")

    def test_anonimo_sin_rol_operativo_y_gets_no_mutan(self):
        urls = [reverse("trazabilidad:" + nombre, args=[self.partida.pk]) for nombre in
                ("gestionar_procesamiento", "enviar_partida_tunel", "retirar_partida_tunel")]
        urls += [reverse("trazabilidad:" + nombre, args=[self.partida.pk, self.etapas[0].pk])
                 for nombre in ("iniciar_etapa", "finalizar_etapa")]
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 302)
            self.assertEqual(self.client.post(url).status_code, 302)
        rol = Rol.objects.create(codigo="CONSULTOR", nombre="Consultor")
        self.client.force_login(Usuario.objects.create_user(username="consultor", rol=rol))
        for url in urls:
            self.assertEqual(self.client.post(url).status_code, 403)
        self.client.force_login(self.usuario)
        for url in urls[2:]:
            self.assertEqual(self.client.get(url).status_code, 405)
        self.assertFalse(EventoProceso.objects.exists())
        self.assertNotEqual(self.client.get(reverse("admin:trazabilidad_rutaproceso_add")).status_code, 200)

    def test_acciones_segun_situacion_y_etapas(self):
        self.client.force_login(self.usuario)
        detalle = reverse("trazabilidad:detalle_partida", args=[self.partida.pk])
        gestionar = reverse("trazabilidad:gestionar_procesamiento", args=[self.partida.pk])
        self.assertNotContains(self.client.get(detalle), "Gestionar procesamiento")
        self.iniciar()
        self.assertContains(self.client.get(detalle), "Gestionar procesamiento")
        respuesta = self.client.get(gestionar)
        self.assertNotContains(respuesta, "Enviar a túnel de congelado")
        self.assertContains(respuesta, "Iniciar etapa", count=1)
        self.empezar()
        respuesta = self.client.get(gestionar)
        self.assertContains(respuesta, "Finalizar etapa", count=1)
        self.assertNotContains(respuesta, "Iniciar etapa")
        self.terminar()
        for indice in (1, 2):
            self.empezar(indice)
            self.terminar(indice)
        self.assertContains(self.client.get(gestionar), "Enviar a túnel de congelado")
        self.enviar()
        respuesta = self.client.get(detalle)
        self.assertContains(respuesta, "Retirar del túnel")
        self.assertNotContains(respuesta, "Gestionar procesamiento")
        retirar_de_tunel(partida=self.partida, usuario=self.usuario)
        respuesta = self.client.get(detalle)
        self.assertContains(respuesta, "Registra el peso obtenido antes de continuar al empaque.")
        for accion in ("Retirar del túnel", "Gestionar procesamiento", "Enviar a proceso", "Enviar a cámara"):
            self.assertNotContains(respuesta, accion)

    def test_filtros_congelacion_pesaje_sin_n_mas_uno(self):
        self.iniciar()
        self.completar()
        self.enviar()
        self.client.force_login(self.usuario)
        url = reverse("trazabilidad:lista_partidas")
        respuesta = self.client.get(url, {"situacion": "EN_CONGELACION"})
        self.assertEqual([p.pk for p in respuesta.context["page_obj"]], [self.partida.pk])
        retirar_de_tunel(partida=self.partida, usuario=self.usuario)
        respuesta = self.client.get(url, {"situacion": "PENDIENTE_PESAJE"})
        self.assertEqual([p.pk for p in respuesta.context["page_obj"]], [self.partida.pk])
        gestionar = reverse("trazabilidad:gestionar_procesamiento", args=[self.partida.pk])
        with CaptureQueriesContext(connection) as consultas:
            self.client.get(gestionar)
        self.assertLessEqual(len(consultas), 8)


class ConcurrenciaProcesamientoTests(DatosProcesamiento, TransactionTestCase):
    def setUp(self):
        self.preparar()
        self.iniciar()

    def competir(self, servicio, **kwargs):
        barrera = Barrier(2)

        def ejecutar(_):
            close_old_connections()
            try:
                partida = PartidaProceso.objects.get(pk=self.partida.pk)
                usuario = Usuario.objects.get(pk=self.usuario.pk)
                barrera.wait(timeout=10)
                try:
                    servicio(partida=partida, usuario=usuario, **kwargs)
                except ValidationError:
                    return "rechazada"
                return "aceptada"
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            resultados = list(pool.map(ejecutar, range(2)))
        self.assertCountEqual(resultados, ["aceptada", "rechazada"])

    @skipUnlessDBFeature("has_select_for_update")
    def test_doble_inicio_misma_etapa(self):
        self.competir(iniciar_etapa_proceso, etapa=self.etapas[0])
        self.assertEqual(EventoProceso.objects.filter(etapa_ruta=self.etapas[0]).count(), 1)

    @skipUnlessDBFeature("has_select_for_update")
    def test_doble_envio_tunel(self):
        self.completar()
        self.competir(enviar_a_tunel, unidad=self.tunel)
        self.assertEqual(EstanciaPartida.objects.count(), 1)

    @skipUnlessDBFeature("has_select_for_update")
    def test_doble_retiro_tunel(self):
        self.completar()
        self.enviar()
        self.competir(retirar_de_tunel)
        self.assertEqual(EstanciaPartida.objects.count(), 1)
        self.assertEqual(self.situacion(), "PENDIENTE_PESAJE")
