import datetime

from django import template
from django.core.cache import cache
from django.utils.safestring import mark_safe

from tracet.models import Stream


register = template.Library()


@register.simple_tag
def stream_status(**kwargs):
    oldest_stream = Stream.objects.filter(enabled=True).order_by("-last_polled").first()

    # Case 1: No streams are enabled
    if oldest_stream is None:
        return mark_safe(
            '<code>No enabled streams <span class="stream-status off">Off</span></code>'
        )

    # Case 2: Stream has never run (last_polled is None)
    if oldest_stream.last_polled is None:
        return mark_safe(
            f'<code title="Stream {oldest_stream.name} has never successfully polled">Stream FAILURE <span class="stream-status fail">Failed</span></code>'
        )

    # Case 3: Remaining cases to calculate lag
    lag = datetime.datetime.now(datetime.UTC) - oldest_stream.last_polled

    if lag < datetime.timedelta(seconds=5):
        return mark_safe(
            f'<code title="Largest stream lag is {lag.seconds + lag.microseconds / 1e6:.1f} seconds ago">Stream OK <span class="stream-status ok">OK</span></code>'
        )
    elif lag < datetime.timedelta(seconds=60):
        return mark_safe(
            f'<code title="Largest stream lag is {lag.seconds + lag.microseconds / 1e6:.1f} seconds ago">Stream DELAYED <span class="stream-status delayed">Delayed</span></code>'
        )
    else:
        return mark_safe(
            f'<code title="Largest stream lag is {lag} ago">Stream FAILURE <span class="stream-status fail">Failed</span></code>'
        )
