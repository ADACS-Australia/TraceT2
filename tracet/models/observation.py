from astropy.coordinates import SkyCoord

from django.db import models
from django.urls import reverse
from django.utils import timezone


class Observation(models.Model):
    """
    The record of an (attempted) observation request sent to a telescope.

    Created by the telescope's ``create_observation`` method when a Decision
    passes. The ``status`` tracks the outcome: API_OK means the observatory
    accepted the request; other statuses indicate failures or clashes.
    Observations are never deleted — they persist even when a Trigger's
    criteria change and an Event is disabled.
    """

    class Status(models.TextChoices):
        API_OK = "api_ok", "OK"
        API_FAILURE = "api_failure", "Failure"
        CLASH = "clash", "Clashing observation"
        REQUEST_FAILURE = "request_failure", "Could not make API request"
        DATA_FAILURE = "data_failure", "Unable to prepare request"
        UNKNOWN_FAILURE = "unknown_failure", "An unexpected failure occurred"

    decision = models.ForeignKey(
        "Decision",
        null=True,
        related_name="observations",
        on_delete=models.CASCADE,
    )
    created = models.DateTimeField(default=timezone.now)
    finish = models.DateTimeField(null=True)
    observatory = models.CharField(max_length=500)
    configuration = models.CharField(blank=True, max_length=500)
    _pointings = models.JSONField(default=list)
    priority = models.IntegerField()
    status = models.CharField(choices=Status)
    istest = models.BooleanField()
    log = models.TextField()

    def __bool__(self):
        return self.status == Observation.Status.API_OK and not self.istest

    def get_absolute_url(self):
        return reverse("observationview", args=[self.id])

    def get_istest_display(self):
        return "Test" if self.istest else "Active"

    def in_progress(self):
        if self.status == Observation.Status.API_OK and self.created and self.finish:
            return self.created <= timezone.now() <= self.finish
        else:
            return False

    @property
    def pointings(self):
        return [SkyCoord(c[0], c[1], unit=("deg", "deg")) for c in self._pointings]

    @pointings.setter
    def pointings(self, coords):
        self._pointings = [(c.ra.deg, c.dec.deg) for c in coords]
