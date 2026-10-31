"""Navegación de presentación; las vistas conservan sus controles de acceso."""
from django import template
from apps.usuarios.permisos import tiene_rol
from apps.trazabilidad.models import SolicitudRuta

register = template.Library()

# Nombres completos y explícitos: nunca coincidencias parciales de URL.
ENLACES = (
    ('dashboard', 'Dashboard', 'reportes:dashboard', ('reportes:dashboard', 'reportes:umbrales'), 'jefe'),
    ('consulta', 'Consulta de trazabilidad', 'trazabilidad:consulta_integral', ('trazabilidad:consulta_integral', 'trazabilidad:ficha_integral'), 'gestion'),
    ('reportes', 'Reportes', 'reportes:sernapesca', ('reportes:sernapesca', 'reportes:descargar_sernapesca'), 'jefe'),
    ('rutas', 'Rutas de procesamiento', 'trazabilidad:rutas', ('trazabilidad:rutas', 'trazabilidad:nueva_ruta', 'trazabilidad:editar_ruta', 'trazabilidad:duplicar_ruta', 'trazabilidad:estado_ruta', 'trazabilidad:operaciones', 'trazabilidad:editar_operacion'), 'jefe'),
    ('solicitudes', 'Solicitudes de rutas', 'trazabilidad:solicitudes_ruta', ('trazabilidad:solicitudes_ruta', 'trazabilidad:resolver_solicitud'), 'personal'),
    ('usuarios', 'Usuarios', 'usuarios:lista_usuarios', ('usuarios:lista_usuarios', 'usuarios:crear_usuario', 'usuarios:detalle_usuario', 'usuarios:editar_rol', 'usuarios:reset_contrasena', 'usuarios:desactivar_usuario', 'usuarios:reactivar_usuario'), 'jefe'),
    ('recepciones', 'Recepciones', 'trazabilidad:lista_recepciones', ('trazabilidad:lista_recepciones', 'trazabilidad:nueva_recepcion', 'trazabilidad:detalle_recepcion', 'trazabilidad:corregir_recepcion'), 'gestion'),
    ('seguimiento', 'Seguimiento de proceso', 'trazabilidad:lista_partidas', ('trazabilidad:lista_partidas', 'trazabilidad:detalle_partida', 'trazabilidad:seleccionar_operaciones', 'trazabilidad:registrar_peso_postproceso', 'trazabilidad:registrar_merma_proceso', 'trazabilidad:gestionar_procesamiento', 'trazabilidad:iniciar_etapa', 'trazabilidad:finalizar_etapa', 'trazabilidad:enviar_partida_tunel', 'trazabilidad:retirar_partida_tunel', 'trazabilidad:enviar_partida_mantencion', 'trazabilidad:retirar_partida_mantencion', 'trazabilidad:procesar_partida'), 'personal'),
    ('lotes', 'Lotes de producción', 'producto_terminado:lista_lotes', ('producto_terminado:lista_lotes', 'producto_terminado:crear_lote', 'producto_terminado:detalle_lote', 'producto_terminado:agregar_producto_lote'), 'gestion'),
    ('cajas', 'Cajas', 'producto_terminado:lista_cajas', ('producto_terminado:seleccionar_lote_caja', 'producto_terminado:lista_cajas', 'producto_terminado:consulta_caja_qr', 'producto_terminado:caja_qr_png', 'producto_terminado:detalle_caja', 'producto_terminado:crear_caja'), 'personal'),
    ('inventario', 'Inventario', 'inventario:lista', ('inventario:lista', 'inventario:ingresar', 'inventario:mover', 'inventario:lista_bajas', 'inventario:crear_baja'), 'personal'),
    ('despachos', 'Despachos', 'inventario:lista_despachos', ('inventario:lista_despachos', 'inventario:crear_despacho', 'inventario:detalle_despacho'), 'gestion'),
 )

@register.inclusion_tag("components/_sidebar.html", takes_context=True)
def menu_lateral(context):
    request = context["request"]
    user = request.user
    actual = getattr(request.resolver_match, "view_name", "")
    permisos = {"jefe": tiene_rol(user, "JEFE"),
                "gestion": tiene_rol(user, "JEFE", "ENCARGADA"),
                "personal": tiene_rol(user, "JEFE", "ENCARGADA", "OPERARIA")}
    pendientes = 0
    if permisos["personal"]:
        if not hasattr(request, "_menu_solicitudes_pendientes"):
            consulta = SolicitudRuta.objects.filter(estado="PENDIENTE")
            if not permisos["jefe"]:
                consulta = consulta.filter(solicitante=user)
            request._menu_solicitudes_pendientes = consulta.count()
        pendientes = request._menu_solicitudes_pendientes
    grupos = []
    for titulo, enlaces in (("Control y administración", ENLACES[:6]), ("Flujo productivo", ENLACES[6:])):
        visibles = [dict(clave=clave, nombre=nombre, destino=destino, activo=actual in paginas,
                         pendientes=pendientes if clave == "solicitudes" else 0)
                    for clave, nombre, destino, paginas, permiso in enlaces if permisos[permiso]]
        if visibles:
            grupos.append(dict(titulo=titulo, enlaces=visibles))
    return {"grupos": grupos, "mostrar_inicio": user.is_authenticated and not permisos["jefe"],
            "inicio_activo": actual == "usuarios:inicio"}
