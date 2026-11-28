"""Enlace "Corregir" e historial de correcciones en las páginas de detalle."""
from django import template
from django.urls import reverse

from apps.trazabilidad.constantes import ETIQUETAS_CAMPOS_RECEPCION
from apps.trazabilidad.correcciones import tipo_correccion
from apps.trazabilidad.formato import formatear_valor_correccion
from apps.trazabilidad.models import Correccion, MermaProceso

register = template.Library()

ETIQUETAS = {
    **ETIQUETAS_CAMPOS_RECEPCION,
    "tipo_destino": "Tipo de destino", "destino": "Destinatario", "rut_destinatario": "RUT del destinatario",
    "pais_destino": "País de destino", "tipo_documento": "Tipo de documento", "numero_documento": "Número de documento",
    "fecha_documento": "Fecha del documento", "fecha_hora_inicio": "Inicio", "fecha_hora_termino": "Término",
    "fecha_hora_ingreso": "Ingreso", "fecha_hora_salida": "Salida", "peso_kg": "Peso postproceso",
    "tipo": "Tipo", "cantidad_kg": "Cantidad", "motivo": "Motivo de la merma",
    "fecha_vencimiento": "Fecha de vencimiento", "peso_neto_kg": "Peso neto",
}


def _elegibles(valor, opciones):
    return dict(opciones).get(valor, valor)


def _describir(registro):
    nombre = type(registro).__name__
    if nombre == "EventoProceso":
        return f"Etapa {registro.tipo_proceso.nombre}" if registro.etapa_ruta_id else "Procesamiento"
    if nombre == "EstanciaPartida":
        return registro.unidad_frio.nombre
    if nombre == "Pesaje":
        return registro.tipo_visible
    if nombre == "MermaProceso":
        return registro.get_tipo_display()
    if nombre == "Caja":
        return f"Caja #{registro.pk}"
    if nombre == "LoteProduccion":
        return "Lote"
    if nombre == "Despacho":
        return "Despacho"
    return "Registro"


def _aplanar(objetos):
    for objeto in objetos:
        if objeto is None:
            continue
        if hasattr(objeto, "_meta"):
            yield objeto
        else:
            yield from _aplanar(objeto)


@register.simple_tag(takes_context=True)
def puede_corregir_registro(context, tipo, registro):
    """True si el usuario actual puede corregir este registro ahora."""
    tipo_registro = tipo_correccion(tipo)
    request = context.get("request")
    return bool(tipo_registro and request and registro is not None and tipo_registro.permitido(request.user, registro))


@register.simple_tag
def url_correccion(tipo, registro):
    return reverse("trazabilidad:corregir_registro", args=[tipo, registro.pk])


@register.inclusion_tag("trazabilidad/correcciones/_historial.html")
def historial_correcciones(*objetos, titulo="Historial de correcciones"):
    registros = {(type(o).__name__, str(o.pk)): o for o in _aplanar(objetos)}
    if not registros:
        return {"correcciones": [], "titulo": titulo}
    from django.db.models import Q
    filtro = Q(pk__in=[])
    for entidad in {e for e, _ in registros}:
        filtro |= Q(entidad_afectada=entidad, identificador_registro__in=[pk for e, pk in registros if e == entidad])
    from apps.inventario.models import Despacho
    correcciones = list(Correccion.objects.filter(filtro).select_related("usuario").order_by("-fecha_hora", "-pk"))
    for c in correcciones:
        c.registro_visible = _describir(registros[(c.entidad_afectada, c.identificador_registro)])
        c.campo_visible = ETIQUETAS.get(c.campo, c.campo.replace("_", " ").capitalize())
        anterior, nuevo = c.valor_anterior, c.valor_nuevo
        if c.campo == "tipo" and c.entidad_afectada == "MermaProceso":
            anterior, nuevo = _elegibles(anterior, MermaProceso.Tipo.choices), _elegibles(nuevo, MermaProceso.Tipo.choices)
        if c.campo == "tipo_destino":
            anterior, nuevo = _elegibles(anterior, Despacho.TipoDestino.choices), _elegibles(nuevo, Despacho.TipoDestino.choices)
        c.anterior_visible = formatear_valor_correccion(c.campo, anterior)
        c.nuevo_visible = formatear_valor_correccion(c.campo, nuevo)
    return {"correcciones": correcciones, "titulo": titulo}
