from django.core.exceptions import ValidationError
from django.db import transaction

from .models import DetalleRecepcion, Recepcion


@transaction.atomic
def registrar_recepcion(
    *,
    fecha_hora_recepcion,
    registrado_por,
    detalles,
    observaciones="",
):
    detalles = list(detalles)
    if not detalles:
        raise ValidationError("Una recepción debe tener al menos un detalle.")

    recepcion = Recepcion(
        fecha_hora_recepcion=fecha_hora_recepcion,
        registrado_por=registrado_por,
        observaciones=observaciones,
    )
    recepcion.full_clean()
    recepcion.save()

    for datos in detalles:
        detalle = DetalleRecepcion(
            recepcion=recepcion,
            origen_sernapesca=datos["origen_sernapesca"],
            especie=datos["especie"],
            peso_origen_kg=datos["peso_origen_kg"],
            peso_recepcion_kg=datos["peso_recepcion_kg"],
        )
        detalle.full_clean()
        detalle.save()

    return recepcion
