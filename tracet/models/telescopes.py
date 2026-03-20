from base64 import b64decode
import datetime
import hashlib
from io import BytesIO
import json
import logging
import traceback

from astropy.coordinates import AltAz, Angle, EarthLocation, ICRS, SkyCoord
from astropy.io import fits
from astropy.table import Table
import astropy.time
from astropy.units import hourangle
import astropy_healpix as ah
import numpy as np
import requests

from django.core.cache import cache
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.safestring import mark_safe

from tracet.models.fields import JXPathField


logger = logging.getLogger(__name__)


class Observatory(models.TextChoices):
    ATCA = "atca", "ATCA"
    MWA = "mwa", "MWA"


class Observation(models.Model):
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
    observatory = models.CharField(choices=Observatory, max_length=500)
    configuration = models.CharField(blank=True, max_length=500)
    _pointings = models.JSONField(default=list)
    priority = models.IntegerField()
    status = models.CharField(choices=Status)
    istest = models.BooleanField()
    log = models.TextField()

    def __bool__(self):
        return self.success and not self.istest

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


class Telescope(models.Model):
    class Meta:
        abstract = True

    class PreparationException(BaseException):
        pass

    class OverrideException(BaseException):
        pass

    class RequestException(BaseException):
        pass

    class RejectionException(BaseException):
        pass

    def __init__(self, *args, **kwargs):
        self._logs = []
        return super().__init__(*args, **kwargs)

    def log(self, title: str, message: str | BaseException | None = None):
        timestamp = datetime.datetime.now(datetime.UTC).isoformat()
        self._logs.append("\n" + timestamp + ": " + title + "\n")

        if issubclass(type(message), BaseException):
            self._logs.extend(
                ["> " + line.strip() for line in traceback.format_exception(message)]
            )
        elif message is not None:
            self._logs.extend(["> " + line for line in str(message).splitlines()])

    def get_log(self) -> str:
        return "\n".join(self._logs).strip()

    def create_observation(self, decision):
        observation = Observation(
            decision=decision,
            observatory=self.OBSERVATORY,
            configuration=self.CONFIGURATION,
            priority=decision.event.trigger.priority,
            istest=(not self.trigger.active),
            log="",
        )

        try:
            observation.pointings = self.get_pointings(decision.event)
            self.prepare_request(observation)

            current_observation = (
                Observation.objects.filter(
                    status=Observation.Status.API_OK,
                    observatory=self.OBSERVATORY,
                    finish__gte=timezone.now(),
                    istest=observation.istest,
                )
                .order_by("-finish")
                .first()
            )

            if current_observation is not None:
                self.check_override(current_observation, observation)

            self.make_request(observation)
            observation.status = Observation.Status.API_OK
        except Telescope.PreparationException:
            observation.status = Observation.Status.DATA_FAILURE
        except Telescope.OverrideException:
            observation.status = Observation.Status.CLASH
        except Telescope.RequestException:
            observation.status = Observation.Status.REQUEST_FAILURE
        except Telescope.RejectionException:
            observation.status = Observation.Status.API_FAILURE
        except Exception as e:
            self.log(
                "An unknown exception was thrown during Telescope.create_observation()",
                e,
            )

            observation.status = Observation.Status.UNKNOWN_FAILURE

        observation.log = self.get_log()
        return observation.save()

    def get_pointings(self, event: "Event") -> list[SkyCoord]:
        raise NotImplementedError

    def prepare_request(self, observation: Observation):
        raise NotImplementedError()

    def make_request(self, observation: Observation):
        raise NotImplementedError()

    def repoint(
        self, current_observation: Observation, proposed_observation: Observation
    ) -> bool:
        raise NotImplementedError()

    def check_override(
        self, current_observation: Observation, proposed_observation: Observation
    ):
        # Default implementation
        if proposed_observation.priority > current_observation.priority:
            self.log(
                "Preexisting observation",
                f"Preexisting observation (id={current_observation.id}) in effect with "
                f"priority {current_observation.priority} (versus our priority: {proposed_observation.priority})",
            )
            raise Telescope.OverrideException()


