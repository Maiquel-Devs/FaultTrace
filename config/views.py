from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from accounts.decorators import organization_admin_required
from agent.errors import LLMConfigurationError, LLMProviderError
from agent.factory import get_active_provider

from .forms import AIConfigurationForm
from .models import AIConfiguration
from .services import AIConfigurationError, save_ai_configuration


@organization_admin_required
@require_http_methods(["GET", "POST"])
def ai_configuration(request):
    configuration = AIConfiguration.objects.filter(
        organization=request.user.organization
    ).first()
    form = AIConfigurationForm(
        request.POST or None,
        configuration=configuration,
    )
    if request.method == "POST" and form.is_valid():
        try:
            configuration = save_ai_configuration(
                organization=request.user.organization,
                **form.cleaned_data,
            )
            if request.POST.get("action") == "test":
                get_active_provider(request.user.organization).test_connection()
        except AIConfigurationError as error:
            form.add_error(None, str(error))
        except (LLMConfigurationError, LLMProviderError):
            messages.error(
                request,
                "Não foi possível conectar ao provider. Verifique a credencial e o modelo.",
            )
        else:
            if request.POST.get("action") == "test":
                messages.success(request, "Conexão realizada com sucesso.")
            else:
                messages.success(request, "Configuração de IA salva.")
            return redirect("ai_configuration")

    return render(
        request,
        "config/ai_configuration.html",
        {
            "form": form,
            "configuration": configuration,
        },
    )
