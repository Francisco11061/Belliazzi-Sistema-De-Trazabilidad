import re
from datetime import datetime
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.usuarios.permisos import ROL_ENCARGADA, ROL_JEFE, ROL_OPERARIA, tiene_rol

from .models import Correccion, DetalleRecepcion, Especie, OrigenSernapesca, PartidaProceso, Recepcion
from .models import EstanciaPartida, EventoProceso, TipoProceso, UnidadFrio, RutaProceso, Pesaje, MermaProceso
from .selectors import recepcion_con_actividad, partidas_con_situacion, SITUACIONES_MERMA
from .rutas import validar_etapas_ruta
from .models import LoteProduccion, ConsumoLote
from .selectors import partidas_con_disponibilidad, puede_asignar_lote
from .models import Caja, ComposicionCaja, PresentacionBolsa
from .selectors import lotes_con_empaque, puede_crear_caja
from .constantes import PESO_MAXIMO_CAJA_KG
from .lotes import segmento_folio
from .selectors import origen_unico_lote


def _normalizar_texto(valor, campo):
    if not isinstance(valor, str):
        raise ValidationError({campo: "Debe ingresar un texto válido."})
    return valor.strip()


def _validar_datos_recepcion(*, fecha_hora_recepcion, origen, detalles, observaciones):
    detalles = list(detalles)
    if not detalles:
        raise ValidationError("Debe ingresar al menos una especie recibida.")

    if not isinstance(fecha_hora_recepcion, datetime) or timezone.is_naive(fecha_hora_recepcion):
        raise ValidationError({
            "fecha_hora_recepcion": "Debe ingresar una fecha y hora válida con zona horaria."
        })
    if fecha_hora_recepcion > timezone.now():
        raise ValidationError({
            "fecha_hora_recepcion": "La recepción no puede registrarse con una fecha y hora posterior a la actual."
        })

    textos = {
        campo: _normalizar_texto(origen.get(campo, ""), campo)
        for campo in ("folio_origen", "tipo_origen", "codigo_agente", "proveedor")
    }
    observaciones = _normalizar_texto(observaciones, "observaciones")
    if not textos["folio_origen"]:
        raise ValidationError({"folio_origen": "El folio de origen Sernapesca es obligatorio."})

    ids = []
    for datos in detalles:
        especie = datos.get("especie")
        if not isinstance(especie, Especie) or especie.pk is None:
            raise ValidationError("Debe seleccionar una especie válida.")
        ids.append(especie.pk)

    # Releer y bloquear las especies evita confiar en instancias desactualizadas
    # y mantiene su estado estable hasta terminar esta transacción.
    especies = Especie.objects.select_for_update().order_by("pk").in_bulk(ids)
    for pk in ids:
        especie = especies.get(pk)
        if especie is None:
            raise ValidationError("Debe seleccionar una especie válida.")
        if not especie.activo:
            raise ValidationError(f"La especie '{especie.nombre}' no se encuentra activa.")

    vistas = set()
    repetidas = set()
    errores = []
    for pk in ids:
        if pk in vistas and pk not in repetidas:
            errores.append(
                f"La especie '{especies[pk].nombre}' ya fue agregada a esta recepción. "
                "Consolide los pesos en una sola fila."
            )
            repetidas.add(pk)
        vistas.add(pk)
    if errores:
        raise ValidationError(errores)

    pendientes = []
    for datos, pk in zip(detalles, ids):
        detalle = DetalleRecepcion(
            especie=especies[pk],
            peso_origen_kg=datos["peso_origen_kg"],
            peso_recepcion_kg=datos["peso_recepcion_kg"],
        )
        # Validar y convertir los decimales con los campos del modelo antes
        # de compararlos; aún no existen recepción ni origen.
        detalle.clean_fields(exclude=["recepcion", "origen_sernapesca"])
        if detalle.peso_recepcion_kg > detalle.peso_origen_kg:
            raise ValidationError({
                "peso_recepcion_kg": "El peso en recepción no puede ser mayor que el peso de origen."
            })
        pendientes.append(detalle)

    return textos, observaciones, pendientes


