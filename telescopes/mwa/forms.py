import django.forms as forms

import tracet.models as models


class MWABaseInlineFormset(forms.BaseInlineFormSet):
    template_name = "mwa/formset.html"


class MWACorrelator(forms.ModelForm):
    shortname = "mwacorrelator"
    name = "MWA Correlator"
    template_name = "tracet/forms/base.html"

    class Meta:
        model = models.MWACorrelator
        fields = [
            "projectid",
            "secure_key",
            "ra_path",
            "dec_path",
            "tileset",
            "frequency",
            "frequency_resolution",
            "time_resolution",
            "exposure",
            "nobs",
            "repointing_threshold",
        ]


MWACorrelator.Meta.error_messages = {
    field: {
        "required": f"{getattr(models.MWACorrelator, field).field.verbose_name.capitalize()} is required"
    }
    for field in MWACorrelator.Meta.fields
}


MWACorrelatorFormset: type[forms.BaseInlineFormSet] = forms.inlineformset_factory(
    models.Trigger,
    models.MWACorrelator,
    form=MWACorrelator,
    formset=MWABaseInlineFormset,
    extra=0,
    max_num=1,
)


class MWAVCS(forms.ModelForm):
    shortname = "mwavcs"
    name = "MWA VCS"
    template_name = "tracet/forms/base.html"

    class Meta:
        model = models.MWAVCS
        fields = [
            "projectid",
            "secure_key",
            "ra_path",
            "dec_path",
            "tileset",
            "frequency",
            "frequency_resolution",
            "time_resolution",
            "exposure",
            "nobs",
            "repointing_threshold",
        ]


MWAVCS.Meta.error_messages = {
    field: {
        "required": f"{getattr(models.MWAVCS, field).field.verbose_name.capitalize()} is required"
    }
    for field in MWAVCS.Meta.fields
}


MWAVCSFormset: type[forms.BaseInlineFormSet] = forms.inlineformset_factory(
    models.Trigger,
    models.MWAVCS,
    form=MWAVCS,
    formset=MWABaseInlineFormset,
    extra=0,
    max_num=1,
)


class MWAGW(forms.ModelForm):
    shortname = "mwagw"
    name = "MWA Graviational Wave"
    template_name = "tracet/forms/base.html"

    class Meta:
        model = models.MWAGW
        fields = [
            "projectid",
            "secure_key",
            "skymap_path",
            "frequency",
            "frequency_resolution",
            "time_resolution",
            "exposure",
            "nobs",
            "repointing_threshold",
        ]


MWAGW.Meta.error_messages = {
    field: {
        "required": f"{getattr(models.MWAGW, field).field.verbose_name.capitalize()} is required"
    }
    for field in MWAGW.Meta.fields
}


MWAGWFormset: type[forms.BaseInlineFormSet] = forms.inlineformset_factory(
    models.Trigger,
    models.MWAGW,
    form=MWAGW,
    formset=MWABaseInlineFormset,
    extra=0,
    max_num=1,
)
