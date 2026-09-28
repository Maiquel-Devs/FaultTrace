from django import forms

from .models import Document, Equipment


class EquipmentForm(forms.ModelForm):
    class Meta:
        model = Equipment
        fields = (
            "name",
            "code",
            "manufacturer",
            "model",
            "location",
            "description",
            "status",
        )
        labels = {
            "name": "Nome",
            "code": "Código",
            "manufacturer": "Fabricante",
            "model": "Modelo",
            "location": "Localização",
            "description": "Descrição",
            "status": "Status",
        }
        widgets = {"description": forms.Textarea(attrs={"rows": 4})}


class DocumentForm(forms.ModelForm):
    class Meta:
        model = Document
        fields = ("title", "description", "file", "equipments")
        labels = {
            "title": "Título",
            "description": "Descrição",
            "file": "Arquivo",
            "equipments": "Equipamentos relacionados",
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "equipments": forms.CheckboxSelectMultiple(),
        }

    def __init__(self, *args, organization, **kwargs):
        super().__init__(*args, **kwargs)
        self.organization = organization
        self.fields["equipments"].queryset = Equipment.objects.filter(
            organization=organization
        )

    def clean_equipments(self):
        equipments = self.cleaned_data["equipments"]
        if equipments.exclude(organization=self.organization).exists():
            raise forms.ValidationError("Selecione apenas equipamentos da sua empresa.")
        return equipments
