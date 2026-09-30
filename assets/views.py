from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from django.views.decorators.http import require_POST

from accounts.decorators import organization_admin_required
from knowledge.retrieval import index_document

from .forms import DocumentForm, EquipmentForm
from .models import Document, Equipment


def _document_file_available(document):
    if not document.file.name:
        return False
    try:
        return document.file.storage.exists(document.file.name)
    except OSError:
        return False


@login_required
def equipment_list(request):
    equipments = Equipment.objects.filter(organization=request.user.organization)
    return render(request, "assets/equipment_list.html", {"equipments": equipments})


@login_required
def equipment_detail(request, pk):
    equipment = get_object_or_404(
        Equipment.objects.filter(organization=request.user.organization), pk=pk
    )
    return render(request, "assets/equipment_detail.html", {"equipment": equipment})


@organization_admin_required
@require_http_methods(["GET", "POST"])
def equipment_create(request):
    form = EquipmentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        equipment = form.save(commit=False)
        equipment.organization = request.user.organization
        equipment.save()
        messages.success(request, "Equipamento cadastrado.")
        return redirect("equipment_detail", pk=equipment.pk)
    return render(request, "assets/equipment_form.html", {"form": form})


@organization_admin_required
@require_http_methods(["GET", "POST"])
def equipment_update(request, pk):
    equipment = get_object_or_404(
        Equipment.objects.filter(organization=request.user.organization), pk=pk
    )
    form = EquipmentForm(request.POST or None, instance=equipment)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Equipamento atualizado.")
        return redirect("equipment_detail", pk=equipment.pk)
    return render(
        request,
        "assets/equipment_form.html",
        {"form": form, "equipment": equipment},
    )


@login_required
def document_list(request):
    documents = Document.objects.filter(
        organization=request.user.organization
    ).prefetch_related("equipments")
    return render(request, "assets/document_list.html", {"documents": documents})


@login_required
def document_detail(request, pk):
    document = get_object_or_404(
        Document.objects.filter(organization=request.user.organization).prefetch_related(
            "equipments"
        ),
        pk=pk,
    )
    return render(
        request,
        "assets/document_detail.html",
        {
            "document": document,
            "indexed_pages": document.sections.count(),
            "file_available": _document_file_available(document),
        },
    )


@login_required
def document_download(request, pk):
    document = get_object_or_404(
        Document.objects.filter(organization=request.user.organization), pk=pk
    )
    if not _document_file_available(document):
        messages.warning(
            request,
            "O arquivo físico deste documento está indisponível. O conteúdo já indexado continua pesquisável.",
        )
        return redirect("document_detail", pk=document.pk)
    try:
        stream = document.file.open("rb")
    except OSError:
        messages.warning(
            request,
            "O arquivo físico deste documento está indisponível. O conteúdo já indexado continua pesquisável.",
        )
        return redirect("document_detail", pk=document.pk)
    return FileResponse(
        stream,
        as_attachment=False,
        filename=document.file.name.rsplit("/", 1)[-1],
    )


@organization_admin_required
@require_http_methods(["GET", "POST"])
def document_create(request):
    form = DocumentForm(
        request.POST or None,
        request.FILES or None,
        organization=request.user.organization,
    )
    if request.method == "POST" and form.is_valid():
        document = form.save(commit=False)
        document.organization = request.user.organization
        document.save()
        form.save_m2m()
        result = index_document(document)
        if result.status == "INDEXED":
            messages.success(
                request,
                f"Documento enviado e {result.pages_indexed} página(s) indexada(s).",
            )
        elif result.status == "NO_TEXT":
            messages.warning(
                request,
                "Documento enviado, mas o PDF não possui texto extraível.",
            )
        elif result.status == "FAILED":
            messages.warning(
                request,
                "Documento enviado, mas a extração de texto falhou.",
            )
        else:
            messages.warning(
                request,
                "Documento enviado. Apenas PDFs textuais são indexados nesta fase.",
            )
        return redirect("document_detail", pk=document.pk)
    return render(request, "assets/document_form.html", {"form": form})


@organization_admin_required
@require_POST
def document_reindex(request, pk):
    document = get_object_or_404(
        Document.objects.filter(organization=request.user.organization),
        pk=pk,
    )
    if not _document_file_available(document):
        messages.warning(
            request,
            "Não é possível reindexar: o arquivo físico está indisponível. O conteúdo já indexado foi preservado.",
        )
        return redirect("document_detail", pk=document.pk)
    result = index_document(document)
    if result.status == "INDEXED":
        messages.success(
            request,
            f"{result.pages_indexed} página(s) indexada(s).",
        )
    elif result.status == "NO_TEXT":
        messages.warning(request, "O PDF não possui texto extraível.")
    elif result.status == "FAILED":
        messages.error(request, "Não foi possível extrair texto do PDF.")
    else:
        messages.warning(request, "Apenas PDFs textuais são suportados.")
    return redirect("document_detail", pk=document.pk)
