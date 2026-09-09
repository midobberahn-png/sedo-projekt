from django.apps import AppConfig

class SedoConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'sedo'

    def ready(self):
        from . import signals  # noqa: F401
