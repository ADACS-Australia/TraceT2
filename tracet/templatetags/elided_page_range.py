from django import template

register = template.Library()


@register.simple_tag
def elided_page_range(paginator, pagenumber):
    return paginator.get_elided_page_range(pagenumber)