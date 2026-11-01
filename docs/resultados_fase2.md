# Resultados de fase 2 — Belliazzi

Fecha: 31 de octubre de 2026. Base de trabajo: `main`, commit `74045af86df1d5ac09f0362ee07b76a6ec8a537d`, confirmado como HEAD remoto al comenzar. El repositorio estaba limpio. Los cambios de esta fase están sin commit.

## Resultado

Los cuatro fallos originales quedaron corregidos sin eliminar ni relajar sus aserciones. La suite completa pasa en MySQL y PostgreSQL. La configuración de producción está separada de la local y se verificaron la recolección de estáticos, Nginx y Gunicorn en pruebas locales aisladas. No se desplegó en AWS ni se certifica todavía una infraestructura AWS real.

## Archivos modificados y nuevos

| Archivos | Cambio |
|---|---|
| `apps/reportes/templates/reportes/dashboard.html` | Restituye actividad reciente con enlaces y fechas; muestra «Sin actividad reciente.» cuando está vacía. Añade la sección real `criterios-colores` sin cambiar los gráficos existentes. |
| `apps/reportes/templates/reportes/sernapesca.html` | Aviso visible de que el reporte de apoyo no reemplaza declaraciones oficiales. Conserva criterios y descargas existentes. |
| `config/settings.py` | Selector MySQL/PostgreSQL por `DB_ENGINE`, opciones separadas, conexiones configurables y `STATIC_ROOT`. MySQL sigue por defecto. No carga `.env` al seleccionar producción. |
| `config/settings_production.py` | Configuración explícita de producción con validaciones de secretos, hosts, CSRF, PostgreSQL/TLS, cookies, HTTPS, proxy y HSTS condicionado. |
| `pyproject.toml`, `uv.lock` | psycopg/psycopg-binary 3.3.6 y Gunicorn 26.2.0 en el lock; Gunicorn solo en plataformas no Windows. |
| `.env.example`, `.gitignore` | Documentan variables locales y excluyen la salida `staticfiles/`. No se modificó `.env`. |
| `deploy/production.env.example` | Variables de producción; secretos vacíos, dominio de ejemplo y HSTS sin activar. |
| `deploy/gunicorn.conf.py` | WSGI en loopback, workers/timeouts configurables y logs sin querystrings/cookies/cuerpos. Django controla la confianza del esquema. |
| `deploy/nginx.conf.example` | Esquema ALB → Nginx → Gunicorn, estáticos, normalización de cabeceras y health check básico. |
| `deploy/compose.postgres-test.yml` | PostgreSQL local de pruebas con contraseña por entorno, loopback y tmpfs. |
| `scripts/test_isolated.py`, `scripts/test_runner.py` | Runner reproducible con bases nuevas de nombre único y resultados JSON. Incluye modo de concurrencia. |
| `apps/usuarios/test_configuracion.py` | Seis pruebas nuevas de selección de motor, aislamiento de producción y rechazo de configuraciones inseguras. |
| `README.md`, `docs/postgresql_aws.md`, este informe | Procedimientos, restricciones, evidencias y pendientes. |

No cambiaron modelos, migraciones, servicios productivos, permisos, URLs, lógica de pesos ni identificadores. No se modificaron los tests originales de Reportes/Dashboard para ocultar fallos. Se reutilizaron los estilos existentes; no fue necesario recompilar Tailwind ni cambiar `output.css`.

## Pruebas ejecutadas

Python del entorno local: 3.14.7. Django: 5.2.17. Axes: 8.3.1. MySQL: 8.0.46. PostgreSQL: 17.11 en contenedor `postgres:17` temporal.

| Ejecución | Descubiertas/ejecutadas | Aprobadas | Fallos/errores | Omitidas | Duración del runner |
|---|---:|---:|---:|---:|---:|
| Suite completa MySQL | 708 | 708 | 0 | 0 | 235,341 s |
| Suite completa PostgreSQL | 708 | 708 | 0 | 0 | 463,928 s |
| Concurrencia independiente MySQL | 23 | 23 | 0 | 0 | 9,322 s |
| Concurrencia independiente PostgreSQL | 23 | 23 | 0 | 0 | 38,556 s |
| Fixtures adicionales de renderizado PostgreSQL | 3 | 3 | 0 | 0 | 2,564 s |

