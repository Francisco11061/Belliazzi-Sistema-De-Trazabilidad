"""Formato interno Belliazzi; no representa una fórmula oficial de Sernapesca."""
import re
import unicodedata

from django.core.exceptions import ValidationError


def segmento_folio(folio):
    digitos = "".join(str(unicodedata.decimal(c)) for c in folio if c.isdecimal())
    if not digitos:
        raise ValidationError("No fue posible generar el código del lote porque el folio de origen no contiene números.")
    return digitos[-5:].zfill(5)


def presentar_codigo_lote(codigo):
    if re.fullmatch(r"[0-9]{17}", codigo):
        return f"{codigo[:5]} {codigo[5:9]} {codigo[9:]}"
    return codigo
