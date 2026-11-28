"""Correcciones auditadas de registros operativos.

Quién corrige: el jefe, siempre; la encargada, solo sus propios registros y
solo el mismo día en que los creó. Cada campo cambiado queda en Correccion con
el valor anterior, el nuevo, el usuario, la fecha y el motivo.

Qué se corrige: solo datos que no arrastran a otros registros. Lo que ya quedó
enlazado (cajas de un despacho, kilos asignados a lotes, composición de cajas,
fecha de elaboración que forma el código del lote) no se toca.
"""
from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .constantes import PESO_MAXIMO_CAJA_KG
from .formato import formatear_kg
from .permisos_correccion import puede_corregir
from .models import (
    Caja, ConsumoLote, Correccion, DetalleRecepcion, EstanciaPartida, EventoProceso, LoteProduccion,
    MermaProceso, PartidaProceso, Pesaje, Recepcion, UnidadFrio,
)
from .selectors import lotes_con_empaque
from .services import _normalizar_texto, _validar_decimal_registro, _valor_historial


# ---------------------------------------------------------------- permisos

def _exigir_permiso(usuario, autor_id, creado_en):
    if not puede_corregir(usuario, autor_id, creado_en):
        raise PermissionDenied


@dataclass(frozen=True)
class TipoCorreccion:
    """Lo necesario para decidir si un registro se puede corregir y quién lo creó."""

    modelo: type
    autor: str
    creado: str
    nombre: str

    def autor_id(self, registro):
        return getattr(registro, self.autor)

    def creado_en(self, registro):
        return getattr(registro, self.creado)

    def bloqueo(self, registro):
        """Motivo por el que el registro ya no admite correcciones, o None."""
        return BLOQUEOS.get(self.nombre, lambda _: None)(registro)

    def permitido(self, usuario, registro):
        return self.bloqueo(registro) is None and puede_corregir(usuario, self.autor_id(registro), self.creado_en(registro))


def _bloqueo_etapa(evento):
    return None if evento.etapa_ruta_id else "Solo se corrigen las etapas de la ruta."


def _bloqueo_frio(estancia):
    if estancia.unidad_frio.tipo != UnidadFrio.TipoUnidad.TUNEL_CONGELADO:
        return "La cámara de mantención está ligada a las divisiones del producto y no se corrige."
    return None


def _bloqueo_pesaje(pesaje):
    return None if pesaje.tipo == Pesaje.POSTPROCESO else "Solo se corrige el peso final."


def _bloqueo_caja(caja):
    from apps.inventario.models import BajaCaja, DetalleDespacho
    if DetalleDespacho.objects.filter(caja_id=caja.pk).exists():
        return "La caja ya fue despachada; su peso queda como se registró."
    if BajaCaja.objects.filter(caja_id=caja.pk).exists():
        return "La caja tiene una baja registrada; su peso queda como se registró."
    return None


BLOQUEOS = {"etapa": _bloqueo_etapa, "frio": _bloqueo_frio, "pesaje": _bloqueo_pesaje, "caja": _bloqueo_caja}


def _tipos():
    from apps.inventario.models import Despacho
    return {
        "recepcion": TipoCorreccion(Recepcion, "registrado_por_id", "creado_en", "recepcion"),
        "despacho": TipoCorreccion(Despacho, "registrado_por_id", "creado_en", "despacho"),
        "etapa": TipoCorreccion(EventoProceso, "iniciado_por_id", "creado_en", "etapa"),
        "frio": TipoCorreccion(EstanciaPartida, "ingresado_por_id", "fecha_hora_ingreso", "frio"),
        "pesaje": TipoCorreccion(Pesaje, "registrado_por_id", "creado_en", "pesaje"),
        "merma": TipoCorreccion(MermaProceso, "registrado_por_id", "creado_en", "merma"),
        "lote": TipoCorreccion(LoteProduccion, "registrado_por_id", "creado_en", "lote"),
        "caja": TipoCorreccion(Caja, "registrado_por_id", "creado_en", "caja"),
    }


def tipo_correccion(nombre):
    return _tipos().get(nombre)


# ---------------------------------------------------------------- utilidades

def _motivo(motivo):
    motivo = _normalizar_texto(motivo if isinstance(motivo, str) else "", "motivo")
    if not motivo:
        raise ValidationError({"motivo": "Indica el motivo de la corrección."})
    return motivo


