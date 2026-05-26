from lxml import etree
import jsonpath_rfc9535 as jsonpath

from django.core.exceptions import ValidationError
from django.db import models


class JXPathField(models.CharField):
    def __init__(self, *args, **kwargs):
        kwargs.pop("max_length", None)  # Required to allow migrations to keep working
        super().__init__(max_length=500, *args, **kwargs)

    def validate(self, value, model_instance):
        super().validate(value, model_instance)

        try:
            etree.XPath(value)
        except etree.XPathSyntaxError:
            try:
                jsonpath.compile(value)
            except jsonpath.JSONPathSyntaxError:
                raise ValidationError("Invalid XPath or JPath selector", "invalid")