class MWABase(Telescope):
    class Meta:
        abstract = True

    class TileSet(models.TextChoices):
        PHASE_ONE = "phase_one", "Phase 1"
        P1_HEXES = "p1+hexes", "Phase 1 + Hexes"
        P1_SOLAR = "p1+solar", "Phase 1 + Solar"
        P2_COMPACT = "p2_compact", "Phase 2 Compact"
        P2_EXTENDED = "p2_extended", "Phase 2 Extended"
        T256 = "256T", "256 tiles"

    OBSERVATORY = "MWA"

    trigger = models.OneToOneField(
        "Trigger", related_name="%(class)s", on_delete=models.CASCADE
    )

    projectid = models.CharField(max_length=500)
    secure_key = models.CharField(max_length=500)
    repointing_threshold = models.FloatField(
        help_text=(
            "In the case that an observation has already been requested, request a new observation "
            "only if the updated pointing coordinates differ by more than this threshold. [degree]"
        )
    )
    tileset = models.CharField(
        choices=TileSet,
        help_text="Select the set of tiles to use for this observation. More tiles gives better sensitivity but at the expense of larger data requirements.",
    )
    frequency = models.CharField(
        max_length=500,
        help_text=mark_safe(
            "A space separated list of MWA coarse channel specifications. The specification format "
            "is documented <a href='https://mwatelescope.atlassian.net/wiki/spaces/MP/pages/24972656/Triggering+web+services#Channel-selection-specifier-strings'>here.</a> "
            "For example: '145,24' will observe with 24 channels centered at channel 145; a space "
            "separated list like '121:24 145:160;165:170' will schedule two separate observations."
        ),
    )
    frequency_resolution = models.FloatField(
        default=10, help_text="Correlator frequency resolution. [kHz]"
    )
    time_resolution = models.FloatField(
        default=0.5, help_text="Correlator integration time. [second]"
    )
    exposure = models.FloatField(
        default=120,
        help_text="The duration of each observation (for each frequency range). [second]",
    )  # TODO: validate as modulo 8 second
    nobs = models.IntegerField(
        default=15,
        help_text="The total number of observations. The total time will equal: (nobs) * (number of frequency ranges) * (exposure time)",
    )

    def check_override(self, current_observation, proposed_observation):
        super().check_override(current_observation, proposed_observation)

        # Default implementation for all MWA telescopes
        # Return true if any of the proposed pointings is > repointing_threshold from
        # an existing pointing.
        if current_observation.priority == proposed_observation.priority:
            maxsep = max(
                min(c0.separation(c1) for c1 in current_observation.pointings).deg
                for c0 in proposed_observation.pointings
            )

            if maxsep < self.repointing_threshold:
                self.log(
                    "Repointing threshold not met",
                    f"The proposed pointing is at most {maxsep} degrees apart from the current pointing, "
                    f"and this does not exceed the repointing threshold ({self.repointing_threshold}).",
                )
                raise Telescope.OverrideException()
            else:
                self.log(
                    "Repointing threshold exceeded",
                    f"The proposed pointing is at most {maxsep} degrees apart from the current pointing, "
                    f"and this exceed the repointing threshold ({self.repointing_threshold}).",
                )


