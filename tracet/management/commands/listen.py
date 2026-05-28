import logging
import os
import threading
import time

import confluent_kafka
import datetime

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand

from tracet.models import Notice, Stream, Topic
from tracet.utils import ThreadsafeBool


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Connect to enabled streams and process incoming notices"

    def handle(self, *args, **kwargs):
        if not os.getenv("KAFKA_GROUP_ID"):
            raise Exception("No KAFKA_GROUP_ID found in environment variables")

        # If the `reset_streams` flag has been set to True, we assume something has changed
        # with regards to:
        #  - new stream
        #  - new topics
        # Then simply let existing threads die and respawn listeners with updated config.
        while True:
            cache.set("reset_streams", False)

            keepalive = ThreadsafeBool(True)
            threads = [
                threading.Thread(target=Command.listen, args=(stream, keepalive))
                for stream in Stream.objects.filter(enabled=True)
            ]

            for thread in threads:
                thread.start()

            while not cache.get("reset_streams", False):
                time.sleep(5)

            keepalive(False)

    @staticmethod
    def listen(stream: Stream, keepalive: ThreadsafeBool):
        try:
            consumer = stream.make_consumer()
        except Exception as e:
            logger.error(
                f"Stream {stream.name} failed to connect to remote Kafka broker",
                exc_info=e,
            )
            raise
        else:
            logger.info(f"Stream {stream.name} successfully connected to Kafka broker")

        while keepalive:
            # Retrieve new batch of pending messages
            try:
                messages = consumer.consume(timeout=1)
            except Exception as e:
                logger.error(
                    f"Stream {stream.name} returned an error when attempting to consume new messages",
                    exc_info=e,
                )
                raise
            else:
                # Record time of successful poll
                # To avoid overriding any changes that have been made to stream in the interim,
                # we make a call to update() rather than the objects save() method.
                Stream.objects.filter(id=stream.id).update(
                    last_polled=datetime.datetime.now(datetime.UTC)
                )

            # Process messages
            for message in messages:
                # Process the message timestamp
                timestamptype, created = message.timestamp()
                if timestamptype == confluent_kafka.TIMESTAMP_NOT_AVAILABLE:
                    created = None
                else:
                    # Kafka timestamp is in milliseconds since Unix epoch
                    created = datetime.datetime.fromtimestamp(
                        created / 1000, datetime.UTC
                    )

                logger.info(
                    f"Received a new message ({stream.name} | {message.topic()} #{message.offset()} @ {created})"
                )

                if message.error():
                    logger.warning(message.error())

                    try:
                        error_status = f"Error ({message.error().str()})"
                        Topic.objects.filter(name=message.topic()).update(status=error_status)
                    except Exception as e:
                        logger.error(
                            "Tried and failed to record Kafka error message",
                            exc_info=e,
                        )
                else:
                    try:
                        topic = Topic.objects.get(name=message.topic())

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

                        # Update status to reflect successful receipt
                        ok_status = f"OK (Last message received: {datetime.datetime.now(datetime.UTC)})"
                        if topic.status != ok_status:
                            topic.status = ok_status
                            topic.save(update_fields=["status"])
                    except ValidationError as e:
                        logger.error(
                            "A ValidationError occurred saving a new notice; assuming we have already seen this notice",
                            exc_info=e,
                        )

                        # Assume we've received the same offset twice, in which case
                        # commit to acknowledge receipt
                        consumer.commit(message)
                    except Exception as e:
                        logger.error(
                            "An error occurred saving a new notice:", exc_info=e
                        )

        logger.info(f"Keepalive wants me dead; closing {stream.name} Kafka consumer")
        consumer.close()
