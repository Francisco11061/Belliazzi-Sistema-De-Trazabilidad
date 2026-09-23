from django.core.exceptions import ValidationError
from django.db import transaction

from .models import DetalleRecepcion, OrigenSernapesca, Recepcion


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
        raise ValidationError("Una recepción debe tener al menos una especie recibida.")

    recepcion = Recepcion(
        fecha_hora_recepcion=fecha_hora_recepcion,
        registrado_por=registrado_por,
        observaciones=observaciones,
    )
    recepcion.full_clean()
    recepcion.save()

    origen_sernapesca = OrigenSernapesca(
        folio_origen=origen["folio_origen"],
        tipo_origen=origen.get("tipo_origen", ""),
        codigo_agente=origen.get("codigo_agente", ""),
        proveedor=origen.get("proveedor", ""),
    )
    origen_sernapesca.full_clean()
    origen_sernapesca.save()

    for datos in detalles:
        detalle = DetalleRecepcion(
            recepcion=recepcion,
            origen_sernapesca=origen_sernapesca,
            especie=datos["especie"],
            peso_origen_kg=datos["peso_origen_kg"],
            peso_recepcion_kg=datos["peso_recepcion_kg"],
        )
        detalle.full_clean()
        detalle.save()

    return recepcion
