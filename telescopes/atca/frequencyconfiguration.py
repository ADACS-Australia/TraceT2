from dataclasses import dataclass

import django.forms as forms
from django.db import models
from django.core.exceptions import ValidationError


N_OFFSETS = 4


@dataclass(frozen=True)
class FrequencyConfiguration:
    freq: float
    offsets: tuple

    def __post_init__(self):
        if len(self.offsets) != N_OFFSETS:
            raise ValueError(
                f"ATCA Frequency Configuration expected {N_OFFSETS} offsets, got {len(self.offsets)}"
            )

    def __str__(self):
        return f"{self.freq: .0f} MHz with offsets ({', '.join(str(x) for x in self.offsets)})"

    def serialize(self):
        return ",".join(str(x) for x in (self.freq, *self.offsets))

    @classmethod
    def deserialize(cls, text):
        try:
            freq, *offsets = [p.strip() for p in text.split(",")]
            return cls(float(freq), tuple(int(o) for o in offsets))
        except (ValueError, TypeError) as exc:
            raise ValidationError(
                f"Enter a comma-sepatated list of: frequency [MHz], followed by {N_OFFSETS} integer offsets.",
                code="invalid",
            ) from exc


class FrequencyConfigurationWidget(forms.MultiWidget):
    template_name = (
        "atca/widgets/frequencyconfiguration.html"  # optional, see bottom of file
    )

    def __init__(self, attrs=None):
        widgets = [
            forms.NumberInput(
                attrs={"placeholder": "Freq", "step": "any", "title": "Frequency"}
            ),
            *[
                forms.NumberInput(
                    attrs={
                        "placeholder": f"Ch{i}",
                        "step": 1,
                        "title": f"Channel {i} offset",
                    }
                )
                for i in range(1, 4 + 1)
            ],
        ]
        super().__init__(widgets, attrs)

    def decompress(self, value):
        """Model/initial value -> list of 5 values, one per subwidget."""
        if isinstance(value, FrequencyConfiguration):
            return [value.freq, *value.offsets]
        return [None] * (N_OFFSETS + 1)


class FrequencyConfigurationFormField(forms.MultiValueField):
    widget = FrequencyConfigurationWidget

    def __init__(self, **kwargs):
        fields = [
            forms.FloatField(label="Frequency"),
            *[
                forms.IntegerField(label=f"Channel {i} offset")
                for i in range(1, N_OFFSETS + 1)
            ],
        ]
        # require_all_fields=True (the default) makes all five required
        super().__init__(fields=fields, **kwargs)

    def compress(self, data_list):
        if not data_list:
            return None
        freq, *offsets = data_list
        return FrequencyConfiguration(freq, tuple(offsets))


class FrequencyConfigurationField(models.Field):
    description = "A frequency and four channel offsets"

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_length", 64)
        super().__init__(*args, **kwargs)

    def get_internal_type(self):
        return "CharField"  # stored as VARCHAR(max_length)

    # DB -> Python
    def from_db_value(self, value, expression, connection):
        return None if value is None else FrequencyConfiguration.deserialize(value)

    # Deserialisation / clean() -> Python
    def to_python(self, value):
        if value is None or isinstance(value, FrequencyConfiguration):
            return value
        return FrequencyConfiguration.deserialize(value)

    # Python -> DB
    def get_prep_value(self, value):
        value = self.to_python(value)
        return None if value is None else value.serialize()

    # For dumpdata / serializers
    def value_to_string(self, obj):
        return self.get_prep_value(self.value_from_object(obj)) or ""

    def formfield(self, **kwargs):
        defaults = {"form_class": FrequencyConfigurationFormField}
        defaults.update(kwargs)
        return super().formfield(**defaults)
