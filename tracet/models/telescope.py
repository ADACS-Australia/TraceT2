import datetime
import logging
import traceback

from astropy.coordinates import SkyCoord

from django.db import models
from django.template.loader import render_to_string
from django.utils import timezone

import tracet.models
from tracet.models.observation import Observation


logger = logging.getLogger(__name__)


class AbstractTelescope(models.Model):
    """
    Base class for telescope integrations.

    Concrete subclasses (ATCA, MWACorrelator, etc.) are linked to a Trigger
    via a one-to-one relationship. When a Decision passes, the telescope's
    ``create_observation`` method runs: it extracts pointings from the Event,
    prepares the API request, checks for clashing observations, and fires
    the request. The ``_logs`` accumulator captures what happened at each
    step and is written to the Observation record on success or failure.
    """

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

    def create_observation(self, decision) -> Observation:
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
                    istest=observation.istest,  # Test and non-test observations do not see each other
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
        observation.save()
        return observation

    def get_pointings(self, event: tracet.models.Event) -> list[SkyCoord]:
        raise NotImplementedError

    def prepare_request(self, observation: Observation) -> None:
        raise NotImplementedError()

    def make_request(self, observation: Observation) -> None:
        raise NotImplementedError()

    def check_override(
        self, current_observation: Observation, proposed_observation: Observation
    ) -> None:
        # Lower numeric priority = higher priority. Only strictly higher-priority
        # observations (lower number) may override.
        if proposed_observation.priority >= current_observation.priority:
            self.log(
                "Preexisting observation blocks override",
                f"Current observation (id={current_observation.id}) has priority "
                f"{current_observation.priority} <= proposed priority "
                f"{proposed_observation.priority}",
            )
            raise AbstractTelescope.OverrideException()

    def summary(self) -> str:
        return render_to_string(self.SUMMARY_TEMPLATE, {"telescope": self})
