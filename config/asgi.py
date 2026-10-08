import os
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
application = get_asgi_application()

from sedo.services.credentials import migrate_initialized_credentials
from concurrent.futures import ThreadPoolExecutor
from django.db import connections


def _prepare_credentials():
    try:
        migrate_initialized_credentials()
    finally:
        connections.close_all()


# ASGI servers may import this module from a running event loop. Keep the
# synchronous startup gate off that loop, and do not serve until it succeeds.
with ThreadPoolExecutor(max_workers=1, thread_name_prefix='sedo-credential-start') as startup:
    startup.submit(_prepare_credentials).result()
