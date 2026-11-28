"""Navegación de presentación; las vistas conservan sus controles de acceso."""
from django import template
from apps.usuarios.permisos import tiene_rol

register = template.Library()

# Nombres completos y explícitos: nunca coincidencias parciales de URL.
ENLACES = (
    ('dashboard', 'Dashboard', 'reportes:dashboard', ('reportes:dashboard', 'reportes:umbrales'), 'jefe'),
    ('consulta', 'Consulta de trazabilidad', 'trazabilidad:consulta_integral', ('trazabilidad:consulta_integral', 'trazabilidad:ficha_integral'), 'gestion'),
    ('reportes', 'Reportes', 'reportes:sernapesca', ('reportes:sernapesca', 'reportes:descargar_sernapesca'), 'jefe'),
    ('rutas', 'Rutas de procesamiento', 'trazabilidad:rutas', ('trazabilidad:rutas', 'trazabilidad:nueva_ruta', 'trazabilidad:editar_ruta', 'trazabilidad:duplicar_ruta', 'trazabilidad:estado_ruta', 'trazabilidad:operaciones', 'trazabilidad:editar_operacion'), 'gestion'),
    ('usuarios', 'Usuarios', 'usuarios:lista_usuarios', ('usuarios:lista_usuarios', 'usuarios:crear_usuario', 'usuarios:detalle_usuario', 'usuarios:editar_rol', 'usuarios:reset_contrasena', 'usuarios:desactivar_usuario', 'usuarios:reactivar_usuario'), 'jefe'),
    ('recepciones', 'Recepciones', 'trazabilidad:lista_recepciones', ('trazabilidad:lista_recepciones', 'trazabilidad:nueva_recepcion', 'trazabilidad:detalle_recepcion', 'trazabilidad:corregir_recepcion'), 'gestion'),
    ('seguimiento', 'Seguimiento de proceso', 'trazabilidad:lista_partidas', ('trazabilidad:lista_partidas', 'trazabilidad:detalle_partida', 'trazabilidad:seleccionar_operaciones', 'trazabilidad:registrar_peso_postproceso', 'trazabilidad:registrar_merma_proceso', 'trazabilidad:gestionar_procesamiento', 'trazabilidad:iniciar_etapa', 'trazabilidad:finalizar_etapa', 'trazabilidad:enviar_partida_tunel', 'trazabilidad:retirar_partida_tunel', 'trazabilidad:enviar_partida_mantencion', 'trazabilidad:retirar_partida_mantencion', 'trazabilidad:procesar_partida'), 'personal'),
    ('lotes', 'Lotes de producción', 'producto_terminado:lista_lotes', ('producto_terminado:lista_lotes', 'producto_terminado:crear_lote', 'producto_terminado:detalle_lote', 'producto_terminado:agregar_producto_lote'), 'gestion'),
    ('cajas', 'Cajas', 'producto_terminado:lista_cajas', ('producto_terminado:seleccionar_lote_caja', 'producto_terminado:lista_cajas', 'producto_terminado:consulta_caja_qr', 'producto_terminado:caja_qr_png', 'producto_terminado:detalle_caja', 'producto_terminado:crear_caja'), 'personal'),
    ('inventario', 'Inventario', 'inventario:lista', ('inventario:lista', 'inventario:ingresar', 'inventario:mover', 'inventario:lista_bajas', 'inventario:crear_baja'), 'personal'),
    ('despachos', 'Despachos', 'inventario:lista_despachos', ('inventario:lista_despachos', 'inventario:crear_despacho', 'inventario:detalle_despacho'), 'gestion'),
 )

def _contadores_planta(request, gestion):
    """Lo que espera en cada sección, con dos consultas por página como máximo."""
    if not hasattr(request, "_menu_contadores"):
        from apps.inventario.selectors import cajas_con_inventario
        from apps.trazabilidad.models import PartidaProceso
        from apps.trazabilidad.pendientes import SALDO_MINIMO_KG
        from apps.trazabilidad.seguimiento_operativo import seguimientos_operativos

        # Se cuenta en Python: un aggregate condicional sobre estas anotaciones es muy lento en PostgreSQL.
        filas = seguimientos_operativos().filter(estado=PartidaProceso.Estado.ACTIVA).values_list(
            "categoria_operativa", "disponible_lote_kg", "situacion")
        atender = empaque = historial = 0
        for categoria, disponible, situacion in filas:
            atender += categoria == "atender"
            empaque += categoria == "empaque" and disponible >= SALDO_MINIMO_KG
            historial += situacion == "NO_OPERABLE"
        estados = list(cajas_con_inventario().filter(estado_inventario__in=("PENDIENTE", "REVISION")).values_list(
            "estado_inventario", flat=True))
        request._menu_contadores = {
            "seguimiento": (atender, "productos por atender"),
            "lotes": (empaque if gestion else 0, "productos listos para asignar a lote"),
            "inventario": (estados.count("PENDIENTE"), "cajas por guardar en cámara"),
        }
        # Para la campana: así no repite estas consultas.
        request._menu_revisiones = {"historial": historial, "ubicacion": estados.count("REVISION")}
    return request._menu_contadores


@register.inclusion_tag("components/_sidebar.html", takes_context=True)
def menu_lateral(context):
    request = context["request"]
    user = request.user
    actual = getattr(request.resolver_match, "view_name", "")
    permisos = {"jefe": tiene_rol(user, "JEFE"),
                "gestion": tiene_rol(user, "JEFE", "ENCARGADA"),
                "personal": tiene_rol(user, "JEFE", "ENCARGADA", "OPERARIA")}
    contadores = {}
    if permisos["personal"]:
        contadores.update(_contadores_planta(request, permisos["gestion"]))
    grupos = []
    for titulo, enlaces in (("Control y administración", ENLACES[:5]), ("Flujo productivo", ENLACES[5:])):
        visibles = [dict(clave=clave, nombre=nombre, destino=destino, activo=actual in paginas,
                         pendientes=contadores.get(clave, (0, ""))[0], pendientes_texto=contadores.get(clave, (0, ""))[1])
                    for clave, nombre, destino, paginas, permiso in enlaces if permisos[permiso]]
        if visibles:
            grupos.append(dict(titulo=titulo, enlaces=visibles))
    return {"grupos": grupos, "mostrar_inicio": user.is_authenticated and not permisos["jefe"],
            "inicio_activo": actual == "usuarios:inicio"}


@register.inclusion_tag("components/_campana.html", takes_context=True)
def campana(context):
    request = context["request"]
    user = request.user
    # La operaria no tiene avisos propios: lo suyo está en su inicio.
    if not tiene_rol(user, "JEFE", "ENCARGADA"):
        return {"mostrar": False}
    if not hasattr(request, "_campana"):
        from apps.reportes.avisos import avisos_para
        _contadores_planta(request, True)
        request._campana = avisos_para(user, request._menu_revisiones)
    return {"mostrar": True, **request._campana}
