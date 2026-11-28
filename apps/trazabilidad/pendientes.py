"""Lo que falta hacer en planta, para la pantalla de inicio. Solo lectura."""
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from apps.inventario.selectors import cajas_con_inventario
from apps.usuarios.permisos import tiene_rol

from .formato import formatear_kg, hace
from .models import PartidaProceso, UnidadFrio
from .selectors import lotes_con_empaque, presentar_partida, puede_asignar_lote, puede_crear_caja
from .seguimiento_operativo import seguimientos_operativos

MAXIMO_POR_GRUPO = 5
# Saldos menores no son trabajo del día: suelen ser restos de empaque. Se informan aparte.
SALDO_MINIMO_KG = Decimal("5")

# Orden de urgencia: primero lo que se echa a perder o bloquea el resto.
GRUPOS = (
    ("revision", "Revisar antes de seguir", "Los registros no cuadran; revisa qué falta antes de operar."),
    ("EN_CONGELACION", "En el túnel", "Sácalos cuando terminen de congelarse."),
    ("EN_PROCESO", "En proceso", "Registra el inicio y término de cada etapa."),
    ("PENDIENTE_PESAJE", "Falta el peso final", "Ya salieron del túnel: pésalos para poder asignarlos a lote."),
    ("empaque", "Listos para asignar a lote", "Tienen peso final y kilos sin asignar."),
    ("encajar", "Lotes con producto por encajar", "Quedan kilos del lote sin caja."),
    ("guardar", "Cajas por guardar en cámara", "Están registradas pero aún no entran a almacenamiento."),
    ("DISPONIBLE", "Esperando proceso", "Llegaron y todavía no empiezan."),
    ("EN_MANTENCION", "En cámara de mantención", "Guardados a la espera de proceso."),
)


def _grupo_partida(partida):
    if partida.revision_operativa:
        return "revision"
    if partida.categoria_operativa == "empaque":
        return "empaque"
    return partida.situacion


def _contexto_partida(partida, ahora, umbrales):
    """Una frase que ayuda a decidir qué atender primero."""
    situacion = partida.situacion
    if partida.revision_operativa:
        return partida.revision_operativa, False
    if situacion in ("EN_CONGELACION", "EN_MANTENCION"):
        texto = ""
        if partida.ubicacion:
            tiempo = hace(partida.ingreso_actual, ahora)
            texto = (f"Recién entró a {partida.ubicacion}" if tiempo == "recién"
                     else f"Lleva {tiempo.removeprefix('hace ')} en {partida.ubicacion}")
        umbral = umbrales.get(partida.ubicacion)
        if umbral and partida.ingreso_actual:
            horas = Decimal((ahora - partida.ingreso_actual).total_seconds()) / 3600
            if horas > umbral:
                return f"{texto}. Ya pasó el tiempo recomendado.", True
        return texto, False
    if situacion == "EN_PROCESO":
        return f"Último registro {hace(partida.ultima_operacion, ahora)}" if partida.ultima_operacion else "", False
    if situacion == "PENDIENTE_PESAJE":
        return f"Salió del túnel {hace(partida.ultima_salida, ahora)}" if partida.ultima_salida else "", False
    if partida.categoria_operativa == "empaque":
        return f"{formatear_kg(partida.disponible_lote_kg)} sin asignar", False
    if situacion == "DISPONIBLE":
        tiempo = hace(partida.llegada, ahora)
        return ("Recién llegado" if tiempo == "recién" else f"Lleva {tiempo.removeprefix('hace ')} esperando"), False
    return "", False


def _fecha_contexto(partida):
    """La fecha exacta detrás de «hace…», para mostrarla al pasar el mouse."""
    if partida.revision_operativa or partida.categoria_operativa == "empaque":
        return None
    return {"EN_CONGELACION": partida.ingreso_actual, "EN_MANTENCION": partida.ingreso_actual,
            "EN_PROCESO": partida.ultima_operacion, "PENDIENTE_PESAJE": partida.ultima_salida,
            "DISPONIBLE": partida.llegada}.get(partida.situacion)


def _enlace_lista(clave):
    base = reverse("trazabilidad:lista_partidas")
    if clave == "revision":
        return base + "?categoria=atender"
    if clave == "empaque":
        return base + "?categoria=empaque"
    return base + f"?categoria=todos&situacion={clave}"


def pendientes_de_planta(usuario, ahora=None):
    ahora = ahora or timezone.now()
    gestion = tiene_rol(usuario, "JEFE", "ENCARGADA")
    umbrales = {nombre: umbral for nombre, umbral in UnidadFrio.objects.filter(
        umbral_alerta_horas__isnull=False).values_list("nombre", "umbral_alerta_horas")}
    items = {clave: [] for clave, *_ in GRUPOS}
    saldos = {"empaque": 0, "encajar": 0}

    # Filtrar por estado y no por categoría en SQL: es mucho más rápido con estas anotaciones.
    for partida in seguimientos_operativos().filter(estado=PartidaProceso.Estado.ACTIVA):
        if partida.categoria_operativa == "historial":
            continue
        clave = _grupo_partida(partida)
        if clave not in items or (clave == "empaque" and not gestion):
            continue
        presentar_partida(partida)
        partida.puede_asignar_lote = puede_asignar_lote(partida)
        if partida.revision_operativa:
            partida.puede_registrar_merma = False
        if clave == "empaque" and partida.disponible_lote_kg < SALDO_MINIMO_KG:
            saldos["empaque"] += 1
            continue
        partida.contexto, partida.alerta = _contexto_partida(partida, ahora, umbrales)
        partida.contexto_fecha = _fecha_contexto(partida)
        items[clave].append(partida)

    for lote in lotes_con_empaque().order_by("fecha_elaboracion", "pk"):
        if puede_crear_caja(lote):
            if lote.disponible_empacar < SALDO_MINIMO_KG:
                saldos["encajar"] += 1
            else:
                items["encajar"].append(lote)

    pendientes = cajas_con_inventario().filter(estado_inventario="PENDIENTE").order_by("creado_en", "pk")
    items["guardar"] = list(pendientes)

    grupos = []
    for clave, titulo, ayuda in GRUPOS:
        lista = items[clave]
        if not lista:
            continue  # Un grupo solo con saldos pequeños no es pendiente del día.
        enlace_todos = None
        if len(lista) > MAXIMO_POR_GRUPO:
            if clave == "guardar":
                enlace_todos = reverse("inventario:lista") + "?estado=PENDIENTE"
            elif clave == "encajar":
                enlace_todos = reverse("producto_terminado:seleccionar_lote_caja")
            else:
                enlace_todos = _enlace_lista(clave)
        saldo = saldos.get(clave, 0)
        if saldo and not enlace_todos:
            enlace_todos = (reverse("producto_terminado:seleccionar_lote_caja") if clave == "encajar"
                            else _enlace_lista(clave))
        grupos.append(dict(clave=clave, titulo=titulo, ayuda=ayuda, total=len(lista), saldos=saldo,
                           items=lista[:MAXIMO_POR_GRUPO], enlace_todos=enlace_todos,
                           urgente=clave in ("revision", "EN_CONGELACION") or any(
                               getattr(item, "alerta", False) for item in lista)))
    return dict(grupos=grupos, total=sum(g["total"] for g in grupos), gestion=gestion)
