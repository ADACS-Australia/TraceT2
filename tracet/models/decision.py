from django.db import models
from django.utils import timezone
from django.utils.html import escape
from django.utils.safestring import SafeText, mark_safe


class Vote(models.IntegerChoices):
    """
    Outcome of a single Condition evaluation, or of an entire Decision.

    The final Decision is the minimum of all Factor votes: one Fail means
    the whole Decision fails. A Maybe is promoted to Pass for MANUAL
    decisions (operator override), but stays Maybe for NOTICE decisions.
    """

    FAIL = -1, "Fail"
    MAYBE = 0, "Maybe"
    PASS = 1, "Pass"


class Decision(models.Model):
    """
    An evaluation of all trigger conditions at a instance of time for an Event.

    A new Decision is created each time a Notice arrives. On save, it runs
    every Condition (expiration, numeric range, boolean, equality) against
    each Notice chronologically, producing one Factor per condition.
    Condition inheritance is implemented via the (non-commutative) addition of
    factors, which either inherits the last vote or takes the new vote if non-null.
    If the final conclusion is PASS, the Trigger's telescope fires an Observation.

    SIMULATED decisions are created lazily when viewing a Trigger, so the
    UI can show what _would_ have happened without actually observing.
    """

    class Source(models.TextChoices):
        NOTICE = ("notice", "Notice")
        MANUAL = ("manual", "Manually triggered")
        SIMULATED = ("simulated", "Simulated")

    class Manager(models.Manager):
        def get_queryset(self):
            return (
                super()
                .get_queryset()
                .prefetch_related("factors")
                .select_related("event")
            )

    objects = Manager()

    event = models.ForeignKey(
        "Event", related_name="decisions", on_delete=models.CASCADE
    )
    created = models.DateTimeField(default=timezone.now)
    source = models.CharField(choices=Source)

    def save(self, *args, **kwargs):
        res = super().save(*args, **kwargs)

        self.factors.all().delete()

        # Attach all factors
        notices = list(
            self.event.notices.filter(received__lte=self.created).order_by("received")
        )

        if len(notices) == 0:
            factors = [Factor(condition="Event contains no notices", vote=Vote.FAIL)]
        else:
            conditions = self.event.trigger.get_conditions()

            # Initialize factors list with oldest notice
            notice = notices.pop(0)
            factors = [c.vote(notice, self) for c in conditions]

            # Append all additional factors from remaining notices
            for notice in notices:
                for i, c in enumerate(conditions):
                    # The following + is doing a lot!
                    #   - Indeterminate votes (== None) will inherit most recent non-null Factor
                    #   - Otherwise, we give precedence to the most recent Factor
                    factors[i] += c.vote(notice, self)

        self.factors.add(*factors, bulk=False)

        # If this is a real decision and it's a PASS, trigger observations
        # Manually triggered observations will run if only a MAYBE
        if (
            self.isreal()
            and self.conclusion == Vote.PASS
            and (telescope := self.event.trigger.get_telescope())
        ):
            telescope.create_observation(self)

        return res

    def isreal(self):
        return self.source != Decision.Source.SIMULATED

    @property
    def conclusion(self) -> Vote:
        conclusion = min(
            *[
                (Vote.FAIL if factor.vote is None else factor.vote)
                for factor in self.factors.all()
            ],
            Vote.PASS,  # default policy: pass
            Vote.PASS,  # repeat twice, in case conditions is empty
        )

        # MAYBE gets promoted to YES if source == MANUAL
        if conclusion == Vote.MAYBE and self.source == Decision.Source.MANUAL:
            return Vote.PASS
        else:
            return Vote(conclusion)

    @classmethod
    def get_interesting_decisions(self):
        """
        This union of queries is getting the most recent *interesting* decisions.

        For each event, the most interesting decision, in order of precendence, is:
          1. Decision with an associated successful observation
          2. Decision with an associated unsucessful observation
          3. Decision with no associated observation (i.e. due to Vote.FAIL)

        The complexity of this comes from the fact that we want only one representative
        decision per event and these union, group by and subquery expressions are not
        possible using the Django ORM.
        """

        return Decision.objects.raw("""
            /*
             * First: get most recent decision per event that triggered a successful observation.
             */
            SELECT tracet_decision.* FROM tracet_decision
            LEFT JOIN tracet_observation ON tracet_decision.id = tracet_observation.decision_id
            LEFT JOIN tracet_event ON tracet_decision.event_id = tracet_event.id
            WHERE
                tracet_decision.source <> "simulated" AND
                tracet_observation.status = "api_ok" AND
                NOT tracet_event.disabled
            GROUP BY tracet_decision.event_id
            HAVING MAX(tracet_observation.created)

            UNION

            /*
             * Next: get most recent decision per event that triggered _any_ observation,
             * but excluding events from the fist query.
             */
            SELECT tracet_decision.* FROM tracet_decision
            LEFT JOIN tracet_observation ON tracet_decision.id = tracet_observation.decision_id
            LEFT JOIN tracet_event ON tracet_decision.event_id = tracet_event.id
            WHERE
                tracet_decision.source <> "simulated" AND
                NOT tracet_event.disabled AND
                tracet_decision.event_id NOT IN (
                    SELECT event_id FROM tracet_decision
                    LEFT JOIN tracet_observation ON tracet_decision.id = tracet_observation.decision_id
                    WHERE tracet_observation.status = "api_ok"
                    GROUP BY tracet_decision.event_id
                )
            GROUP BY tracet_decision.event_id
            HAVING MAX(tracet_observation.created)

            UNION

            /*
             * Finally, get most recent decision per event excluding those of the first
             * two queries. In practice, this means those decisions with no associated observation.
             */
            SELECT tracet_decision.* FROM tracet_decision
            LEFT JOIN tracet_observation ON tracet_decision.id = tracet_observation.decision_id
            LEFT JOIN tracet_event ON tracet_decision.event_id = tracet_event.id
            WHERE
                tracet_decision.source <> "simulated" AND
                NOT tracet_event.disabled AND
                tracet_decision.event_id NOT IN (
                    SELECT tracet_decision.event_id FROM tracet_decision
                    INNER JOIN tracet_observation ON tracet_decision.id = tracet_observation.decision_id  /* INNER JOIN requires an observation to exist */
                    GROUP BY tracet_decision.event_id
                )
            GROUP BY tracet_decision.event_id
            HAVING MAX(tracet_decision.created)

            ORDER BY tracet_decision.created DESC
        """)


class Factor(models.Model):
    """
    A single condition's vote on a Decision.

    Each Factor corresponds to one Condition. The ``vote`` can be Pass,
    Maybe, or Fail (or null if the condition's selector returned nothing).
    The ``__add__`` operator implements condition inheritance:
    if the new vote is null, the previous vote is kept (marked inherited).
    """

    decision = models.ForeignKey(
        "Decision", related_name="factors", on_delete=models.CASCADE
    )

    condition = models.TextField()
    vote = models.IntegerField(null=True, blank=True, choices=Vote)
    inherited = models.BooleanField(default=False)

    def __add__(self, other: Factor) -> Factor:
        # Note that this operation is not commutative: order matters!
        if other.vote is None:
            return Factor(condition=self.condition, vote=self.vote, inherited=True)
        else:
            return Factor(condition=other.condition, vote=other.vote)

    def get_vote_display(self) -> str:
        if self.vote is None:
            return "Error"
        else:
            return Vote(self.vote).label

    def html(self) -> SafeText:
        return mark_safe(
            f'<span class="vote {self.get_vote_display().lower()} {"inherited" if self.inherited else ""}" title="{escape(self.condition)}"></span>'
        )
