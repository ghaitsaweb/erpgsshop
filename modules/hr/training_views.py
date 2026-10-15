import csv

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Max, Min, Q
from django.forms import inlineformset_factory
from django.http import StreamingHttpResponse
from django.shortcuts import redirect, render

from .access import scope_hr_form, scope_hr_queryset
from .forms import TrainingConductForm, TrainingConductParticipantForm, TrainingConductScheduleForm
from .models import TrainingConduct, TrainingConductParticipant, TrainingConductSchedule


class _Echo:
    def write(self, value):
        return value


def _restrict_line_choices(formset, user):
    for line_form in formset.forms:
        scope_hr_form(line_form, user, "training-conducts")
    scope_hr_form(formset.empty_form, user, "training-conducts")


def _disable_form(form):
    for field in form.fields.values():
        field.disabled = True


def _disable_formset(formset):
    for line_form in formset.forms:
        _disable_form(line_form)
    _disable_form(formset.empty_form)


def _sync_summary(conduct):
    schedule_summary = conduct.schedule_lines.aggregate(date_start=Min("start_date"), date_end=Max("end_date"))
    participant_count = conduct.participant_lines.exclude(
        status=TrainingConductParticipant.Status.CANCELLED
    ).count()
    TrainingConduct.objects.filter(pk=conduct.pk).update(
        date_start=schedule_summary["date_start"],
        date_end=schedule_summary["date_end"],
        participant_count=participant_count,
    )
    conduct.date_start = schedule_summary["date_start"]
    conduct.date_end = schedule_summary["date_end"]
    conduct.participant_count = participant_count


