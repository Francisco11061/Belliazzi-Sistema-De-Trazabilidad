# Fase 2: PostgreSQL y preparación para AWS

Esta configuración prepara un despliegue futuro; no crea infraestructura ni migra datos existentes. Usar un entorno de staging antes de producción.

## Configuración local

`config.settings` sigue siendo la configuración predeterminada. Lee `.env` sin sobreescribir las variables del proceso. `DB_ENGINE=mysql` es el valor predeterminado y conserva `utf8mb4`, puerto 3306 y las credenciales locales. PostgreSQL se selecciona con `DB_ENGINE=postgresql`; configurar también `DB_PORT=5432` o el puerto publicado por el contenedor. Las opciones `charset` de MySQL no se envían a PostgreSQL.

`STATIC_ROOT` admite una ruta de entorno; por defecto es `staticfiles/`, ignorada por Git. No apunta a las carpetas fuente de assets. El proyecto incorpora psycopg 3 con su paquete binario, y Gunicorn solo en plataformas distintas de Windows. Los paquetes exactos quedan registrados en `uv.lock`.

```powershell
uv sync --frozen
uv run python manage.py check
uv run python scripts/test_isolated.py --backend mysql
uv run python scripts/test_isolated.py --backend mysql --concurrency-only
```

El runner genera nombres aleatorios nuevos, comprueba que no existan y solo admite hosts loopback. Django crea y elimina exclusivamente esa base temporal; no usa `--keepdb`. No ejecutar comandos manuales de `migrate` sobre la base de desarrollo para realizar estas pruebas. El usuario SQL de pruebas necesita crear/eliminar bases; no se cambian sus permisos automáticamente. Si hay un fallo de conexión o permisos, el runner se detiene.

Cada ejecución escribe `tests.log` y `results.json` en un directorio temporal e imprime su ubicación. El segundo proceso de la prueba de Axes hereda la base de pruebas, no la base de desarrollo.

## PostgreSQL temporal reproducible

Requiere Docker Desktop/Engine disponible. El archivo Compose es exclusivamente de pruebas: publica loopback y usa tmpfs, sin volumen de datos persistente. El usuario del contenedor tiene permisos para crear las bases de tests y NO es un ejemplo de usuario de aplicación en RDS.

En una terminal PowerShell nueva, desde el repositorio:

```powershell
$env:POSTGRES_TEST_PASSWORD = (uv run python -c "import secrets; print(secrets.token_urlsafe(32))")
$env:POSTGRES_TEST_PORT = '55432'
docker compose -p belliazzi-tests -f deploy/compose.postgres-test.yml up -d --wait
$env:DB_ENGINE = 'postgresql'
$env:DB_HOST = '127.0.0.1'
$env:DB_PORT = $env:POSTGRES_TEST_PORT
$env:DB_USER = 'belliazzi_test'
$env:DB_PASSWORD = $env:POSTGRES_TEST_PASSWORD
$env:DB_NAME = 'postgres'
$env:DB_SSLMODE = 'disable'
uv run python scripts/test_isolated.py --backend postgresql
uv run python scripts/test_isolated.py --backend postgresql --concurrency-only
docker compose -p belliazzi-tests -f deploy/compose.postgres-test.yml down
```

Cerrar esa terminal elimina los overrides de entorno y evita confundir los datos de conexión con MySQL local. `DB_SSLMODE=disable` solo corresponde al contenedor local aislado. Nunca usarlo para RDS. Si el puerto está ocupado, seleccionar otro antes de iniciar Compose. Las imágenes `postgres:17` reciben actualizaciones: registrar su versión/digest en cada ejecución.

Si Docker/PostgreSQL no está disponible, ejecutar `docker version`, documentar el error y no declarar la suite PostgreSQL aprobada. No sustituirla por SQLite: no valida los mismos bloqueos ni la concurrencia.

## Producción separada

Seleccionar **`DJANGO_SETTINGS_MODULE=config.settings_production`**. Este módulo no lee `.env` del repositorio. Las variables deben inyectarse en el proceso por el gestor de servicios/secretos. `deploy/production.env.example` enumera nombres y valores no sensibles de referencia; los secretos están vacíos y esa plantilla no sirve para arrancar sin completarla.

La configuración exige:

- `SECRET_KEY` aleatoria, al menos 50 caracteres, suministrada por entorno; rechaza claves con prefijo de desarrollo.
- `DEBUG=False` incluso si el entorno contiene `DEBUG=True`.
- Hosts y orígenes CSRF HTTPS explícitos; no acepta `*`.
- PostgreSQL, nombre/usuario/contraseña/host explícitos, `sslmode=verify-full` y archivo CA existente indicado por `DB_SSLROOTCERT`.
- Cookies CSRF y sesión seguras, redirección HTTPS y Axes activado.
- Registro de errores en stderr; las páginas amigables no suprimen las excepciones del servidor.

No basta con que el archivo CA exista: hay que usar el CA vigente de RDS y comprobar una conexión TLS real con nombre de host verificado. Las comprobaciones de configuración sin conexión no certifican el certificado ni la red. RDS debe permanecer privado y su security group admitir solo los clientes autorizados. Separar permisos de migración de los permisos habituales de la aplicación.

Las mismas instancias deben compartir RDS y una configuración coherente de secreto/sesiones/Axes. Aplicar las migraciones una sola vez sobre la base de staging nueva antes de servir tráfico, mediante un procedimiento autorizado. No se incluye ningún paso automático que migre una base real al iniciar Gunicorn.