@transaction.atomic
def registrar_recepcion(
    *, fecha_hora_recepcion, registrado_por, origen, detalles, observaciones="",
):
    textos, observaciones, pendientes = _validar_datos_recepcion(
        fecha_hora_recepcion=fecha_hora_recepcion, origen=origen,
        detalles=detalles, observaciones=observaciones,
    )
    recepcion = Recepcion(
        fecha_hora_recepcion=fecha_hora_recepcion,
        registrado_por=registrado_por,
        observaciones=observaciones,
    )
    recepcion.full_clean()
    recepcion.save()

    origen_sernapesca = OrigenSernapesca(**textos)
    origen_sernapesca.full_clean()
    origen_sernapesca.save()

    for detalle in pendientes:
        detalle.recepcion = recepcion
        detalle.origen_sernapesca = origen_sernapesca
        detalle.full_clean()
        detalle.save()
        PartidaProceso.objects.create(
            detalle_recepcion=detalle, cantidad_inicial_kg=detalle.peso_recepcion_kg,
            estado=PartidaProceso.Estado.ACTIVA, creado_por=registrado_por,
        )

    return recepcion


def _valor_historial(valor):
    if isinstance(valor, Especie):
        return valor.nombre
    if isinstance(valor, Decimal):
        return format(valor, ".2f")
    if isinstance(valor, datetime):
        return timezone.localtime(valor).isoformat(sep=" ")
    return "" if valor is None else str(valor)


@transaction.atomic
def corregir_recepcion(
    *, recepcion, usuario, fecha_hora_recepcion, observaciones, origen, detalles, motivo,
):
    if not tiene_rol(usuario, ROL_JEFE):
        raise PermissionDenied
    actual = Recepcion.objects.select_for_update().get(pk=recepcion.pk)
    existentes = list(
        actual.detalles.select_for_update().select_related("especie").order_by("pk")
    )
    origen_ids = {detalle.origen_sernapesca_id for detalle in existentes}
    if len(origen_ids) != 1:
        raise ValidationError(
            "Esta recepción utiliza una estructura histórica que no puede corregirse "
            "desde este formulario simplificado."
        )
    origen_actual = OrigenSernapesca.objects.select_for_update().get(pk=origen_ids.pop())
    detalles = list(detalles)
    enviados = [datos.get("detalle_id") for datos in detalles]
    reales = {detalle.pk for detalle in existentes}
    if (
        any(type(pk) is not int for pk in enviados)
        or len(enviados) != len(reales)
        or set(enviados) != reales
    ):
        raise ValidationError(
            "Debes corregir exactamente las especies existentes de esta recepción; "
            "no se permite agregar, quitar ni repetir detalles."
        )
    motivo = _normalizar_texto(motivo, "motivo")
    if not motivo:
        raise ValidationError("Indica el motivo de la corrección.")

    textos, observaciones, pendientes = _validar_datos_recepcion(
        fecha_hora_recepcion=fecha_hora_recepcion, origen=origen,
        detalles=detalles, observaciones=observaciones,
    )
    por_id = {detalle.pk: detalle for detalle in existentes}
    # Mismo orden de bloqueos que las operaciones: detalle y después partida.
    raices = list(PartidaProceso.objects.select_for_update().filter(
        detalle_recepcion_id__in=reales,
    ).order_by("pk"))
    procesada = recepcion_con_actividad(actual)
    cambios = []

    def preparar(objeto, valores):
        campos = []
        for campo, nuevo in valores.items():
            anterior = getattr(objeto, campo)
            if anterior != nuevo:
                cambios.append(Correccion(
                    usuario=usuario, entidad_afectada=type(objeto).__name__,
                    identificador_registro=str(objeto.pk), campo=campo,
                    valor_anterior=_valor_historial(anterior),
                    valor_nuevo=_valor_historial(nuevo), motivo=motivo,
                ))
                setattr(objeto, campo, nuevo)
                campos.append(campo)
        return campos

    campos_recepcion = preparar(actual, {
        "fecha_hora_recepcion": fecha_hora_recepcion, "observaciones": observaciones,
    })
    campos_origen = preparar(origen_actual, textos)
    # Un origen antiguo puede estar compartido entre recepciones. No extender
    # silenciosamente una corrección a otras recepciones.
    if campos_origen and origen_actual.detalles_recepcion.exclude(recepcion=actual).exists():
        raise ValidationError(
            "El origen está compartido con otras recepciones históricas y no puede "
            "corregirse desde este formulario simplificado."
        )

    actualizaciones = [(actual, campos_recepcion), (origen_actual, campos_origen)]
    for datos, pendiente in zip(detalles, pendientes):
        detalle = por_id[datos["detalle_id"]]
        valores = {
            "especie": pendiente.especie,
            "peso_origen_kg": pendiente.peso_origen_kg,
            "peso_recepcion_kg": pendiente.peso_recepcion_kg,
        }
        if procesada and any(getattr(detalle, campo) != valor for campo, valor in valores.items()):
            raise ValidationError(
                "Esta recepción ya inició su procesamiento. Las especies y sus pesos "
                "no pueden modificarse para preservar la trazabilidad."
            )
        actualizaciones.append((detalle, preparar(detalle, valores)))
        if not procesada:
            for raiz in raices:
                if raiz.detalle_recepcion_id == detalle.pk and raiz.cantidad_inicial_kg != detalle.peso_recepcion_kg:
                    raiz.cantidad_inicial_kg = detalle.peso_recepcion_kg
                    actualizaciones.append((raiz, ["cantidad_inicial_kg"]))

    if not cambios:
        raise ValidationError("No se detectaron cambios para registrar.")
    # Validar todo antes de escribir. Cualquier fallo posterior también revierte
    # tanto los cambios como su auditoría mediante transaction.atomic.
    for objeto, campos in actualizaciones:
        if campos:
            objeto.full_clean()
    for correccion in cambios:
        correccion.full_clean()
    for objeto, campos in actualizaciones:
        if campos:
            objeto.save(update_fields=campos)
    for correccion in cambios:
        correccion.save()
    return actual


