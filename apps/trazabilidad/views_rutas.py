from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST
from apps.usuarios.permisos import jefe_o_encargada_requerido
from .models import Especie, RutaProceso, TipoProceso
from .forms_rutas import RutaGestionForm, OperacionGestionForm, SecuenciaFormSet, SeleccionOperacionesForm
from .services_rutas import guardar_ruta, guardar_operacion, estado_ruta, nombre_para_secuencia, rutas_equivalentes
from .selectors import partidas_con_situacion, presentar_partida
from .services import iniciar_procesamiento


def error_form(form, error):
    form.add_error(None, " ".join(error.messages) if isinstance(error, ValidationError)
                   else "La configuración cambió o ya existe. Recarga y revisa los datos.")


@jefe_o_encargada_requerido
@require_http_methods(["GET"])
def rutas(request):
    consulta = RutaProceso.objects.select_related("especie", "creado_por").prefetch_related("etapas__tipo_proceso").order_by("especie__nombre", "nombre", "pk")
    if request.GET.get("q"):
        consulta = consulta.filter(nombre__icontains=request.GET["q"][:120])
    if request.GET.get("especie", "").isdecimal():
        consulta = consulta.filter(especie_id=request.GET["especie"][:18])
    if request.GET.get("estado") in ("activa", "inactiva"):
        consulta = consulta.filter(activo=request.GET["estado"] == "activa")
    return render(request, "trazabilidad/rutas/lista.html", {"page_obj": Paginator(consulta, 20).get_page(request.GET.get("page")), "especies": Especie.objects.all()})


@jefe_o_encargada_requerido
@require_http_methods(["GET", "POST"])
def editar_ruta(request, pk=None, duplicar=False):
    ruta = get_object_or_404(RutaProceso, pk=pk) if pk else None
    bloqueada = bool(ruta and not duplicar and ruta.tiene_historial())
    inicial = {k: getattr(ruta, k) for k in ("nombre", "especie", "descripcion", "activo", "predeterminada")} if ruta else {}
    if duplicar:
        inicial.update(nombre=f"{ruta.nombre} (copia)"[:120], predeterminada=False, activo=False)
    form = RutaGestionForm(request.POST if request.method == "POST" else None, initial=inicial)
    etapas = list(ruta.etapas.values("tipo_proceso", "orden")) if ruta else []
    secuencia = SecuenciaFormSet(request.POST if request.method == "POST" else None, prefix="etapas",
        initial=[{"tipo_proceso": e["tipo_proceso"], "ORDER": e["orden"]} for e in etapas])
    if request.method == "POST" and all([form.is_valid(), secuencia.is_valid()]):
        try:
            guardar_ruta(usuario=request.user, ruta=None if duplicar else ruta, operaciones=secuencia.operaciones(), **form.cleaned_data)
        except (ValidationError, IntegrityError) as error:
            error_form(form, error)
        else:
            messages.success(request, "Ruta guardada.")
            return redirect("trazabilidad:rutas")
    return render(request, "trazabilidad/rutas/form.html", {"form": form, "secuencia": secuencia, "ruta": ruta,
        "bloqueada": bloqueada, "titulo": "Duplicar ruta" if duplicar else "Editar ruta" if ruta else "Nueva ruta"})


@jefe_o_encargada_requerido
@require_POST
def cambiar_estado_ruta(request, pk):
    ruta = get_object_or_404(RutaProceso, pk=pk)
    if request.POST.get("accion") not in ("activar", "desactivar", "predeterminada"):
        return redirect("trazabilidad:rutas")
    try:
        estado_ruta(usuario=request.user, ruta=ruta, activo=request.POST["accion"] != "desactivar",
                    predeterminada=request.POST["accion"] == "predeterminada")
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(request, "Estado de la ruta actualizado.")
    return redirect("trazabilidad:rutas")


