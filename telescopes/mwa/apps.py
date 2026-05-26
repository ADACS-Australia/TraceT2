from django.apps import AppConfig


class MwaConfig(AppConfig):
    name = 'telescopes.mwa'

    def ready(self):
        from telescopes.mwa.forms import MWACorrelatorFormset, MWAGWFormset, MWAVCSFormset
        from telescopes.mwa.models import MWACorrelator, MWAGW, MWAVCS
        from tracet.telescope_registry import telescope_registry

        telescope_registry.register("mwacorrelator", "MWA Correlator", MWACorrelator, MWACorrelatorFormset)
        telescope_registry.register("mwagw", "MWA Gravitational Wave", MWAGW, MWAGWFormset)
        telescope_registry.register("mwavcs", "MWA VCS", MWAVCS, MWAVCSFormset)
