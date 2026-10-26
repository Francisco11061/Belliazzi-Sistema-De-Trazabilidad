# Rutas de procesamiento y consulta integral

Entrega local del 23 de octubre de 2026. No se realizaron commits, push, merge ni despliegues. No se aplicaron migraciones ni se escribieron datos en la base de trabajo; las pruebas utilizaron la base temporal de Django.

## Funcionalidades y pantallas

- Gestión del catálogo de rutas por especie: búsqueda, filtros, secuencias ordenadas, edición de configuraciones no utilizadas, duplicación, activación y predeterminada.
- Gestión de operaciones: se preservan código y nombre cuando tienen referencias. `PROCESAMIENTO` continúa reservado al evento general.
- Inicio con una ruta habitual que muestra sus operaciones, o selección de operaciones y búsqueda de una coincidencia exacta. Las coincidencias ambiguas requieren elección explícita.
- Solicitudes de secuencias nuevas, aprobación o rechazo por jefe. Las solicitudes pendientes no generan eventos ni divisiones de producto. Las aprobaciones y altas se serializan por especie para evitar duplicados concurrentes.
- Dashboard: accesos a nueva ruta, gestión y solicitudes pendientes; gráficos sin modificaciones.
- Buscador por folio Sernapesca, recepción, seguimiento, lote, caja/código/UUID/URL QR o despacho.
- Ficha integral con orígenes, familias de seguimientos, aportes, planificación y ejecución real, pesajes, mermas, frío, cajas, almacenamiento, despachos, bajas, conciliación, cronología y correcciones verificables.
- Enlaces «Ver ficha integral» desde los detalles. Actividad y mediciones del seguimiento e historial del QR quedan desplegables; no se eliminó información operativa.

## Reglas y decisiones

Una ruta puede terminar en cualquier operación válida del catálogo. Para ingresar al túnel deben estar correctamente finalizadas todas sus etapas; se mantienen los controles de orden, duplicados, etapas abiertas, especie y operaciones inactivas. No se agregaron destinos posteriores al procesamiento.

Las rutas ya usadas conservan su configuración. Para modificarla se duplica la ruta; la copia comienza inactiva y no predeterminada. Una configuración activa equivalente impide activar otra nueva con la misma especie y secuencia.

La conciliación separa cantidades de distintas etapas y suma las hojas de las divisiones, sin agregar otra vez el padre. Los datos inexistentes o ambiguos no se convierten en pesos cero. Las diferencias se presentan para revisión, sin convertirlas automáticamente en mermas.

La ficha muestra los aportes registrados al lote. No calcula qué parte de cada aporte entró físicamente en una caja. Las correcciones se relacionan por entidad e identificador exactos y la cronología usa las fechas de cada registro. No existe una nueva tabla de eventos copiados.

## Permisos

| Función | Jefe / superusuario | Encargada | Operario |
| --- | --- | --- | --- |
| Administrar rutas y operaciones | Sí | No | No |
| Resolver solicitudes | Sí | No | No |
| Usar rutas y solicitar secuencias | Sí | Sí | Sí |
| Consultar solicitudes | Todas | Propias | Propias |
| Consulta integral | Sí | Sí | No |

La ficha completa contiene datos de recepciones, lotes y despachos, que el operario actualmente no puede consultar en detalle. Por eso la protección se aplica tanto en las vistas como en los selectores, además de los enlaces. Se conservan autenticación, sesiones y retorno con `next`. Las escrituras usan POST, CSRF y servicios transaccionales.

## Archivos

Nuevos, dentro de `apps/trazabilidad/`:

- `services_rutas.py`, `forms_rutas.py`, `views_rutas.py`.
- `consulta_integral.py`, `views_consulta.py`.
- `test_gestion_rutas.py`, `test_consulta_integral.py`.
- `static/trazabilidad/secuencia.js`.
- `migrations/0014_solicitudes_rutas.py`.
- `templates/trazabilidad/rutas/`: `_campos.html`, `_fila.html`, `_secuencia.html`, `form.html`, `lista.html`, `operaciones.html`, `seleccionar.html`, `solicitudes.html`, `resolver.html`.
- `templates/trazabilidad/consulta/`: `buscar.html`, `ficha.html`, `_peso.html`, `_seguimiento.html`.

Nuevos componentes: `templates/components/_enlace_integral.html` y `_navegacion_trazabilidad.html`.

Modificados:

