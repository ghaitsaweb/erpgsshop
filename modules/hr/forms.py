from django import forms

from core.forms import StyledModelForm

from .models import TrainingConduct, TrainingConductParticipant, TrainingConductSchedule


class TrainingConductForm(StyledModelForm):
    class Meta:
        model = TrainingConduct
        fields = [
            "company",
            "name",
            "training_type",
            "training_source",
            "course",
            "program",
            "pic",
            "location",
            "use_minimum_participant",
            "minimum_participants",
            "use_maximum_participant",
            "maximum_participants",
            "active",
        ]
        labels = {
            "name": "Conduct Name",
            "course": "Training Course",
            "program": "Training Program",
            "pic": "PIC",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["company"].required = True
        self.fields["name"].required = True
        self.fields["pic"].required = True
        self.fields["training_type"].widget = forms.RadioSelect(
            choices=TrainingConduct.TrainingType.choices,
            attrs={"class": "conduct-type-options"},
        )

    def clean(self):
        cleaned = super().clean()
        source = cleaned.get("training_source")
        if source == TrainingConduct.TrainingSource.COURSE:
            cleaned["program"] = None
            if not cleaned.get("course"):
                self.add_error("course", "Select a Training Course.")
        elif source == TrainingConduct.TrainingSource.PROGRAM:
            cleaned["course"] = None
            if not cleaned.get("program"):
                self.add_error("program", "Select a Training Program.")
        return cleaned


class TrainingConductScheduleForm(StyledModelForm):
    class Meta:
        model = TrainingConductSchedule
        fields = ["sequence", "course", "trainer", "notes", "start_date", "end_date", "start_time", "end_time"]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
            "start_time": forms.TimeInput(attrs={"type": "time"}),
            "end_time": forms.TimeInput(attrs={"type": "time"}),
        }


class TrainingConductParticipantForm(StyledModelForm):
    class Meta:
        model = TrainingConductParticipant
        fields = ["employee", "status", "notes"]
