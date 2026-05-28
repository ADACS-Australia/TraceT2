import datetime

from django import template
from django.core.cache import cache
from django.utils.safestring import mark_safe

from tracet.models import Stream


register = template.Library()


@register.simple_tag
def stream_status(**kwargs):
    oldest_stream = Stream.objects.filter(enabled=True).order_by("last_polled").first()

    # Case 1: No streams are enabled
    if oldest_stream is None:
        return mark_safe(
            '<code>No enabled streams <span class="stream-status off">Off</span></code>'
        )

    # Case 2: Remaining cases to calculate lag
    lag = datetime.datetime.now(datetime.UTC) - oldest_stream.last_polled

    if lag < datetime.timedelta(seconds=5):
        return mark_safe(
            f'<code title="Largest stream lag is {lag.total_seconds():.1f} seconds ago">Stream OK <span class="stream-status ok">OK</span></code>'
        )
    elif lag < datetime.timedelta(seconds=60):
        return mark_safe(
            f'<code title="Largest stream lag is {lag.total_seconds():.1f} seconds ago">Stream DELAYED <span class="stream-status delayed">Delayed</span></code>'
        )
    else:
        return mark_safe(
            f'<code title="Largest stream lag is {lag} ago">Stream FAILURE <span class="stream-status fail">Failed</span></code>'
        )