Las ejecuciones de concurrencia repiten 23 casos ya incluidos en cada suite; no son 23 pruebas nuevas. Las tres pruebas de renderizado repiten escenarios existentes para capturar páginas reales con fixtures. No se utilizaron datos operativos reales.

Cada suite completa incluye:

| Aplicación | MySQL | PostgreSQL |
|---|---:|---:|
| Usuarios, permisos y seguridad | 97/97 | 97/97 |
| Trazabilidad, rutas, procesamiento, lotes, cajas y QR | 413/413 | 413/413 |
| Inventario, despachos y bajas | 93/93 | 93/93 |
| Dashboard y reportes | 105/105 | 105/105 |

La diferencia frente a las 702 pruebas anteriores son las seis pruebas de configuración nuevas. Las 105 de Reportes pasaron, incluidos los cuatro fallos originales. Las migraciones se ejecutaron sobre bases nuevas y también pasaron los casos existentes de conservación histórica. No se ejecutó `migrate` contra la base de desarrollo.

Comandos principales:

```text
uv run python manage.py check
uv run python scripts/test_isolated.py --backend mysql
uv run python scripts/test_isolated.py --backend postgresql
uv run python scripts/test_isolated.py --backend mysql --concurrency-only
uv run python scripts/test_isolated.py --backend postgresql --concurrency-only
uv run python manage.py makemigrations --check --dry-run
git diff --check
```

`manage.py check`: sin incidencias. `makemigrations --check --dry-run`: sin cambios. `git diff --check`: sin errores; Git solo informa normalización LF/CRLF en algunos archivos.

## Comprobaciones de producción, estáticos e interfaz

- `check --deploy` con `config.settings_production`, variables de prueba y HSTS inicial: únicamente **W004**. No se usaron ni almacenaron secretos de producción.
- Simulación con `SECURE_HSTS_SECONDS=300`: únicamente **W005** (subdominios) y **W021** (preload), mantenidas deliberadamente. No se habilitaron esos flags para silenciar advertencias.
- `collectstatic --noinput` bajo configuración de producción: **139 archivos copiados** a un directorio temporal. Verificados `usuarios/css/output.css`, `reportes/dashboard.css`, `reportes/dashboard.js`, `reportes/vendor/chart.umd.min.js` y `usuarios/js/formularios.js`.
- `docker compose ... config --quiet`: configuración de pruebas válida.
- `nginx -t` dentro de un contenedor Linux temporal: correcto.
- Gunicorn **26.2.0** con Python 3.14 en Linux: `--check-config` correcto y arranque WSGI comprobado. GET de login con esquema HTTPS del proxy: **200**, formulario presente y cookie CSRF `Secure`; solicitud HTTP: **301** a la URL HTTPS del dominio de prueba.
- La verificación de Gunicorn no conectó a RDS. Utilizó credenciales aleatorias de prueba y un archivo marcado expresamente como no utilizable como CA: valida carga/configuración, no TLS de una base real. La suite PostgreSQL local se ejecutó sin TLS, dentro de loopback.
- Edge/Playwright: **9 combinaciones**, dashboard con actividad, vacío y Reportes a **320, 390 y 1440 px**. Sin overflow horizontal ni errores JavaScript, enlace a criterios funcional y aviso presente. Se inspeccionaron capturas de la sección móvil y el aviso de escritorio. No es una auditoría completa de accesibilidad ni una prueba de todos los módulos en navegador.

## Diferencias entre motores

Fue necesario separar la configuración del controlador: MySQL usa `charset=utf8mb4` y puerto habitual 3306; PostgreSQL usa psycopg, puerto habitual 5432 y sus parámetros TLS. No fue necesario introducir ramas SQL específicas, cambiar restricciones o modificar reglas de negocio para que pasaran los casos existentes.

