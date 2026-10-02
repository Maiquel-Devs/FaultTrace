import json
from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models import Organization, User
from assets.models import Document, Equipment
from knowledge import retrieval
from knowledge.models import (
    DocumentSection,
    Evidence,
    Fact,
    Hypothesis,
    HypothesisEvidence,
)
from maintenance.models import Incident, Investigation, Intervention

from .investigation_context import SourceCandidate, SourceKind
from .investigation_context_adapter import (
    PROJECTION_POLICY,
    InvestigationContextAuthorizationError,
    InvestigationContextIntegrityError,
    InvestigationRootNotFound,
    build_authorized_investigation_context,
    expand_with_documentation,
    expand_with_equipment_history,
    expand_with_similar_incidents,
)
from .investigation_contracts import (
    ReferenceContractError,
    parse_investigation_result_json,
)


class InvestigationContextAdapterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        cls.organization = Organization.objects.create(name="Planta de teste")
        cls.external_organization = Organization.objects.create(
            name="Planta externa"
        )
        cls.technician = User.objects.create_user(
            username="adapter-tech",
            password="test-password",
            organization=cls.organization,
            role=User.Role.TECHNICIAN,
        )
        cls.external_technician = User.objects.create_user(
            username="external-adapter-tech",
            password="test-password",
            organization=cls.external_organization,
            role=User.Role.TECHNICIAN,
        )
        cls.equipment_a = Equipment.objects.create(
            organization=cls.organization,
            name="Unidade de teste A",
            code="EQ-A",
            manufacturer="Fabricante comum",
            model="Modelo comum",
            location="Área A",
            description="Equipamento usado pela investigação A.",
        )
        cls.equipment_b = Equipment.objects.create(
            organization=cls.organization,
            name="Unidade de teste B",
            code="EQ-B",
            manufacturer="Fabricante diferente",
            model="Modelo diferente",
            location="Área B",
            description="DADO-EXCLUSIVO-EQUIPAMENTO-B",
        )
        cls.external_equipment = Equipment.objects.create(
            organization=cls.external_organization,
            name="Unidade externa",
            code="EQ-X",
            description="DADO-EXTERNO-EQUIPAMENTO",
        )
        cls.incident_a = Incident.objects.create(
            organization=cls.organization,
            equipment=cls.equipment_a,
            description="Falha atual exclusiva A.",
            reported_by=cls.technician,
            occurred_at=now,
        )
        cls.incident_b = Incident.objects.create(
            organization=cls.organization,
            equipment=cls.equipment_b,
            description="FALHA-EXCLUSIVA-INVESTIGACAO-B",
            reported_by=cls.technician,
            occurred_at=now - timedelta(minutes=1),
        )
        cls.external_incident = Incident.objects.create(
            organization=cls.external_organization,
            equipment=cls.external_equipment,
            description="FALHA-SIGILOSA-EXTERNA",
            reported_by=cls.external_technician,
            occurred_at=now - timedelta(minutes=2),
        )
        cls.investigation_a = Investigation.objects.create(
            incident=cls.incident_a,
            technician=cls.technician,
        )
        cls.investigation_b = Investigation.objects.create(
            incident=cls.incident_b,
            technician=cls.technician,
        )
        cls.external_investigation = Investigation.objects.create(
            incident=cls.external_incident,
            technician=cls.external_technician,
        )
        cls.fact_a = Fact.objects.create(
            investigation=cls.investigation_a,
            content="FATO-EXCLUSIVO-A",
            source_type=Fact.SourceType.TECHNICIAN,
            created_by=cls.technician,
        )
        cls.fact_b = Fact.objects.create(
            investigation=cls.investigation_b,
            content="FATO-EXCLUSIVO-B",
            source_type=Fact.SourceType.TECHNICIAN,
            created_by=cls.technician,
        )
        cls.evidence_a_support = Evidence.objects.create(
            investigation=cls.investigation_a,
            content="EVIDENCIA-SUPORTE-A",
            source_type=Evidence.SourceType.TECHNICIAN,
            source_technician=cls.technician,
            source_reference="Medição independente",
            created_by=cls.technician,
        )
        cls.evidence_a_oppose = Evidence.objects.create(
            investigation=cls.investigation_a,
            content="EVIDENCIA-OPOSICAO-A",
            source_type=Evidence.SourceType.TECHNICIAN,
            source_technician=cls.technician,
            source_reference="Inspeção local",
            created_by=cls.technician,
        )
        cls.evidence_b = Evidence.objects.create(
            investigation=cls.investigation_b,
            content="EVIDENCIA-EXCLUSIVA-B",
            source_type=Evidence.SourceType.TECHNICIAN,
            source_technician=cls.technician,
            created_by=cls.technician,
        )
        cls.hypothesis_a = Hypothesis.objects.create(
            investigation=cls.investigation_a,
            description="HIPOTESE-REGISTRADA-A",
            status=Hypothesis.Status.WEAKENED,
            created_by=cls.technician,
        )
        HypothesisEvidence.objects.create(
            hypothesis=cls.hypothesis_a,
            evidence=cls.evidence_a_support,
            relation=HypothesisEvidence.Relation.SUPPORTS,
        )
        HypothesisEvidence.objects.create(
            hypothesis=cls.hypothesis_a,
            evidence=cls.evidence_a_oppose,
            relation=HypothesisEvidence.Relation.CONTRADICTS,
        )
        cls.hypothesis_b = Hypothesis.objects.create(
            investigation=cls.investigation_b,
            description="HIPOTESE-REGISTRADA-B",
            created_by=cls.technician,
        )
        HypothesisEvidence.objects.create(
            hypothesis=cls.hypothesis_b,
            evidence=cls.evidence_b,
            relation=HypothesisEvidence.Relation.SUPPORTS,
        )

        cls.history_a = Incident.objects.create(
            organization=cls.organization,
            equipment=cls.equipment_a,
            description="HISTORICO-AUTORIZADO-A",
            status=Incident.Status.RESOLVED,
            reported_by=cls.technician,
            occurred_at=now - timedelta(days=5),
        )
        cls.history_a_intervention = Intervention.objects.create(
            incident=cls.history_a,
            technician=cls.technician,
            action_taken="INTERVENCAO-HISTORICA-A",
            confirmed_cause="Causa histórica fictícia",
            result="Operação restabelecida",
        )
        cls.unretrieved_history_b = Incident.objects.create(
            organization=cls.organization,
            equipment=cls.equipment_b,
            description="HISTORICO-NAO-RECUPERADO-B",
            reported_by=cls.technician,
            occurred_at=now - timedelta(days=8),
        )

        cls.document_a = Document.objects.create(
            organization=cls.organization,
            title="Manual autorizado A",
            description="Documento relacionado ao equipamento A.",
            file="documents/manual-a.pdf",
        )
        cls.document_a.equipments.add(cls.equipment_a)
        cls.section_a = DocumentSection.objects.create(
            document=cls.document_a,
            page_number=7,
            content="ALERTA-TERMICO-A e procedimento de teste.",
        )
        cls.document_b = Document.objects.create(
            organization=cls.organization,
            title="Manual exclusivo B",
            description="Documento relacionado ao equipamento B.",
            file="documents/manual-b.pdf",
        )
        cls.document_b.equipments.add(cls.equipment_b)
        cls.section_b = DocumentSection.objects.create(
            document=cls.document_b,
            page_number=9,
            content="ALERTA-TERMICO-B conteúdo não autorizado para A.",
        )
        cls.external_document = Document.objects.create(
            organization=cls.external_organization,
            title="Manual externo",
            file="documents/manual-x.pdf",
        )
        cls.external_document.equipments.add(cls.external_equipment)
        cls.external_section = DocumentSection.objects.create(
            document=cls.external_document,
            page_number=3,
            content="ALERTA-TERMICO-EXTERNO",
        )

    def _producer_text(self, context):
        return json.dumps(context.to_producer_dict(), ensure_ascii=False)

    def _source(self, context, kind):
        return next(source for source in context.sources if source.kind is kind)

    def test_initial_context_contains_only_investigation_a(self):
        context = build_authorized_investigation_context(incident=self.incident_a)
        producer = self._producer_text(context)

        self.assertIn("Falha atual exclusiva A.", producer)
        self.assertIn("FATO-EXCLUSIVO-A", producer)
        self.assertIn("EVIDENCIA-SUPORTE-A", producer)
        self.assertIn("HIPOTESE-REGISTRADA-A", producer)
        for forbidden in (
            "FALHA-EXCLUSIVA-INVESTIGACAO-B",
            "DADO-EXCLUSIVO-EQUIPAMENTO-B",
            "FATO-EXCLUSIVO-B",
            "EVIDENCIA-EXCLUSIVA-B",
            "HIPOTESE-REGISTRADA-B",
            "FALHA-SIGILOSA-EXTERNA",
            "HISTORICO-AUTORIZADO-A",
            "ALERTA-TERMICO-A",
        ):
            self.assertNotIn(forbidden, producer)

    def test_isolation_is_bidirectional(self):
        context_b = build_authorized_investigation_context(
            incident=self.incident_b
        )
        producer = self._producer_text(context_b)

        self.assertIn("FALHA-EXCLUSIVA-INVESTIGACAO-B", producer)
        self.assertIn("FATO-EXCLUSIVO-B", producer)
        self.assertNotIn("Falha atual exclusiva A.", producer)
        self.assertNotIn("FATO-EXCLUSIVO-A", producer)
        self.assertNotIn("EVIDENCIA-SUPORTE-A", producer)

    def test_projection_uses_explicit_allow_lists(self):
        context = build_authorized_investigation_context(incident=self.incident_a)
        producer_payload = context.to_producer_dict()
        for source in producer_payload["sources"]:
            self.assertEqual(set(source), {"ref", "kind", "content"})
        for hypothesis in producer_payload["registered_hypotheses"]:
            self.assertEqual(
                set(hypothesis),
                {
                    "ref",
                    "statement",
                    "observed_status",
                    "supporting_source_refs",
                    "opposing_source_refs",
                },
            )
        by_kind = {
            source.kind: set(json.loads(source.authorized_content))
            for source in context.sources
        }
        for kind, fields in by_kind.items():
            self.assertEqual(fields, set(PROJECTION_POLICY.fields_by_kind[kind]))

        serialized = self._producer_text(context)
        for excluded in (
            '"id"',
            '"pk"',
            '"organization"',
            '"reported_by"',
            '"technician"',
            '"created_by"',
            '"created_at"',
            '"updated_at"',
            '"file"',
            "internal_locator",
            "display_label",
        ):
            self.assertNotIn(excluded, serialized)

    def test_registered_hypothesis_snapshot_and_relations_are_projected(self):
        context = build_authorized_investigation_context(incident=self.incident_a)

        self.assertEqual(len(context.registered_hypotheses), 1)
        snapshot = context.registered_hypotheses[0]
        self.assertEqual(snapshot.ref, "ctx_hypothesis_1")
        self.assertEqual(snapshot.statement, "HIPOTESE-REGISTRADA-A")
        self.assertEqual(snapshot.observed_status, Hypothesis.Status.WEAKENED)
        self.assertEqual(len(snapshot.supporting_source_refs), 1)
        self.assertEqual(len(snapshot.opposing_source_refs), 1)
        referenced = {
            context.resolve_source(ref).kind
            for ref in (
                *snapshot.supporting_source_refs,
                *snapshot.opposing_source_refs,
            )
        }
        self.assertEqual(referenced, {SourceKind.EVIDENCE})
        self.assertIn(snapshot.ref, context.registered_hypothesis_catalog)

    def test_initial_context_does_not_expose_attached_documents(self):
        context = build_authorized_investigation_context(incident=self.incident_a)
        kinds = {source.kind for source in context.sources}

        self.assertNotIn(SourceKind.DOCUMENT, kinds)
        self.assertNotIn(SourceKind.DOCUMENT_SECTION, kinds)
        self.assertNotIn("Manual autorizado A", self._producer_text(context))

    def test_equipment_history_enters_only_after_allowed_retrieval(self):
        context = build_authorized_investigation_context(incident=self.incident_a)
        self.assertNotIn("HISTORICO-AUTORIZADO-A", self._producer_text(context))

        expanded = expand_with_equipment_history(
            context,
            incident=self.incident_a,
        )
        producer = self._producer_text(expanded)

        self.assertIn("HISTORICO-AUTORIZADO-A", producer)
        self.assertIn("INTERVENCAO-HISTORICA-A", producer)
        self.assertNotIn("HISTORICO-NAO-RECUPERADO-B", producer)

    def test_similar_incident_may_use_another_equipment_only_if_retrieved(self):
        context = build_authorized_investigation_context(incident=self.incident_a)
        expanded = expand_with_similar_incidents(
            context,
            incident=self.incident_a,
            query="HISTORICO-NAO-RECUPERADO-B",
        )

        self.assertIn(
            "HISTORICO-NAO-RECUPERADO-B",
            self._producer_text(expanded),
        )

    def test_document_section_enters_only_after_equipment_scoped_retrieval(self):
        context = build_authorized_investigation_context(incident=self.incident_a)
        expanded = expand_with_documentation(
            context,
            incident=self.incident_a,
            query="ALERTA-TERMICO",
        )
        producer = self._producer_text(expanded)

        self.assertIn("ALERTA-TERMICO-A", producer)
        self.assertIn("Manual autorizado A", producer)
        self.assertNotIn("ALERTA-TERMICO-B", producer)
        self.assertNotIn("ALERTA-TERMICO-EXTERNO", producer)
        self.assertEqual(
            sum(
                source.kind is SourceKind.DOCUMENT_SECTION
                for source in expanded.sources
            ),
            1,
        )

    def test_forged_document_retrieval_result_is_rejected(self):
        context = build_authorized_investigation_context(incident=self.incident_a)
        forged = retrieval.DocumentSearchResult(
            document_id=self.document_b.pk,
            document_title=self.document_b.title,
            page=self.section_b.page_number,
            content=self.section_b.content,
            reference=self.section_b.reference,
        )
        with patch(
            "agent.investigation_context_adapter.retrieval.search_documentation",
            return_value=(forged,),
        ):
            with self.assertRaises(InvestigationContextAuthorizationError):
                expand_with_documentation(
                    context,
                    incident=self.incident_a,
                    query="ALERTA-TERMICO",
                )

    def test_history_from_another_organization_is_rejected(self):
        context = build_authorized_investigation_context(incident=self.incident_a)
        forged = retrieval.IncidentSummary(
            incident_id=self.external_incident.pk,
            equipment_id=self.external_equipment.pk,
            equipment_code=self.external_equipment.code,
            description=self.external_incident.description,
            status=self.external_incident.status,
            occurred_at=self.external_incident.occurred_at.isoformat(),
            interventions=(),
            reference="external",
        )
        with patch(
            "agent.investigation_context_adapter.retrieval.search_equipment_history",
            return_value=(forged,),
        ):
            with self.assertRaises(InvestigationContextAuthorizationError):
                expand_with_equipment_history(
                    context,
                    incident=self.incident_a,
                )

    def test_context_cannot_be_expanded_using_another_root(self):
        context_a = build_authorized_investigation_context(incident=self.incident_a)

        with self.assertRaises(InvestigationContextAuthorizationError):
            expand_with_equipment_history(context_a, incident=self.incident_b)

    def test_arbitrary_source_candidate_is_not_an_adapter_input(self):
        context = build_authorized_investigation_context(incident=self.incident_a)
        foreign = SourceCandidate(
            internal_locator=f"fact:{self.fact_b.pk}",
            kind=SourceKind.FACT,
            authorized_content='{"content":"FATO-EXCLUSIVO-B"}',
            display_label="foreign",
        )

        with self.assertRaises(TypeError):
            expand_with_documentation(
                context,
                incident=self.incident_a,
                query="test",
                sources=(foreign,),
            )

    def test_incremental_expansion_preserves_all_previously_issued_refs(self):
        initial = build_authorized_investigation_context(incident=self.incident_a)
        initial_mapping = {
            source.internal_locator: source.ref for source in initial.sources
        }
        with_history = expand_with_equipment_history(
            initial,
            incident=self.incident_a,
        )
        with_document = expand_with_documentation(
            with_history,
            incident=self.incident_a,
            query="ALERTA-TERMICO",
        )
        final_mapping = {
            source.internal_locator: source.ref for source in with_document.sources
        }

        self.assertEqual(
            {key: final_mapping[key] for key in initial_mapping},
            initial_mapping,
        )
        new_numbers = [
            int(ref.removeprefix("src_"))
            for locator, ref in final_mapping.items()
            if locator not in initial_mapping
        ]
        self.assertTrue(new_numbers)
        self.assertGreater(min(new_numbers), len(initial_mapping))

    def test_repeating_same_tool_expansion_is_idempotent(self):
        initial = build_authorized_investigation_context(incident=self.incident_a)
        once = expand_with_documentation(
            initial,
            incident=self.incident_a,
            query="ALERTA-TERMICO",
        )
        twice = expand_with_documentation(
            once,
            incident=self.incident_a,
            query="ALERTA-TERMICO",
        )

        self.assertEqual(once, twice)

    def test_building_same_root_from_scratch_is_deterministic(self):
        first = build_authorized_investigation_context(incident=self.incident_a)
        second = build_authorized_investigation_context(incident=self.incident_a)

        self.assertEqual(first, second)

    def test_current_incident_is_an_authorized_problem_source(self):
        context = build_authorized_investigation_context(incident=self.incident_a)
        problem_source = self._source(context, SourceKind.CURRENT_INCIDENT)

        payload = self._valid_payload(context, problem_source.ref)
        parsed = parse_investigation_result_json(
            json.dumps(payload),
            source_catalog=context.source_catalog,
            registered_hypothesis_catalog=context.registered_hypothesis_catalog,
        )

        self.assertEqual(parsed.problem.source_refs, (problem_source.ref,))

    def test_orm_to_contract_pipeline_without_ai(self):
        context = build_authorized_investigation_context(incident=self.incident_a)
        context = expand_with_documentation(
            context,
            incident=self.incident_a,
            query="ALERTA-TERMICO",
        )
        problem_ref = self._source(context, SourceKind.CURRENT_INCIDENT).ref
        payload = self._valid_payload(context, problem_ref)
        raw_json = json.dumps(payload, ensure_ascii=False)

        result = parse_investigation_result_json(
            raw_json,
            source_catalog=context.source_catalog,
            registered_hypothesis_catalog=context.registered_hypothesis_catalog,
        )

        self.assertEqual(result.schema_version, "1.0")
        self.assertEqual(result.problem.source_refs, (problem_ref,))
        self.assertIn("sources", context.to_producer_dict())

    def test_source_ref_not_owned_by_context_is_rejected(self):
        context_a = build_authorized_investigation_context(incident=self.incident_a)
        for index in range(3):
            Fact.objects.create(
                investigation=self.investigation_b,
                content=f"Fato B adicional {index}",
                source_type=Fact.SourceType.SYSTEM,
            )
        context_b = build_authorized_investigation_context(incident=self.incident_b)
        context_b = expand_with_documentation(
            context_b,
            incident=self.incident_b,
            query="ALERTA-TERMICO-B",
        )
        foreign_ref = context_b.sources[-1].ref
        self.assertNotIn(foreign_ref, context_a.source_catalog)
        payload = self._valid_payload(context_a, foreign_ref)

        with self.assertRaises(ReferenceContractError):
            parse_investigation_result_json(
                json.dumps(payload),
                source_catalog=context_a.source_catalog,
                registered_hypothesis_catalog=(
                    context_a.registered_hypothesis_catalog
                ),
            )

    def test_pk_and_display_label_cannot_be_used_as_source_refs(self):
        context = build_authorized_investigation_context(incident=self.incident_a)
        current = self._source(context, SourceKind.CURRENT_INCIDENT)
        for invalid_ref in (str(self.incident_a.pk), current.display_label):
            with self.subTest(invalid_ref=invalid_ref):
                payload = self._valid_payload(context, invalid_ref)
                with self.assertRaises(ReferenceContractError):
                    parse_investigation_result_json(
                        json.dumps(payload),
                        source_catalog=context.source_catalog,
                        registered_hypothesis_catalog=(
                            context.registered_hypothesis_catalog
                        ),
                    )

    def test_build_is_read_only_for_domain_records_and_relations(self):
        before = self._domain_snapshot()

        context = build_authorized_investigation_context(incident=self.incident_a)
        expand_with_equipment_history(context, incident=self.incident_a)

        self.assertEqual(self._domain_snapshot(), before)

    def test_empty_facts_evidence_and_hypotheses_are_valid(self):
        empty_incident = Incident.objects.create(
            organization=self.organization,
            equipment=self.equipment_a,
            description="Contexto mínimo.",
            reported_by=self.technician,
        )
        Investigation.objects.create(
            incident=empty_incident,
            technician=self.technician,
        )

        context = build_authorized_investigation_context(incident=empty_incident)

        self.assertEqual(context.registered_hypotheses, ())
        self.assertFalse(
            any(
                source.kind in {SourceKind.FACT, SourceKind.EVIDENCE}
                for source in context.sources
            )
        )

    def test_missing_or_unsaved_root_and_missing_investigation_are_controlled(self):
        unsaved = Incident()
        without_investigation = Incident.objects.create(
            organization=self.organization,
            equipment=self.equipment_a,
            description="Ainda sem investigação.",
            reported_by=self.technician,
        )

        for root in (unsaved, without_investigation):
            with self.subTest(root=root):
                with self.assertRaises(InvestigationRootNotFound):
                    build_authorized_investigation_context(incident=root)

    def test_inconsistent_equipment_organization_is_rejected(self):
        inconsistent = Incident.objects.create(
            organization=self.organization,
            equipment=self.external_equipment,
            description="Relação inconsistente criada sem full_clean.",
            reported_by=self.technician,
        )
        Investigation.objects.create(
            incident=inconsistent,
            technician=self.technician,
        )

        with self.assertRaises(InvestigationContextIntegrityError):
            build_authorized_investigation_context(incident=inconsistent)

    def test_evidence_with_external_provenance_is_rejected(self):
        Evidence.objects.create(
            investigation=self.investigation_a,
            content="Proveniência inconsistente.",
            source_type=Evidence.SourceType.TECHNICIAN,
            source_technician=self.external_technician,
            created_by=self.technician,
        )

        with self.assertRaises(InvestigationContextIntegrityError):
            build_authorized_investigation_context(incident=self.incident_a)

    def test_hypothesis_cannot_reference_evidence_from_other_investigation(self):
        HypothesisEvidence.objects.create(
            hypothesis=self.hypothesis_a,
            evidence=self.evidence_b,
            relation=HypothesisEvidence.Relation.SUPPORTS,
        )

        with self.assertRaises(InvestigationContextIntegrityError):
            build_authorized_investigation_context(incident=self.incident_a)

    def test_query_count_is_bounded_for_initial_graph(self):
        with CaptureQueriesContext(connection) as captured:
            build_authorized_investigation_context(incident=self.incident_a)

        self.assertEqual(len(captured), 5)

    def test_query_count_does_not_grow_with_more_facts(self):
        with CaptureQueriesContext(connection) as baseline:
            build_authorized_investigation_context(incident=self.incident_a)
        for index in range(5):
            Fact.objects.create(
                investigation=self.investigation_a,
                content=f"Fato adicional {index}",
                source_type=Fact.SourceType.SYSTEM,
            )

        with CaptureQueriesContext(connection) as expanded:
            build_authorized_investigation_context(incident=self.incident_a)

        self.assertEqual(len(expanded), len(baseline))

    def _valid_payload(self, context, problem_ref):
        hypothesis = context.registered_hypotheses[0]
        return {
            "schema_version": "1.0",
            "problem": {
                "statement": "Falha operacional registrada.",
                "source_refs": [problem_ref],
            },
            "summary": "Investigação simulada sem chamada de IA.",
            "known_facts": [],
            "evidence": [],
            "contradictions": [],
            "hypotheses": [
                {
                    "id": "hypothesis_1",
                    "origin": "REGISTERED",
                    "registered_ref": hypothesis.ref,
                    "statement": hypothesis.statement,
                    "evidence_assessment": "INSUFFICIENT",
                    "supporting_finding_refs": [],
                    "opposing_finding_refs": [],
                    "rationale": "Hipótese registrada requer nova análise.",
                }
            ],
            "checks": [],
        }

    def _domain_snapshot(self):
        return {
            "counts": {
                "incidents": Incident.objects.count(),
                "investigations": Investigation.objects.count(),
                "facts": Fact.objects.count(),
                "evidence": Evidence.objects.count(),
                "hypotheses": Hypothesis.objects.count(),
                "links": HypothesisEvidence.objects.count(),
                "interventions": Intervention.objects.count(),
                "documents": Document.objects.count(),
                "sections": DocumentSection.objects.count(),
            },
            "incident": deepcopy(
                Incident.objects.filter(pk=self.incident_a.pk).values().get()
            ),
            "investigation": deepcopy(
                Investigation.objects.filter(pk=self.investigation_a.pk)
                .values()
                .get()
            ),
            "fact": deepcopy(Fact.objects.filter(pk=self.fact_a.pk).values().get()),
            "evidence": deepcopy(
                Evidence.objects.filter(pk=self.evidence_a_support.pk)
                .values()
                .get()
            ),
            "hypothesis": deepcopy(
                Hypothesis.objects.filter(pk=self.hypothesis_a.pk)
                .values()
                .get()
            ),
            "relations": list(
                HypothesisEvidence.objects.filter(hypothesis=self.hypothesis_a)
                .order_by("pk")
                .values()
            ),
        }
