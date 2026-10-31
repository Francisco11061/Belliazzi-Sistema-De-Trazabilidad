# Seguridad, errores y feedback de formularios

## Alcance y archivos

- `pyproject.toml`, `uv.lock`: django-axes **8.3.1**, fijado y compatible con el Django 5.2 instalado.
- `config/settings.py`, `.env.example`: Axes, persistencia compartida, proxies confiables y español de Chile.
- `apps/usuarios/seguridad.py`: dirección IP validada, almacenamiento de fallos sin contenido GET/POST y respuesta 429 con `Retry-After`.
- `config/errores.py`, `config/urls.py`, `templates/errores/{base,400,403,404,429,500}.html`: páginas independientes del sidebar, usuario y sesión.
- `apps/usuarios/static/usuarios/js/formularios.js`, `css/formularios.css`: módulo único de carga y recuperación.
- `apps/usuarios/templatetags/formularios.py`, `templates/components/_form_errors.html`: resumen accesible con enlaces a campos y filas de formsets.
- `templates/base.html` y `apps/usuarios/templates/usuarios/login.html`: carga del módulo común; el login conserva `next`.
- Templates POST de usuarios, umbrales, recepciones, seguimientos, rutas, lotes, cajas, almacenamiento, bajas y despachos: atributos de carga y resumen de errores. Los botones de selección de rutas conservan acciones distintas.
- `apps/inventario/forms.py`: formato ISO explícito de los filtros de fecha, para conservar los controles HTML `date` al activar `es-cl`.
- `apps/usuarios/test_seguridad.py`, `test_errores.py`, `test_formularios.py`: pruebas nuevas.
- `apps/usuarios/tests.py`, `test_gestion.py`: llamadas de prueba a `authenticate` con `request`, requerido por Axes.
- Dos presupuestos de consultas en `apps/trazabilidad/test_partidas.py` y `tests.py` incluyen la consulta única del contador de solicitudes del sidebar de la ronda anterior; no se eliminó la comprobación contra N+1.

El árbol de trabajo también contiene los cambios anteriores de sidebar e historial/QR, descritos en `docs/historial_producto_qr.md`. No se revirtieron cambios previos del dashboard. Esta mejora de seguridad no cambia modelos productivos, UUID, pesos, permisos ni servicios de inventario.

Templates con integración de formularios (rutas relativas a cada carpeta):

| Carpeta | Archivos |
|---|---|
| `apps/usuarios/templates/usuarios/` | `login.html`, `gestion/formulario.html`, `gestion/detalle.html` |
| `apps/reportes/templates/reportes/` | `umbrales.html` |
| `apps/inventario/templates/inventario/` | `operacion.html`, `bajas/crear.html`, `despachos/crear.html` |
| `apps/trazabilidad/templates/trazabilidad/recepciones/` | `nueva.html`, `corregir.html` |
| `apps/trazabilidad/templates/trazabilidad/partidas/` | `_siguiente_paso.html`, `operar.html`, `procesamiento.html`, `registrar_medicion.html`, `tunel.html` |
| `apps/trazabilidad/templates/trazabilidad/lotes/` | `crear.html`, `agregar.html` |
| `apps/trazabilidad/templates/trazabilidad/cajas/` | `crear.html` |
| `apps/trazabilidad/templates/trazabilidad/rutas/` | `form.html`, `lista.html`, `operaciones.html`, `resolver.html`, `seleccionar.html` |

## Política de autenticación

Se mantiene `LoginView`, `AuthenticationForm`, CSRF, sesiones y `ModelBackend`. Axes se ejecuta antes de `ModelBackend` y su middleware transforma el bloqueo en HTTP 429.

- Cinco fallos acumulados por combinación **usuario + IP**. El quinto ya devuelve 429.
- Enfriamiento de 15 minutos según la política soportada por Axes: inactividad desde el último fallo contabilizado. No es una ventana deslizante de eventos individuales.
- Los intentos durante el bloqueo no prolongan el plazo. El éxito borra los fallos de esa combinación.
- Un usuario bloqueado no bloquea por sí solo otras cuentas de la misma red. Otra IP tiene contador independiente, como exige la política combinada; esto no pretende ser una defensa global contra ataques distribuidos.
- `Retry-After` expresa segundos restantes. No se informa si la cuenta existe.
- Backend de base de datos oficial, con actualizaciones transaccionales. No usa contadores Python ni `LocMemCache`; las dos instancias comparten RDS.
- La subclase del handler solo evita almacenar datos GET/POST. No sustituye los algoritmos de conteo de Axes. Los logs ocultan usuario/IP; no se guardan contraseñas, tokens ni datos del formulario. Se deshabilitan registros adicionales de sesiones y de fallos históricos.
- Los intentos vencidos dejan de bloquear y Axes limpia registros vencidos durante comprobaciones posteriores. No se configuró una tarea periódica externa de limpieza.

