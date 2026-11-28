"""Pantalla única de corrección para despachos, etapas, túnel, pesajes, mermas, lotes y cajas."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.usuarios.permisos import ROL_JEFE, jefe_o_encargada_requerido, tiene_rol

from .correcciones import (
    CAMPOS_DESPACHO, corregir_caja, corregir_despacho, corregir_estancia_tunel, corregir_etapa,
    corregir_lote, corregir_merma, corregir_pesaje, tipo_correccion,
)
from .forms_correcciones import (
    CorreccionCajaForm, CorreccionDespachoForm, CorreccionEtapaForm, CorreccionLoteForm,
    CorreccionMermaForm, CorreccionPesajeForm, CorreccionTunelForm,
)


MIGAS = {
    "despacho": ("Inventario", "Despachos", "inventario:lista_despachos"),
    "lote": ("Producto terminado", "Lotes de producción", "producto_terminado:lista_lotes"),
    "caja": ("Producto terminado", "Cajas", "producto_terminado:lista_cajas"),
}
MIGA_SEGUIMIENTO = ("Producción", "Seguimiento de proceso", "trazabilidad:lista_partidas")


def _volver_seguimiento(registro):
    return reverse("trazabilidad:detalle_partida", args=[registro.partida_id])


PANTALLAS = {
    "despacho": dict(
        form=CorreccionDespachoForm,
        titulo=lambda r: f"Corregir despacho #{r.pk}",
        volver=lambda r: reverse("inventario:detalle_despacho", args=[r.pk]),
        inicial=lambda r: {campo: getattr(r, campo) for campo in CAMPOS_DESPACHO},
        fijo="Las cajas despachadas y la fecha del despacho no se corrigen: ya descontaron el stock.",
        guardar=lambda r, u, d, m: corregir_despacho(despacho=r, usuario=u, datos=d, motivo=m),
    ),
    "etapa": dict(
        form=CorreccionEtapaForm,
        opciones=lambda r: {"en_curso": r.fecha_hora_termino is None},
        titulo=lambda r: f"Corregir etapa: {r.tipo_proceso.nombre}",
        volver=_volver_seguimiento,
        inicial=lambda r: {"fecha_hora_inicio": r.fecha_hora_inicio, "fecha_hora_termino": r.fecha_hora_termino,
                           "observaciones": r.observaciones},
        fijo="Se corrigen las horas y las observaciones. La etapa y su orden en la ruta no cambian.",
        guardar=lambda r, u, d, m: corregir_etapa(
            evento=r, usuario=u, fecha_hora_inicio=d["fecha_hora_inicio"],
            fecha_hora_termino=d.get("fecha_hora_termino"), observaciones=d.get("observaciones", ""), motivo=m),
    ),
    "frio": dict(
        form=CorreccionTunelForm,
        opciones=lambda r: {"en_curso": r.fecha_hora_salida is None},
        titulo=lambda r: f"Corregir túnel: {r.unidad_frio.nombre}",
        volver=_volver_seguimiento,
        inicial=lambda r: {"fecha_hora_ingreso": r.fecha_hora_ingreso, "fecha_hora_salida": r.fecha_hora_salida},
        fijo="Se corrigen las horas de ingreso y salida. El túnel usado no cambia.",
        guardar=lambda r, u, d, m: corregir_estancia_tunel(
            estancia=r, usuario=u, fecha_hora_ingreso=d["fecha_hora_ingreso"],
            fecha_hora_salida=d.get("fecha_hora_salida"), motivo=m),
    ),
    "pesaje": dict(
        form=CorreccionPesajeForm,
        titulo=lambda r: "Corregir peso final",
        volver=_volver_seguimiento,
        inicial=lambda r: {"peso_kg": r.peso_kg},
        fijo="Los kilos ya asignados a lotes no cambian: el peso no puede quedar por debajo de ellos.",
        guardar=lambda r, u, d, m: corregir_pesaje(pesaje=r, usuario=u, peso_kg=d["peso_kg"], motivo=m),
    ),
    "merma": dict(
        form=CorreccionMermaForm,
        titulo=lambda r: f"Corregir {r.get_tipo_display().lower()}",
        volver=_volver_seguimiento,
        inicial=lambda r: {"tipo": r.tipo, "cantidad_kg": r.cantidad_kg, "detalle": r.motivo},
        fijo="El total de mermas no puede superar los kilos del producto.",
        guardar=lambda r, u, d, m: corregir_merma(merma=r, usuario=u, tipo=d["tipo"], cantidad_kg=d["cantidad_kg"],
                                                  detalle=d["detalle"], motivo=m),
    ),
    "lote": dict(
        form=CorreccionLoteForm,
        titulo=lambda r: f"Corregir lote {r.codigo_visible}",
        volver=lambda r: reverse("producto_terminado:detalle_lote", args=[r.pk]),
        inicial=lambda r: {"fecha_vencimiento": r.fecha_vencimiento, "observaciones": r.observaciones},
        fijo="La fecha de elaboración no se corrige: forma parte del código del lote y ya está impresa en las etiquetas.",
        guardar=lambda r, u, d, m: corregir_lote(lote=r, usuario=u, fecha_vencimiento=d.get("fecha_vencimiento"),
                                                 observaciones=d.get("observaciones", ""), motivo=m),
        reimprimir={"fecha_vencimiento"},
    ),
    "caja": dict(
        form=CorreccionCajaForm,
        titulo=lambda r: f"Corregir caja #{r.pk}",
        volver=lambda r: reverse("producto_terminado:detalle_caja", args=[r.pk]),
        inicial=lambda r: {"peso_neto_kg": r.peso_neto_kg},
        fijo="El lote, las bolsas y el código QR no se corrigen. Solo mientras la caja no se despache ni se dé de baja.",
        guardar=lambda r, u, d, m: corregir_caja(caja=r, usuario=u, peso_neto_kg=d["peso_neto_kg"], motivo=m),
        reimprimir={"peso_neto_kg"},
    ),
}


@jefe_o_encargada_requerido
@require_http_methods(["GET", "POST"])
def corregir(request, tipo, pk):
    tipo_registro, pantalla = tipo_correccion(tipo), PANTALLAS.get(tipo)
    if tipo_registro is None or pantalla is None:
        raise Http404
    registro = get_object_or_404(tipo_registro.modelo, pk=pk)
    volver = pantalla["volver"](registro)
    bloqueo = tipo_registro.bloqueo(registro)
    if bloqueo is None and not tipo_registro.permitido(request.user, registro):
        raise PermissionDenied
    inicial = pantalla["inicial"](registro)
    opciones = pantalla.get("opciones", lambda r: {})(registro)
    form = pantalla["form"](request.POST if request.method == "POST" and not bloqueo else None, initial=inicial, **opciones)
    if request.method == "POST" and not bloqueo and form.is_valid():
        datos = dict(form.cleaned_data)
        motivo = datos.pop("motivo")
        try:
            cambios = pantalla["guardar"](registro, request.user, datos, motivo)
        except ValidationError as error:
            if hasattr(error, "error_dict"):
                for campo, errores in error.message_dict.items():
                    form.add_error(campo if campo in form.fields else None, errores)
            else:
                form.add_error(None, error.messages)
        else:
            mensaje = "Corrección guardada. El cambio quedó en el historial."
            if pantalla.get("reimprimir", set()) & {campo for campo, _, _ in cambios}:
                mensaje += " Reimprime la etiqueta: los datos impresos ya no coinciden."
            messages.success(request, mensaje)
            return redirect(volver)
    es_jefe = tiene_rol(request.user, ROL_JEFE)
    return render(request, "trazabilidad/correcciones/corregir.html", {
        "form": form, "titulo": pantalla["titulo"](registro), "volver": volver, "fijo": pantalla["fijo"],
        "bloqueo": bloqueo, "registro": registro,
        "aviso_plazo": None if es_jefe else "Puedes corregir este registro hasta hoy a las 23:59. Después, solo el jefe.",
        "miga": dict(zip(("grupo", "pagina", "destino"), (lambda g, p, u: (g, p, reverse(u)))(*MIGAS.get(tipo, MIGA_SEGUIMIENTO)))),
        "hoy": timezone.localdate(),
    })