def _bloquear_partida(partida, usuario):
    if not tiene_rol(usuario, ROL_JEFE, ROL_ENCARGADA, ROL_OPERARIA):
        raise PermissionDenied
    detalle_id = PartidaProceso.objects.values_list("detalle_recepcion_id", flat=True).get(pk=partida.pk)
    DetalleRecepcion.objects.select_for_update().get(pk=detalle_id)
    actual = PartidaProceso.objects.select_for_update().get(pk=partida.pk)
    if actual.estado != PartidaProceso.Estado.ACTIVA or actual.subpartidas.exists():
        raise ValidationError("Solo se puede operar un seguimiento activo sin divisiones.")
    return actual


def _validar_cantidad(partida, cantidad):
    if not isinstance(cantidad, Decimal) or not cantidad.is_finite():
        raise ValidationError("Ingrese una cantidad decimal válida.")
    if cantidad.as_tuple().exponent < -2:
        raise ValidationError("La cantidad admite como máximo dos decimales.")
    if cantidad <= 0 or cantidad > partida.cantidad_inicial_kg:
        raise ValidationError("La cantidad debe ser mayor que cero y no superar la cantidad de este seguimiento.")


def _comprobar_disponible(partida):
    if partida.ruta_proceso_id or partida.estancias_frio.filter(fecha_hora_salida__isnull=True).exists() or partida.eventos.exists():
        raise ValidationError("El producto no está disponible: está en cámara o ya inició procesamiento.")


@transaction.atomic
def _dividir_partida(*, partida, cantidad_seleccionada, usuario):
    partida = _bloquear_partida(partida, usuario)
    _validar_cantidad(partida, cantidad_seleccionada)
    _comprobar_disponible(partida)
    if cantidad_seleccionada == partida.cantidad_inicial_kg:
        return partida, None
    partida.estado = PartidaProceso.Estado.DIVIDIDA
    partida.save(update_fields=["estado"])
    hijas = [PartidaProceso.objects.create(
        detalle_recepcion_id=partida.detalle_recepcion_id, partida_padre=partida,
        cantidad_inicial_kg=cantidad, creado_por=usuario,
    ) for cantidad in (cantidad_seleccionada, partida.cantidad_inicial_kg - cantidad_seleccionada)]
    return tuple(hijas)


