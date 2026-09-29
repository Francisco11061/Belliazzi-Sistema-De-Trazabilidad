from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from django.db.models import Q
from django.utils import timezone

from apps.trazabilidad.lotes import presentar_codigo_lote
from apps.usuarios.permisos import personal_operativo_requerido
from .forms import DespachoFiltroForm, DespachoForm, DestinoForm, InventarioFiltroForm
from .selectors import cajas_con_inventario, resumen_inventario, despachos_con_totales
from .services import ingresar_caja, mover_caja, registrar_despacho


@personal_operativo_requerido
@require_http_methods(["GET"])
def inventario(request):
    cajas = cajas_con_inventario()
    filtro = InventarioFiltroForm(request.GET)
    if filtro.is_valid():
        for campo, lookup in (("especie", "lote_produccion__especie"), ("lote", "lote_produccion"), ("camara", "unidad_abierta_id"), ("estado", "estado_inventario")):
            valor = filtro.cleaned_data[campo]
            if valor:
                cajas = cajas.filter(**{lookup: valor})
        if filtro.cleaned_data["camara"]:
            cajas = cajas.filter(estado_inventario="ALMACENADA")
    else:
        cajas = cajas.none()
    resumen = resumen_inventario(cajas)
    for fila in resumen["por_lote"]:
        fila["codigo_visible"] = presentar_codigo_lote(fila["lote_produccion__codigo_lote"])
    pagina = Paginator(cajas.order_by("-pk"), 10).get_page(request.GET.get("page"))
    def enlace(numero):
        parametros = request.GET.copy()
        parametros["page"] = numero
        return "?" + parametros.urlencode()
    return render(request, "inventario/lista.html", {
        "filtro": filtro, "page_obj": pagina, **resumen,
        "pagina_anterior": enlace(pagina.previous_page_number()) if pagina.has_previous() else None,
        "pagina_siguiente": enlace(pagina.next_page_number()) if pagina.has_next() else None,
    })


def _operacion(request, pk, movimiento):
    caja = get_object_or_404(cajas_con_inventario(), pk=pk)
    form = DestinoForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            estancia = (mover_caja if movimiento else ingresar_caja)(caja=caja, unidad=form.cleaned_data["unidad"], usuario=request.user)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            verbo = "trasladada" if movimiento else "ingresada"
            messages.success(request, f"Caja {verbo} a {estancia.unidad_frio.nombre}.")
            return redirect("producto_terminado:detalle_caja", pk=caja.pk)
    return render(request, "inventario/operacion.html", {"caja": caja, "form": form, "movimiento": movimiento})


@personal_operativo_requerido
@require_http_methods(["GET", "POST"])
def ingresar(request, pk):
    return _operacion(request, pk, False)


@personal_operativo_requerido
@require_http_methods(["GET", "POST"])
def mover(request, pk):
    return _operacion(request, pk, True)


@personal_operativo_requerido
@require_http_methods(["GET"])
def lista_despachos(request):
    despachos = despachos_con_totales()
    filtro = DespachoFiltroForm(request.GET)
    if filtro.is_valid():
        datos = filtro.cleaned_data
        if datos["destinatario"]:
            despachos = despachos.filter(Q(destino__icontains=datos["destinatario"]) | Q(pais_destino__icontains=datos["destinatario"]))
        if datos["tipo_destino"]:
            despachos = despachos.filter(tipo_destino=datos["tipo_destino"])
        if datos["fecha"]:
            # Rango aware: no depende de tablas de zonas horarias de MySQL.
            from datetime import datetime, time, timedelta
            inicio = timezone.make_aware(datetime.combine(datos["fecha"], time.min))
            fin = timezone.make_aware(datetime.combine(datos["fecha"] + timedelta(days=1), time.min))
            despachos = despachos.filter(fecha_hora_despacho__gte=inicio, fecha_hora_despacho__lt=fin)
    else:
        despachos = despachos.none()
    pagina = Paginator(despachos, 10).get_page(request.GET.get("page"))
    def enlace(numero):
        parametros = request.GET.copy()
        parametros["page"] = numero
        return "?" + parametros.urlencode()
    return render(request, "inventario/despachos/lista.html", {"filtro":filtro, "page_obj":pagina,
        "pagina_anterior": enlace(pagina.previous_page_number()) if pagina.has_previous() else None,
        "pagina_siguiente": enlace(pagina.next_page_number()) if pagina.has_next() else None})


@personal_operativo_requerido
@require_http_methods(["GET", "POST"])
def crear_despacho(request):
    form = DespachoForm(request.POST if request.method == "POST" else None,
                        initial={"tipo_destino":"NACIONAL", "fecha_documento":timezone.localdate()})
    if request.method == "POST" and form.is_valid():
        try:
            despacho = registrar_despacho(usuario=request.user, **form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, "Despacho registrado correctamente.")
            return redirect("inventario:detalle_despacho", pk=despacho.pk)
    seleccionadas = set(request.POST.getlist("cajas"))
    disponibles = list(form.fields["cajas"].queryset)
    for caja in disponibles:
        caja.seleccionada = str(caja.pk) in seleccionadas
        caja.peso_centigramos = int(caja.peso_neto_kg * 100)
    elegidas = [c for c in disponibles if c.seleccionada]
    return render(request, "inventario/despachos/crear.html", {"form":form, "cajas_disponibles":disponibles,
        "cantidad_seleccionada":len(elegidas), "peso_seleccionado":sum((c.peso_neto_kg for c in elegidas), Decimal("0.00"))})


@personal_operativo_requerido
@require_http_methods(["GET"])
def detalle_despacho(request, pk):
    despacho = get_object_or_404(despachos_con_totales(), pk=pk)
    detalles = despacho.detalles.select_related("caja__lote_produccion__especie").order_by("caja_id")
    return render(request, "inventario/despachos/detalle.html", {"despacho":despacho, "detalles":detalles})
