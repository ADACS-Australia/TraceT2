import datetime
import logging
import traceback

from astropy.coordinates import SkyCoord

from django.db import models
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone


logger = logging.getLogger(__name__)


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
    observatory = models.CharField(max_length=500)
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


class AbstractTelescope(models.Model):
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

    OBSERVATORY = ""
    CONFIGURATION = ""
    SUMMARY_TEMPLATE = ""

    trigger = models.OneToOneField(
        "tracet.Trigger", related_name="%(class)s", on_delete=models.CASCADE
    )

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
        except AbstractTelescope.PreparationException:
            observation.status = Observation.Status.DATA_FAILURE
        except AbstractTelescope.OverrideException:
            observation.status = Observation.Status.CLASH
        except AbstractTelescope.RequestException:
            observation.status = Observation.Status.REQUEST_FAILURE
        except AbstractTelescope.RejectionException:
            observation.status = Observation.Status.API_FAILURE
        except Exception as e:
            self.log(
                "An unknown exception was thrown during AbstractTelescope.create_observation()",
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
            raise AbstractTelescope.OverrideException()

    def summary(self) -> str:
        return render_to_string(self.SUMMARY_TEMPLATE, {"telescope": self})