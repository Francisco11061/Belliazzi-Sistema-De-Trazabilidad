"""Reglas compartidas por los servicios operativos y la configuración en Admin."""
from django.core.exceptions import ValidationError


def validar_etapas_ruta(etapas, *, exigir_activos=True):
    if not etapas:
        raise ValidationError("La ruta debe tener al menos una etapa.")
    ordenes = [etapa.orden for etapa in etapas]
    if any(orden is None or orden < 1 for orden in ordenes) or ordenes != sorted(set(ordenes)):
        raise ValidationError("Las etapas deben tener órdenes positivos, únicos y crecientes.")
    if any(etapa.tipo_proceso.codigo == "PROCESAMIENTO" for etapa in etapas):
        raise ValidationError("PROCESAMIENTO no puede utilizarse como etapa de una ruta.")
    if exigir_activos and any(not etapa.tipo_proceso.activo for etapa in etapas):
        raise ValidationError("Todas las etapas de una nueva asignación deben estar activas.")
    if etapas[-1].tipo_proceso.codigo != "EMPARRILLADO":
        raise ValidationError("La última etapa de la ruta debe ser Emparrillado.")
