from decimal import Decimal


PESO_MAXIMO_CAJA_KG = Decimal("25.00")

ORIGEN_EMBARCACION = "Embarcación artesanal"
ORIGEN_COMERCIALIZADORA = "Comercializadora"
TIPOS_ORIGEN = (ORIGEN_EMBARCACION, ORIGEN_COMERCIALIZADORA, "Otro")
CAMPOS_EMBARCACION = (
    "nombre_embarcacion", "matricula_embarcacion", "rpa_embarcacion",
    "caleta_desembarque", "fecha_desembarque",
)
