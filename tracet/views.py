import datetime
import logging

from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.core.paginator import EmptyPage, Paginator
from django.contrib import messages
from django.contrib.auth import get_user
from django.forms import formset_factory, modelformset_factory, inlineformset_factory
from django.shortcuts import (
    get_object_or_404,
    get_list_or_404,
    render,
    resolve_url,
    HttpResponseRedirect,
    HttpResponse,
)
from django.urls import reverse
from django.utils import timezone
from django.views import View

from . import models, forms, filters


logger = logging.getLogger(__name__)


class Home(View):
    def get(self, request):
        # Data used in the summary
        activetriggercount = models.Trigger.objects.filter(active=True).count()
        inactivetriggercount = models.Trigger.objects.filter(active=False).count()
        recentsuccessfulobservation = (
            models.Observation.objects.filter(status=models.Observation.Status.API_OK)
            .order_by("-created")
            .first()
        )
        kafkaok = datetime.datetime.now(datetime.UTC) - cache.get(
            "gcn_heartbeat_received", default=datetime.datetime(1900, 1, 1, tzinfo=datetime.UTC)
        ) < datetime.timedelta(seconds=15)
        mostrecentnotice = models.Notice.objects.order_by("-created").first()

        return render(
            request,
            "tracet/home.html",
            {
                "user": get_user(request),
                "activetriggercount": activetriggercount,
                "inactivetriggercount": inactivetriggercount,
                "recentsuccessfulobservation": recentsuccessfulobservation,
                "kafkaok": kafkaok,
                "mostrecentnotice": mostrecentnotice,
                "notices": models.Notice.objects.order_by("-created")[:10],
                "decisions": models.Decision.get_interesting_decisions()[:15],
                "observations": models.Observation.objects.order_by("-created")[:10],
            },
        )


class Notice(View):
    def get(self, request, id):
        notice = get_object_or_404(models.Notice, id=id)
        return render(
            request,
            "tracet/notice/get.html",
            {"notice": notice},
        )


class NoticeList(View):
    def get(self, request):
        filter = filters.Notice(
            request.GET,
            models.Notice.objects.order_by("-created").select_related("topic"),
        )

        paginator = Paginator(filter.qs, 100)
        pagenumber = request.GET.get("page", 1)
        notices = paginator.get_page(pagenumber)

        return render(
            request,
            "tracet/notice/list.html",
            {"notices": notices, "filter": filter},
        )


class ObservationList(View):
    def get(self, request):
        filter = filters.Observation(
            request.GET,
            models.Observation.objects.order_by("-created"),
        )

        paginator = Paginator(filter.qs, 100)
        pagenumber = request.GET.get("page", 1)

        try:
            observations = paginator.page(pagenumber)
        except EmptyPage:
            # If out of range, display last page
            observations = paginator.page(paginator.num_pages)

        return render(
            request,
            "tracet/observation/list.html",
            {"observations": observations, "filter": filter},
        )


class ObservationView(View):
    def get(self, request, id):
        observation = get_object_or_404(models.Observation, id=id)
        return render(
            request,
            "tracet/observation/get.html",
            {"observation": observation},
        )


class TriggerList(View):
    def setup(self, request):
        super().setup(request)

        user = get_user(request)
        if user.has_perm("tracet.admin_triggers"):
            Form = forms.TriggerAdmin
        else:
            Form = forms.TriggerAdminDisabled

        self.TriggerFormset = modelformset_factory(
            models.Trigger,
            form=Form,
            extra=0,
            edit_only=True,
        )

    def get(self, request):
        activetriggers = self.TriggerFormset(
            prefix="activetriggers",
            queryset=models.Trigger.objects.order_by("priority").filter(active=True),
        )
        inactivetriggers = self.TriggerFormset(
            prefix="inactivetriggers",
            queryset=models.Trigger.objects.order_by("priority").filter(active=False),
        )

        return render(
            request,
            "tracet/trigger/list.html",
            {
                "user": get_user(request),
                "activetriggers": activetriggers,
                "inactivetriggers": inactivetriggers,
            },
        )

    def post(self, request):
        activetriggers = self.TriggerFormset(request.POST, prefix="activetriggers")
        inactivetriggers = self.TriggerFormset(request.POST, prefix="inactivetriggers")

        if activetriggers.is_valid() and inactivetriggers.is_valid():
            activetriggers.save()
            inactivetriggers.save()
            messages.success(
                request,
                "Trigger statuses and priorities have been successfully updated.",
            )
            return HttpResponseRedirect(reverse("triggers"))
        else:
            messages.success(
                request,
                "Trigger statusesand/or priorities could not be updated due to a form error.",
            )
            return render(
                request,
                "tracet/trigger/list.html",
                {
                    "user": get_user(request),
                    "activetriggers": activetriggers,
                    "inactivetriggers": inactivetriggers,
                },
            )


