from django.contrib import admin

from .models import DocumentSection, Evidence, Fact, Hypothesis, HypothesisEvidence


@admin.register(Fact)
class FactAdmin(admin.ModelAdmin):
    list_display = ("id", "investigation", "source_type", "created_by", "created_at")
    list_filter = ("source_type", "investigation__incident__organization")


@admin.register(Evidence)
class EvidenceAdmin(admin.ModelAdmin):
    list_display = ("id", "investigation", "source_type", "created_by", "created_at")
    list_filter = ("source_type", "investigation__incident__organization")


class HypothesisEvidenceInline(admin.TabularInline):
    model = HypothesisEvidence
    extra = 0


@admin.register(Hypothesis)
class HypothesisAdmin(admin.ModelAdmin):
    list_display = ("id", "investigation", "status", "created_by", "updated_at")
    list_filter = ("status", "investigation__incident__organization")
    inlines = (HypothesisEvidenceInline,)


@admin.register(HypothesisEvidence)
class HypothesisEvidenceAdmin(admin.ModelAdmin):
    list_display = ("hypothesis", "evidence", "relation", "created_at")
    list_filter = ("relation", "hypothesis__investigation__incident__organization")


@admin.register(DocumentSection)
class DocumentSectionAdmin(admin.ModelAdmin):
    list_display = ("document", "page_number", "created_at")
    list_filter = ("document__organization",)
    search_fields = ("document__title", "content")