class MWACorrelator(MWABase):
    CONFIGURATION = "Correlator"

    ra_path = JXPathField(
        help_text="The (x|j)path to the Right Ascension. This value is set by the most recent matching notice.",
    )
    dec_path = JXPathField(
        help_text="The (x|j)path to the Declination. This value is set by the most recent matching notice.",
    )

    def __str__(self):
        return "MWA Correlator Configuration"

    def get_pointings(self, event, createdbefore=None):
        try:
            ra = event.querylatest(self.ra_path, createdbefore)
            dec = event.querylatest(self.dec_path, createdbefore)
            return [SkyCoord(float(ra), float(dec), unit=("deg", "deg"))]
        except TypeError, ValueError:
            return []

    def prepare_request(self, observation: Observation):
        if len(observation.pointings) != 1:
            self.log("An error occurred attempting to parse RA,Dec values:")
            raise Telescope.PreparationException()

        self.api_params = dict(
            project_id=self.projectid,
            secure_key=self.secure_key,
            calibrator=True,  # Hard-coded to always make a calibrator observation.
            ra=observation.pointings[0].ra.deg,
            dec=observation.pointings[0].dec.deg,
            avoidsun=True,  # Hard-coded to always place sun in null.
            freqspecs=json.dumps(self.frequency.split()),
            tileset=self.tileset,
            pretend=(not self.trigger.active),
        )
        self.log("API params", json.dumps(self.api_params, indent=4))

    def make_request(self, observation: Observation):
        try:
            response = requests.get(
                "http://mro.mwa128t.org/trigger/triggerobs", params=self.api_params
            )
            response.raise_for_status()

            response = json.loads(response.text)
            self.log("Pretty API response", json.dumps(response, indent=4))
        except requests.RequestException as e:
            self.log("An error occurred making the HTTP request to the MWA API", e)
            raise Telescope.RequestException() from e
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            self.log("Raw API response", response.text)
            self.log("The MWA API returned invalid JSON", e)

        if response.get("success", False):
            observation.finish = datetime.datetime.now(
                datetime.UTC
            ) + datetime.timedelta(
                seconds=self.nobs * len(self.frequency.split()) * self.exposure
                + 120  # 120 is the default calibration time
            )
        else:
            raise Telescope.RejectionException()


class MWAVCS(MWABase):
    CONFIGURATION = "VCS"

    ra_path = JXPathField(
        help_text="The (x|j)path to the Right Ascension. This value is set by the most recent matching notice.",
    )
    dec_path = JXPathField(
        help_text="The (x|j)path to the Declination. This value is set by the most recent matching notice.",
    )

    def __str__(self):
        return "MWA VCS Configuration"

    def get_pointings(self, event, createdbefore=None):
        try:
            ra = event.querylatest(self.ra_path, createdbefore)
            dec = event.querylatest(self.dec_path, createdbefore)
            return [SkyCoord(float(ra), float(dec), unit=("deg", "deg"))]
        except TypeError, ValueError:
            return []

    def prepare_request(self, observation: Observation):
        if len(observation.pointings) != 1:
            self.log("An error occurred attempting to parse RA,Dec values:")
            raise Telescope.PreparationException()

        self.api_params = dict(
            project_id=self.projectid,
            secure_key=self.secure_key,
            calibrator=True,  # Hard-coded to always make a calibrator observation.
            ra=observation.pointings[0].ra.deg,
            dec=observation.pointings[0].dec.deg,
            avoidsun=True,  # Hard-coded to always place sun in null.
            freqspecs=json.dumps(self.frequency.split()),
            tileset=self.tileset,
            pretend=(not self.trigger.active),
        )
        self.log("API params", json.dumps(self.api_params, indent=4))

    def make_request(self, observation: Observation):
        try:
            response = requests.get(
                "http://mro.mwa128t.org/trigger/triggervcs", params=self.api_params
            )
            response.raise_for_status()

            response = json.loads(response.text)
            self.log("Pretty API response", json.dumps(response, indent=4))
        except requests.RequestException as e:
            self.log("An error occurred making the HTTP request to the MWA API", e)
            raise Telescope.RequestException() from e
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            self.log("Raw API response", response.text)
            self.log("The MWA API returned invalid JSON", e)

        if response.get("success", False):
            observation.finish = datetime.datetime.now(
                datetime.UTC
            ) + datetime.timedelta(
                seconds=self.nobs * len(self.frequency.split()) * self.exposure
                + 120  # 120 is the default calibration time
            )
        else:
            raise Telescope.RejectionException()


