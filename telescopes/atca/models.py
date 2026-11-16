import datetime
import json
import logging

from astropy.coordinates import SkyCoord
from astropy.units import hourangle
from astropy.time import Time
import requests

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.safestring import mark_safe

from tracet.fields import EncryptedTextField, JXPathField
from tracet.models import AbstractTelescope, Observation
from telescopes.atca.frequencyconfiguration import FrequencyConfigurationField


logger = logging.getLogger(__name__)


class ATCA(AbstractTelescope):
    OBSERVATORY = "ATCA"
    CONFIGURATION = ""
    SUMMARY_TEMPLATE = "atca/summary.html"

    project_code = models.CharField(max_length=500)
    authentication_token = EncryptedTextField(null=True)
    ra_path = JXPathField(
        help_text="The (x|j)path to the Right Ascension. This value is set by the most recent matching notice.",
    )
    dec_path = JXPathField(
        help_text="The (x|j)path to the Declination. This value is set by the most recent matching notice.",
    )
    frequency_configuration = FrequencyConfigurationField(
        help_text=mark_safe(
            "The observation frequency configuration provided: central frequency [MHz] "
            "and 4 channel offsets. e.g. '8128 MHz, 160, 160, 480, 480'. More information is available "
            "<a href='https://www.narrabri.atnf.csiro.au/observing/bigcat/bigcat_rf.html'>here</a>."
        ),
    )
    maximum_lag = models.FloatField(
        help_text="The maximum delay allowed for scheduling this observation. If the observation cannot be scheduled to start within this time, it will not be scheduled at all. [minute]",
        validators=[MinValueValidator(0)],
    )
    minimum_integration = models.FloatField(
        help_text="The minimum exposure time required for this trigger. The trigger will be rejected if ATCA cannot schedule a total exposure of at least this time. [minute]",
        validators=[MinValueValidator(0)],
    )
    maximum_integration = models.FloatField(
        help_text="The maximum exposure time required for this trigger. [minute]",
        validators=[MinValueValidator(0)],
    )
    minimum_elevation = models.FloatField(
        help_text="Ensure scheduled observations have a local elevation angle no smaller than this value. [degree]"
    )
    calibration_integration = models.FloatField(
        help_text="The integration time for each calibrator visit (does not apply to flux density calibrators). [minute]",
        validators=[MinValueValidator(0)],
    )
    calibration_gap = models.FloatField(
        help_text="The maximum time spent on-source between calibrator visits. [minute]",
        validators=[MinValueValidator(0)],
    )

    def __str__(self):
        return "ATCA Configuration"

    def clean(self):
        super().clean()

        # Lodge a dummy test request and report any errors back
        self.api_params = self.make_params(
            "Test", SkyCoord(0, 0, unit=("deg", "deg")), True
        )

        try:
            self.make_request(None)  # TODO Remove Observation from make_request
        except AbstractTelescope.RejectionException as e:
            raise ValidationError("The ATCA API returned an error: " + str(e), "atca")
        except Exception:
            # If the request fails for other reasons (e.g. the API is down)
            # silently ignore the failure.
            logger.warning(
                "The ATCA API could not be reached to perform telescope configuration validation"
            )

    def get_pointings(self, event, createdbefore=None):
        try:
            ra = event.querylatest(self.ra_path, createdbefore)
            dec = event.querylatest(self.dec_path, createdbefore)
            return [SkyCoord(float(ra), float(dec), unit=("deg", "deg"))]
        except TypeError, ValueError:
            return []

    def make_params(self, name: str, radec: SkyCoord, istest: bool) -> dict:
        return {
            "projectCode": self.project_code,
            "authToken": self.authentication_token,
            "requestType": "request_observations",
            "test": istest,
            "validTo": (Time.now() + datetime.timedelta(self.maximum_lag)).mjd,
            "targetList": [
                {
                    "name": name,
                    "ra": radec.ra.to_string(unit=hourangle, sep=":"),
                    "dec": radec.dec.to_string(sep=":"),
                    "correlator_setting": "Continuum",
                    "frequency_configuration": {
                        "centreFreq": self.frequency_configuration.freq,
                        "offset_setting": self.frequency_configuration.offsets,
                        "band_setting": "midlow",  # TODO: Temp fix just to get the API working
                    },
                    "integration_time_min": self.maximum_integration,
                    "minimum_integration_time_min": self.minimum_integration,
                    "min_elevation_deg": self.minimum_elevation,
                    "calibrator_integration_min": self.calibration_integration,
                    "max_calibrator_integration_gap_min": self.calibration_gap,
                }
            ],
        }

    def prepare_request(self, observation: Observation):
        if len(observation.pointings) != 1:
            self.log("An error occurred attempting to parse RA,Dec values:")
            raise AbstractTelescope.PreparationException()

        istest = not self.trigger.active

        self.api_params = self.make_params(
            str(observation.decision.event),
            observation.pointings[0],
            istest,
        )
        self.log(
            "API params",
            json.dumps(self.api_params | {"authToken": "HIDDEN"}, indent=4),
        )

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
                "https://www.narrabri.atnf.csiro.au/cgi-bin/obstools/rapid_response/rapidresponse_web_service.py",
                json=self.api_params,
            )
            response.raise_for_status()

            response = json.loads(response.text)
            self.log("Pretty API Response", json.dumps(response, indent=4))
        except requests.RequestException as e:
            status_code = (
                e.response.status_code if e.response is not None else "unknown"
            )
            self.log(
                f"An error occurred making the HTTP request to the ATCA API (status code: {status_code})"
            )
            raise AbstractTelescope.RequestException() from e
        except json.JSONDecodeError as e:
            self.log("Raw API response", response.text)
            self.log("The ATCA API returned invalid JSON", e)
            raise AbstractTelescope.RequestException()

        if isinstance(response, dict) and response.get("accepted", False):
            # TODO: Save the request ID
            pass
        else:
            raise AbstractTelescope.RejectionException(
                response.get("error", "No reason given")
            )