def _aplicar(registro, usuario, valores, motivo, *, exigir_cambios=True):
    """Guarda los campos que cambiaron y deja una fila de historial por cada uno."""
    cambios = [(campo, getattr(registro, campo), nuevo) for campo, nuevo in valores.items()
               if getattr(registro, campo) != nuevo]
    if not cambios:
        if exigir_cambios:
            raise ValidationError("No hay cambios para guardar.")
        return []
    for campo, _, nuevo in cambios:
        setattr(registro, campo, nuevo)
    registro.full_clean()
    registro.save(update_fields=[campo for campo, _, _ in cambios])
    Correccion.objects.bulk_create([
        Correccion(usuario=usuario, entidad_afectada=type(registro).__name__,
                   identificador_registro=str(registro.pk), campo=campo,
                   valor_anterior=_valor_historial(anterior), valor_nuevo=_valor_historial(nuevo), motivo=motivo)
        for campo, anterior, nuevo in cambios
    ])
    return cambios


def _no_futura(fecha, campo):
    if fecha is not None and fecha > timezone.now():
        raise ValidationError({campo: "La fecha y hora no puede ser futura."})


def _bloquear_seguimiento(partida_id):
    """Mismo orden de bloqueo que las operaciones: detalle de recepción y luego seguimiento."""
    detalle_id = PartidaProceso.objects.values_list("detalle_recepcion_id", flat=True).get(pk=partida_id)
    DetalleRecepcion.objects.select_for_update().get(pk=detalle_id)
    return PartidaProceso.objects.select_for_update(of=("self",)).select_related("detalle_recepcion__recepcion").get(pk=partida_id)


# ---------------------------------------------------------------- despacho

CAMPOS_DESPACHO = ("tipo_destino", "destino", "rut_destinatario", "pais_destino",
                   "tipo_documento", "numero_documento", "fecha_documento", "observaciones")


@transaction.atomic
def corregir_despacho(*, despacho, usuario, datos, motivo):
    from apps.inventario.despachos import validar_datos_despacho
    from apps.inventario.models import Despacho
    despacho = Despacho.objects.select_for_update().get(pk=despacho.pk)
    _exigir_permiso(usuario, despacho.registrado_por_id, despacho.creado_en)
    motivo = _motivo(motivo)
    entrada = {campo: datos.get(campo, "") for campo in CAMPOS_DESPACHO if campo != "fecha_documento"}
    entrada["fecha_documento"] = datos.get("fecha_documento")
    valores = validar_datos_despacho(entrada)
    return _aplicar(despacho, usuario, {campo: valores[campo] for campo in CAMPOS_DESPACHO}, motivo)


# ---------------------------------------------------------------- seguimiento

@transaction.atomic
def corregir_etapa(*, evento, usuario, fecha_hora_inicio, fecha_hora_termino, observaciones, motivo):
    partida = _bloquear_seguimiento(evento.partida_id)
    evento = EventoProceso.objects.select_for_update(of=("self",)).select_related("etapa_ruta").get(pk=evento.pk)
    if _bloqueo_etapa(evento):
        raise ValidationError(_bloqueo_etapa(evento))
    _exigir_permiso(usuario, evento.iniciado_por_id, evento.creado_en)
    motivo = _motivo(motivo)
    if fecha_hora_inicio is None:
        raise ValidationError({"fecha_hora_inicio": "Indica la fecha y hora de inicio."})
    if evento.fecha_hora_termino is None and fecha_hora_termino is not None:
        raise ValidationError({"fecha_hora_termino": "La etapa sigue en curso; su término se registra al finalizarla."})
    if evento.fecha_hora_termino is not None and fecha_hora_termino is None:
        raise ValidationError({"fecha_hora_termino": "Indica la fecha y hora de término."})
    _no_futura(fecha_hora_inicio, "fecha_hora_inicio")
    _no_futura(fecha_hora_termino, "fecha_hora_termino")
    if fecha_hora_termino is not None and fecha_hora_termino < fecha_hora_inicio:
        raise ValidationError({"fecha_hora_termino": "El término no puede ser anterior al inicio."})
    recepcion = partida.detalle_recepcion.recepcion.fecha_hora_recepcion
    if fecha_hora_inicio < recepcion:
        raise ValidationError({"fecha_hora_inicio": "La etapa no puede comenzar antes de la recepción."})
    # Las etapas de la ruta son consecutivas: no pueden solaparse con la anterior ni la siguiente.
    otras = list(partida.eventos.select_related("etapa_ruta").filter(etapa_ruta__isnull=False).exclude(pk=evento.pk))
    orden = evento.etapa_ruta.orden
    anteriores = [e for e in otras if e.etapa_ruta.orden < orden]
    siguientes = [e for e in otras if e.etapa_ruta.orden > orden]
    if anteriores:
        limite = max(e.fecha_hora_termino or e.fecha_hora_inicio for e in anteriores)
        if fecha_hora_inicio < limite:
            raise ValidationError({"fecha_hora_inicio": "La etapa no puede comenzar antes de que termine la etapa anterior."})
    fin = fecha_hora_termino or fecha_hora_inicio
    if siguientes and fin > min(e.fecha_hora_inicio for e in siguientes):
        raise ValidationError({"fecha_hora_termino": "La etapa no puede terminar después de que empiece la siguiente."})
    tunel = partida.estancias_frio.filter(unidad_frio__tipo=UnidadFrio.TipoUnidad.TUNEL_CONGELADO).order_by("fecha_hora_ingreso").first()
    if tunel and fin > tunel.fecha_hora_ingreso:
        raise ValidationError({"fecha_hora_termino": "La etapa no puede terminar después del ingreso al túnel."})
    observaciones = _normalizar_texto(observaciones or "", "observaciones")
    return _aplicar(evento, usuario, {"fecha_hora_inicio": fecha_hora_inicio, "fecha_hora_termino": fecha_hora_termino,
                                      "observaciones": observaciones}, motivo)


