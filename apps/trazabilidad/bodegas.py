"""Bodega Sernapesca de la materia prima recibida.

La recepción no pregunta la bodega: se deduce de lo ya registrado. Si la
especie esperó en una cámara de mantención con bodega asignada, se usa esa;
si no, la bodega marcada por defecto. Solo lee datos.
"""
from dataclasses import dataclass

from django.db.models import Prefetch

from .models import BodegaSernapesca, EstanciaPartida, UnidadFrio


@dataclass(frozen=True)
class BodegaCalculada:
    bodega: BodegaSernapesca | None
    motivo: str

    @property
    def nombre(self):
        return self.bodega.nombre if self.bodega else ""


def bodega_por_defecto():
    return BodegaSernapesca.objects.filter(por_defecto=True, activo=True).first()


def prefetch_bodega(prefijo=""):
    """Lookups para calcular la bodega de varios detalles sin consultas extra."""
    return (
        f"{prefijo}partidas",
        Prefetch(
            f"{prefijo}partidas__estancias_frio",
            queryset=EstanciaPartida.objects.select_related("unidad_frio__bodega_sernapesca"),
        ),
    )


def bodega_de_detalle(detalle, defecto):
    """Calcula la bodega de un detalle de recepción con sus partidas precargadas."""
    estancias = [
        estancia
        for partida in detalle.partidas.all()
        for estancia in partida.estancias_frio.all()
        if estancia.unidad_frio.tipo == UnidadFrio.TipoUnidad.MANTENCION
    ]
    if estancias:
        primera = min(estancias, key=lambda estancia: (estancia.fecha_hora_ingreso, estancia.pk))
        camara = primera.unidad_frio
        if camara.bodega_sernapesca_id:
            return BodegaCalculada(camara.bodega_sernapesca, f"Según la cámara {camara.nombre}")
    if defecto:
        return BodegaCalculada(defecto, "Bodega por defecto")
    return BodegaCalculada(None, "Sin bodega configurada")
