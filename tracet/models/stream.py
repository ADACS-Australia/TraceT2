import copy
import datetime
import os
import json
import logging

import certifi
import confluent_kafka
from django.core.exceptions import ValidationError
from django.db import models


logger = logging.getLogger(__name__)


class PrettyJSONEncoder(json.JSONEncoder):
    def __init__(self, *args, indent, sort_keys, **kwargs):
        super().__init__(*args, indent=4, sort_keys=True, **kwargs)


class Stream(models.Model):
    name = models.CharField(max_length=500)
    domain = models.CharField(max_length=500)
    config = models.JSONField(
        default=dict, blank=True, null=True, encoder=PrettyJSONEncoder
    )
    enabled = models.BooleanField(default=False)
    last_polled = models.DateTimeField(
        default=datetime.datetime(1900, 1, 1, tzinfo=datetime.UTC)
    )

    def __str__(self):
        return self.name

    def make_consumer(self) -> confluent_kafka.Consumer:
        config = copy.copy(self.config)

        # Forcefully override any values with our defaults
        # We don't want these changed as our code depends on some of them
        # (auto commit off and earliest)
        config.update(
            {
                "bootstrap.servers": self.domain,
                "group.id": os.getenv("GCN_GROUP_ID"),
                "ssl.ca.location": certifi.where(),
                "https.ca.location": certifi.where(),
                "auto.offset.reset": "earliest",
                "enable.auto.commit": False,
            }
        )

        logger.debug(f"Connecting to stream {self.name} with Kafka config: {config}")
        consumer = confluent_kafka.Consumer(config)

        # Calling self.topics will error if we haven't been saved to the database.
        # Use the presence of the primary key to make this determination.
        if self.id:
            topics = [t.name for t in self.topics.all()]
            logger.debug(
                f"Stream {self.name} subscribing to {len(topics)} topics: {' '.join(topics)}"
            )
            # It is an error to call subscribe() with an empty list.
            if len(topics):
                consumer.subscribe(topics)

        return consumer

    def clean(self):
        super().clean()

        # Perform basic validation that stream configuration allows for connection to
        # Kafka broker
        try:
            consumer = self.make_consumer()
            consumer.close()
        except Exception as e:
            logger.error(
                "Stream validation failed due to kafka connection test failure",
                exc_info=e,
            )
            raise ValidationError("Incorrect KAFKA configuration: " + str(e))
        else:
            logger.error(f"Stream validation succeeded ({self.name} @ {self.domain})")


class Topic(models.Model):
    class Format(models.TextChoices):
        XML = ("xml", "XML")
        JSON = ("json", "JSON")

    name = models.CharField(max_length=500, unique=True)
    stream = models.ForeignKey(
        "Stream", related_name="topics", on_delete=models.RESTRICT
    )
    type = models.CharField(
        max_length=500, choices=Format, default="xml", verbose_name="Format"
    )
    status = models.CharField(max_length=500, default="—")

    def __str__(self):
        return f"{self.stream} | {self.name} [{self.type}]"