@transaction.atomic
def enviar_a_mantencion(*, partida, cantidad_kg, unidad, usuario):
    partida = _bloquear_partida(partida, usuario)
    _validar_cantidad(partida, cantidad_kg)
    _comprobar_disponible(partida)
    unidad = UnidadFrio.objects.select_for_update().filter(pk=unidad.pk).first()
    if unidad is None or not unidad.activo or unidad.tipo != UnidadFrio.TipoUnidad.MANTENCION:
        raise ValidationError("Seleccione una unidad activa de mantención.")
    seleccionada, restante = _dividir_partida(
        partida=partida, cantidad_seleccionada=cantidad_kg, usuario=usuario,
    )
    EstanciaPartida.objects.create(
        partida=seleccionada, unidad_frio=unidad,
        fecha_hora_ingreso=timezone.now(), ingresado_por=usuario,
    )
    return seleccionada, restante


@transaction.atomic
def retirar_de_mantencion(*, partida, cantidad_kg, usuario):
    partida = _bloquear_partida(partida, usuario)
    _validar_cantidad(partida, cantidad_kg)
    abiertas = list(partida.estancias_frio.select_for_update().filter(fecha_hora_salida__isnull=True))
    if len(abiertas) != 1 or abiertas[0].unidad_frio.tipo != UnidadFrio.TipoUnidad.MANTENCION:
        raise ValidationError("El seguimiento debe tener exactamente una estancia abierta en mantención.")
    if partida.eventos.exists():
        raise ValidationError("El producto ya inició procesamiento.")
    estancia = abiertas[0]
    ahora = timezone.now()
    estancia.fecha_hora_salida = ahora
    estancia.retirado_por = usuario
    estancia.save(update_fields=["fecha_hora_salida", "retirado_por"])
    seleccionada, restante = _dividir_partida(
        partida=partida, cantidad_seleccionada=cantidad_kg, usuario=usuario,
    )
    if restante:
        EstanciaPartida.objects.create(
            partida=restante, unidad_frio=estancia.unidad_frio,
            fecha_hora_ingreso=ahora, ingresado_por=usuario,
        )
    return seleccionada, restante


@transaction.atomic
def iniciar_procesamiento(*, partida, cantidad_kg, ruta, usuario):
    partida = _bloquear_partida(partida, usuario)
    _validar_cantidad(partida, cantidad_kg)
    _comprobar_disponible(partida)
    ruta, _ = _validar_ruta(partida, ruta, nueva=True)
    tipo = TipoProceso.objects.select_for_update().filter(codigo="PROCESAMIENTO").first()
    if tipo is None or not tipo.activo:
        raise ValidationError("El tipo de procesamiento general está inactivo.")
    seleccionada, restante = _dividir_partida(
        partida=partida, cantidad_seleccionada=cantidad_kg, usuario=usuario,
    )
    seleccionada.ruta_proceso = ruta
    seleccionada.save(update_fields=["ruta_proceso"])
    EventoProceso.objects.create(
        partida=seleccionada, tipo_proceso=tipo,
        fecha_hora_inicio=timezone.now(), iniciado_por=usuario,
    )
    return seleccionada, restante


def _validar_ruta(partida, ruta, *, nueva):
    ruta = RutaProceso.objects.select_for_update().filter(pk=getattr(ruta, "pk", None)).first()
    if ruta is None:
        raise ValidationError("Seleccione una ruta de procesamiento.")
    if ruta.especie_id != partida.detalle_recepcion.especie_id:
        raise ValidationError("La ruta debe corresponder a la especie de este seguimiento.")
    if nueva and not ruta.activo:
        raise ValidationError("La ruta seleccionada está inactiva.")
    etapas = list(ruta.etapas.select_for_update().select_related("tipo_proceso").order_by("orden"))
    validar_etapas_ruta(etapas, exigir_activos=nueva)
    return ruta, etapas


