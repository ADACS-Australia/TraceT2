import json

import jsonpath_rfc9535 as jsonpath
from lxml import etree

from django import forms
from django.db.models import Q
from django.contrib.auth import get_user_model
from django.utils.safestring import mark_safe

from . import models
from . import validators


class DateInput(forms.DateInput):
    input_type = "date"


class DateTimeInput(forms.DateTimeInput):
    input_type = "datetime-local"


class Trigger(forms.ModelForm):
    user = forms.ModelChoiceField(
        get_user_model().objects, disabled=True, widget=forms.HiddenInput
    )
    topics = forms.ModelMultipleChoiceField(
        models.Topic.objects.filter(enabled=True),
        validators=[validators.unique_topic_format(pk="id")],
    )

    class Meta:
        model = models.Trigger
        fields = ["name", "user", "topics", "eventid_path", "time_path", "expiry"]
        widgets = {"expiry": forms.NumberInput(attrs={"autocomplete": "off"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Override the queryset for topics to ensure topics are displayed only if:
        # 1. The topic is enabled, OR
        # 2. The trigger already subscribes to the topic
        if self.instance.id:
            self.fields["topics"].queryset = models.Topic.objects.filter(
                Q(enabled=True) | Q(trigger=self.instance)
            ).distinct()

    def clean(self):
        super().clean()

        notices = models.Notice.objects.filter(
            topic__id__in=self.cleaned_data["topics"]
        )

        try:
            eventid_path = self.cleaned_data["eventid_path"]
            for notice in notices:
                if not notice.query(eventid_path):
                    self.add_error(
                        "eventid_path",
                        mark_safe(
                            "Event ID path was empty for one or more archival notice "
                            f"(e.g. <a href='{notice.get_absolute_url()}'>notice {notice.id}</a>)"
                        ),
                    )
                    break
        except etree.XPathEvalError, jsonpath.JSONPathSyntaxError:
            # This is handled by the field validation
            pass

        try:
            timepath = self.cleaned_data["time_path"]
            for notice in notices:
                if not notice.query(timepath):
                    self.add_error(
                        "time_path",
                        mark_safe(
                            "Time path was empty for one or more archival notice "
                            f"(e.g. <a href='{notice.get_absolute_url()}'>notice {notice.id}</a>)"
                        ),
                    )
                    break
        except etree.XPathEvalError, jsonpath.JSONPathSyntaxError:
            # This is handled by the field validation
            pass


class NumericRangeCondition(forms.ModelForm):
    template_name = "tracet/forms/numericrangecondition.html"

    class Meta:
        model = models.NumericRangeCondition
        fields = ["val1", "selector", "val2", "if_true", "if_false"]
        widgets = {
            "val1": forms.NumberInput(attrs={"placeholder": "Lower"}),
            "val2": forms.NumberInput(attrs={"placeholder": "Upper"}),
            "selector": forms.TextInput(attrs={"placeholder": "Selector"}),
        }


NumericRangeCondition.Meta.error_messages = {
    field: {
        "required": f"{getattr(models.NumericRangeCondition, field).field.verbose_name.capitalize()} is required"
    }
    for field in NumericRangeCondition.Meta.fields
}


class BooleanCondition(forms.ModelForm):
    template_name = "tracet/forms/base.html"

    class Meta:
        model = models.BooleanCondition
        fields = ["selector", "if_true", "if_false"]
        widgets = {"selector": forms.TextInput(attrs={"placeholder": "Selector"})}


BooleanCondition.Meta.error_messages = {
    field: {
        "required": f"{getattr(models.BooleanCondition, field).field.verbose_name.capitalize()} is required"
    }
    for field in BooleanCondition.Meta.fields
}


class EqualityCondition(forms.ModelForm):
    template_name = "tracet/forms/base.html"

    class Meta:
        model = models.EqualityCondition
        fields = ["selector", "vals", "if_true", "if_false"]
        widgets = {"selector": forms.TextInput(attrs={"placeholder": "Selector"})}


EqualityCondition.Meta.error_messages = {
    field: {
        "required": f"{getattr(models.EqualityCondition, field).field.verbose_name.capitalize()} is required"
    }
    for field in EqualityCondition.Meta.fields
}


class EventTrigger(forms.Form):
    eventid = forms.IntegerField(widget=forms.HiddenInput)


class TriggerAdmin(forms.ModelForm):
    class Meta:
        model = models.Trigger
        fields = ["priority", "active"]


class TriggerAdminDisabled(forms.ModelForm):
    priority = forms.IntegerField(disabled=True)
    active = forms.BooleanField(disabled=True)

    class Meta:
        model = models.Trigger
        fields = ["priority", "active"]
