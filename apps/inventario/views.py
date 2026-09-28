from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from apps.trazabilidad.lotes import presentar_codigo_lote
from apps.usuarios.permisos import personal_operativo_requerido
from .forms import DestinoForm, InventarioFiltroForm
from .selectors import cajas_con_inventario, resumen_inventario
from .services import ingresar_caja, mover_caja


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