class MWAGW(MWABase):
    CONFIGURATION = "GW"

    class SweetSpots:
        MWA = EarthLocation.from_geodetic(
            lat="-26:42:11.95", lon="116:40:14.93", height=377.8
        )

        def __init__(self, time):
            with open(settings.MWA_SWEET_SPOTS_PATH) as f:
                # SWEET SPOTS file has two line of header (which we skip)
                # and then 4 "|"-delineated columns:
                # ID | Azimuth [deg] | Eelevation [deg] | Delays
                # We are only interested in the direction of the sweet spots.
                try:
                    lines = f.readlines()[2:]
                    azs = [
                        Angle(float(line.split("|")[1]), unit="deg") for line in lines
                    ]
                    els = [
                        Angle(float(line.split("|")[2]), unit="deg") for line in lines
                    ]
                except Exception as e:
                    raise Exception(
                        "An error occurred reading or parsing the MWA sweet spots database"
                    ) from e

                self.sweetspots = AltAz(
                    az=azs, alt=els, location=self.MWA, obstime=time
                )

        def get_nearest(self, coord: SkyCoord) -> AltAz:
            return self.sweetspots[np.argmin(self.sweetspots.separation(coord))]

    # MWAGW uses a fixed set of 4 sub arrays and will ignore any tileset setting
    tileset = None

    skymap_path = JXPathField(
        help_text="The (x|j)path to either: a URL to a FITS image; or a FITS image embedded directly in the notice using a Base64 encoding. This value is set by the most recent matching notice.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Default to dumping the buffer.
        # This will be set to False if we are overriding a previous MWA GW observation
        self.dumpbuffer = True

    def __str__(self):
        return "MWA GW Configuration"

    def get_pointings(self, event, createdbefore=None):
        skymap = event.querylatest(self.skymap_path, createdbefore)
        if not skymap:
            self.log("No skymap was found at the specified path.")
            return []

        cachekey = f"MWAGW-pointings-{hashlib.sha256(skymap.encode()).hexdigest()}"
        if (pointings := cache.get(cachekey)) is not None:
            return pointings

        # Skymap can be either a Base64 encoding of a fits image or a URL to a fits image
        try:
            url, index = skymap.split(",")
            URLValidator()(url)
            self.log(
                "Skymap looks like a URL; attempting to download",
                f"skymap={url} index={index}",
            )
            skymap = fits.open(BytesIO(requests.get(url).content))[int(index)].data
        except ValueError, ValidationError:
            self.log(
                "Skymap isn't a URL; assuming it is an embedded FITS object encoded as Base64",
                f"skymap={skymap}",
            )
            try:
                skymap = Table.read(BytesIO(b64decode(skymap)))
            except Exception as e:
                logger.warning(f"Unable to parse skymap: skymap={skymap[:250]}")
                self.log("An error occurred attempting to read the skymap", e)
                return cache.get_or_set(cachekey, [], timeout=None)

        try:
            # Calculate 4 pointings that:
            # - are MWA sweetspots
            # - are chosen greedily in order of the skymap's probability density
            # - are separated by at least (minsep) degrees

            # First, list the SkyCoord values of the skymap _in order_ of probability
            # density (highest to lowest)
            uniqs = skymap[np.flip(np.argsort(skymap["PROBDENSITY"]))]["UNIQ"]
            levels, ipixs = ah.uniq_to_level_ipix(uniqs)
            ras, decs = ah.healpix_to_lonlat(
                ipixs, ah.level_to_nside(levels), order="nested"
            )
            coords = SkyCoord(ras, decs)
            self.log("Ordered SkyMap coordinates", coords)

            # Then: iterate through this list and add a new sweetspot pointing so long as it
            # is separated from any existing pointings by at least (minsep). Stop when we have 4.
            sweetspots = self.SweetSpots(astropy.time.Time.now())
            pointings: list[AltAz] = []
            for coord in coords:
                sweetspot = sweetspots.get_nearest(coord)
                separations = [sweetspot.separation(c) for c in pointings]

                if min(separations, default=Angle(180, unit="deg")) > Angle(
                    10, unit="deg"
                ):
                    pointings.append(sweetspot)

                if len(pointings) >= 4:
                    break
        except Exception as e:
            logger.warning(
                f"Unable to generate 4 sweetspot pointings: skymap={skymap[:250]}"
            )
            self.log(
                "An error occurred attempting to generate 4 sweetspot pointings",
                e,
            )
            cache.set(cachekey, [])
            return cache.get_or_set(cachekey, [], timeout=None)

        self.log(
            "Determined 4 MWA sweetspots to observe",
            ", ".join(f"(Alt={p.alt.deg}° Az={p.az.deg}°)" for p in pointings),
        )

        # Convert from local to sky coordinates
        pointings = [p.transform_to(ICRS()) for p in pointings]
        return cache.get_or_set(cachekey, pointings, timeout=None)

    def prepare_request(self, observation: Observation):
        if len(observation.pointings) != 4:
            raise Telescope.PreparationException()

        self.api_params = dict(
            project_id=self.projectid,
            secure_key=self.secure_key,
            calibrator=True,  # Hard-coded to always make a calibrator observation.
            ra=[p.ra.deg for p in observation.pointings],
            dec=[p.dec.deg for p in observation.pointings],
            avoidsun=True,  # Hard-coded to always place sun in null.
            freqspecs=json.dumps(self.frequency.split()),
            subarrays=["all_ne", "all_nw", "all_se", "all_sw"],
            pretend=(not self.trigger.active),
        )
        self.log("VCS API params", json.dumps(self.api_params, indent=4))

        self.bufferdump_params = dict(
            project_id=self.projectid,
            secure_key=self.secure_key,
            pretend=(not self.trigger.active),
            start_time=0,  # 0 implies earliest available data
            obstime=10,  # we will immediately trigger a VCS mode
        )
        self.log("Bufferdump API params", json.dumps(self.bufferdump_params, indent=4))

    def check_override(self, current_observation, proposed_observation):
        super().check_override(current_observation, proposed_observation)

        if current_observation.configuration == self.CONFIGURATION:
            self.log(
                "Setting dumpbuffer=False",
                "No buffer dump will be attempted due to pre-existing observation",
            )
            self.dumpbuffer = False

    def make_request(self, observation: Observation):
        if self.dumpbuffer:
            try:
                response = requests.get(
                    "http://mro.mwa128t.org/trigger/triggerbuffer",
                    params=self.bufferdump_params,
                )
                response.raise_for_status()

                response = json.loads(response.text)
                self.log("Buffer dump API response", json.dumps(response, indent=4))
            except requests.RequestException as e:
                self.log("An error occurred making the HTTP request to the MWA API", e)
                # Don't throw RequestException: still try to schedule VCS observation
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                self.log("Raw API response", response.text)
                self.log("The MWA API returned invalid JSON", e)

        try:
            response = requests.get(
                "http://mro.mwa128t.org/trigger/triggervcs", params=self.api_params
            )
            response.raise_for_status()

            response = json.loads(response.text)
            self.log("Pretty API response", json.dumps(response, indent=4))
        except requests.RequestException as e:
            self.log("An error occurred making the HTTP request to the MWA API", e)
            raise Telescope.RequestException() from e
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            self.log("Raw API response", response.text)
            self.log("The MWA API returned invalid JSON", e)

        if response.get("success", False):
            observation.finish = datetime.datetime.now(
                datetime.UTC
            ) + datetime.timedelta(
                seconds=self.nobs * len(self.frequency.split()) * self.exposure
                + 120  # 120 is the default calibration time
            )
        else:
            raise Telescope.RejectionException()


class ATCA(Telescope):
    OBSERVATORY = "ATCA"
    CONFIGURATION = ""

    trigger = models.OneToOneField(
        "Trigger", related_name="%(class)s", on_delete=models.CASCADE
    )
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
            raise Telescope.PreparationException()

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
        raise Telescope.OverrideException()

    def make_request(self, observation: Observation):
        try:
            response = requests.post(
                "https://www.narrabri.atnf.csiro.au/cgi-bin/obstools/rapid_response/rapid_response_service.py",
                self.api_params,
            )
            response.raise_for_status()
        except requests.RequestException as e:
            self.log("An error occurred making the HTTP request to the ATCA API", e)
            raise Telescope.RequestException() from e

        self.log("Raw API response", response.text)

        # TODO: parse the response and detect success or failure
        raise Telescope.RejectionException()


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