@jefe_o_encargada_requerido
@require_http_methods(["GET", "POST"])
def operaciones(request, pk=None):
    operacion = get_object_or_404(TipoProceso, pk=pk) if pk else None
    inicial = {k: getattr(operacion, k) for k in ("codigo", "nombre", "activo")} if operacion else {}
    form = OperacionGestionForm(request.POST if request.method == "POST" else None, initial=inicial)
    if operacion and operacion.tiene_referencias():
        form.fields["codigo"].disabled = form.fields["nombre"].disabled = True
    if request.method == "POST" and form.is_valid():
        try:
            guardar_operacion(usuario=request.user, operacion=operacion, **form.cleaned_data)
        except (ValidationError, IntegrityError) as error:
            error_form(form, error)
        else:
            messages.success(request, "Operación guardada.")
            return redirect("trazabilidad:operaciones")
    return render(request, "trazabilidad/rutas/operaciones.html", {"form": form, "operacion": operacion,
        "page_obj": Paginator(TipoProceso.objects.exclude(codigo="PROCESAMIENTO").order_by("nombre", "pk"), 20).get_page(request.GET.get("page"))})


@jefe_o_encargada_requerido
@require_http_methods(["GET", "POST"])
def seleccionar_operaciones(request, pk):
    """Elegir operaciones y, si ninguna ruta calza, crearla aquí mismo. La operaria usa las rutas habituales."""
    partida = presentar_partida(get_object_or_404(partidas_con_situacion(), pk=pk))
    especie = partida.detalle_recepcion.especie
    form = SeleccionOperacionesForm(request.POST if request.method == "POST" else None, initial={"cantidad_kg": partida.cantidad_inicial_kg})
    secuencia = SecuenciaFormSet(request.POST if request.method == "POST" else None, prefix="etapas")
    consultado = request.method == "POST" and secuencia.is_valid()
    coincidencias, ids = [], []
    if consultado:
        ids = secuencia.operaciones()
        coincidencias = rutas_equivalentes(especie, ids)
        form.fields["ruta"].queryset = RutaProceso.objects.filter(pk__in=[r.pk for r in coincidencias])
    crear = consultado and not coincidencias
    if not coincidencias:
        del form.fields["ruta"]  # Sin rutas que calcen, el selector queda vacío y no sirve.
    if crear:
        form.fields["nombre_ruta"].widget.attrs["placeholder"] = nombre_para_secuencia(especie, ids)
    else:
        del form.fields["nombre_ruta"]
    if consultado:
        if form.is_valid():
            try:
                if partida.situacion != "DISPONIBLE":
                    raise ValidationError("Este producto ya no está disponible para enviar a proceso.")
                accion = request.POST.get("accion", "buscar")
                if accion == "iniciar":
                    ruta = form.cleaned_data.get("ruta")
                    if ruta is None and len(coincidencias) == 1:
                        ruta = coincidencias[0]
                    if ruta is None:
                        raise ValidationError("Selecciona una de las rutas que calzan con la secuencia.")
                    seleccionada, _ = iniciar_procesamiento(partida=partida, cantidad_kg=form.cleaned_data["cantidad_kg"], ruta=ruta, usuario=request.user)
                    return redirect("trazabilidad:gestionar_procesamiento", pk=seleccionada.pk)
                if accion == "crear" and crear:
                    with transaction.atomic():
                        ruta = guardar_ruta(usuario=request.user, especie=especie, descripcion="", activo=True,
                            predeterminada=False, operaciones=ids,
                            nombre=form.cleaned_data["nombre_ruta"].strip() or nombre_para_secuencia(especie, ids))
                        seleccionada, _ = iniciar_procesamiento(partida=partida, cantidad_kg=form.cleaned_data["cantidad_kg"],
                                                                ruta=ruta, usuario=request.user)
                    messages.success(request, f"Ruta «{ruta.nombre}» creada.")
                    return redirect("trazabilidad:gestionar_procesamiento", pk=seleccionada.pk)
            except (ValidationError, IntegrityError) as error:
                error_form(form, error)
    return render(request, "trazabilidad/rutas/seleccionar.html", {"form": form, "secuencia": secuencia,
        "partida": partida, "coincidencias": coincidencias, "consultado": consultado})