@transaction.atomic
def corregir_estancia_tunel(*, estancia, usuario, fecha_hora_ingreso, fecha_hora_salida, motivo):
    partida = _bloquear_seguimiento(estancia.partida_id)
    estancia = EstanciaPartida.objects.select_for_update(of=("self",)).select_related("unidad_frio").get(pk=estancia.pk)
    if _bloqueo_frio(estancia):
        raise ValidationError(_bloqueo_frio(estancia))
    _exigir_permiso(usuario, estancia.ingresado_por_id, estancia.fecha_hora_ingreso)
    motivo = _motivo(motivo)
    if fecha_hora_ingreso is None:
        raise ValidationError({"fecha_hora_ingreso": "Indica la fecha y hora de ingreso."})
    if estancia.fecha_hora_salida is None and fecha_hora_salida is not None:
        raise ValidationError({"fecha_hora_salida": "El producto sigue en el túnel; la salida se registra al retirarlo."})
    if estancia.fecha_hora_salida is not None and fecha_hora_salida is None:
        raise ValidationError({"fecha_hora_salida": "Indica la fecha y hora de salida."})
    _no_futura(fecha_hora_ingreso, "fecha_hora_ingreso")
    _no_futura(fecha_hora_salida, "fecha_hora_salida")
    if fecha_hora_salida is not None and fecha_hora_salida < fecha_hora_ingreso:
        raise ValidationError({"fecha_hora_salida": "La salida no puede ser anterior al ingreso."})
    etapas = list(partida.eventos.filter(etapa_ruta__isnull=False))
    fin_etapas = max((e.fecha_hora_termino or e.fecha_hora_inicio for e in etapas), default=None)
    if fin_etapas and fecha_hora_ingreso < fin_etapas:
        raise ValidationError({"fecha_hora_ingreso": "El ingreso al túnel no puede ser anterior al término de la última etapa."})
    pesaje = partida.pesajes.filter(tipo=Pesaje.POSTPROCESO).order_by("fecha_hora_evento").first()
    if pesaje and fecha_hora_salida and fecha_hora_salida > pesaje.fecha_hora_evento:
        raise ValidationError({"fecha_hora_salida": "La salida del túnel no puede ser posterior al peso final."})
    anterior_ingreso = estancia.fecha_hora_ingreso
    cambios = _aplicar(estancia, usuario, {"fecha_hora_ingreso": fecha_hora_ingreso,
                                           "fecha_hora_salida": fecha_hora_salida}, motivo)
    # El procesamiento general termina justo al entrar al túnel: se mantienen alineados.
    general = partida.eventos.select_for_update().filter(etapa_ruta__isnull=True, fecha_hora_termino=anterior_ingreso).first()
    if general:
        _aplicar(general, usuario, {"fecha_hora_termino": fecha_hora_ingreso}, motivo, exigir_cambios=False)
    return cambios