## ALB, Nginx y Gunicorn

Diseño preparado: **cliente HTTPS → ALB → Nginx HTTP en EC2 → Gunicorn loopback**.

- Listener ALB 80 redirige a HTTPS; listener 443 utiliza el certificado del dominio. X-Forwarded-For debe estar en modo append.
- Puerto 80 de EC2 accesible solo desde el security group del ALB. No exponer el puerto 8001; Gunicorn escucha exclusivamente en loopback.
- Sustituir `belliazzi.example.com` en la configuración Nginx y el entorno. Instalar `deploy/nginx.conf.example` dentro del contexto `http` y revisar los sitios predeterminados de la distribución.
- Nginx normaliza X-Forwarded-Proto, conserva el Host validable y agrega su peer a X-Forwarded-For. No confiar en esa cabecera si cualquier cliente puede saltarse el ALB y llegar a Nginx.
- Activar `TRUST_PROXY_HEADERS=True` solo después de verificar esa restricción. Gunicorn no infiere el esquema por su cuenta: Django controla el opt-in. Esto evita un comportamiento diferente entre el check de Django y el proceso WSGI.
- `TRUSTED_PROXY_CIDRS` para Axes debe incluir loopback y las redes concretas de los proxies/ALB. Nunca `0.0.0.0/0` o `::/0`. La IP se toma recorriendo desde la derecha hasta el primer salto no confiable.
- Nginx y Gunicorn omiten querystrings/cookies/cuerpos en sus logs de acceso. Los identificadores de recursos de la ruta siguen presentes: restringir acceso y definir retención de logs.
- `/healthz` en este ejemplo comprueba Nginx, no PostgreSQL ni Gunicorn. Antes de aceptar staging, completar una comprobación operativa de la aplicación y definir la política de readiness/monitorización. No interpretar ese 200 como salud integral.

En Linux, con las variables de producción ya inyectadas:

```sh
uv sync --frozen
uv run python manage.py check --deploy
uv run python manage.py collectstatic --noinput
uv run gunicorn --check-config --config deploy/gunicorn.conf.py config.wsgi:application
uv run gunicorn --config deploy/gunicorn.conf.py config.wsgi:application
nginx -t
```

Configurar un servicio no privilegiado con reinicio, directorio de trabajo y entorno adecuados. La distribución Linux debe incluir los requisitos de compilación/librerías de `mysqlclient`, que sigue declarado para no romper MySQL. La comprobación de Gunicorn en contenedor no reemplaza verificar `uv sync --frozen` y el servicio completo en la AMI elegida.

## Estáticos

`STATIC_ROOT=/srv/belliazzi/staticfiles` coincide con el alias del ejemplo Nginx. Dar permisos de lectura a Nginx y de escritura solo al proceso de preparación del release. No usar `runserver` para servir archivos en producción.

Los bundles Tailwind/Flowbite y Chart.js están incluidos en Git. No se necesita Node.js en producción. Solo se recompilan cuando se modifican sus fuentes/clases. Las correcciones de esta fase reutilizan clases existentes.

Para una verificación local sin escribir en las fuentes:

```powershell
$env:STATIC_ROOT = Join-Path $env:TEMP ('belliazzi-static-' + [guid]::NewGuid().ToString('N'))
uv run python manage.py collectstatic --noinput
```

En producción exigir HTTPS también para estáticos mediante la política del ALB; la validación sintáctica de Nginx no verifica listeners ni certificados AWS.

## HSTS y advertencias deliberadas

`SECURE_HSTS_SECONDS=0` es el valor inicial: queda **security.W004**. No se silencia. Después de validar dominio, HTTPS y redirecciones, se puede iniciar un valor corto como 300 segundos y aumentarlo con criterio operativo.

Con HSTS activo pero subdominios/preload desactivados aparecen **security.W005** y **security.W021**. No activar esos flags solo para eliminar advertencias: todos los subdominios deben cumplir HTTPS antes de incluirlos; preload requiere una decisión explícita y sus requisitos propios. No se configuró ni solicitó preload en esta fase.

## Migración de datos y límites

Las suites validan creación y evolución de esquema, fixtures, restricciones y operaciones sobre bases nuevas. No transfieren ni validan los datos reales de MySQL. Una futura transferencia necesita respaldo probado, conciliación de PK/FK, UUID, folios, NULL, pesos y fechas, secuencias PostgreSQL y plan de rollback. Revisar también diferencias de collation y sensibilidad a mayúsculas para datos existentes.

La paginación del historial sigue materializando relaciones y los reportes siguen generándose en memoria. No se realizaron pruebas de carga a escala de producción. La protección de doble clic conserva los controles existentes; no se introdujo idempotencia global ni se cambió la lógica de negocio.

## Referencias

- [Django 5.2 y PostgreSQL](https://docs.djangoproject.com/en/5.2/ref/databases/#postgresql-notes).
- [Confianza del encabezado de protocolo en Django](https://docs.djangoproject.com/en/5.2/ref/settings/#secure-proxy-ssl-header).
- [Configuración de Gunicorn](https://gunicorn.org/reference/settings/).
- [TLS en RDS PostgreSQL](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/PostgreSQL.Concepts.General.SSL.html).

Los resultados concretos de esta implementación se registran en `docs/resultados_fase2.md`.
