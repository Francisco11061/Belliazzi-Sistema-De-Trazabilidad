"""Proyección documental de relaciones reales; nunca asigna aportes físicos a cajas."""
from collections import Counter, defaultdict
from decimal import Decimal
from uuid import UUID
from urllib.parse import urlsplit

from django.core.exceptions import PermissionDenied
from django.db.models import Q, Prefetch
from django.shortcuts import get_object_or_404
from django.urls import reverse
from apps.usuarios.permisos import tiene_rol
from apps.inventario.models import Despacho, DetalleDespacho, EstanciaCaja, BajaCaja
from apps.inventario.selectors import cajas_con_inventario
from .models import (OrigenSernapesca, Recepcion, DetalleRecepcion, PartidaProceso, EventoProceso,
    EstanciaPartida, Pesaje, MermaProceso, ConsumoLote, LoteProduccion, Caja, ComposicionCaja, Correccion)
from .selectors import lotes_con_empaque, partidas_con_disponibilidad, presentar_partida

TIPOS = (("origen", "Folio Sernapesca"), ("recepcion", "Número de recepción"),
         ("seguimiento", "Identificador de seguimiento"), ("lote", "Código de lote"),
         ("caja", "Caja / código / QR"), ("despacho", "Número de despacho"))
MODELOS = dict(origen=OrigenSernapesca, recepcion=Recepcion, seguimiento=PartidaProceso,
               lote=LoteProduccion, caja=Caja, despacho=Despacho)


def exigir_consulta(usuario):
    if not tiene_rol(usuario, "JEFE", "ENCARGADA"):
        raise PermissionDenied


def enlace(tipo, pk):
    return reverse("trazabilidad:ficha_integral", args=[tipo, pk])


def buscar_integral(usuario, tipo, termino):
    exigir_consulta(usuario)
    modelo = MODELOS[tipo]
    q = termino.strip()
    if tipo == "origen":
        consulta = modelo.objects.filter(folio_origen__icontains=q)
    elif tipo == "lote":
        consulta = modelo.objects.filter(Q(codigo_lote__icontains=q) | Q(codigo_lote=q.replace(" ", "")))
    elif tipo == "caja":
        filtro = Q(codigo_caja__icontains=q)
        if q.isdecimal() and len(q) <= 18:
            filtro |= Q(pk=int(q))
        try:
            identificador = UUID(urlsplit(q).path.strip("/").split("/")[-1])
        except (ValueError, IndexError):
            pass
        else:
            filtro |= Q(identificador_qr=identificador)
        consulta = modelo.objects.filter(filtro)
    else:
        consulta = modelo.objects.filter(pk=int(q)) if q.isdecimal() and len(q) <= 18 else modelo.objects.none()
    return consulta.order_by("pk")


def familia_partidas(ids):
    """Sube a los padres y recorre todas las divisiones, por niveles y con control de ciclos."""
    vistos, frontera = set(ids), set(ids)
    while frontera:
        padres = set(PartidaProceso.objects.filter(pk__in=frontera, partida_padre__isnull=False).values_list("partida_padre_id", flat=True))
        frontera = padres - vistos
        vistos.update(frontera)
    frontera = set(vistos)
    while frontera:
        hijas = set(PartidaProceso.objects.filter(partida_padre_id__in=frontera).values_list("pk", flat=True))
        frontera = hijas - vistos
        vistos.update(frontera)
    return vistos


def suma_documentada(valores):
    valores = list(valores)
    return sum(valores, Decimal("0")) if valores and all(v is not None for v in valores) else None


