import datetime
import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from tracet import models
from tracet.models.decision import Vote
from tracet.tests.telescope.models import Telescope


class BaseTestCase(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # Set up standard objects to be used in tests
        cls.user = get_user_model().objects.create_user(
            username="user1",
            email="test@test.com",
            password="password",
        )

        cls.stream1 = models.Stream.objects.create(
            name="stream1",
            domain="kafka-one.test:9092",
            enabled=True,
        )
        cls.stream2 = models.Stream.objects.create(
            name="stream2",
            domain="kafka-two.test:9092",
            enabled=True,
        )

        for stream in (cls.stream1, cls.stream2):
            models.Topic.objects.create(
                name="xml-topic",
                stream=stream,
                type="xml",
            )
            models.Topic.objects.create(
                name="json-topic",
                stream=stream,
                type="json",
            )

        cls.trigger_xml = models.Trigger.objects.create(
            name="XML Trigger 1",
            user=cls.user,
            priority=1,
            expiry=10,
            eventid_path="/voe:VOEvent/What/Group[@name='Svom_Identifiers']/Param[@name='Burst_Id']/@value",
            time_path="/voe:VOEvent/WhereWhen/ObsDataLocation/ObservationLocation/AstroCoords/Time/TimeInstant/ISOTime/text()",
            active=True,
        )
        cls.trigger_xml.topics.set(models.Topic.objects.filter(type="xml"))

        Telescope.objects.create(
            trigger=cls.trigger_xml,
            ra_path="/voe:VOEvent/WhereWhen/ObsDataLocation/ObservationLocation/AstroCoords/Position2D/Value2/C1/text()",
            dec_path="/voe:VOEvent/WhereWhen/ObsDataLocation/ObservationLocation/AstroCoords/Position2D/Value2/C2/text()",
        )

        cls.trigger_json = models.Trigger.objects.create(
            name="JSON Trigger 1",
            user=cls.user,
            priority=1,
            expiry=10,
            eventid_path="$.id",
            time_path="$.trigger_time",
            active=True,
        )
        cls.trigger_json.topics.set(models.Topic.objects.filter(type="json"))

        Telescope.objects.create(
            trigger=cls.trigger_json,
            ra_path="$.ra",
            dec_path="$.dec",
        )

    def get_xml(self, event_id="evt-001", event_time=None):
        if event_time is None:
            event_time = timezone.now()

        with open(Path(__file__).parent / "notice.xml") as f:
            return (
                f.read()
                .format(
                    event_id=event_id,
                    event_time=event_time,
                )
                .encode()
            )

    def get_json(self, event_id="evt-001", event_time=None):
        if event_time is None:
            event_time = timezone.now()

        with open(Path(__file__).parent / "notice.json") as f:
            d = json.load(f)

        d["id"] = event_id
        d["trigger_time"] = event_time.isoformat()
        return json.dumps(d).encode()


class NoticeIngestionTest(BaseTestCase):
    """Notices are created from Kafka messages and trigger the pipeline."""

    def test_notice_created_on_subscribed_topic_fires_decision(self):
        """A notice on a topic the trigger listens to creates an Event + Decision."""

        models.Notice.objects.create(
            topic=self.trigger_xml.topics.first(),
            offset=1,
            payload=self.get_xml(event_id="evt-001"),
        )

        # Signal should have created 1 Event and 1 Decision
        self.assertEqual(models.Event.objects.count(), 1)
        self.assertEqual(models.Decision.objects.count(), 1)

        event = models.Event.objects.all()[0]
        self.assertEqual(event.eventid, "evt-001")
        self.assertEqual(event.notices.count(), 1)

        decision = models.Decision.objects.all()[0]
        self.assertEqual(decision.source, models.Decision.Source.NOTICE)

    def test_multiple_notices_same_event_grouped(self):
        """Multiple notices with the same event_id are grouped into one Event."""

        event_time = datetime.datetime(2025, 1, 1, 12, 30, 15, tzinfo=datetime.UTC)

        models.Notice.objects.create(
            topic=self.trigger_json.topics.first(),
            offset=1,
            payload=self.get_json(event_id="evt-123", event_time=event_time),
        )

        models.Notice.objects.create(
            topic=self.trigger_json.topics.first(),
            offset=2,
            payload=self.get_json(event_id="evt-123", event_time=event_time),
        )

        self.assertEqual(models.Event.objects.count(), 1)

        event = models.Event.objects.all()[0]
        self.assertEqual(event.eventid, "evt-123")
        self.assertEqual(event.time, event_time)
        self.assertEqual(event.notices.count(), 2)
        self.assertEqual(event.decisions.count(), 2)

    def test_multiple_triggers_priority_order(self):
        """Notices fire decisions for all matching triggers, ordered by priority."""

        # Clone a second trigger with a lower priority
        trigger_xml2 = models.Trigger.objects.get(id=self.trigger_xml.id)
        trigger_xml2.pk = None
        trigger_xml2.priority = 2
        trigger_xml2.save()
        trigger_xml2.topics.set(self.trigger_xml.topics.all())
        telescope = self.trigger_xml.get_telescope()
        telescope.pk = None
        telescope.trigger = trigger_xml2
        telescope.save()

        models.Notice.objects.create(
            topic=self.trigger_xml.topics.first(),
            offset=1,
            payload=self.get_xml(event_id="evt-001"),
        )

        self.assertEqual(self.trigger_xml.events.count(), 1)
        self.assertEqual(trigger_xml2.events.count(), 1)

        decision1 = self.trigger_xml.events.first().decisions.first()
        decision2 = trigger_xml2.events.first().decisions.first()

        self.assertLess(decision1.created, decision2.created)
        self.assertEqual(decision1.observation.status, models.Observation.Status.API_OK)
        self.assertEqual(decision2.observation.status, models.Observation.Status.CLASH)


class ExpirationConditionTest(BaseTestCase):
    """Expiration condition votes PASS within the window, MAYBE after."""

    def test_within_expiration_window(self):
        now = datetime.datetime.now(datetime.UTC)
        event_time = now - datetime.timedelta(minutes=5)

        models.Notice.objects.create(
            topic=self.trigger_json.topics.first(),
            offset=1,
            payload=self.get_json(event_id="evt-123", event_time=event_time),
        )

        factors = list(models.Decision.objects.first().factors.all())
        self.assertEqual(len(factors), 1)
        self.assertEqual(factors[0].vote, models.Vote.PASS)

    def test_expired_event(self):
        """An event older than the expiry window gets a MAYBE from expiration."""
        now = datetime.datetime.now(datetime.UTC)
        event_time = now - datetime.timedelta(minutes=12)

        models.Notice.objects.create(
            topic=self.trigger_json.topics.first(),
            offset=1,
            payload=self.get_json(event_id="evt-123", event_time=event_time),
        )

        factors = list(models.Decision.objects.first().factors.all())
        self.assertEqual(len(factors), 1)
        self.assertEqual(factors[0].vote, models.Vote.MAYBE)


class NumericRangeConditionTest(BaseTestCase):
    """Numeric range condition: val1 <= value < val2."""

    def test_value_in_range(self):
        models.NumericRangeCondition.objects.create(
            trigger=self.trigger_json,
            selector="$.snr",
            val1=10,
            val2=14,
            if_true=Vote.PASS,
            if_false=Vote.FAIL,
        )

        models.Notice.objects.create(
            topic=self.trigger_json.topics.first(),
            offset=1,
            payload=self.get_json(
                event_id="evt-123", event_time=datetime.datetime.now(datetime.UTC)
            ),
        )
        decision = models.Decision.objects.first()
        self.assertEqual(decision.conclusion, Vote.PASS)

    def test_value_out_of_range(self):
        models.NumericRangeCondition.objects.create(
            trigger=self.trigger_json,
            selector="$.snr",
            val1=14,
            val2=16,
            if_true=Vote.PASS,
            if_false=Vote.MAYBE,
        )

        models.Notice.objects.create(
            topic=self.trigger_json.topics.first(),
            offset=1,
            payload=self.get_json(
                event_id="evt-123", event_time=datetime.datetime.now(datetime.UTC)
            ),
        )
        decision = models.Decision.objects.first()
        self.assertEqual(decision.conclusion, Vote.MAYBE)

    def test_missing_selector_returns_null_vote(self):
        """When the selector finds nothing, the factor vote is null."""
        models.NumericRangeCondition.objects.create(
            trigger=self.trigger_json,
            selector="$.snr_missing",
            val1=14,
            val2=16,
            if_true=Vote.PASS,
            if_false=Vote.FAIL,
        )

        models.Notice.objects.create(
            topic=self.trigger_json.topics.first(),
            offset=1,
            payload=self.get_json(
                event_id="evt-123", event_time=datetime.datetime.now(datetime.UTC)
            ),
        )
        decision = models.Decision.objects.first()
        self.assertEqual(decision.conclusion, Vote.FAIL)
        self.assertIsNone(decision.factors.last().vote)

    def test_non_numeric_value(self):
        models.NumericRangeCondition.objects.create(
            trigger=self.trigger_json,
            selector="$.trigger_time_inf_freq",
            val1=14,
            val2=16,
            if_true=Vote.PASS,
            if_false=Vote.MAYBE,
        )

        models.Notice.objects.create(
            topic=self.trigger_json.topics.first(),
            offset=1,
            payload=self.get_json(
                event_id="evt-123", event_time=datetime.datetime.now(datetime.UTC)
            ),
        )
        decision = models.Decision.objects.first()
        self.assertEqual(decision.conclusion, Vote.FAIL)


class BooleanConditionTest(BaseTestCase):
    def test_truthy_value(self):
        models.BooleanCondition.objects.create(
            trigger=self.trigger_xml,
            selector="/voe:VOEvent/What/Group[@name='Misc_Flags']/Param[@name='Flt_Generated']/@value",
            if_true=Vote.FAIL,
            if_false=Vote.PASS,
        )
        models.Notice.objects.create(
            topic=self.trigger_xml.topics.first(),
            offset=1,
            payload=self.get_xml(),
        )

        decision = models.Decision.objects.first()
        self.assertEqual(decision.factors.last().vote, Vote.FAIL)

    def test_falsy_value(self):
        models.BooleanCondition.objects.create(
            trigger=self.trigger_xml,
            selector="/voe:VOEvent/What/Group[@name='Misc_Flags']/Param[@name='Near_Bright_Star']/@value",
            if_true=Vote.PASS,
            if_false=Vote.FAIL,
        )
        models.Notice.objects.create(
            topic=self.trigger_xml.topics.first(),
            offset=1,
            payload=self.get_xml(),
        )

        decision = models.Decision.objects.first()
        self.assertEqual(decision.factors.last().vote, Vote.FAIL)

    def test_missing_selector_null_vote(self):
        models.BooleanCondition.objects.create(
            trigger=self.trigger_xml,
            selector="/voe:VOEvent/What/Group[@name='Misc_Flags']/Param[@name='Missing']/@value",
            if_true=Vote.PASS,
            if_false=Vote.FAIL,
        )
        models.Notice.objects.create(
            topic=self.trigger_xml.topics.first(),
            offset=1,
            payload=self.get_xml(),
        )

        decision = models.Decision.objects.first()
        self.assertIsNone(decision.factors.last().vote)


class EqualityConditionTest(BaseTestCase):
    def test_value_matches(self):
        models.EqualityCondition.objects.create(
            trigger=self.trigger_json,
            selector="$.alert_type",
            vals="initial\nprelim\nfinal",
            if_true=Vote.PASS,
            if_false=Vote.FAIL,
        )

        models.Notice.objects.create(
            topic=self.trigger_json.topics.first(),
            offset=1,
            payload=self.get_json(),
        )
        decision = models.Decision.objects.first()
        self.assertEqual(decision.factors.last().vote, Vote.PASS)

    def test_value_does_not_match(self):
        models.EqualityCondition.objects.create(
            trigger=self.trigger_json,
            selector="$.alert_type",
            vals="prelim\nfinal",
            if_true=Vote.PASS,
            if_false=Vote.MAYBE,
        )

        models.Notice.objects.create(
            topic=self.trigger_json.topics.first(),
            offset=1,
            payload=self.get_json(),
        )
        decision = models.Decision.objects.first()
        self.assertEqual(decision.factors.last().vote, Vote.MAYBE)

    def test_single_candidate(self):
        models.EqualityCondition.objects.create(
            trigger=self.trigger_json,
            selector="$.alert_type",
            vals="initial",
            if_true=Vote.FAIL,
            if_false=Vote.PASS,
        )

        models.Notice.objects.create(
            topic=self.trigger_json.topics.first(),
            offset=1,
            payload=self.get_json(),
        )
        decision = models.Decision.objects.first()
        self.assertEqual(decision.factors.last().vote, Vote.FAIL)


class DecisionConclusionTest(BaseTestCase):
    """The decision conclusion is the minimum of all factor votes."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        models.BooleanCondition.objects.create(
            trigger=cls.trigger_xml,
            selector="/voe:VOEvent/What/Group[@name='Misc_Flags']/Param[@name='Flt_Generated']/@value",
            if_true=Vote.PASS,
            if_false=Vote.FAIL,
        )

        models.NumericRangeCondition.objects.create(
            trigger=cls.trigger_xml,
            selector="/voe:VOEvent/What/Group/Param[@name='SNR']/@value",
            val1=5,
            val2=10,
            if_true=Vote.PASS,
            if_false=Vote.FAIL,
        )

        models.EqualityCondition.objects.create(
            trigger=cls.trigger_xml,
            selector="//WhereWhen/ObsDataLocation/ObservatoryLocation/@id",
            vals="GEOLEO",
            if_true=Vote.PASS,
            if_false=Vote.MAYBE,
        )

    def test_all_pass_concludes_pass(self):
        models.Notice.objects.create(
            topic=self.trigger_xml.topics.first(),
            offset=1,
            payload=self.get_xml(),
        )
        decision = models.Decision.objects.first()
        self.assertEqual(decision.conclusion, Vote.PASS)

    def test_one_fail_concludes_fail(self):
        c = self.trigger_xml.booleanconditions.first()
        c.if_true = Vote.FAIL
        c.save()

        models.Notice.objects.create(
            topic=self.trigger_xml.topics.first(),
            offset=1,
            payload=self.get_xml(),
        )
        decision = models.Decision.objects.first()
        self.assertEqual(decision.conclusion, Vote.FAIL)

    def test_one_maybe_concludes_maybe(self):
        c = self.trigger_xml.equalityconditions.first()
        c.if_true = Vote.MAYBE
        c.save()

        models.Notice.objects.create(
            topic=self.trigger_xml.topics.first(),
            offset=1,
            payload=self.get_xml(),
        )
        decision = models.Decision.objects.first()
        self.assertEqual(decision.conclusion, Vote.MAYBE)

    def test_null_vote_treated_as_fail(self):
        c = self.trigger_xml.numericrangeconditions.first()
        c.selector = "//NOT_PRESENT/text()"
        c.save()

        models.Notice.objects.create(
            topic=self.trigger_xml.topics.first(),
            offset=1,
            payload=self.get_xml(),
        )
        decision = models.Decision.objects.first()
        self.assertEqual(decision.conclusion, Vote.FAIL)


class TelescopeTriggeringTest(BaseTestCase):
    """Telescope creates observations when decision passes, and is skipped on fail."""

    def test_pass_decision_creates_observation(self):
        models.Notice.objects.create(
            topic=self.trigger_json.topics.first(),
            offset=1,
            payload=self.get_json(),
        )

        obs = models.Observation.objects.first()
        self.assertIsNotNone(obs)
        self.assertEqual(obs.status, models.Observation.Status.API_OK)
        self.assertFalse(obs.istest)

    def test_fail_decision_no_observation(self):
        """When a condition fails, no observation should be created."""
        models.Notice.objects.create(
            topic=self.trigger_json.topics.first(),
            offset=1,
            payload=self.get_json(
                event_time=datetime.datetime.now(datetime.UTC)
                - datetime.timedelta(minutes=20)
            ),
        )

        decision = models.Decision.objects.first()
        self.assertEqual(decision.conclusion, Vote.MAYBE)
        self.assertFalse(hasattr(decision, "observation"))

    def test_telescope_rejection_recorded(self):
        telescope = self.trigger_xml.get_telescope()
        telescope.refresh_from_db()
        telescope.reject = True
        telescope.save()

        models.Notice.objects.create(
            topic=self.trigger_xml.topics.first(),
            offset=1,
            payload=self.get_xml(),
        )

        obs = models.Observation.objects.first()
        self.assertIsNotNone(obs)
        self.assertEqual(obs.status, models.Observation.Status.API_FAILURE)

    def test_inactive_trigger_marks_test_observation(self):
        """Inactive triggers produce test observations."""

        self.trigger_json.refresh_from_db()
        self.trigger_json.active = False
        self.trigger_json.save()

        models.Notice.objects.create(
            topic=self.trigger_json.topics.last(),
            offset=1,
            payload=self.get_json(),
        )

        obs = models.Observation.objects.first()
        self.assertIsNotNone(obs)
        self.assertTrue(obs.istest)

    def test_observation_clash_lower_priority(self):
        """A new observation with an equal priority is blocked.

        Two triggers subscribe to different topics so each only sees its own
        notice, but both use the same observatory so clash detection fires.
        """

        models.Notice.objects.create(
            topic=self.trigger_xml.topics.first(),
            offset=1,
            payload=self.get_xml(),
        )

        models.Notice.objects.create(
            topic=self.trigger_json.topics.last(),
            offset=1,
            payload=self.get_json(),
        )

        # The later observation should be blocked as a clash
        obs1 = models.Observation.objects.filter(
            decision__event__trigger=self.trigger_xml,
        ).first()
        obs2 = models.Observation.objects.filter(
            decision__event__trigger=self.trigger_json,
        ).first()

        self.assertEqual(obs1.priority, 1)
        self.assertEqual(obs2.priority, 1)
        self.assertLessEqual(obs1.created, obs2.created)
        self.assertEqual(obs1.status, models.Observation.Status.API_OK)
        self.assertEqual(obs2.status, models.Observation.Status.CLASH)

    def test_missing_pointings_data_failure(self):
        """When get_pointings returns empty list, observation reports as DATA_FAILURE."""

        telescope = self.trigger_xml.get_telescope()
        telescope.refresh_from_db()
        telescope.ra_path = "//RA-garbage/@value"
        telescope.save()

        models.Notice.objects.create(
            topic=self.trigger_xml.topics.first(),
            offset=1,
            payload=self.get_xml(),
        )

        obs = models.Observation.objects.first()
        self.assertIsNotNone(obs)
        self.assertEqual(obs.status, models.Observation.Status.DATA_FAILURE)


class FactorInheritanceTest(BaseTestCase):
    """Factors from successive notices inherit previous votes when selector is null."""

    def test_factor_inheritance(self):
        """When a second notice's selector returns null, the first factor is inherited."""

        models.NumericRangeCondition.objects.create(
            trigger=self.trigger_json,
            selector="$.snr",
            val1=10,
            val2=100.0,
            if_true=Vote.PASS,
            if_false=Vote.FAIL,
        )

        # First notice: has flux → PASS
        models.Notice.objects.create(
            topic=self.trigger_json.topics.last(),
            offset=1,
            payload=self.get_json(),
        )

        # Second notice: no flux → null vote → inherits PASS from first
        d = json.loads(self.get_json())
        del d["snr"]

        models.Notice.objects.create(
            topic=self.trigger_json.topics.last(),
            offset=2,
            payload=json.dumps(d).encode(),
        )

        (decision1, decision2) = list(models.Decision.objects.all().order_by("created"))

        self.assertEqual(decision1.conclusion, Vote.PASS)
        self.assertEqual(decision2.conclusion, Vote.PASS)

        self.assertFalse(decision1.factors.last().inherited)
        self.assertTrue(decision2.factors.last().inherited)
