from django import forms

from .models import AIConfiguration


DEFAULT_MODELS = {
    AIConfiguration.Provider.MISTRAL: "mistral-small-latest",
    AIConfiguration.Provider.OPENAI: "gpt-5-mini",
    AIConfiguration.Provider.GEMINI: "gemini-3.8-flash",
    AIConfiguration.Provider.ANTHROPIC: "claude-sonnet-5",
    AIConfiguration.Provider.GROQ: "openai/gpt-oss-120b",
}


class AIConfigurationForm(forms.Form):
    provider = forms.ChoiceField(
        choices=AIConfiguration.Provider.choices,
        label="Provider",
    )
    model = forms.CharField(max_length=100, label="Modelo")
    api_key = forms.CharField(
        required=False,
        label="API Key",
        strip=True,
        widget=forms.PasswordInput(render_value=False),
        help_text="Deixe em branco para manter a chave atual.",
    )
    is_active = forms.BooleanField(required=False, initial=True, label="Ativa")

    def __init__(self, *args, configuration=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.configuration = configuration
        if not self.is_bound:
            if configuration:
                self.initial.update(
                    {
                        "provider": configuration.provider,
                        "model": configuration.model,
                        "is_active": configuration.is_active,
                    }
                )
                self.fields["api_key"].widget.attrs["placeholder"] = (
                    configuration.masked_api_key
                )
            else:
                self.initial.update(
                    {
                        "provider": AIConfiguration.Provider.MISTRAL,
                        "model": DEFAULT_MODELS[AIConfiguration.Provider.MISTRAL],
                    }
                )
                self.fields["api_key"].help_text = "Informe a API key do provider."

    def clean_model(self):
        model = self.cleaned_data["model"].strip()
        if not model:
            raise forms.ValidationError("Informe o modelo.")
        return model

    def clean(self):
        cleaned_data = super().clean()
        provider = cleaned_data.get("provider")
        api_key = cleaned_data.get("api_key")
        if self.configuration is None and not api_key:
            self.add_error("api_key", "Informe uma API key.")
        elif (
            self.configuration
            and provider != self.configuration.provider
            and not api_key
        ):
            self.add_error(
                "api_key",
                "Informe uma nova API key ao trocar de provider.",
            )
        return cleaned_data