def _evento_general_abierto(partida):
    if partida.estancias_frio.filter(fecha_hora_salida__isnull=True).exists():
        raise ValidationError("El producto está en una unidad de frío, no en procesamiento.")
    generales = list(partida.eventos.select_for_update().filter(
        tipo_proceso__codigo="PROCESAMIENTO", etapa_ruta__isnull=True,
        fecha_hora_termino__isnull=True,
    ))
    if len(generales) != 1:
        raise ValidationError("El seguimiento debe tener exactamente un procesamiento general abierto.")
    return generales[0]


def _contexto_etapas(partida):
    ruta, etapas = _validar_ruta(partida, partida.ruta_proceso, nueva=False)
    general = _evento_general_abierto(partida)
    eventos = list(partida.eventos.select_for_update().exclude(pk=general.pk))
    por_etapa = {}
    tipos = {etapa.pk: etapa.tipo_proceso_id for etapa in etapas}
    for evento in eventos:
        if (evento.etapa_ruta_id not in tipos or evento.etapa_ruta_id in por_etapa
                or evento.tipo_proceso_id != tipos[evento.etapa_ruta_id]):
            raise ValidationError("El historial de etapas es inconsistente y requiere revisión.")
        por_etapa[evento.etapa_ruta_id] = evento
    return general, etapas, por_etapa


@transaction.atomic
def asignar_ruta_proceso(*, partida, ruta, usuario):
    partida = _bloquear_partida(partida, usuario)
    ruta, _ = _validar_ruta(partida, ruta, nueva=True)
    _evento_general_abierto(partida)
    if partida.ruta_proceso_id or partida.eventos.filter(etapa_ruta__isnull=False).exists():
        raise ValidationError("No se puede cambiar una ruta ya asignada o con etapas iniciadas.")
    if partida.eventos.exclude(tipo_proceso__codigo="PROCESAMIENTO").exists():
        raise ValidationError("Los eventos históricos específicos requieren revisión antes de asignar una ruta.")
    partida.ruta_proceso = ruta
    partida.save(update_fields=["ruta_proceso"])
    return partida


@transaction.atomic
def iniciar_etapa_proceso(*, partida, etapa, usuario):
    partida = _bloquear_partida(partida, usuario)
    _, etapas, eventos = _contexto_etapas(partida)
    actual = next((e for e in etapas if e.pk == etapa.pk), None)
    if actual is None:
        raise ValidationError("La etapa no pertenece a la ruta de este seguimiento.")
    if actual.pk in eventos:
        raise ValidationError("La etapa ya fue iniciada.")
    if any(e.fecha_hora_termino is None for e in eventos.values()):
        raise ValidationError("Debe finalizar la etapa en curso antes de iniciar otra.")
    if any(e.pk not in eventos or eventos[e.pk].fecha_hora_termino is None
           for e in etapas if e.orden < actual.orden):
        raise ValidationError("Debe completar todas las etapas anteriores; no se pueden saltar etapas.")
    return EventoProceso.objects.create(
        partida=partida, etapa_ruta=actual, tipo_proceso=actual.tipo_proceso,
        fecha_hora_inicio=timezone.now(), iniciado_por=usuario,
    )


@transaction.atomic
def finalizar_etapa_proceso(*, partida, etapa, usuario):
    partida = _bloquear_partida(partida, usuario)
    _, _, eventos = _contexto_etapas(partida)
    evento = eventos.get(etapa.pk)
    if evento is None or evento.fecha_hora_termino is not None:
        raise ValidationError("No existe una etapa abierta para finalizar.")
    evento.fecha_hora_termino = timezone.now()
    evento.finalizado_por = usuario
    evento.save(update_fields=["fecha_hora_termino", "finalizado_por"])
    return evento


