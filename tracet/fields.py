import os

import jsonpath_rfc9535 as jsonpath
from cryptography.fernet import Fernet
from django import forms
from django.core.exceptions import ValidationError
from django.db import models
from lxml import etree


class EncryptedTextField(models.TextField):
    """A TextField that transparently encrypts on write, decrypts on read,
    and behaves as a write-only, leave-unchanged-if-blank form field."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("blank", True)
        super().__init__(*args, **kwargs)

    def get_prep_value(self, value):
        if value in (None, ""):
            return None
        return self.get_cipher().encrypt(str(value).encode()).decode()

    def from_db_value(self, value, expression, connection):
        if value is None:
            return ""
        return self.get_cipher().decrypt(value.encode()).decode()

    def formfield(self, **kwargs):
        defaults = {
            "required": False,
            "widget": forms.PasswordInput(
                render_value=False,  # Django's own default; explicit for clarity
                attrs={"placeholder": "••••••••••••••••"},
            ),
        }
        defaults.update(kwargs)
        return forms.CharField(**defaults)

    def save_form_data(self, instance, data):
        # Called by ModelForm.save() for every field. Blank submission means
        # "no change" -- skip the setattr so the stored value is left alone.
        if data:
            super().save_form_data(instance, data)

    @staticmethod
    def get_cipher() -> Fernet:
        return Fernet(os.environ["FIELD_ENCRYPTION_KEY"])


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