No se encontraron incompatibilidades funcionales en las 708 pruebas ni en los 23 casos concurrentes de PostgreSQL. Esto no garantiza compatibilidad de todos los datos reales: la transferencia de registros, collations, secuencias e históricos debe ensayarse por separado.

PostgreSQL tardó más en estas ejecuciones. MySQL fue nativo en Windows y PostgreSQL estuvo en Docker; hubo trabajo simultáneo en el equipo. Los tiempos se informan como evidencia de ejecución, **no como benchmark comparable entre motores** ni dimensionamiento para RDS.

## Pendientes y riesgos antes de AWS

1. Proveer dominio, certificado, secretos independientes, hosts y orígenes reales. No reutilizar la clave local advertida en la auditoría ni los valores de ejemplo.
2. Configurar RDS privado y verificar una conexión real `verify-full` con su CA vigente. La existencia del archivo CA no demuestra que sea correcto.
3. Restringir EC2 al SG del ALB y Gunicorn a loopback. Ajustar proxies confiables y habilitar `TRUST_PROXY_HEADERS` solo después de comprobar la cadena. Verificar IP real, HTTPS y URLs QR públicas en el ALB real.
4. Resolver operativamente HSTS: W004 es esperada mientras está desactivado; W005/W021 quedan si no se incluyen subdominios/preload. Habilitarlos requiere validar sus efectos sobre el dominio completo.
5. Instalar y comprobar el servicio Linux completo y dependencias de la AMI. El proyecto conserva `mysqlclient`; su instalación en Linux puede requerir compilador/librerías. Gunicorn se probó con las dependencias de runtime PostgreSQL, no se provisionó una AMI ni se ejecutó el pipeline completo de instalación allí.
6. El `/healthz` de Nginx solo verifica ese servidor: definir comprobaciones reales de aplicación/DB, monitorización y alertas antes de aceptar staging.
7. Si se transfieren datos existentes, hacer respaldo/restauración, conciliación de saldos/UUID/relaciones, secuencias y rollback. No hay autorización implícita para migrar datos reales.
8. Falta ensayo de carga y multiinstancia real. Las pruebas de bloqueo y Axes compartido no sustituyen medir latencia, memoria y fallos de red. Historial y XLSX aún materializan datos en memoria.

El código está preparado para la siguiente validación controlada de staging; **no constituye autorización ni certificación de un despliegue AWS**.

## Integridad y estado de Git

- `.env` y los datos reales no fueron modificados.
- Se verificó la ausencia de las bases de pruebas creadas, tanto en MySQL como en PostgreSQL, después del teardown.
- Se detuvo y eliminó el contenedor PostgreSQL temporal; las credenciales temporales de sus archivos auxiliares se retiraron. Los contenedores Nginx/Gunicorn fueron efímeros. Las imágenes pueden permanecer en la caché local de Docker.
- No se crearon recursos AWS, ramas, commits ni PR. No hubo push, merge o despliegue.
- Git queda con los archivos de la tabla anterior modificados/nuevos, pendientes de revisión. Los logs, datos de fixtures capturados y salida de `collectstatic` están fuera del repositorio.

Evidencia local conservada:

- `%TEMP%/belliazzi-mysql-tests-1f_y275w/`: suite MySQL.
- `%TEMP%/belliazzi-postgresql-tests-_r96drcx/`: suite PostgreSQL.
- `%TEMP%/belliazzi-mysql-tests-whpz4xt1/`: concurrencia MySQL.
- `%TEMP%/belliazzi-postgresql-tests-03jctigu/`: concurrencia PostgreSQL.
- `%TEMP%/belliazzi-phase2-x865750o/`: logs de configuración/estáticos/Gunicorn, HTML y capturas de revisión visual.

Procedimiento reproducible y fuentes técnicas: [PostgreSQL y AWS](postgresql_aws.md).
