from django import forms
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from apps.usuarios.permisos import jefe_o_encargada_requerido
from .consulta_integral import TIPOS, MODELOS, buscar_integral, ficha_integral


class BusquedaIntegralForm(forms.Form):
    tipo = forms.ChoiceField(choices=TIPOS, label="Tipo de identificador")
    q = forms.CharField(max_length=200, label="Identificador")


@never_cache
@jefe_o_encargada_requerido
@require_GET
def buscar(request):
    form = BusquedaIntegralForm(request.GET or None)
    pagina = None
    if form.is_bound and form.is_valid():
        pagina = Paginator(buscar_integral(request.user, form.cleaned_data["tipo"], form.cleaned_data["q"]), 20).get_page(request.GET.get("page"))
    return render(request, "trazabilidad/consulta/buscar.html", {"form": form, "page_obj": pagina, "tipo": form.cleaned_data.get("tipo") if form.is_bound else None})


@never_cache
@jefe_o_encargada_requerido
@require_GET
def ficha(request, tipo, pk):
    if tipo not in MODELOS:
        raise Http404
    return mostrar_historial(request, tipo, pk)


def mostrar_historial(request, tipo, pk):
    """Misma ficha para la búsqueda y la URL histórica del QR; el selector exige permisos."""
    contexto = ficha_integral(request.user, tipo, pk)
    contexto["pagina_cajas"] = Paginator(contexto["cajas"], 20).get_page(request.GET.get("cajas_page"))
    contexto["pagina_partidas"] = Paginator(contexto["partidas"], 10).get_page(request.GET.get("partidas_page"))
    contexto["pagina_eventos"] = Paginator(contexto["timeline"], 50).get_page(request.GET.get("eventos_page"))
    return render(request, "trazabilidad/consulta/ficha.html", contexto)
