from decimal import Decimal


PESO_MAXIMO_CAJA_KG = Decimal("25.00")

ORIGEN_EMBARCACION = "Embarcación artesanal"
TIPOS_ORIGEN = (ORIGEN_EMBARCACION, "Comercializadora", "Otro")
CAMPOS_EMBARCACION = (
    "nombre_embarcacion", "matricula_embarcacion", "rpa_embarcacion",
    "caleta_desembarque", "fecha_desembarque",
)
