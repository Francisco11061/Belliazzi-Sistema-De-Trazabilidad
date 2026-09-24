from datetime import datetime
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.usuarios.permisos import ROL_ENCARGADA, ROL_JEFE, ROL_OPERARIA, tiene_rol

from .models import Correccion, DetalleRecepcion, Especie, OrigenSernapesca, PartidaProceso, Recepcion
from .models import EstanciaPartida, EventoProceso, TipoProceso, UnidadFrio
from .selectors import recepcion_con_actividad


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
        raise ValidationError("Solo se puede operar una partida activa sin divisiones.")
    return actual


def _validar_cantidad(partida, cantidad):
    if not isinstance(cantidad, Decimal) or not cantidad.is_finite():
        raise ValidationError("Ingrese una cantidad decimal válida.")
    if cantidad.as_tuple().exponent < -2:
        raise ValidationError("La cantidad admite como máximo dos decimales.")
    if cantidad <= 0 or cantidad > partida.cantidad_inicial_kg:
        raise ValidationError("La cantidad debe ser mayor que cero y no superar la cantidad de la partida.")


def _comprobar_disponible(partida):
    if partida.estancias_frio.filter(fecha_hora_salida__isnull=True).exists() or partida.eventos.exists():
        raise ValidationError("La partida no está disponible: está en cámara o ya inició procesamiento.")


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
        raise ValidationError("La partida debe tener exactamente una estancia abierta en mantención.")
    if partida.eventos.exists():
        raise ValidationError("La partida ya inició procesamiento.")
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
def iniciar_procesamiento(*, partida, cantidad_kg, usuario):
    partida = _bloquear_partida(partida, usuario)
    _validar_cantidad(partida, cantidad_kg)
    _comprobar_disponible(partida)
    # Catálogo reproducible creado sólo en esta operación explícita, nunca en GET.
    tipo, _ = TipoProceso.objects.get_or_create(
        codigo="PROCESAMIENTO", defaults={"nombre": "Procesamiento"},
    )
    tipo = TipoProceso.objects.select_for_update().get(pk=tipo.pk)
    if not tipo.activo:
        raise ValidationError("El tipo de procesamiento general está inactivo.")
    seleccionada, restante = _dividir_partida(
        partida=partida, cantidad_seleccionada=cantidad_kg, usuario=usuario,
    )
    EventoProceso.objects.create(
        partida=seleccionada, tipo_proceso=tipo,
        fecha_hora_inicio=timezone.now(), iniciado_por=usuario,
    )
    return seleccionada, restante