class TriggerBase(View):
    def setup(self, request, id=None):
        super().setup(request, id)

        if id is not None:
            self.trigger = get_object_or_404(models.Trigger, id=id)
        else:
            self.trigger = None

        post = request.POST if request.POST else None
        user = get_user(request) if self.trigger is None else self.trigger.user

        self.forms = dict(
            triggerform=forms.Trigger(
                post, initial={"user": user}, instance=self.trigger
            ),
            rangeformset=inlineformset_factory(
                models.Trigger,
                models.NumericRangeCondition,
                form=forms.NumericRangeCondition,
                extra=0,
            )(post, instance=self.trigger),
            booleanformset=inlineformset_factory(
                models.Trigger,
                models.BooleanCondition,
                form=forms.BooleanCondition,
                extra=0,
            )(post, instance=self.trigger),
            equalityformset=inlineformset_factory(
                models.Trigger,
                models.EqualityCondition,
                form=forms.EqualityCondition,
                extra=0,
            )(post, instance=self.trigger),
            mwaformset=inlineformset_factory(
                models.Trigger,
                models.MWACorrelator,
                form=forms.MWACorrelator,
                extra=0,
                max_num=1,
            )(post, instance=self.trigger),
            mwagwformset=inlineformset_factory(
                models.Trigger,
                models.MWAGW,
                form=forms.MWAGW,
                extra=0,
                max_num=1,
            )(post, instance=self.trigger),
            mwavcsformset=inlineformset_factory(
                models.Trigger,
                models.MWAVCS,
                form=forms.MWAVCS,
                extra=0,
                max_num=1,
            )(post, instance=self.trigger),
            atcaformset=inlineformset_factory(
                models.Trigger,
                models.ATCA,
                form=forms.ATCA,
                formset=forms.BaseATCAWithBandsFormset,
                extra=0,
                max_num=1,
            )(post, instance=self.trigger),
        )

    def get(self, request, id=None):
        return render(
            request,
            "tracet/trigger/edit.html",
            {"trigger": self.trigger, "title": self.title, "actionurl": self.actionurl}
            | self.forms,
        )

    def post(self, request, id=None):
        # Enforce limit of one telescope configuration per trigger
        telescopeformsets = [
            self.forms["mwaformset"],
            self.forms["mwagwformset"],
            self.forms["mwavcsformset"],
            self.forms["atcaformset"],
        ]

        # Validate each fieldset first to ensure `cleaned_data` exists
        all(f.is_valid() for f in  telescopeformsets)

        # Count the telescopes
        # (There must be a better way to do than simply counting all fields where DELETE=False)
        ntelescopes = sum(
            [
                (
                    hasattr(f, "cleaned_data")
                    and "DELETE" in f.cleaned_data
                    and f.cleaned_data["DELETE"] is False
                )
                for formset in telescopeformsets
                for f in formset
            ]
        )

        # ...and attach the error
        if ntelescopes > 1:
            self.forms["triggerform"].add_error(
                None, "A maximum of one telescope may be configured per trigger."
            )

        if all(f.is_valid() for f in self.forms.values()):
            # Save the parent trigger first
            self.trigger = self.forms["triggerform"].save()
            for name, form in self.forms.items():
                # Then for save each child element, careful to assign the instance in
                # the case that trigger is new.
                if name != "triggerform":
                    form.instance = self.trigger
                    form.save()

            messages.success(request, "Trigger was successfully updated.")
            return HttpResponseRedirect(self.trigger.get_absolute_url())
        else:
            messages.error(
                request, "The trigger could not be updated due to errors in the form."
            )
            return render(
                request,
                "tracet/trigger/edit.html",
                {
                    "trigger": self.trigger,
                    "title": self.title,
                    "actionurl": self.actionurl,
                }
                | self.forms,
            )


