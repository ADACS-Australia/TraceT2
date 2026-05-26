import json

from astropy.coordinates import SkyCoord
from astropy.units import hourangle
import requests

from django.db import models

from tracet.fields import JXPathField
from tracet.models import AbstractTelescope, Observation


class ATCA(AbstractTelescope):
    OBSERVATORY = "ATCA"
    CONFIGURATION = ""
    SUMMARY_TEMPLATE = "atca/summary.html"

    projectid = models.CharField(max_length=500)
    http_username = models.CharField(max_length=500, verbose_name="HTTP Username")
    http_password = models.CharField(max_length=500, verbose_name="HTTP Password")
    email = models.EmailField(
        help_text="The email address that was supplied in the NAPA proposal."
    )
    authentication_token = models.CharField(max_length=500)
    ra_path = JXPathField(
        help_text="The (x|j)path to the Right Ascension. This value is set by the most recent matching notice.",
    )
    dec_path = JXPathField(
        help_text="The (x|j)path to the Declination. This value is set by the most recent matching notice.",
    )
    maximum_lag = models.FloatField(
        help_text="The maximum delay allowed for scheduling this observation. If the observation cannot be scheduled to start within this time, it will not be scheduled at all. [minute]"
    )
    minimum_exposure = models.IntegerField(
        help_text="The minimum exposure time required for this trigger. The trigger will be rejected if ATCA cannot schedule a total exposure of at least this time. [minute]"
    )
    maximum_exposure = models.IntegerField(
        help_text="The maximum exposure time required for this trigger. [minute]"
    )

    def __str__(self):
        return "ATCA Configuration"

    def get_pointings(self, event, createdbefore=None):
        try:
            ra = event.querylatest(self.ra_path, createdbefore)
            dec = event.querylatest(self.dec_path, createdbefore)
            return [SkyCoord(float(ra), float(dec), unit=("deg", "deg"))]
        except TypeError, ValueError:
            return []

    def prepare_request(self, observation: Observation):
        def minutes_to_hms(minutes: float) -> str:
            h = int(minutes // 60)
            minutes -= h * 60

            m = int(minutes)
            minutes -= m

            s = int(minutes * 60)

            return f"{h:02d}:{m:02d}:{s:02d}"

        if len(observation.pointings) != 1:
            self.log("An error occurred attempting to parse RA,Dec values:")
            raise AbstractTelescope.PreparationException()

        istest = not self.trigger.active

        params = dict(
            email=self.email,
            authenticationToken=self.authentication_token,
            maximumLag=self.maximum_lag / 60,  # [minute] -> [hour]
        )

        if istest:
            params |= dict(
                test=istest,
                emailOnly=self.email,  # Send all emails only to this email address (test mode only)
                noTimeLimit=True,  # Assume that we can request an over-ride observation of any length (test mode only)
                noScoreLimit=True,  # Assume that we can over-ride any observation (test mode only)
            )

        request = dict(
            source="gamma ray burst",  # IS THIS ARBITRARY??
            project=self.projectid,
            minExposureLength=minutes_to_hms(self.minimum_exposure),
            maxExposureLength=minutes_to_hms(self.maximum_exposure),
            rightAscension=observation.pointings[0].ra.to_string(
                unit=hourangle, sep=":"
            ),
            declination=observation.pointings[0].dec.to_string(sep=":"),
            scanType="Dwell",
        )

        for atcaband in self.atcabands.order_by("band"):
            request[atcaband.get_band_display()] = dict(
                use=True,
                exposureLength=atcaband.exposure,
                freq1=atcaband.freq1,
                freq2=atcaband.freq2,
            )

        # Request is passed as a JSON string
        params["request"] = json.dumps(request)

        self.api_params = params
        self.log("API params", json.dumps(self.api_params, indent=4))

    def check_override(self, current_observation, proposed_observation):
        # ATCA observations cannot at present be cancelled and/or repointed
        self.log(
            "ATCA repointing refused",
            "The ATCA telescope is currently not configured to handle repointings.",
        )
        raise AbstractTelescope.OverrideException()

    def make_request(self, observation: Observation):
        try:
            response = requests.post(
                "https://www.narrabri.atnf.csiro.au/cgi-bin/obstools/rapid_response/rapid_response_service.py",
                self.api_params,
            )
            response.raise_for_status()
        except requests.RequestException as e:
            self.log("An error occurred making the HTTP request to the ATCA API", e)
            raise AbstractTelescope.RequestException() from e

        self.log("Raw API response", response.text)

        # TODO: parse the response and detect success or failure
        raise AbstractTelescope.RejectionException()


class ATCABand(models.Model):
    class Bands(models.IntegerChoices):
        L3mm = 3, "3mm"
        L7mm = 7, "6mm"
        L15mm = 15, "15mm"
        L4cm = 40, "4cm"
        L16cm = 160, "16cm"

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["atca", "band"], name="unique wavelength configuration"
            )
        ]

    atca = models.ForeignKey(ATCA, related_name="atcabands", on_delete=models.CASCADE)
    band = models.IntegerField(choices=Bands)
    exposure = models.IntegerField(
        help_text="The exposure time of this reciever. Receivers will be continuously cycled up until the full scheduled slot is exhausted. [minute]"
    )
    freq1 = models.IntegerField(
        verbose_name="Frequency 1",
        help_text="Specify the central frequency for the first of the 2 GHz bands at which this receiver will observe. Note: the 16 cm reciever can only observe at 2100 MHz. [MHz]",
    )
    freq2 = models.IntegerField(
        verbose_name="Frequency 2",
        help_text="Specify the central frequency for the second of the 2 GHz bands at which this receiver will observe. Note: the 16 cm reciever can only observe at 2100 MHz. [MHz]",
    )