@transaction.atomic
def enviar_a_tunel(*, partida, unidad, usuario):
    partida = _bloquear_partida(partida, usuario)
    general, etapas, eventos = _contexto_etapas(partida)
    if any(e.pk not in eventos or eventos[e.pk].fecha_hora_termino is None for e in etapas):
        raise ValidationError("Complete todas las etapas, incluido Emparrillado, antes de enviar al túnel.")
    unidad = UnidadFrio.objects.select_for_update().filter(pk=unidad.pk).first()
    if unidad is None or not unidad.activo or unidad.tipo != UnidadFrio.TipoUnidad.TUNEL_CONGELADO:
        raise ValidationError("Seleccione un túnel de congelado activo.")
    ahora = timezone.now()
    general.fecha_hora_termino = ahora
    general.finalizado_por = usuario
    general.save(update_fields=["fecha_hora_termino", "finalizado_por"])
    return EstanciaPartida.objects.create(
        partida=partida, unidad_frio=unidad, fecha_hora_ingreso=ahora, ingresado_por=usuario,
    )


@transaction.atomic
def retirar_de_tunel(*, partida, usuario):
    partida = _bloquear_partida(partida, usuario)
    abiertas = list(partida.estancias_frio.select_for_update().filter(fecha_hora_salida__isnull=True))
    if len(abiertas) != 1 or abiertas[0].unidad_frio.tipo != UnidadFrio.TipoUnidad.TUNEL_CONGELADO:
        raise ValidationError("El seguimiento debe tener exactamente una estancia abierta en túnel de congelado.")
    estancia = abiertas[0]
    estancia.fecha_hora_salida = timezone.now()
    estancia.retirado_por = usuario
    estancia.save(update_fields=["fecha_hora_salida", "retirado_por"])
    return estancia


def _validar_decimal_registro(modelo, campo, valor):
    if not isinstance(valor, Decimal) or not valor.is_finite():
        raise ValidationError("Ingrese una cantidad decimal válida.")
    # Reutilizar precisión y mínimo del modelo también en llamadas directas al service.
    modelo._meta.get_field(campo).clean(valor, None)


@transaction.atomic
def registrar_peso_postproceso(*, partida, peso_kg, usuario):
    partida = _bloquear_partida(partida, usuario)
    actual = partidas_con_situacion().get(pk=partida.pk)
    if actual.situacion != "PENDIENTE_PESAJE" or actual.tiene_postproceso:
        raise ValidationError("El seguimiento debe estar pendiente de pesaje y no tener un peso postproceso registrado.")
    _validar_decimal_registro(Pesaje, "peso_kg", peso_kg)
    pesaje = Pesaje(
        partida=partida, tipo=Pesaje.POSTPROCESO, peso_kg=peso_kg,
        fecha_hora_evento=timezone.now(), registrado_por=usuario,
    )
    pesaje.full_clean()
    pesaje.save()
    return pesaje


@transaction.atomic
def registrar_merma_proceso(*, partida, tipo, cantidad_kg, motivo, usuario):
    partida = _bloquear_partida(partida, usuario)
    actual = partidas_con_situacion().get(pk=partida.pk)
    if actual.situacion not in SITUACIONES_MERMA or actual.tiene_postproceso:
        raise ValidationError("Solo se pueden registrar pérdidas o mermas durante el proceso, la congelación o antes del pesaje postproceso.")
    _validar_decimal_registro(MermaProceso, "cantidad_kg", cantidad_kg)
    motivo = _normalizar_texto(motivo, "motivo")
    # Todas las escrituras operativas comparten el bloqueo detalle -> seguimiento.
    # Leer después de adquirirlo incluye los registros de la transacción anterior.
    cantidades = partida.mermas.select_for_update().values_list("cantidad_kg", flat=True)
    if sum(cantidades, Decimal("0.00")) + cantidad_kg > partida.cantidad_inicial_kg:
        raise ValidationError("Las pérdidas, descartes y mermas acumuladas no pueden superar la cantidad del seguimiento.")
    abiertas = list(partida.eventos.select_for_update().filter(
        etapa_ruta__isnull=False, fecha_hora_termino__isnull=True,
    ))
    if len(abiertas) > 1:
        raise ValidationError("Existe más de una etapa abierta. Revise el historial antes de registrar una merma.")
    merma = MermaProceso(
        partida=partida, tipo=tipo, cantidad_kg=cantidad_kg, motivo=motivo,
        evento_proceso=abiertas[0] if abiertas else None,
        fecha_hora_evento=timezone.now(), registrado_por=usuario,
    )
    merma.full_clean()
    merma.save()
    return merma