class TriggerCreate(TriggerBase):
    def setup(self, request):
        user = get_user(request)
        if not user.has_perm("tracet.add_trigger"):
            raise PermissionDenied("You do not have permission to create triggers.")

        super().setup(request)
        self.title = "Create a new trigger"
        self.actionurl = reverse("triggercreate")


class TriggerUpdate(TriggerBase):
    def setup(self, request, id):
        super().setup(request, id)

        user = get_user(request)
        if not user.has_perm("tracet.change_trigger", self.trigger):
            raise PermissionDenied("You do not have permission to edit this trigger.")

        self.title = f"Update {self.trigger.name}"
        self.actionurl = reverse("triggeredit", args=[id])


class TriggerDelete(View):
    def post(self, request, id):
        trigger = get_object_or_404(models.Trigger, id=id)

        user = get_user(request)
        if not user.has_perm("tracet.delete_trigger", trigger):
            raise PermissionDenied("You do not have permission to delete this trigger.")

        trigger.delete()

        messages.success(request, "Trigger was deleted")
        return HttpResponseRedirect(reverse("home"))


class TriggerView(View):
    def get(self, request, id):
        trigger = get_object_or_404(models.Trigger, id=id)
        telescope = trigger.get_telescope()

        user = get_user(request)
        if not user.has_perm("tracet.view_trigger"):
            raise PermissionDenied("You do not have permission to view this trigger.")

        paginator = Paginator(trigger.events.filter(disabled=False).order_by("-time"), 10)

        # If event_id is passed, set page number so that eventid displays
        events = None
        if eventid := request.GET.get("eventid"):
            events = next(
                (
                    page
                    for page in paginator
                    for event in page
                    if event.eventid == eventid
                ),
                None,
            )

        # Event is none if: 1. no eventid is passed or 2. eventid didn't match any event
        if events is None:
            events = paginator.get_page(request.GET.get("page", 1))

        for event in events:
            event.noticess = list(event.notices.order_by("created"))
            for notice in event.noticess:
                # Retrieve or create simulated decisions for each event on this page
                decision, _ = models.Decision.objects.get_or_create(
                    event=event,
                    created=notice.received,
                    source=models.Decision.Source.SIMULATED,
                )
                notice.decision = decision

                if telescope:
                    notice.pointings = telescope.get_pointings(event, notice.created)

            event.form = forms.EventTrigger(initial={"eventid": event.id})

            event.realdecisions = event.decisions.exclude(
                source=models.Decision.Source.SIMULATED
            ).order_by("created")

        return render(
            request,
            "tracet/trigger/get.html",
            {
                "user": user,
                "trigger": trigger,
                "conditions": trigger.get_conditions(timezone.now()),
                "events": events,
            },
        )

    def post(self, request, id):
        trigger = get_object_or_404(models.Trigger, id=id)

        user = get_user(request)
        if not user.has_perm("tracet.retrigger_trigger", trigger):
            raise PermissionDenied("You do not have permission to manually retrigger.")

        form = forms.EventTrigger(request.POST)

        if form.is_valid():
            event = trigger.events.get(id=form.cleaned_data["eventid"])
            models.Decision.objects.create(
                event=event, source=models.Decision.Source.MANUAL
            )

            return HttpResponseRedirect(event.get_absolute_url())
        else:
            return HttpResponse("Bad Request", status=400)
