import datetime
import logging

from django.db import models

from tracet.fields import JXPathField
from tracet.models.decision import Decision, Factor, Vote
from tracet.models.notice import Notice
from tracet.utils import truthy


logger = logging.getLogger(__name__)


class ExpirationCondition:
    """
    Non-model condition that fails a Decision once the Event has aged past the Trigger's expiry.

    Unlike the other conditions, this is not stored per-Trigger in the
    database; it is built on the fly from ``Trigger.expiry`` and injected
    into the conditions list by ``get_conditions()``.
    """

    def __init__(self, expiration):
        self.expiration = expiration

    def __str__(self) -> str:
        return f"IF NOW - EVENT_TIME <= {self.expiration} [minute] THEN Pass ELSE Maybe"

    def vote(self, notice: Notice, decision: Decision) -> Factor:
        condition = f"IF {decision.created} - {decision.event.time} <= {self.expiration} [minute] THEN Pass ELSE Maybe"

        if decision.created - decision.event.time <= datetime.timedelta(
            minutes=self.expiration
        ):
            return Factor(condition=condition, vote=Vote.PASS)
        else:
            return Factor(condition=condition, vote=Vote.MAYBE)


class NumericRangeCondition(models.Model):
    """Condition that extracts a value from a Notice and tests if it falls within a range."""

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

    def vote(self, notice: Notice, decision: Decision) -> Factor:
        try:
            val = notice.query(self.selector)
            if val is None:
                return Factor(condition=str(self))

            if self.val1 <= float(val) < self.val2:
                return Factor(condition=str(self), vote=self.if_true)
        except TypeError, ValueError:
            # Unable to convert to float
            return Factor(condition=str(self))

        return Factor(condition=str(self), vote=self.if_false)


class BooleanCondition(models.Model):
    """Condition that extracts a value from a Notice and tests for truthiness."""

    selector = JXPathField()
    if_true = models.IntegerField(choices=Vote)
    if_false = models.IntegerField(choices=Vote)
    trigger = models.ForeignKey(
        "Trigger", related_name="booleanconditions", on_delete=models.CASCADE
    )

    def __str__(self):
        return f"IF {self.selector} THEN {self.get_if_true_display()} ELSE {self.get_if_false_display()}"

    def vote(self, notice: Notice, decision: Decision) -> Factor:
        try:
            val = notice.query(self.selector)
            if val is None:
                return Factor(condition=str(self))

            if truthy(val):
                return Factor(condition=str(self), vote=self.if_true)
        except TypeError, ValueError:
            # Unable to convert to boolean
            return Factor(condition=str(self))

        return Factor(condition=str(self), vote=self.if_false)


class EqualityCondition(models.Model):
    """Condition that extracts a value from a Notice and tests for membership in a list."""

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
            return f"IF {self.selector} IN {tuple(vals)}"
        else:
            return f"IF {self.selector} IN ('{vals[0]}', '{vals[1]}', ..., '{vals[-2]}', '{vals[-1]}')"

    def get_vals(self):
        return [line.strip() for line in self.vals.strip().splitlines()]

    def vote(self, notice: Notice, decision: Decision) -> Factor:
        try:
            val = notice.query(self.selector)
            if val is None:
                return Factor(condition=str(self))

            val = str(val)
            if val in self.get_vals():
                return Factor(condition=str(self), vote=self.if_true)
        except TypeError, ValueError:
            # Unable to convert to string
            return Factor(condition=str(self))

        return Factor(condition=str(self), vote=self.if_false)
