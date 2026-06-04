import logging

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.db.models.signals import post_save, m2m_changed
from django.dispatch import receiver
from django.template.loader import render_to_string

from tracet import models


logger = logging.getLogger(__name__)


def resync_events(trigger: models.Trigger):
    with transaction.atomic():
        # First, set all events to disabled
        trigger.events.update(disabled=True)

        # Remove notices from events: we'll add them back again with the updated topic criteria
        for event in trigger.events.all():
            event.notices.clear()

        # And delete all simulated decisions
        models.Decision.objects.filter(
            event__trigger__id=trigger.id, source=models.Decision.Source.SIMULATED
        ).delete()

        # Create events that match the topic and eventid criteria
        for notice in models.Notice.objects.filter(
            topic__in=trigger.topics.all()
        ).order_by("-created"):
            trigger.get_or_create_event(notice)

        # Set or update event time
        for event in models.Event.objects.filter(trigger_id=trigger.id):
            event.updatetime()


@receiver(post_save, sender=models.Trigger)
def on_trigger_save(sender, instance, created, **kwargs):
    """
    When a trigger is saved:
    1. If the trigger is newly created, we need to create associated events from the archive of notices.
    2. Resync each event's time in case time_path has been updated.
    """
    trigger = instance

    # Build the associated list of events, taking into account any changes topic/eventid
    resync_events(trigger)

    # Set or update (where Trigger.time_path has changed) event time
    for event in trigger.events.all():
        event.updatetime()


@receiver(m2m_changed, sender=models.Trigger.topics.through)
def on_trigger_topics_changed(sender, instance, pk_set, action, reverse, **kwargs):
    """
    For each new notice added to an event, update the Event.time field to reflect
    the _earliest_ `trigger.time_path` value.
    """
    trigger = instance

    if not reverse and action.startswith("post"):
        # Build the associated list of events, taking into account any changes topic/eventid
        resync_events(trigger)


@receiver(post_save, sender=models.Notice)
def on_notice_save(sender, instance, created, **kwargs):
    """
    When a notice is created we must:

    1. Create (or update) an event for each Trigger, if the Trigger is listening to the notice's stream.
    2. Run each applicable trigger.
    """
    notice: models.Notice = instance

    if not created:
        return

    # For each trigger...
    for trigger in models.Trigger.objects.order_by("priority"):
        # (Maybe) create a new event
        if event := trigger.get_or_create_event(notice):
            try:
                decision: models.Decision = models.Decision.objects.create(
                    event=event, source=models.Decision.Source.NOTICE
                )
            except Exception as e:
                logger.error(
                    f"Trigger {trigger.name} (id={trigger.id}) threw an exception when trying to form decision in response to notice (id={notice.id})",
                    exc_info=e,
                )
                continue

            # Send an email if:
            # 1. Conclusion is at least a MAYBE (allowing for user-intervention)
            # 2. And there's a configured telescope
            if (
                decision.conclusion in (models.Vote.MAYBE, models.Vote.PASS)
                and trigger.active
                and trigger.get_telescope()
                and (email := trigger.user.email)
            ):
                send_mail(
                    subject=f"TraceT decision alert for trigger '{trigger.name}'",
                    message=render_to_string(
                        "tracet/email/decision.html",
                        context={
                            "trigger": trigger,
                            "user": trigger.user,
                            "decision": decision,
                            "baseurl": settings.BASEURL,
                        },
                    ),
                    from_email=None,
                    recipient_list=[email],
                )


@receiver(post_save, sender=models.NumericRangeCondition)
@receiver(post_save, sender=models.BooleanCondition)
def on_condition_save(sender, instance, created, **kwargs):
    """
    When a Trigger's condition changes, we clear out prior simulated decisions
    This will be automatically generated when needed.
    """
    trigger = instance.trigger
    models.Decision.objects.filter(
        event__trigger__id=trigger.id, source=models.Decision.Source.SIMULATED
    ).delete()


@receiver(m2m_changed, sender=models.Event.notices.through)
def no_event_notices_changed(sender, instance, pk_set, action, reverse, **kwargs):
    """
    For each new notice added to an event, update the Event.time field to reflect
    the _earliest_ `trigger.time_path` value.
    """
    event = instance

    if not reverse and action.startswith("post"):
        event.updatetime()