- `apps/trazabilidad/`: `models.py`, `admin.py`, `rutas.py`, `services.py`, `selectors.py`, `forms.py`, `urls.py`, `test_procesamiento.py`, `test_qr_cajas.py`.
- Templates de trazabilidad: `recepciones/detalle.html`, `partidas/detalle.html`, `partidas/operar.html`, `partidas/procesamiento.html`, `lotes/detalle.html`, `cajas/detalle.html`, `cajas/consulta_qr.html`.
- `apps/inventario/templates/inventario/despachos/detalle.html`.
- `apps/reportes/views.py`, `templates/reportes/dashboard.html`, `test_matriz_permisos.py`.
- `templates/base.html`, `templates/components/_navigation.html`.
- `apps/usuarios/static/usuarios/css/output.css`, recompilado con los argumentos de `build:css`.

## Migración

`0014_solicitudes_rutas` agrega exclusivamente `SolicitudRuta` y `EtapaSolicitudRuta`, con referencias protegidas, orden positivo y orden único dentro de cada solicitud. No cambia ni completa registros históricos. Se ejecutó en las bases temporales de pruebas; no en la base de trabajo.

## Validación

- `uv run python manage.py check`: sin problemas.
- `uv run python manage.py makemigrations --check --dry-run`: sin cambios pendientes.
- `git diff --check`: sin errores.
- Tailwind compilado con `-i ./static/src/input.css -o ./apps/usuarios/static/usuarios/css/output.css --minify`.
- Suite completa: **650 pruebas, 647 correctas, 2 fallos y 1 error**, todos los restantes en reportes.
- Pruebas de las nuevas funciones y límite de consultas de procesamiento: **23 correctas**, repetidas tras los ajustes de interfaz. Incluyen concurrencia y el recorrido recepción → división → procesamiento → pesaje → lote → caja → almacenamiento → despacho, con reconstrucción inversa.
- Matriz de permisos ampliada para todas las nuevas URLs; incluida en la suite completa.
- Edge sin interfaz: 18 páginas renderizadas con datos de pruebas, a 375, 768 y 1280 px, sin overflow horizontal. Comprobados agregar, quitar y reordenar operaciones; capturas de formularios y ficha inspeccionadas visualmente. Se corrigieron el selector comprimido y las casillas estiradas en móvil.

Los tres resultados pendientes se reprodujeron usando los templates de reportes de `HEAD`, anteriores a esta entrega:

1. `test_dashboard_franja_enlaza_criterios_al_final`: falta el destino `id="criterios-colores"`.
2. `test_reportes_conserva_aviso_oficial_y_remite_al_excel`: falta el aviso esperado del reporte de apoyo.
3. `test_pantalla_y_urls_existentes`: espera el mismo aviso ausente.

No se restauraron esos bloques ajenos a esta intervención ni se debilitaron sus tests. La suite completa, por tanto, no está totalmente verde. Logs y capturas locales de diagnóstico están en `.git/integral-*` y no forman parte de los archivos entregables versionables.

## Límites y supuestos a confirmar

- Se consideran autorizadas las rutas activas del catálogo existente. No se creó otra clasificación del producto final: la equivalencia usa especie y secuencia exacta.
- La interfaz admite hasta 100 operaciones por configuración como límite de carga, no como regla industrial de tres etapas.
- La ficha de lote/caja/despacho conserva el alcance de los lotes consultados y muestra las familias de sus aportantes. Desde origen/recepción/seguimiento se recorren sus divisiones y lotes relacionados; cada seguimiento y lote permite continuar la investigación mediante enlaces.
- Búsquedas y catálogos usan paginación de consultas. La ficha pagina la presentación de cajas, seguimientos y cronología, pero construye en memoria el conjunto documental relacionado: un origen con un volumen extraordinario puede requerir una optimización adicional. No se validó con un volumen de producción masivo.
- `ConsumoLote` no registra fecha propia: no se inventó una fecha de asignación ni un evento de movimiento para ese aporte.
- Las pruebas visuales usan HTML real renderizado con datos temporales; no se probaron registros reales ni se aplicó la migración sobre ellos.

## Prueba local

Con la configuración apuntando a una base local de desarrollo:

```powershell
uv run python manage.py migrate
uv run python manage.py check
uv run python manage.py runserver
```

La migración es necesaria antes de usar las pantallas nuevas o el contador del dashboard. El CSS ya está recompilado.

1. Como jefe, abrir `/trazabilidad/rutas/`, crear una ruta, agregar y reordenar operaciones. Revisar `/trazabilidad/operaciones/`.
2. Desde un seguimiento disponible, iniciar por ruta habitual o «Seleccionar operaciones». Proponer una combinación inexistente y comprobar que permanece disponible, sin eventos de producción.
3. Como jefe, resolverla en `/trazabilidad/solicitudes-ruta/`; luego elegir la ruta aprobada desde el seguimiento.
4. Consultar `/trazabilidad/consulta/` con un folio, lote o QR. Revisar aportes, ramas, cajas, conciliación y cronología; comprobar la misma procedencia desde el despacho.
5. Como operario, verificar acceso a solicitudes propias y rechazo de la URL de consulta integral.
