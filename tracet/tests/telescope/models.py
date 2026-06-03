from astropy.coordinates import SkyCoord

from django.db import models
from django.utils import timezone

from tracet.models.telescope import AbstractTelescope


class Telescope(AbstractTelescope):
    """Stub telescope for integration tests."""

    OBSERVATORY = "TestObservatory"
    CONFIGURATION = "TestConfig"

    ra_path = models.CharField()
    dec_path = models.CharField()
    reject = models.BooleanField(
        default=False,
        help_text="If True, make_request raises RejectionException.",
    )

    def __str__(self):
        return f"TestTelescope(trigger={self.trigger.id})"

    def get_pointings(self, event, createdbefore=None):
        try:
            ra = float(event.querylatest(self.ra_path, createdbefore))
            dec = float(event.querylatest(self.dec_path, createdbefore))
            return [SkyCoord(ra, dec, unit="deg")]
        except ValueError, TypeError:
            return []

    def prepare_request(self, observation):
        if len(observation.pointings) != 1:
            raise self.PreparationException()

    def make_request(self, observation):
        if self.reject:
            raise self.RejectionException()
        else:
            observation.finish = timezone.now() + timezone.timedelta(minutes=10)
