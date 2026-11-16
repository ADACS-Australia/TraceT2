import django.forms as forms

import telescopes.atca.models as models
from tracet.models import Trigger


class ATCA(forms.ModelForm):
    template_name = "tracet/forms/base.html"

    class Meta:
        model = models.ATCA
        fields = (
            "ra_path",
            "dec_path",
            "project_code",
            "authentication_token",
            "frequency_configuration",
            "maximum_lag",
            "minimum_integration",
            "maximum_integration",
            "minimum_elevation",
            "calibration_integration",
            "calibration_gap",
        )


ATCA.Meta.error_messages = {
    field: {
        "required": f"{getattr(models.ATCA, field).field.verbose_name.capitalize()} is required"
    }
    for field in ATCA.Meta.fields
}


class ATCAInlineFormset(forms.BaseInlineFormSet):
    template_name = "atca/formset.html"


ATCAFormset: type[forms.BaseInlineFormSet] = forms.inlineformset_factory(
    Trigger,
    models.ATCA,
    form=ATCA,
    formset=ATCAInlineFormset,
    extra=0,
    max_num=1,
)
