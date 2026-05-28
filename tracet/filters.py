import django_filters
import django.forms

from . import models, forms


class BooleanWidget(django.forms.Select):
    """Convert true/false values into the internal Python True/False.
    This can be used for AJAX queries that pass true/false from JavaScript's
    internal types through.
    """

    def __init__(self, attrs=None):
        choices = (("", ("------")), ("true", ("Yes")), ("false", ("No")))
        super().__init__(attrs, choices)

    def render(self, name, value, attrs=None, renderer=None):
        try:
            value = {True: "true", False: "false", "1": "true", "0": "false"}[value]
        except KeyError:
            value = ""
        return super().render(name, value, attrs, renderer=renderer)

    def value_from_datadict(self, data, files, name):
        value = data.get(name, None)
        if isinstance(value, str):
            value = value.lower()

        return {
            "1": True,
            "0": False,
            "true": True,
            "false": False,
            True: True,
            False: False,
        }.get(value, None)


class DateRangeWidget(django_filters.widgets.SuffixedMultiWidget):
    suffixes = ["after", "before"]
    template_name = "django_filters/widgets/multiwidget.html"

    def __init__(self, attrs=None):
        widgets = (forms.DateInput, forms.DateInput)
        super().__init__(widgets, attrs)

    def decompress(self, value):
        if value:
            return [value.start, value.stop]
        return [None, None]


class Notice(django_filters.FilterSet):
    topic = django_filters.ModelChoiceFilter(queryset=models.Topic.objects.all())
    created = django_filters.DateFromToRangeFilter(widget=DateRangeWidget())

    class Meta:
        model = models.Notice
        fields = ["topic", "topic__type", "created"]


class Observation(django_filters.FilterSet):
    istest = django_filters.BooleanFilter(label="Is Test", widget=BooleanWidget)
    observatory = django_filters.ChoiceFilter()

    class Meta:
        model = models.Observation
        fields = ["observatory", "status", "istest"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Observatory is just a CharField but we want it to behave like it has `choices` set.
        # We can't manually set this in the model, since the list of observatories changes dynamically based
        # on which telescopes are registered. To do this we:
        # 1. Set observatory as a ChoiceFilter
        # 2. Populate the select list with all unique existing values for observatory
        self.filters["observatory"].extra["choices"] = [
            (val["observatory"], val["observatory"])
            for val in models.Observation.objects.values("observatory").distinct()
        ]
