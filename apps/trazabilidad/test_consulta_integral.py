from decimal import Decimal
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from apps.usuarios.models import Rol, Usuario
from apps.inventario.models import EstanciaCaja, DetalleDespacho, BajaCaja
from apps.inventario.services import ingresar_caja, registrar_baja
from apps.inventario.test_despachos import DatosDespacho
from .consulta_integral import buscar_integral, ficha_integral
from .models import PartidaProceso, ConsumoLote, Correccion, Caja, LoteProduccion
from .services import _dividir_partida, enviar_a_tunel, retirar_de_tunel, registrar_peso_postproceso


class ConsultaIntegralTests(DatosDespacho, TestCase):
    def setUp(self):
        self.preparar_despacho()
        self.client.force_login(self.jefe)

    def ficha(self, tipo="lote", objeto=None):
        return ficha_integral(self.jefe, tipo, (objeto or self.lote).pk)

    def test_busquedas_tipos_ids_qr_y_coincidencias(self):
        despacho = self.despachar()
        detalle = self.partida.detalle_recepcion
        casos = [("origen", detalle.origen_sernapesca.folio_origen, detalle.origen_sernapesca_id),
            ("recepcion", str(detalle.recepcion_id), detalle.recepcion_id),
            ("seguimiento", str(self.partida.pk), self.partida.pk),
            ("lote", self.lote.codigo_lote, self.lote.pk),
            ("caja", str(self.registro.pk), self.registro.pk),
            ("caja", str(self.registro.identificador_qr), self.registro.pk),
            ("caja", f"https://ejemplo.test/producto-terminado/cajas/qr/{self.registro.identificador_qr}/", self.registro.pk),
            ("despacho", str(despacho.pk), despacho.pk)]
        for tipo, q, pk in casos:
            with self.subTest(tipo=tipo, q=q):
                self.assertIn(pk, list(buscar_integral(self.jefe, tipo, q).values_list("pk", flat=True)))
                respuesta = self.client.get(reverse("trazabilidad:consulta_integral"), {"tipo": tipo, "q": q})
                self.assertEqual(respuesta.status_code, 200)
                self.assertContains(respuesta, reverse("trazabilidad:ficha_integral", args=[tipo, pk]))
        self.assertFalse(buscar_integral(self.jefe, "recepcion", "9" * 100).exists())
        self.assertFalse(buscar_integral(self.jefe, "caja", "uuid-invalido").exists())

    def test_recorrido_inverso_y_adelante_no_asigna_proporcion_a_caja(self):
        detalle = self.partida.detalle_recepcion
        hacia_atras = self.ficha("caja", self.registro)
        self.assertEqual({d.pk for d in hacia_atras["detalles"]}, {detalle.pk})
        self.assertEqual({p.pk for p in hacia_atras["partidas"]}, {self.partida.pk})
        hacia_adelante = self.ficha("origen", detalle.origen_sernapesca)
        self.assertEqual({c.pk for c in hacia_adelante["cajas"]}, {self.registro.pk})
        respuesta = self.client.get(reverse("trazabilidad:ficha_integral", args=["caja", self.registro.pk]))
        self.assertNotContains(respuesta, "Esta caja pertenece a un lote elaborado con producto de varios seguimientos.")
        self.assertContains(respuesta, "Ruta y operaciones")
        self.assertContains(respuesta, "Operaciones realizadas")

    def test_varios_aportantes_lotes_cajas_y_despachos_parciales(self):
        otra = self.otra_lista(peso="40")
        self.agregar(self.lote, partida=otra, cantidad="20", usuario=self.jefe)
        otro_lote = self.crear_lote(partida=otra, cantidad_kg=Decimal("20"), usuario=self.jefe)
        otra_caja = self.caja()
        tercera = self.caja()
        ingresar_caja(caja=otra_caja, unidad=self.camara1, usuario=self.usuario)
        primero = self.despachar()
        segundo = self.despachar(cajas=[otra_caja])
        registrar_baja(caja=tercera, usuario=self.usuario, tipo="DANO", motivo="Rotura documentada")
        datos = self.ficha()
        self.assertEqual({a.partida_id for a in datos["aportes"]}, {self.partida.pk, otra.pk})
        self.assertEqual({d["registro"].pk for d in datos["despachos"]}, {primero.pk, segundo.pk})
        self.assertEqual(len(datos["cajas"]), 3)
        self.assertEqual(len(datos["bajas"]), 1)
        self.assertEqual(dict(datos["lotes"][0].distribucion), {"Despachada": 2, "Baja": 1})
        origen = self.ficha("origen", self.partida.detalle_recepcion.origen_sernapesca)
        self.assertIn(otro_lote.pk, {l.pk for l in origen["lotes"]})
        for tipo, objeto in (("lote", self.lote), ("despacho", primero), ("seguimiento", otra)):
            self.assertEqual(self.client.get(reverse("trazabilidad:ficha_integral", args=[tipo, objeto.pk])).status_code, 200)

    def test_divisiones_no_suman_padre_y_hijas(self):
        padre = self.crear_partida()
        seleccionada, restante = _dividir_partida(partida=padre, cantidad_seleccionada=Decimal("200"), usuario=self.usuario)
        datos = self.ficha("seguimiento", seleccionada)
        self.assertEqual({p.pk for p in datos["partidas"]}, {padre.pk, seleccionada.pk, restante.pk})
        self.assertEqual(datos["conciliacion"]["hojas"], Decimal("500"))
        self.assertEqual(datos["conciliacion"]["recepcion"], Decimal("500"))
        divisiones = [e for e in datos["timeline"] if e["clase"] == "División: seguimiento hijo"]
        self.assertEqual(len(divisiones), 2)
        self.assertIsNone(datos["conciliacion"]["postproceso"])

    def test_flujo_completo_con_division_y_recorrido_inverso(self):
        padre = self.crear_partida()
        self.iniciar(partida=padre, cantidad="100")
        hija = padre.subpartidas.get(ruta_proceso=self.ruta)
        self.completar(partida=hija)
        enviar_a_tunel(partida=hija, unidad=self.tunel, usuario=self.usuario)
        retirar_de_tunel(partida=hija, usuario=self.usuario)
        registrar_peso_postproceso(partida=hija, peso_kg=Decimal("80"), usuario=self.usuario)
        lote = self.crear_lote(partida=hija, cantidad_kg=Decimal("60"), usuario=self.jefe)
        caja = self.caja(lote=lote)
        ingresar_caja(caja=caja, unidad=self.camara1, usuario=self.usuario)
        despacho = self.despachar(cajas=[caja])
        datos = self.ficha("despacho", despacho)
        self.assertEqual({d.pk for d in datos["detalles"]}, {padre.detalle_recepcion_id})
        self.assertEqual({l.pk for l in datos["lotes"]}, {lote.pk})
        self.assertEqual(datos["conciliacion"]["hojas"], Decimal("500"))
        self.assertEqual(datos["conciliacion"]["procesado"], Decimal("100"))
        self.assertEqual(datos["conciliacion"]["postproceso"], Decimal("80"))
        self.assertEqual(datos["conciliacion"]["asignado"], Decimal("60"))
        self.assertEqual(datos["conciliacion"]["cajas"], Decimal("25"))
        self.assertIn(caja.pk, {c.pk for c in self.ficha("recepcion", padre.detalle_recepcion.recepcion)["cajas"]})

    def test_correcciones_por_entidad_e_id_no_por_texto(self):
        recibo = self.partida.detalle_recepcion.recepcion_id
        correcta = Correccion.objects.create(usuario=self.jefe, entidad_afectada="Recepcion", identificador_registro=str(recibo), campo="observaciones", valor_anterior="", valor_nuevo="Revisado", motivo="Corrección documentada")
        Correccion.objects.create(usuario=self.jefe, entidad_afectada="OtraEntidad", identificador_registro=str(recibo), campo="observaciones", valor_nuevo=self.lote.codigo_lote, motivo="Mismo texto, otra entidad")
        datos = self.ficha()
        self.assertEqual([c.pk for c in datos["correcciones"]], [correcta.pk])
        self.assertEqual(len([e for e in datos["timeline"] if e["clase"] == "Corrección auditada"]), 1)

    def test_historia_incompleta_no_inventa_pesos_ni_eventos(self):
        Caja.objects.filter(pk=self.registro.pk).update(peso_neto_kg=None)
        datos = self.ficha()
        self.assertIsNone(datos["conciliacion"]["cajas"])
        self.assertIsNone(datos["lotes"][0].total_empacado)
        self.assertIsNone(datos["lotes"][0].disponible_empacar)
        vacio = LoteProduccion.objects.create(codigo_lote="HISTORICO-SIN-ORIGEN", especie=self.especie,
            fecha_elaboracion=timezone.localdate(), registrado_por=self.jefe)
        historial = self.ficha(objeto=vacio)
        self.assertEqual(historial["partidas"], [])
        self.assertIsNone(historial["lotes"][0].total_asignado)
        self.assertEqual([e["clase"] for e in historial["timeline"]], ["Registro del lote"])
        self.assertEqual(self.client.get(reverse("trazabilidad:ficha_integral", args=["lote", vacio.pk])).status_code, 200)

    def test_cronologia_estable_y_fechas_reales(self):
        despacho = self.despachar()
        datos = self.ficha()
        orden = [(e["fecha"], e["orden"], e["identificador"]) for e in datos["timeline"]]
        self.assertEqual(orden, sorted(orden))
        self.assertEqual(orden, [(e["fecha"], e["orden"], e["identificador"]) for e in self.ficha()["timeline"]])
        evento = next(e for e in datos["timeline"] if e["clase"] == "Despacho")
        self.assertEqual(evento["fecha"], despacho.fecha_hora_despacho)
        self.assertIn("Almacenamiento de caja / ingreso", {e["clase"] for e in datos["timeline"]})
        self.assertIn("Frío de proceso / ingreso", {e["clase"] for e in datos["timeline"]})

    def test_permisos_en_vistas_selectores_y_enlaces_qr(self):
        url = reverse("trazabilidad:ficha_integral", args=["caja", self.registro.pk])
        encargada = Usuario.objects.create_user(username="consulta_encargada", rol=Rol.objects.get(codigo="ENCARGADA"))
        for usuario in (self.jefe, encargada):
            self.client.force_login(usuario)
            self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_login(self.usuario)
        for tipo, objeto in (("caja", self.registro), ("lote", self.lote), ("seguimiento", self.partida), ("recepcion", self.partida.detalle_recepcion.recepcion)):
            self.assertEqual(self.client.get(reverse("trazabilidad:ficha_integral", args=[tipo, objeto.pk])).status_code, 403)
            with self.assertRaises(PermissionDenied):
                ficha_integral(self.usuario, tipo, objeto.pk)
        with self.assertRaises(PermissionDenied):
            buscar_integral(self.usuario, "lote", self.lote.codigo_lote)
        self.assertEqual(self.client.get(reverse("trazabilidad:consulta_integral")).status_code, 403)
        qr = self.client.get(reverse("producto_terminado:consulta_caja_qr", args=[self.registro.identificador_qr]))
        self.assertNotContains(qr, "Ver historial del producto")
        self.client.logout()
        respuesta = self.client.get(url)
        self.assertEqual(respuesta.status_code, 302)
        self.assertIn("next=", respuesta.url)

    def test_consultas_constantes_y_cajas_paginadas(self):
        url = reverse("trazabilidad:ficha_integral", args=["lote", self.lote.pk])
        with CaptureQueriesContext(connection) as pocas:
            self.client.get(url)
        for i in range(23):
            Caja.objects.create(lote_produccion=self.lote, peso_neto_kg=None, fecha_armado=timezone.now(), registrado_por=self.usuario)
        with CaptureQueriesContext(connection) as muchas:
            respuesta = self.client.get(url, {"cajas_page": 2})
        self.assertEqual(len(pocas), len(muchas))
        self.assertEqual(len(respuesta.context["pagina_cajas"]), 4)
        self.assertEqual(respuesta.context["pagina_cajas"].paginator.count, 24)

    def test_get_no_modifica_historial_y_post_no_permitido(self):
        antes = (PartidaProceso.objects.count(), ConsumoLote.objects.count(), Caja.objects.count(), EstanciaCaja.objects.count(), DetalleDespacho.objects.count(), BajaCaja.objects.count())
        url = reverse("trazabilidad:ficha_integral", args=["lote", self.lote.pk])
        self.client.get(url)
        self.assertEqual(self.client.post(url, {}).status_code, 405)
        despues = (PartidaProceso.objects.count(), ConsumoLote.objects.count(), Caja.objects.count(), EstanciaCaja.objects.count(), DetalleDespacho.objects.count(), BajaCaja.objects.count())
        self.assertEqual(antes, despues)
