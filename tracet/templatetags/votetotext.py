from django import template

from tracet.models.decision import Vote


register = template.Library()

@register.simple_tag
def votetotext(vote):
    if vote is None:
        return "error"
    return Vote(vote).label.lower()