def _validar_asignacion_lote(partida, cantidad_kg):
    # El llamador ya bloqueó detalle -> seguimiento. Releer saldo dentro de atomic.
    actual = partidas_con_disponibilidad().get(pk=partida.pk)
    if not puede_asignar_lote(actual):
        raise ValidationError("El seguimiento debe estar listo para empaque, tener ruta, un peso postproceso y cantidad disponible.")
    _validar_decimal_registro(ConsumoLote, "cantidad_kg_utilizada", cantidad_kg)
    if cantidad_kg > actual.disponible_lote_kg:
        raise ValidationError("La cantidad a asignar no puede superar el peso postproceso disponible.")
    return actual


def _crear_consumo(lote, partida, cantidad_kg):
    if lote.consumos.filter(partida=partida).exists():
        raise ValidationError("Este seguimiento ya fue asignado a este lote. No se permite modificar su aporte.")
    consumo = ConsumoLote(partida=partida, lote_produccion=lote, cantidad_kg_utilizada=cantidad_kg)
    consumo.full_clean()
    consumo.save()
    return consumo


@transaction.atomic
def crear_lote_produccion(*, partida, cantidad_kg, fecha_elaboracion,
                         fecha_vencimiento, observaciones, usuario):
    partida = _bloquear_partida(partida, usuario)
    actual = _validar_asignacion_lote(partida, cantidad_kg)
    # Orden global: detalle -> seguimiento -> especie; nunca volver a otra raíz.
    especie = Especie.objects.select_for_update().get(pk=actual.detalle_recepcion.especie_id)
    if not especie.codigo_lote or not re.fullmatch(r"[0-9]{2}", especie.codigo_lote):
        raise ValidationError("La especie necesita un código de lote de dos dígitos configurado en Administración.")
    lote = LoteProduccion(
        especie_id=actual.detalle_recepcion.especie_id, ruta_proceso_id=actual.ruta_proceso_id,
        fecha_elaboracion=fecha_elaboracion, fecha_vencimiento=fecha_vencimiento,
        observaciones=_normalizar_texto(observaciones, "observaciones"), registrado_por=usuario,
    )
    lote.full_clean(exclude=["codigo_lote"])
    prefijo = segmento_folio(actual.detalle_recepcion.origen_sernapesca.folio_origen) + especie.codigo_lote
    fecha = f"{lote.fecha_elaboracion.day:02d}{lote.fecha_elaboracion.month:02d}{lote.fecha_elaboracion.year:04d}"
    codigos = LoteProduccion.objects.filter(codigo_lote__startswith=prefijo, codigo_lote__endswith=fecha).values_list("codigo_lote", flat=True)
    usados = {int(c[7:9]) for c in codigos if re.fullmatch(r"[0-9]{17}", c)}
    siguiente = next((n for n in range(1, 100) if n not in usados), None)
    if siguiente is None:
        raise ValidationError("No hay más correlativos disponibles para esta especie, origen y fecha de elaboración.")
    lote.codigo_lote = f"{prefijo}{siguiente:02d}{fecha}"
    lote.full_clean()
    try:
        # Savepoint para traducir una colisión de código entre seguimientos distintos.
        with transaction.atomic():
            lote.save()
    except IntegrityError:
        if LoteProduccion.objects.filter(codigo_lote=lote.codigo_lote).exists():
            raise ValidationError("Ya existe un lote con este código.") from None
        raise
    _crear_consumo(lote, partida, cantidad_kg)
    return lote


@transaction.atomic
def agregar_consumo_lote(*, lote, partida, cantidad_kg, usuario):
    # Mismo orden que los demás flujos: detalle -> seguimiento -> lote.
    partida = _bloquear_partida(partida, usuario)
    actual = _validar_asignacion_lote(partida, cantidad_kg)
    lote = LoteProduccion.objects.select_for_update().filter(pk=lote.pk).first()
    if lote is None or not lote.ruta_proceso_id:
        raise ValidationError("El lote no existe o no tiene una ruta documentada para recibir producto.")
    if lote.especie_id != actual.detalle_recepcion.especie_id or lote.ruta_proceso_id != actual.ruta_proceso_id:
        raise ValidationError("El producto debe tener la misma especie y la misma ruta de procesamiento del lote.")
    origen_id = origen_unico_lote(lote)
    if origen_id is None:
        raise ValidationError("El origen de este lote requiere revisión antes de agregar producto.")
    if origen_id != actual.detalle_recepcion.origen_sernapesca_id:
        raise ValidationError("No se puede agregar producto de un origen diferente a este lote.")
    return _crear_consumo(lote, partida, cantidad_kg)


@transaction.atomic
def crear_caja(*, lote, composiciones, peso_neto_kg, usuario):
    if not tiene_rol(usuario, ROL_JEFE, ROL_ENCARGADA, ROL_OPERARIA):
        raise PermissionDenied
    # No tomar bloqueos de seguimientos desde aquí: lote -> presentaciones por PK.
    # agregar_consumo_lote también bloquea el lote antes de escribir un aporte.
    lote = LoteProduccion.objects.select_for_update().filter(pk=lote.pk).first()
    if lote is None:
        raise ValidationError("El lote no existe.")
    actual = lotes_con_empaque().get(pk=lote.pk)
    if not puede_crear_caja(actual):
        raise ValidationError("El lote debe tener ruta documentada, historial de cajas completo y producto disponible para empacar.")
    _validar_decimal_registro(Caja, "peso_neto_kg", peso_neto_kg)
    if peso_neto_kg > PESO_MAXIMO_CAJA_KG:
        raise ValidationError(f"El peso neto real no puede superar {PESO_MAXIMO_CAJA_KG} kg.")
    if peso_neto_kg > actual.disponible_empacar:
        raise ValidationError("El peso neto real debe no superar el disponible para empacar.")
    composiciones = list(composiciones)
    if not composiciones:
        raise ValidationError("Agrega al menos una presentación a la caja.")
    ids = []
    for datos in composiciones:
        presentacion = datos.get("presentacion")
        if not isinstance(presentacion, PresentacionBolsa) or not presentacion.pk:
            raise ValidationError("Selecciona una presentación válida.")
        if presentacion.pk in ids:
            raise ValidationError("No repitas una presentación en la misma caja; consolida la cantidad de bolsas.")
        ids.append(presentacion.pk)
        cantidad = datos.get("cantidad")
        if type(cantidad) is not int:
            raise ValidationError("La cantidad de bolsas debe ser un número entero mayor que cero.")
        ComposicionCaja._meta.get_field("cantidad").clean(cantidad, None)
    presentaciones = PresentacionBolsa.objects.select_for_update().filter(pk__in=ids).order_by("pk").in_bulk()
    lineas = []
    total = Decimal("0.00")
    for datos, pk in zip(composiciones, ids):
        presentacion = presentaciones.get(pk)
        if presentacion is None or not presentacion.activo:
            raise ValidationError("Selecciona solamente presentaciones activas.")
        _validar_decimal_registro(PresentacionBolsa, "peso_nominal_kg", presentacion.peso_nominal_kg)
        linea = ComposicionCaja(presentacion=presentacion, cantidad=datos["cantidad"],
                               peso_unitario_kg=presentacion.peso_nominal_kg)
        total += linea.subtotal_kg
        lineas.append(linea)
    if total <= 0 or total > PESO_MAXIMO_CAJA_KG:
        raise ValidationError(f"El peso teórico de la composición debe ser mayor que cero y no superar {PESO_MAXIMO_CAJA_KG} kg.")
    caja = Caja(lote_produccion=lote, peso_neto_kg=peso_neto_kg, registrado_por=usuario, fecha_armado=timezone.now())
    caja.full_clean()
    caja.save()
    for linea in lineas:
        linea.caja = caja
        linea.full_clean()
        linea.save()
    return caja
