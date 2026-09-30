from django import forms

from accounts.models import User
from assets.models import Document
from maintenance.models import Incident

from .models import Evidence, Fact, Hypothesis, HypothesisEvidence


class FactForm(forms.ModelForm):
    class Meta:
        model = Fact
        fields = ("content", "source_type")
        labels = {"content": "Fato", "source_type": "Origem"}
        widgets = {"content": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, investigation, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.investigation = investigation
        self.instance.created_by = user
        choices = list(self.fields["source_type"].choices)
        choices[0] = ("", "Selecione a origem")
        self.fields["source_type"].choices = choices


class EvidenceForm(forms.ModelForm):
    class Meta:
        model = Evidence
        fields = (
            "content",
            "source_type",
            "source_document",
            "source_incident",
            "source_technician",
            "source_reference",
        )
        labels = {
            "content": "Evidência",
            "source_type": "Tipo de fonte",
            "source_document": "Documento",
            "source_incident": "Ocorrência anterior",
            "source_technician": "Técnico que forneceu a informação",
            "source_reference": "Referência/localização da fonte",
        }
        help_texts = {
            "source_reference": "Ex.: Manual AX-200 — seção 7.2 ou página 34.",
        }
        widgets = {"content": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, investigation, **kwargs):
        super().__init__(*args, **kwargs)
        choices = list(self.fields["source_type"].choices)
        choices[0] = ("", "Selecione o tipo de fonte")
        self.fields["source_type"].choices = choices
        organization = investigation.incident.organization
        self.fields["source_document"].queryset = Document.objects.filter(
            organization=organization
        )
        self.fields["source_incident"].queryset = Incident.objects.filter(
            organization=organization
        ).exclude(pk=investigation.incident_id)
        self.fields["source_technician"].queryset = User.objects.filter(
            organization=organization,
            role=User.Role.TECHNICIAN,
        )
        self.fields["source_document"].empty_label = "Selecione o documento"
        self.fields["source_incident"].empty_label = "Selecione a ocorrência anterior"
        self.fields["source_technician"].empty_label = "Selecione o técnico"


class HypothesisForm(forms.ModelForm):
    class Meta:
        model = Hypothesis
        fields = ("description",)
        labels = {"description": "Hipótese"}
        widgets = {"description": forms.Textarea(attrs={"rows": 4})}


class HypothesisStatusForm(forms.ModelForm):
    class Meta:
        model = Hypothesis
        fields = ("status",)
        labels = {"status": "Status"}


class HypothesisEvidenceForm(forms.Form):
    evidence = forms.ModelChoiceField(queryset=Evidence.objects.none(), label="Evidência")
    relation = forms.ChoiceField(
        choices=HypothesisEvidence.Relation.choices,
        label="Relação",
    )

    def __init__(self, *args, investigation, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["evidence"].queryset = investigation.evidence.all()
