from django.apps import AppConfig


class AtcaConfig(AppConfig):
    name = "telescopes.atca"

    def ready(self):
        from telescopes.atca.forms import ATCAFormset
        from tracet.models import ATCA
        from tracet.telescope_registry import telescope_registry

        telescope_registry.register("atca", "ATCA", ATCA, ATCAFormset)
