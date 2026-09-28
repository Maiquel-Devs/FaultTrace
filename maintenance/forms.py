from django import forms

from assets.models import Equipment

from .models import Incident, Intervention


class IncidentForm(forms.ModelForm):
    class Meta:
        model = Incident
        fields = ("equipment", "description", "occurred_at")
        labels = {
            "equipment": "Equipamento",
            "description": "Descrição",
            "occurred_at": "Data e hora da ocorrência",
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "occurred_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }

    def __init__(self, *args, organization, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["equipment"].queryset = organization.equipments.filter(
            status=Equipment.Status.ACTIVE
        )


class InterventionForm(forms.ModelForm):
    class Meta:
        model = Intervention
        fields = ("action_taken", "confirmed_cause", "result")
        labels = {
            "action_taken": "Ação realizada",
            "confirmed_cause": "Causa confirmada",
            "result": "Resultado",
        }
        widgets = {
            "action_taken": forms.Textarea(attrs={"rows": 3}),
            "confirmed_cause": forms.Textarea(attrs={"rows": 3}),
            "result": forms.Textarea(attrs={"rows": 3}),
        }
