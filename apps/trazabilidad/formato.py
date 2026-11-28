"""Formato de números y tiempos para lo que lee el usuario (es-CL).

Los números muestran solo los decimales que aportan: 15 kg, 15,5 kg, 15,25 kg.
Los tiempos largos se expresan en días: 23 días 9 h en vez de 561 h 57 min.
"""
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

MINUTOS_DIA = 24 * 60


def formatear_numero(valor, decimales=2):
    """Separador de miles con punto, decimales con coma y sin ceros sobrantes."""
    if valor is None or valor == "":
        return ""
    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, ValueError):
        return str(valor)
    if not numero.is_finite():
        return ""
    numero = numero.quantize(Decimal(1).scaleb(-int(decimales)), rounding=ROUND_HALF_UP)
    signo = "-" if numero < 0 else ""
    entero, _, fraccion = f"{abs(numero):f}".partition(".")
    fraccion = fraccion.rstrip("0")
    entero = f"{int(entero):,}".replace(",", ".")
    return f"{signo}{entero},{fraccion}" if fraccion else f"{signo}{entero}"


def formatear_kg(valor):
    texto = formatear_numero(valor)
    return f"{texto} kg" if texto else ""


def _dias(dias):
    return f"{dias} día" if dias == 1 else f"{dias} días"


def formatear_duracion(duracion, menos_de_un_minuto="menos de 1 min"):
    """Desde 24 h se expresa en días y horas; los minutos ya no aportan."""
    minutos = max(0, int(duracion.total_seconds())) // 60
    if minutos < 1:
        return menos_de_un_minuto
    if minutos < 60:
        return f"{minutos} min"
    horas, minutos = divmod(minutos, 60)
    if horas < 24:
        return f"{horas} h {minutos:02d} min"
    dias, horas = divmod(horas, 24)
    return f"{_dias(dias)} {horas} h" if horas else _dias(dias)


def formatear_horas(horas):
    """Umbral guardado en horas: 48 → «2 días», 20 → «20 h», 1,5 → «1 h 30 min»."""
    if horas is None or horas == "":
        return ""
    minutos = int((Decimal(str(horas)) * 60).to_integral_value(rounding=ROUND_HALF_UP))
    if minutos and minutos % MINUTOS_DIA == 0:
        return _dias(minutos // MINUTOS_DIA)
    if minutos >= 60 and minutos % 60 == 0:
        horas_enteras = minutos // 60
        if horas_enteras < 24:
            return f"{horas_enteras} h"
    return formatear_duracion(timedelta(minutes=minutos))


def formatear_valor_correccion(campo, valor):
    """Valor guardado en el historial de correcciones, legible para el usuario."""
    if not valor:
        return valor
    if campo.endswith("_kg"):
        return formatear_kg(valor)
    try:
        if campo.startswith("fecha_hora"):
            from django.utils import timezone
            return timezone.localtime(datetime.fromisoformat(valor)).strftime("%d/%m/%Y %H:%M")
        if campo.startswith("fecha"):
            return date.fromisoformat(valor).strftime("%d/%m/%Y")
    except (TypeError, ValueError):
        return valor
    return valor


def hace(fecha, ahora=None):
    """«hace 5 min», «hace 3 h», «hace 2 días»: lo justo para saber qué va primero."""
    if not fecha:
        return ""
    from django.utils import timezone
    minutos = max(0, int(((ahora or timezone.now()) - fecha).total_seconds())) // 60
    if minutos < 1:
        return "recién"
    if minutos < 60:
        return f"hace {minutos} min"
    if minutos < MINUTOS_DIA:
        return f"hace {minutos // 60} h"
    return f"hace {_dias(minutos // MINUTOS_DIA)}"
