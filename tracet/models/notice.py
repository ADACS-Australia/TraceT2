import io
import logging
import json

import jsonpath_rfc9535 as jsonpath
from lxml import etree

from django.db import models
from django.urls import reverse
from django.utils import timezone


logger = logging.getLogger(__name__)


class Notice(models.Model):
    """
    A single message received from Kafka.

    Notices are the raw payload of an alert, stored as binary so they can
    be re-parsed against different XPath/JSONPath selectors. When saved,
    the ``on_notice_save`` signal iterates every Trigger (by priority) and
    creates or updates an Event, then fires a Decision.
    """

    topic = models.ForeignKey("Topic", related_name="notices", on_delete=models.CASCADE)
    offset = models.IntegerField()
    created = models.DateTimeField(null=True)
    received = models.DateTimeField(default=timezone.now)
    payload = models.BinaryField()

    class Meta:
        ordering = ["-created"]
        indexes = [models.Index(fields=["created", "received"])]
        unique_together = ("topic", "offset")

    def __str__(self):
        return f"{self.topic} @ offset {self.offset}"

    def get_absolute_url(self):
        return reverse("notice", args=[self.id])

    def file_type(self):
        return self.topic.get_type_display()

    def query(self, path):
        # Handle empty paths gracefully
        if not path:
            return None

        try:
            if self.topic.type == "xml":
                rootnode = etree.parse(io.BytesIO(self.payload)).getroot()
                return rootnode.xpath(path, namespaces=rootnode.nsmap)[0]
            elif self.topic.type == "json":
                return jsonpath.find(path, json.loads(self.payload))[0].value
        except IndexError:
            # In the case that no value is found at the path, we return None
            return None
        except Exception as e:
            # Failure can occur if:
            # 1. The query string is invalid, or
            # 2. The payload is not valid
            logger.warning("A XPath/JSONPath query failed", exc_info=e)
            return None

    def pretty_payload(self):
        try:
            if self.topic.type == "xml":
                return etree.tostring(
                    etree.parse(io.BytesIO(self.payload)), pretty_print=True
                ).decode()
            elif self.topic.type == "json":
                return json.dumps(json.loads(self.payload), indent=4)
        except etree.XMLSyntaxError, json.JSONDecodeError:
            return f"(Error: the payload is not valid {self.topic.type})"
