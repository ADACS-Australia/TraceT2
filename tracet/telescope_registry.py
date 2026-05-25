import copy
from collections import namedtuple

import django.db.models
import django.forms


class TelescopeRegistry:
    TelescopeConfig = namedtuple(
        "TelescopeConfig", ["shortname", "name", "model", "formset"]
    )

    def __init__(self):
        self.registry: dict[str, TelescopeRegistry.TelescopeConfig] = {}

    def register(
        self,
        shortname: str,
        name: str,
        model: type[django.db.models.Model],
        formset: type[django.forms.BaseInlineFormSet],
    ):
        self.registry[shortname] = TelescopeRegistry.TelescopeConfig(
            shortname, name, model, formset
        )

    def get(self) -> dict[str, TelescopeConfig]:
        return copy.copy(self.registry)


telescope_registry = TelescopeRegistry()
