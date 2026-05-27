from django.apps import AppConfig
from django.core.signals import setting_changed


class TraceTConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'tracet'

    def ready(self):
        from . import permissions
        from . import signals