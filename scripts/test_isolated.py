"""Ejecuta la suite en una base local de nombre único; nunca reutiliza una base.

uv run python scripts/test_isolated.py --backend mysql
uv run python scripts/test_isolated.py --backend postgresql --concurrency-only
Credenciales mediante DB_*; no recibe contraseñas como argumentos.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=['mysql', 'postgresql'], required=True)
    parser.add_argument('--concurrency-only', action='store_true')
    args = parser.parse_args()
    os.environ.update(DJANGO_SETTINGS_MODULE='config.settings', DB_ENGINE=args.backend)
    import django
    django.setup()
    from django.conf import settings
    cfg = settings.DATABASES['default']
    if cfg['HOST'] not in ('localhost', '127.0.0.1', '::1'):
        parser.error('Este runner solo admite bases locales aisladas. No utiliza RDS ni hosts remotos.')
    base = 'belliazzi_test_' + uuid.uuid4().hex[:12]
    test_name = 'test_' + base
    if args.backend == 'mysql':
        import MySQLdb
        connection = MySQLdb.connect(host=cfg['HOST'], port=int(cfg['PORT']), user=cfg['USER'], passwd=cfg['PASSWORD'])
        query = 'SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME IN (%s,%s)'
    else:
        import psycopg
        connection = psycopg.connect(host=cfg['HOST'], port=int(cfg['PORT']), user=cfg['USER'], password=cfg['PASSWORD'], dbname='postgres', **cfg['OPTIONS'])
        query = 'SELECT datname FROM pg_database WHERE datname IN (%s,%s)'
    with connection.cursor() as cursor:
        cursor.execute(query, [base, test_name])
        if cursor.fetchone():
            connection.close()
            raise RuntimeError('El nombre de base ya existe; se rechaza cualquier reutilización o borrado.')
    connection.close()
    output = Path(tempfile.mkdtemp(prefix=f'belliazzi-{args.backend}-tests-'))
    env = os.environ | {'DB_NAME': base, 'AXES_ENABLED': 'True', 'PYTHONUTF8': '1',
                        'BELLIAZZI_TEST_RESULTS': str(output/'results.json')}
    if args.concurrency_only:
        # Solo descubre métodos; no ejecuta fixtures ni consultas en la base original.
        from django.test.runner import DiscoverRunner
        from django.test.utils import iter_test_cases
        suite = DiscoverRunner(verbosity=0).build_suite(['apps.usuarios', 'apps.trazabilidad', 'apps.inventario', 'apps.reportes'])
        labels = [test.id() for test in iter_test_cases(suite) if 'Concurrencia' in type(test).__name__]
        if not labels:
            raise RuntimeError('No se descubrieron pruebas de concurrencia.')
    else:
        labels = ['apps.usuarios', 'apps.trazabilidad', 'apps.inventario', 'apps.reportes']
    print(f'Motor: {args.backend}; base temporal: {test_name}; evidencia: {output}', flush=True)
    command = [sys.executable, 'manage.py', 'test', *labels, '--verbosity', '2', '--noinput', '--testrunner', 'scripts.test_runner.IsolatedTestRunner']
    with (output/'tests.log').open('w', encoding='utf8') as log:
        result = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    print(f'Resultado: {result.returncode}; registro: {output / "tests.log"}', flush=True)
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
