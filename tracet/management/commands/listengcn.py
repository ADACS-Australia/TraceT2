import logging
import os
import time

import confluent_kafka
import datetime
import dateutil.parser

import django
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand

from tracet.models import Notice, Stream, Topic


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Connect to enabled streams and process incoming notices"

    def handle(self, *args, **kwargs):
        if not os.getenv("GCN_GROUP_ID"):
            raise Exception("No GCN_GROUP_ID found in environment variables")

        # Every 5 minutes or so, requery the enabled set of streams
        # This is a lazy way for us to detect:
        #  - new stream
        #  - new topics

        # TODO:
        # Catch exceptions from call to make_exception(), and from consume()

        while True:
            logger.info("(Re)connecting to Kafka streams")

            streams, consumers = [], []
            for stream in Stream.objects.filter(enabled=True):
                try:
                    consumers.append(stream.make_consumer())
                    streams.append(stream)
                except Exception as e:
                    logger.error(
                        f"Stream {stream.name} failed to connect to remote Kakfa broker",
                        exc_info=e,
                    )
                    # TODO Record failure somewhere?
                else:
                    logger.info(f"Stream {stream.name} successfully connected to Kafka broker")

            # Record the time we created the consumer
            t0 = datetime.datetime.now()

            while datetime.datetime.now() - t0 < datetime.timedelta(minutes=5):
                for stream, consumer in zip(streams, consumers):
                    # Retrieve new batch of pending messages
                    try:
                        messages = consumer.consume(timeout=1, num_messages=100)
                    except Exception as e:
                        logger.error(
                            f"Stream {stream.name} returned an error when attempting to consume new messages",
                            exc_info=e,
                        )

                        # Sleep for one section and then try to consume again.
                        time.sleep(1)
                        continue
                    else:
                        # Record time of successful poll
                        stream.last_polled = datetime.datetime.now(datetime.UTC)
                        stream.save()

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
                            f"Recieved a new message ({stream.name} | {message.topic()} #{message.offset()} @ {created})"
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
