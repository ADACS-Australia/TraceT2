import datetime

from django import template

register = template.Library()


@register.filter(expects_localtime=True)
def iso8601(dt):
    if not isinstance(dt, datetime.datetime):
        return ""

    if dt.microsecond >= 500_000:
        dt = dt + datetime.timedelta(seconds=1)
    return (
        dt.replace(microsecond=0)
        .astimezone(datetime.UTC)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )
