import json
import logging
import os

import certifi
import confluent_kafka
import datetime
import dateutil.parser

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand


from tracet.models import Notice, Topic

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Start listening to GCN notices"

    def handle(self, *args, **kwargs):
        if not os.getenv("GCN_GROUP_ID"):
            raise Exception("No GCN_GROUP_ID found in environment variables")

        # We periodically reconnect to GCN every ~10 minutes so that we pick up any
        # new topics that may have been added in the meantime.
        while True:
            logger.info("Creating new GCN consumer...")
            topics = ["gcn.heartbeat"] + [t.name for t in Topic.objects.all()]

            consumer = confluent_kafka.Consumer({
                "bootstrap.servers": "gcn.nasa.gov",
                "group.id": os.getenv("GCN_GROUP_ID"),
                "auto.offset.reset": "earliest",
                "enable.auto.commit": False,
                "security.protocol": "sasl_ssl",
                "sasl.mechanism": "OAUTHBEARER",
                "sasl.oauthbearer.method": "oidc",
                "sasl.oauthbearer.client.id": os.getenv("GCN_CLIENT_ID"),
                "sasl.oauthbearer.client.secret": os.getenv("GCN_CLIENT_SECRET"),
                "sasl.oauthbearer.token.endpoint.url": "https://auth.gcn.nasa.gov/oauth2/token",
                "ssl.ca.location": certifi.where(),
                "https.ca.location": certifi.where(),
            })

            consumer.subscribe(topics)

            # Record the time we created the consumer
            t0 = datetime.datetime.now()

            while datetime.datetime.now() - t0 < datetime.timedelta(minutes=5):
                for message in consumer.consume(timeout=1):
                    timestamptype, created = message.timestamp()
                    if timestamptype == confluent_kafka.TIMESTAMP_NOT_AVAILABLE:
                        created = None
                    else:
                        # Kafka timestamp is in milliseconds since Unix epoch
                        created = datetime.datetime.fromtimestamp(
                            created / 1000, datetime.UTC
                        )

                    if message.topic() == "gcn.heartbeat":
                        logger.debug(
                            f"Recieved a new message ({message.topic()} #{message.offset()} @ {created}"
                        )
                    else:
                        logger.info(
                            f"Recieved a new message ({message.topic()} #{message.offset()} @ {created})"
                        )

                    if message.error():
                        logger.warning(message.error())

                        try:
                            topic = Topic.objects.get(name=message.topic())
                            topic.status = f"Error ({message.error().str()})"
                            topic.full_clean()
                            topic.save()
                        except Exception as e:
                            logger.error(
                                "Tried and failed to record Kafka error message",
                                exc_info=e,
                            )

                    elif message.topic() == "gcn.heartbeat":
                        try:
                            cache.set(
                                "gcn_heartbeat_received",
                                datetime.datetime.now(datetime.UTC),
                                timeout=None
                            )
                            cache.set("gcn_heartbeat_created", created)
                            consumer.commit(message)
                        except Exception as e:
                            logger.error(
                                "An error occurred processing GCN heartbeat", exc_info=e
                            )
                    else:
                        try:
                            topic = Topic.objects.get(name=message.topic())
                            topic.status = f"OK (Last message received: {datetime.datetime.now(datetime.UTC)})"
                            topic.full_clean()
                            topic.save()

                            notice = Notice(
                                topic=topic,
                                created=created,
                                offset=message.offset(),
                                payload=message.value(),
                            )
                            notice.full_clean()
                            notice.save()

                            # Let the service know we have processed this message
                            consumer.commit(message)
                        except ValidationError as e:
                            logger.error(
                                "An ValidationError occurred saving a new notice; assuming we have already seen this notice",
                                exc_info=e,
                            )

                            # Assume we've received the same offset twice, in which case
                            # commit to acknowledge receipt
                            consumer.commit(message)
                        except Exception as e:
                            logger.error(
                                "An error occurred saving a new notice:", exc_info=e
                            )