@transaction.atomic
def corregir_pesaje(*, pesaje, usuario, peso_kg, motivo):
    _bloquear_seguimiento(pesaje.partida_id)
    pesaje = Pesaje.objects.select_for_update().get(pk=pesaje.pk)
    if _bloqueo_pesaje(pesaje):
        raise ValidationError(_bloqueo_pesaje(pesaje))
    _exigir_permiso(usuario, pesaje.registrado_por_id, pesaje.creado_en)
    motivo = _motivo(motivo)
    _validar_decimal_registro(Pesaje, "peso_kg", peso_kg)
    asignado = ConsumoLote.objects.filter(partida_id=pesaje.partida_id).aggregate(
        total=Sum("cantidad_kg_utilizada"))["total"] or Decimal("0.00")
    if peso_kg < asignado:
        raise ValidationError({"peso_kg": f"No puede ser menor que lo ya asignado a lotes ({formatear_kg(asignado)})."})
    return _aplicar(pesaje, usuario, {"peso_kg": peso_kg}, motivo)


@transaction.atomic
def corregir_merma(*, merma, usuario, tipo, cantidad_kg, detalle, motivo):
    partida = _bloquear_seguimiento(merma.partida_id)
    merma = MermaProceso.objects.select_for_update().get(pk=merma.pk)
    _exigir_permiso(usuario, merma.registrado_por_id, merma.creado_en)
    motivo = _motivo(motivo)
    if tipo not in MermaProceso.Tipo.values:
        raise ValidationError({"tipo": "Selecciona un tipo válido."})
    _validar_decimal_registro(MermaProceso, "cantidad_kg", cantidad_kg)
    otras = partida.mermas.exclude(pk=merma.pk).aggregate(total=Sum("cantidad_kg"))["total"] or Decimal("0.00")
    if otras + cantidad_kg > partida.cantidad_inicial_kg:
        raise ValidationError({"cantidad_kg": "Las pérdidas, descartes y mermas acumuladas no pueden superar los kilos del producto."})
    detalle = _normalizar_texto(detalle if isinstance(detalle, str) else "", "motivo")
    if not detalle:
        raise ValidationError({"detalle": "Indica el motivo de la merma."})
    return _aplicar(merma, usuario, {"tipo": tipo, "cantidad_kg": cantidad_kg, "motivo": detalle}, motivo)


# ---------------------------------------------------------------- lote y caja

@transaction.atomic
def corregir_lote(*, lote, usuario, fecha_vencimiento, observaciones, motivo):
    lote = LoteProduccion.objects.select_for_update().get(pk=lote.pk)
    _exigir_permiso(usuario, lote.registrado_por_id, lote.creado_en)
    motivo = _motivo(motivo)
    if fecha_vencimiento is not None and fecha_vencimiento < lote.fecha_elaboracion:
        raise ValidationError({"fecha_vencimiento": "La fecha de vencimiento no puede ser anterior a la elaboración."})
    observaciones = _normalizar_texto(observaciones or "", "observaciones")
    return _aplicar(lote, usuario, {"fecha_vencimiento": fecha_vencimiento, "observaciones": observaciones}, motivo)


@transaction.atomic
def corregir_caja(*, caja, usuario, peso_neto_kg, motivo):
    caja = Caja.objects.select_for_update().get(pk=caja.pk)
    LoteProduccion.objects.select_for_update().get(pk=caja.lote_produccion_id)
    if _bloqueo_caja(caja):
        raise ValidationError(_bloqueo_caja(caja))
    _exigir_permiso(usuario, caja.registrado_por_id, caja.creado_en)
    motivo = _motivo(motivo)
    _validar_decimal_registro(Caja, "peso_neto_kg", peso_neto_kg)
    if peso_neto_kg <= 0 or peso_neto_kg > PESO_MAXIMO_CAJA_KG:
        raise ValidationError({"peso_neto_kg": f"El peso neto debe ser mayor que cero y no superar {formatear_kg(PESO_MAXIMO_CAJA_KG)}."})
    lote = lotes_con_empaque().get(pk=caja.lote_produccion_id)
    if lote.disponible_empacar is not None:
        disponible = lote.disponible_empacar + (caja.peso_neto_kg or Decimal("0.00"))
        if peso_neto_kg > disponible:
            raise ValidationError({"peso_neto_kg": f"Supera lo disponible del lote para esta caja ({formatear_kg(disponible)})."})
    return _aplicar(caja, usuario, {"peso_neto_kg": peso_neto_kg}, motivo)
