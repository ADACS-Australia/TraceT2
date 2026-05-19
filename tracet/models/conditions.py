import datetime
import logging
from typing import Optional

from django.db import models
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.utils import timezone

from tracet.models.fields import JXPathField
from tracet.utils import truthy


logger = logging.getLogger(__name__)


class Vote(models.IntegerChoices):
    FAIL = -1, "Fail"
    MAYBE = 0, "Maybe"
    PASS = 1, "Pass"


class Decision(models.Model):
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
            self.event.notices.filter(created__lte=self.created).order_by("created")
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
    def conclusion(self) -> int:
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
            return conclusion

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

        return self.objects.raw("""
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

    def get_vote_display(self):
        if self.vote is None:
            return "Error"
        else:
            return Vote(self.vote).label

    def html(self):
        return mark_safe(
            f'<span class="vote {self.get_vote_display().lower()} {"inherited" if self.inherited else ""}" title="{escape(self.condition)}"></span>'
        )


class ExpirationCondition:
    def __init__(self, expiration):
        self.expiration = expiration

    def __str__(self):
        return f"IF NOW - EVENT_TIME <= {self.expiration} [minute] THEN Pass ELSE Maybe"

    def vote(self, notice: "Notice", decision: Decision) -> Factor:
        condition = f"IF {decision.created} - {decision.event.time} <= {self.expiration} [minute] THEN Pass ELSE Maybe"

        if decision.created - decision.event.time <= datetime.timedelta(minutes=self.expiration):
            return Factor(condition=condition, vote=Vote.PASS)
        else:
            return Factor(condition=condition, vote=Vote.MAYBE)


class NumericRangeCondition(models.Model):
    selector = JXPathField()
    val1 = models.FloatField(verbose_name="Lower bound")
    val2 = models.FloatField(verbose_name="Upper bound")
    if_true = models.IntegerField(choices=Vote)
    if_false = models.IntegerField(choices=Vote)
    trigger = models.ForeignKey(
        "Trigger", related_name="numericrangeconditions", on_delete=models.CASCADE
    )

    def __str__(self):
        return f"IF {self.val1} ≤ {self.selector} < {self.val2} THEN {self.get_if_true_display()} ELSE {self.get_if_false_display()}"

    def vote(self, notice: "Notice", decision: Decision) -> Factor:
        try:
            val = notice.query(self.selector)
            if val is None:
                return Factor(condition=str(self))

            if self.val1 <= float(val) < self.val2:
                return Factor(condition=str(self), vote=self.if_true)
        except ValueError as e:
            # Unable to convert to float
            return Factor(condition=str(self))

        return Factor(condition=str(self), vote=self.if_false)


class BooleanCondition(models.Model):
    selector = JXPathField()
    if_true = models.IntegerField(choices=Vote)
    if_false = models.IntegerField(choices=Vote)
    trigger = models.ForeignKey(
        "Trigger", related_name="booleanconditions", on_delete=models.CASCADE
    )

    def __str__(self):
        return f"IF {self.selector} THEN {self.get_if_true_display()} ELSE {self.get_if_false_display()}"

    def vote(self, notice: "Notice", decision: Decision) -> Factor:
        try:
            val = notice.query(self.selector)
            if val is None:
                return Factor(condition=str(self))

            if truthy(val):
                return Factor(condition=str(self), vote=self.if_true)
        except ValueError as e:
            # Unable to convert to boolean
            return Factor(condition=str(self))

        return Factor(condition=str(self), vote=self.if_false)


class EqualityCondition(models.Model):
    selector = JXPathField()
    vals = models.TextField(
        verbose_name="Candidates",
        help_text="Enter one more or more candidates (one per line) to test for equality with the selector.",
    )
    if_true = models.IntegerField(choices=Vote)
    if_false = models.IntegerField(choices=Vote)
    trigger = models.ForeignKey(
        "Trigger", related_name="equalityconditions", on_delete=models.CASCADE
    )

    def __str__(self):
        vals = self.get_vals()
        if len(vals) <= 4:
            return f"IF {self.selector} IN {tuple(vals)})"
        else:
            return f"IF {self.selector} IN ('{vals[0]}', '{vals[1]}', ..., '{vals[-2]}', '{vals[-1]}')"

    def get_vals(self):
        return [line.strip() for line in self.vals.strip().splitlines()]

    def vote(self, notice: "Notice", decision: Decision) -> Factor:
        try:
            val = notice.query(self.selector)
            if val is None:
                return Factor(condition=str(self))

            val = str(val)
            if val in self.get_vals():
                return Factor(condition=str(self), vote=self.if_true)
        except ValueError as e:
            # Unable to convert to string
            return Factor(condition=str(self))

        return Factor(condition=str(self), vote=self.if_false)
