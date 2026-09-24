from datetime import datetime

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import DetalleRecepcion, Especie, OrigenSernapesca, Recepcion


def _normalizar_texto(valor, campo):
    if not isinstance(valor, str):
        raise ValidationError({campo: "Debe ingresar un texto válido."})
    return valor.strip()


@transaction.atomic
def registrar_recepcion(
    *,
    fecha_hora_recepcion,
    registrado_por,
    origen,
    detalles,
    observaciones="",
):
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

    return recepcion
