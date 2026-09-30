from django import forms


class AgentQuestionForm(forms.Form):
    question = forms.CharField(
        label="O que devemos investigar agora?",
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