def ficha_integral(usuario, tipo, pk):
    exigir_consulta(usuario)
    raiz = get_object_or_404(MODELOS[tipo], pk=pk)
    lote_ids, detalle_ids, partida_ids = set(), set(), set()
    if tipo == "lote":
        lote_ids.add(raiz.pk)
    elif tipo == "caja":
        lote_ids.add(raiz.lote_produccion_id)
    elif tipo == "despacho":
        lote_ids.update(DetalleDespacho.objects.filter(despacho=raiz).values_list("caja__lote_produccion_id", flat=True))
    elif tipo == "origen":
        detalle_ids.update(DetalleRecepcion.objects.filter(origen_sernapesca=raiz).values_list("pk", flat=True))
    elif tipo == "recepcion":
        detalle_ids.update(raiz.detalles.values_list("pk", flat=True))
    else:
        partida_ids.add(raiz.pk)
    partida_ids.update(ConsumoLote.objects.filter(lote_produccion_id__in=lote_ids).values_list("partida_id", flat=True))
    partida_ids.update(PartidaProceso.objects.filter(detalle_recepcion_id__in=detalle_ids).values_list("pk", flat=True))
    partida_ids = familia_partidas(partida_ids)
    if tipo not in ("lote", "caja", "despacho"):
        lote_ids.update(ConsumoLote.objects.filter(partida_id__in=partida_ids).values_list("lote_produccion_id", flat=True))
        # Incluir también los otros aportantes reales de cada lote alcanzado.
        partida_ids |= familia_partidas(set(ConsumoLote.objects.filter(lote_produccion_id__in=lote_ids).values_list("partida_id", flat=True)))
    partidas = list(partidas_con_disponibilidad().filter(pk__in=partida_ids).prefetch_related("ruta_proceso__etapas__tipo_proceso").order_by("creado_en", "pk"))
    por_partida = {p.pk: presentar_partida(p) for p in partidas}
    detalle_ids.update(p.detalle_recepcion_id for p in partidas)
    detalles = list(DetalleRecepcion.objects.filter(pk__in=detalle_ids).select_related("recepcion__registrado_por", "especie", "origen_sernapesca").order_by("pk"))
    recepciones = {d.recepcion_id: d.recepcion for d in detalles}
    if tipo == "recepcion":
        recepciones[raiz.pk] = raiz
    lotes = list(lotes_con_empaque().filter(pk__in=lote_ids).select_related("especie", "ruta_proceso", "registrado_por").order_by("pk"))
    aportes = list(ConsumoLote.objects.filter(lote_produccion_id__in=lote_ids).select_related("lote_produccion").order_by("pk"))
    cajas = list(cajas_con_inventario().filter(lote_produccion_id__in=lote_ids).select_related("registrado_por").prefetch_related(
        Prefetch("composiciones", queryset=ComposicionCaja.objects.select_related("presentacion").order_by("pk"))).order_by("pk"))
    caja_ids = [c.pk for c in cajas]
    eventos = list(EventoProceso.objects.filter(partida_id__in=partida_ids).select_related("tipo_proceso", "etapa_ruta", "iniciado_por", "finalizado_por").order_by("fecha_hora_inicio", "pk"))
    pesos = list(Pesaje.objects.filter(partida_id__in=partida_ids).select_related("registrado_por").order_by("fecha_hora_evento", "pk"))
    mermas = list(MermaProceso.objects.filter(partida_id__in=partida_ids).select_related("registrado_por").order_by("fecha_hora_evento", "pk"))
    frio = list(EstanciaPartida.objects.filter(partida_id__in=partida_ids).select_related("unidad_frio", "ingresado_por", "retirado_por").order_by("fecha_hora_ingreso", "pk"))
    almacenamiento = list(EstanciaCaja.objects.filter(caja_id__in=caja_ids).select_related("unidad_frio", "ingresado_por", "retirado_por").order_by("fecha_hora_ingreso", "pk"))
    despachos = list(DetalleDespacho.objects.filter(caja_id__in=caja_ids).select_related("despacho__registrado_por").order_by("despacho_id", "caja_id"))
    bajas = list(BajaCaja.objects.filter(caja_id__in=caja_ids).select_related("registrado_por").order_by("fecha_hora_evento", "pk"))
    vinculados = {"Recepcion": recepciones.keys(), "DetalleRecepcion": detalle_ids,
        "OrigenSernapesca": {d.origen_sernapesca_id for d in detalles}, "PartidaProceso": partida_ids,
        "LoteProduccion": lote_ids, "Caja": caja_ids, "Despacho": {d.despacho_id for d in despachos}}
    vinculados[raiz.__class__.__name__] = set(vinculados.get(raiz.__class__.__name__, ())) | {raiz.pk}
    filtro_correcciones = Q(pk__in=[])
    for entidad, ids in vinculados.items():
        filtro_correcciones |= Q(entidad_afectada=entidad, identificador_registro__in=[str(i) for i in ids])
    correcciones = list(Correccion.objects.filter(filtro_correcciones).select_related("usuario").order_by("fecha_hora", "pk"))
    for p in partidas:
        p.ejecutadas, p.pesos_documentados, p.mermas_documentadas, p.frio_documentado = [], [], [], []
    for registros, atributo in ((eventos, "ejecutadas"), (pesos, "pesos_documentados"), (mermas, "mermas_documentadas"), (frio, "frio_documentado")):
        for registro in registros:
            getattr(por_partida[registro.partida_id], atributo).append(registro)
    for p in partidas:
        completadas = {e.etapa_ruta_id for e in p.ejecutadas if e.fecha_hora_termino}
        p.etapas_pendientes = [e for e in p.ruta_proceso.etapas.all() if e.pk not in completadas] if p.ruta_proceso else []
        p.rendimiento_documentado = p.peso_final_kg / p.cantidad_inicial_kg * 100 if p.numero_postprocesos == 1 and p.cantidad_inicial_kg else None
        p.diferencia_peso = p.cantidad_inicial_kg - p.peso_final_kg if p.numero_postprocesos == 1 else None
    por_lote = defaultdict(list)
    for c in cajas:
        por_lote[c.lote_produccion_id].append(c)
    for lote in lotes:
        lote.distribucion = sorted(Counter(c.estado_visible for c in por_lote[lote.pk]).items())
        lote.asignaciones = [a for a in aportes if a.lote_produccion_id == lote.pk]
        # Sin aportes registrados no existe evidencia de un total asignado cero.
        if not lote.asignaciones:
            lote.total_asignado = lote.disponible_empacar = None
    timeline = []
    def evento(fecha, clase, registro, responsable, texto, destino, orden):
        if fecha is not None:
            timeline.append(dict(fecha=fecha, clase=clase, identificador=registro, responsable=responsable,
                                 texto=texto, url=destino, orden=orden))
    for r in recepciones.values():
        evento(r.fecha_hora_recepcion, "Recepción", r.pk, r.registrado_por, r.observaciones, enlace("recepcion", r.pk), 0)
    for p in partidas:
        if p.partida_padre_id:
            evento(p.creado_en, "División: seguimiento hijo", p.pk, p.creado_por, f"Padre #{p.partida_padre_id}; {p.cantidad_inicial_kg} kg", enlace("seguimiento", p.pk), 1)
    for e in eventos:
        texto = f"Seguimiento #{e.partida_id}: {e.tipo_proceso.nombre}. {e.observaciones}"
        evento(e.fecha_hora_inicio, "Inicio de operación", e.pk, e.iniciado_por, texto, enlace("seguimiento", e.partida_id), 2)
        evento(e.fecha_hora_termino, "Fin de operación", e.pk, e.finalizado_por, texto, enlace("seguimiento", e.partida_id), 3)
    for lista, tipo_registro, clase in ((frio, "seguimiento", "Frío de proceso"), (almacenamiento, "caja", "Almacenamiento de caja")):
        for e in lista:
            destino_id = e.partida_id if tipo_registro == "seguimiento" else e.caja_id
            texto = f"{tipo_registro.title()} #{destino_id}: {e.unidad_frio.nombre} ({e.unidad_frio.get_tipo_display()})"
            evento(e.fecha_hora_ingreso, clase + " / ingreso", e.pk, e.ingresado_por, texto, enlace(tipo_registro, destino_id), 4)
            evento(e.fecha_hora_salida, clase + " / salida", e.pk, e.retirado_por, texto, enlace(tipo_registro, destino_id), 5)
    for e in pesos:
        evento(e.fecha_hora_evento, "Pesaje", e.pk, e.registrado_por, f"Seguimiento #{e.partida_id}: {e.tipo_visible}, {e.peso_kg} kg", enlace("seguimiento", e.partida_id), 6)
    for e in mermas:
        evento(e.fecha_hora_evento, e.get_tipo_display(), e.pk, e.registrado_por, f"Seguimiento #{e.partida_id}: {e.cantidad_kg} kg. {e.motivo}", enlace("seguimiento", e.partida_id), 7)
    for e in lotes:
        evento(e.creado_en, "Registro del lote", e.pk, e.registrado_por, e.codigo_visible, enlace("lote", e.pk), 8)
    for e in cajas:
        evento(e.fecha_armado, "Armado de caja", e.pk, e.registrado_por, f"Lote #{e.lote_produccion_id}", enlace("caja", e.pk), 9)
    por_despacho = {raiz.pk: {"registro": raiz, "cajas": []}} if tipo == "despacho" else {}
    cajas_por_id = {c.pk: c for c in cajas}
    for d in despachos:
        if d.despacho_id not in por_despacho:
            por_despacho[d.despacho_id] = {"registro": d.despacho, "cajas": []}
        por_despacho[d.despacho_id]["cajas"].append(cajas_por_id[d.caja_id])
    for dato in por_despacho.values():
        e = dato["registro"]
        dato["peso_documentado"] = suma_documentada(c.peso_neto_kg for c in dato["cajas"])
        evento(e.fecha_hora_despacho, "Despacho", e.pk, e.registrado_por, e.destino, enlace("despacho", e.pk), 10)
    for e in bajas:
        evento(e.fecha_hora_evento, "Baja de caja", e.pk, e.registrado_por, f"Caja #{e.caja_id}: {e.motivo}", enlace("caja", e.caja_id), 11)
    for e in correcciones:
        evento(e.fecha_hora, "Corrección auditada", e.pk, e.usuario, f"{e.entidad_afectada} #{e.identificador_registro}: {e.campo}. {e.motivo}", "", 12)
    timeline.sort(key=lambda e: (e["fecha"], e["orden"], e["identificador"]))
    padres = {p.partida_padre_id for p in partidas if p.partida_padre_id}
    hojas = [p for p in partidas if p.pk not in padres and p.estado != "DIVIDIDA"]
    conciliacion = {
        "recepcion": suma_documentada(d.peso_recepcion_kg for d in detalles),
        "hojas": suma_documentada(p.cantidad_inicial_kg for p in hojas),
        "procesado": suma_documentada(p.cantidad_inicial_kg for p in hojas if p.procesamiento_iniciado),
        "postproceso": suma_documentada(p.peso_final_kg if p.numero_postprocesos == 1 else None for p in hojas if p.procesamiento_iniciado),
        "mermas": suma_documentada(m.cantidad_kg for m in mermas),
        "asignado": suma_documentada(a.cantidad_kg_utilizada for a in aportes),
        "cajas": suma_documentada(c.peso_neto_kg for c in cajas),
    }
    return dict(raiz=raiz, tipo=tipo, lotes=lotes, detalles=detalles, partidas=partidas, aportes=aportes,
        cajas=cajas, almacenamiento=almacenamiento, despachos=list(por_despacho.values()), bajas=bajas,
        correcciones=correcciones, timeline=timeline, conciliacion=conciliacion)
