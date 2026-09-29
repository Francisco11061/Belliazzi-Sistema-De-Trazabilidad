"""Validación compartida por formulario y service de nuevos despachos."""
from django.core.exceptions import ValidationError

from .models import Despacho


def validar_datos_despacho(datos):
    datos = datos.copy()
    errores = {}
    for campo in ("tipo_destino", "destino", "rut_destinatario", "pais_destino", "tipo_documento", "numero_documento", "observaciones"):
        valor = datos.get(campo, "")
        if not isinstance(valor, str):
            errores[campo] = "Ingrese un texto válido."
            valor = ""
        datos[campo] = valor.strip()
    # Se conserva la puntuación registrada; solo espacios exteriores y caja del DV.
    datos["rut_destinatario"] = datos["rut_destinatario"].upper()
    tipo = datos["tipo_destino"]
    if tipo not in Despacho.TipoDestino.values:
        errores["tipo_destino"] = "Seleccione un tipo de destino."
    if tipo == Despacho.TipoDestino.NACIONAL:
        if not datos["destino"]:
            errores["destino"] = "El destinatario es obligatorio para un despacho nacional."
        if not datos["rut_destinatario"]:
            errores["rut_destinatario"] = "El RUT del destinatario es obligatorio para un despacho nacional."
    if tipo == Despacho.TipoDestino.EXPORTACION and not datos["pais_destino"]:
        errores["pais_destino"] = "El país de destino es obligatorio para una exportación."
    for campo in ("tipo_documento", "numero_documento", "fecha_documento"):
        if not datos.get(campo):
            errores[campo] = "Este dato del documento es obligatorio."
    if errores:
        raise ValidationError(errores)
    return datos