Documentación consultada: [configuración de la versión 8.3.1](https://raw.githubusercontent.com/jazzband/django-axes/8.3.1/docs/4_configuration.rst) y código instalado de esa versión. `Retry-After` se implementa en el callable; no se usa una opción exclusiva de versiones posteriores.

## Activación local y migraciones

**Activación local completada el 27 de octubre de 2026, con autorización explícita del usuario.** Se aplicaron exclusivamente las migraciones de Axes `0001` a `0010` en `belliazzi_trazabilidad` de `127.0.0.1` y se guardó `AXES_ENABLED=True` en `.env`. Incluyen cuatro tablas de la dependencia: `AccessAttempt`, `AccessLog`, `AccessFailureLog` y `AccessAttemptExpiration`. No hay migraciones nuevas de modelos de Belliazzi ni cambios de información histórica.

Después de autorización y comprobando que la conexión corresponde a la base deseada:

```powershell
uv run python manage.py migrate axes --plan
uv run python manage.py migrate axes
```

La configuración local ya tiene `AXES_ENABLED=True`. Con `DEBUG=True`, Axes está desactivado por defecto si no existe esa activación explícita; con `DEBUG=False`, está activado por defecto. En AWS dejarlo explícitamente en `True` y aplicar las migraciones una sola vez sobre RDS antes de servir tráfico con esta versión. No desplegar antes de ese paso.

## Consultar y liberar un bloqueo

Solo personal con acceso administrativo autorizado puede consultar `AccessAttempt` en el administrador Django. Ser JEFE del sistema no concede por sí mismo acceso a ese administrador.

Para desbloquear únicamente la combinación requerida, después de verificar identidad, IP y autorización:

```powershell
uv run python manage.py axes_reset_ip_username 192.0.2.8 nombre_usuario
```

IP primero y usuario después. No ejecutar un borrado global para resolver un caso individual. El comando modifica los registros de intentos; el desbloqueo no cambia la contraseña ni los permisos. Los registros contienen nombre de usuario, IP, fecha y contador, por lo que su acceso debe ser restringido.

## AWS y dirección del cliente

- Ambas EC2 deben usar la misma RDS, `AXES_ENABLED=True`, los mismos parámetros de Axes y la configuración coherente de sesiones/secretos ya existente.
- Configurar `TRUSTED_PROXY_CIDRS` como lista separada por comas de redes concretas de los proxies confiables. No usar `0.0.0.0/0` ni `::/0`. Por defecto la lista está vacía y se utiliza exclusivamente `REMOTE_ADDR`.
- El parser solo lee `X-Forwarded-For` si el peer es confiable; recorre la cadena desde la derecha hasta el primer salto no confiable. Valida IPv4/IPv6 y puertos opcionales. No confía en el primer valor que el cliente puede falsificar.
- Mantener ALB en modo **append** y los security groups de EC2 restringidos al ALB. Si existe Nginx u otro proxy intermedio, revisar toda la cadena y cómo agrega el peer real. No basta con copiar un encabezado arbitrario del cliente.
- Revisar HTTPS, cookies seguras, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` y la confianza del encabezado de protocolo en la configuración de despliegue. No se alteró infraestructura ni se asumieron dominios o CIDR reales.
- Comprobar en AWS que peticiones de la misma combinación atendidas por EC2 distintas comparten el bloqueo. La prueba automatizada ejecutada usa dos procesos contra una misma base de pruebas; no sustituye esa verificación del ALB real.

Referencia: [manejo de X-Forwarded-For por ALB](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/x-forwarded-headers.html).

## Errores y formularios

400/403/404/429/500 conservan su código real, con mensajes españoles y enlaces de recuperación. CSRF tiene un 403 específico. La página 500 se renderiza sin `request` ni procesadores de contexto, sin consultas, sesión o assets externos. Django conserva el registro de la excepción; no se capturan errores generales para devolver 200. Los detalles de desarrollo siguen disponibles con DEBUG.

El módulo de formularios actúa sobre POST nativos. Muestra spinner después de 180 ms, fija el tamaño del botón, anuncia el texto y respeta movimiento reducido. No deshabilita inputs ni cambia `name`/`value` del botón. Bloquea un segundo envío de la misma página, restablece al volver mediante `pageshow` y se recupera si otro manejador cancela el submit. Sin JavaScript, se conserva el POST tradicional.

El resumen indica campos y filas, permite enfocar cada error y no incluye filas marcadas para eliminar. Se mantienen formularios enlazados, errores locales, contraseñas vacías y valores ingresados. Las pruebas comprueban números decimales y fechas ISO con `es-cl`.

Se revisaron los servicios transaccionales y bloqueos de filas de procesamiento, cajas, almacenamiento, bajas y despachos; no se reemplazaron. El bloqueo visual no garantiza idempotencia global frente a reintentos HTTP externos: una nueva creación válida puede representar otra operación legítima. Las reglas existentes de saldos y transiciones siguen siendo la protección del backend.

Se revisaron reportes, búsquedas e historial/QR: mantienen autenticación/permisos y paginación donde existe. No se añadieron cuotas arbitrarias que interrumpan operaciones productivas. La exportación y la construcción completa de un historial grande todavía pueden consumir recursos; no se hizo un ensayo de carga de producción ni se introdujo infraestructura nueva.

## Validación

- `uv run python manage.py check`: sin incidencias.
- `git diff --check`: correcto (Git avisa sobre normalización LF/CRLF en algunos archivos, no sobre errores del diff).
- `uv run python manage.py makemigrations --check --dry-run`: sin cambios de modelos.
- Suite completa con `AXES_ENABLED=True`: **702 pruebas, 698 aprobadas, 3 fallos y 1 error**. Incluye usuarios, trazabilidad, inventario, reportes, rutas, QR, permisos y concurrencia. Las 20 pruebas nuevas pasan.
- Los cuatro problemas restantes están en `DashboardPermisosTests.test_estados_vacios`, `MicrocopyAvisosTests.test_dashboard_franja_enlaza_criterios_al_final`, `MicrocopyAvisosTests.test_reportes_conserva_aviso_oficial_y_remite_al_excel` y `ReporteSernapescaTests.test_pantalla_y_urls_existentes`. Esperan actividad reciente, una sección de criterios y el aviso oficial Sernapesca que tampoco están en los templates de HEAD. No se cambiaron esos textos/secciones ajenos a este alcance para forzar una suite verde.
- Cinco pruebas adicionales de renderizado de caja, despacho, rutas y login: aprobadas. Comprueban, entre otros casos, datos conservados y el enlace enfocable al grupo de cajas cuando la selección falta.
- Edge/Playwright: **70 combinaciones de pantalla/ancho** (320, 390, 768, 1024 y 1440), sin overflow horizontal, enlaces de resumen rotos ni errores JavaScript. Se revisaron capturas de bloqueo, 500 y caja con error en móvil/escritorio.
- Prueba de transporte HTTP local con el JS/CSS real: ocho POST, sin incidencias. Incluye tres acciones de rutas, doble clic, validación HTML5, 500 y Atrás, `pageshow` persistido simulado, cancelación por otro listener, ausencia de JS, movimiento reducido y envío por teclado. La simulación de `pageshow` comprueba el manejador; no afirma que todos los navegadores usen bfcache en toda navegación.
- GET al login del servidor local: 200 con el módulo cargado. No se provocaron bloqueos sobre cuentas reales.
- No fue necesaria una nueva compilación Tailwind para esta mejora: los estilos nuevos son CSS propio. `output.css` ya contiene la compilación de la ronda previa.

La comprobación focalizada final ejecutó **21 pruebas, todas aprobadas** (`.git/seguridad-focused-tests.log`). Tras la activación autorizada, `manage.py check` pasó, las diez migraciones aparecen aplicadas y el GET al login mediante el cliente de Django devolvió 200 con la configuración y base locales. No había servidor escuchando en el puerto 8000 en esa comprobación final; al iniciar `runserver` cargará la configuración activada. No se realizaron intentos fallidos contra cuentas reales.

Evidencia local: `.git/seguridad-final-tests.log`, `.git/form-feedback-results.json`, `.git/seguridad-ui-results.json`, `.git/seguridad-preview/`. No se hizo commit, push, merge ni despliegue. La validación detrás del ALB real sigue pendiente.
