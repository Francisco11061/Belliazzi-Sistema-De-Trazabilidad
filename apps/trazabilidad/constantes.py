from decimal import Decimal


PESO_MAXIMO_CAJA_KG = Decimal("25.00")

ORIGEN_EMBARCACION = "Embarcación artesanal"
ORIGEN_COMERCIALIZADORA = "Comercializadora"
TIPOS_ORIGEN = (ORIGEN_EMBARCACION, ORIGEN_COMERCIALIZADORA, "Otro")
CAMPOS_EMBARCACION = (
    "nombre_embarcacion", "matricula_embarcacion", "rpa_embarcacion",
    "caleta_desembarque", "fecha_desembarque",
)


# Nombres visibles de los campos que se pueden corregir en una recepción.
ETIQUETAS_CAMPOS_RECEPCION = {
    "fecha_hora_recepcion": "Fecha y hora de recepción", "observaciones": "Observaciones",
    "folio_origen": "Folio de origen Sernapesca", "tipo_origen": "Tipo de origen",
    "codigo_agente": "Código agente de origen", "proveedor": "Proveedor", "especie": "Especie",
    "nombre_embarcacion": "Nombre de la embarcación", "matricula_embarcacion": "Matrícula",
    "rpa_embarcacion": "RPA de la embarcación", "caleta_desembarque": "Caleta de desembarque",
    "fecha_desembarque": "Fecha de desembarque",
    "peso_origen_kg": "Peso de origen", "peso_recepcion_kg": "Peso en recepción",
}