def _export_csv(queryset):
    rows = (
        (
            item.reference,
            item.name,
            item.get_training_type_display(),
            item.get_training_source_display(),
            item.course.name if item.course_id else (item.program.name if item.program_id else ""),
            item.date_start or "",
            item.date_end or "",
            item.participant_count,
            item.get_status_display(),
        )
        for item in queryset.iterator(chunk_size=500)
    )
    writer = csv.writer(_Echo())

    def content():
        yield writer.writerow(["Reference", "Conduct Name", "Training Type", "Source", "Course / Program", "Start", "End", "Participants", "Status"])
        for row in rows:
            yield writer.writerow(row)

    response = StreamingHttpResponse(content(), content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="training-conduct.csv"'
    return response


def training_conduct_list(request, config, can_add, can_change):
    queryset = scope_hr_queryset(
        request.user,
        "training-conducts",
        TrainingConduct.objects.select_related("company", "course", "program", "pic"),
    )
    query = request.GET.get("q", "").strip()
    training_type = request.GET.get("type", "all")
    sort = request.GET.get("sort", "name")
    layout = request.GET.get("layout", "kanban")

    if query:
        queryset = queryset.filter(
            Q(reference__icontains=query)
            | Q(name__icontains=query)
            | Q(course__code__icontains=query)
            | Q(course__name__icontains=query)
            | Q(program__code__icontains=query)
            | Q(program__name__icontains=query)
            | Q(pic__name__icontains=query)
        )
    if training_type in {TrainingConduct.TrainingType.INTERNAL, TrainingConduct.TrainingType.PUBLIC}:
        queryset = queryset.filter(training_type=training_type)
    else:
        training_type = "all"

    orderings = {
        "name": ("name", "reference"),
        "start": ("date_start", "name"),
        "newest": ("-created_at", "name"),
    }
    queryset = queryset.annotate(
        visible_participants=Count(
            "participant_lines",
            filter=~Q(participant_lines__status=TrainingConductParticipant.Status.CANCELLED),
            distinct=True,
        )
    ).order_by(
        *orderings.get(sort, orderings["name"])
    )
    if sort not in orderings:
        sort = "name"
    if request.GET.get("export") == "csv":
        return _export_csv(queryset)

    page = Paginator(queryset, 40).get_page(request.GET.get("page"))
    records = list(page.object_list)
    context = {
        "config": config,
        "entity": "training-conducts",
        "page": page,
        "records": records,
        "internal_records": [item for item in records if item.training_type == TrainingConduct.TrainingType.INTERNAL],
        "public_records": [item for item in records if item.training_type == TrainingConduct.TrainingType.PUBLIC],
        "internal_count": sum(item.training_type == TrainingConduct.TrainingType.INTERNAL for item in records),
        "public_count": sum(item.training_type == TrainingConduct.TrainingType.PUBLIC for item in records),
        "query": query,
        "type_filter": training_type,
        "sort": sort,
        "layout": layout if layout in {"kanban", "list"} else "kanban",
        "can_add": can_add,
        "can_change": can_change,
        "access_scope_missing": not request.user.is_superuser and not request.user.employee_profiles.exists(),
    }
    return render(request, "hr/training_conduct_list.html", context)


def training_conduct_form(request, config, instance=None, can_change=True):
    creating = instance is None
    if creating:
        instance = TrainingConduct()
    has_schedule = bool(instance.pk and instance.schedule_lines.exists())
    setup_editable = can_change and (creating or instance.status == TrainingConduct.Status.DRAFT)
    participants_editable = can_change and (creating or instance.status != TrainingConduct.Status.CANCELLED)

    ScheduleFormSet = inlineformset_factory(
        TrainingConduct,
        TrainingConductSchedule,
        form=TrainingConductScheduleForm,
        extra=0 if has_schedule else 1,
        can_delete=True,
        min_num=1,
        validate_min=True,
    )
    ParticipantFormSet = inlineformset_factory(
        TrainingConduct,
        TrainingConductParticipant,
        form=TrainingConductParticipantForm,
        extra=1 if creating else 0,
        can_delete=True,
    )

    if request.method == "POST" and not can_change:
        messages.error(request, "You have read-only access to this Training Conduct.")
        return redirect("entity_edit", entity="training-conducts", pk=instance.pk)
    if request.method == "POST" and instance.status == TrainingConduct.Status.CANCELLED:
        messages.error(request, "A cancelled Training Conduct is locked.")
        return redirect("entity_edit", entity="training-conducts", pk=instance.pk)

    form = TrainingConductForm(request.POST or None, instance=instance)
    schedule_formset = ScheduleFormSet(request.POST or None, instance=instance, prefix="schedule_lines")
    participant_formset = ParticipantFormSet(request.POST or None, instance=instance, prefix="participant_lines")
    scope_hr_form(form, request.user, "training-conducts")
    _restrict_line_choices(schedule_formset, request.user)
    _restrict_line_choices(participant_formset, request.user)

    if not setup_editable:
        _disable_form(form)
        _disable_formset(schedule_formset)
    if not participants_editable:
        _disable_formset(participant_formset)

    if request.method == "POST" and form.is_valid() and schedule_formset.is_valid() and participant_formset.is_valid():
        with transaction.atomic():
            saved = form.save()
            schedule_formset.instance = saved
            schedule_formset.save()
            participant_formset.instance = saved
            participant_formset.save()
            _sync_summary(saved)
        messages.success(request, "Training Conduct saved successfully.")
        return redirect("entity_edit", entity="training-conducts", pk=saved.pk)

    current_status = None if creating else instance.status
    workflow_actions = [
        {"key": key, **definition}
        for key, definition in config.get("workflow", {}).items()
        if can_change and current_status in definition["from"]
    ]
    context = {
        "config": config,
        "entity": "training-conducts",
        "mode": "Create" if creating else "Edit",
        "object": None if creating else instance,
        "form": form,
        "schedule_formset": schedule_formset,
        "participant_formset": participant_formset,
        "setup_editable": setup_editable,
        "participants_editable": participants_editable,
        "workflow_actions": workflow_actions,
        "current_status": current_status,
        "status_choices": TrainingConduct.Status.choices,
    }
    return render(request, "hr/training_conduct_form.html", context)
