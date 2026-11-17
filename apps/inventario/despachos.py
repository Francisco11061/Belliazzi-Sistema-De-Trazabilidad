"""Validación compartida por formulario y service de nuevos despachos."""
from django.core.exceptions import ValidationError

from .models import Despacho


def rut_valido(rut):
    """Valida el RUT chileno (módulo 11); acepta puntos y guion."""
    limpio = rut.replace(".", "").replace("-", "").replace(" ", "").upper()
    if not limpio.isascii():
        return False
    if not (limpio[:-1].isdigit() and 7 <= len(limpio[:-1]) <= 8 and (limpio[-1].isdigit() or limpio[-1] == "K")):
        return False
    suma = sum(int(d) * f for d, f in zip(reversed(limpio[:-1]), (2, 3, 4, 5, 6, 7) * 2))
    esperado = 11 - suma % 11
    return limpio[-1] == {11: "0", 10: "K"}.get(esperado, str(esperado))


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
        elif not rut_valido(datos["rut_destinatario"]):
            errores["rut_destinatario"] = "El RUT no es válido. Revisa el número y el dígito verificador (ej.: 12.345.678-5)."
    if tipo == Despacho.TipoDestino.EXPORTACION and not datos["pais_destino"]:
        errores["pais_destino"] = "El país de destino es obligatorio para una exportación."
    for campo in ("tipo_documento", "numero_documento", "fecha_documento"):
        if not datos.get(campo):
            errores[campo] = "Este dato del documento es obligatorio."
    if errores:
        raise ValidationError(errores)
    return datos
