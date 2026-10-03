"""Small synthetic corpus for Phase 4.1 technical investigation evaluation.

The scenarios supplied to the model and the evaluator-only expectations are
deliberately separate values.  Nothing in this module changes the V1 contract.
"""

from __future__ import annotations

from dataclasses import dataclass

from .investigation_contracts import EvidenceAssessment


@dataclass(frozen=True)
class TechnicalFactFixture:
    locator: str
    content: str


@dataclass(frozen=True)
class TechnicalEvidenceFixture:
    locator: str
    content: str
    source_reference: str
    source_kind: str = "TECHNICIAN"


@dataclass(frozen=True)
class TechnicalHypothesisFixture:
    locator: str
    statement: str
    supporting_evidence_locators: tuple[str, ...] = ()
    opposing_evidence_locators: tuple[str, ...] = ()


@dataclass(frozen=True)
class TechnicalDocumentFixture:
    locator: str
    title: str
    page: int
    content: str


@dataclass(frozen=True)
class TechnicalCaseInput:
    domain: str
    equipment_name: str
    equipment_code: str
    equipment_description: str
    incident_description: str
    request: str
    facts: tuple[TechnicalFactFixture, ...] = ()
    evidence: tuple[TechnicalEvidenceFixture, ...] = ()
    hypotheses: tuple[TechnicalHypothesisFixture, ...] = ()
    documents: tuple[TechnicalDocumentFixture, ...] = ()


@dataclass(frozen=True)
class TechnicalCaseExpectations:
    must_preserve_facts: tuple[str, ...]
    must_cite_locators: tuple[str, ...]
    must_not_assert: tuple[str, ...]
    acceptable_assessments: tuple[EvidenceAssessment, ...]
    expected_reasoning_properties: tuple[str, ...]
    expected_check_properties: tuple[str, ...]
    minimum_contradictions: int = 0
    minimum_checks: int = 1


@dataclass(frozen=True)
class TechnicalEvaluationCase:
    case_id: str
    title: str
    model_input: TechnicalCaseInput
    expectations: TechnicalCaseExpectations


R2_EVALUATION_CASE = TechnicalEvaluationCase(
    case_id="R2",
    title="Round-trip real com documentação autorizada",
    model_input=TechnicalCaseInput(
        domain="motor elétrico",
        equipment_name="Motor sintético R2",
        equipment_code="MTR-SYN-R2",
        equipment_description="Motor de bancada com proteção térmica.",
        incident_description=(
            "O motor parou com alarme térmico T17; o significado do código e o "
            "procedimento de verificação não constam nos dados iniciais."
        ),
        request=(
            "Investigue a ocorrência. Consulte a documentação vinculada para "
            "identificar o significado do alarme térmico T17 e o procedimento "
            "de verificação antes de concluir."
        ),
        facts=(
            TechnicalFactFixture(
                "fact:r2:1", "O código exibido no desligamento foi T17."
            ),
        ),
        documents=(
            TechnicalDocumentFixture(
                "document:r2:1",
                "Manual sintético de proteção térmica MTR-SYN-R2",
                12,
                "Alarme térmico T17: atuação da proteção por temperatura estimada elevada. Antes de novo acionamento, verificar ventilação, corrente e temperatura com instrumentos adequados. O código isolado não confirma a causa do aquecimento.",
            ),
        ),
    ),
    expectations=TechnicalCaseExpectations(
        must_preserve_facts=("T17",),
        must_cite_locators=("fact:r2:1", "document:r2:1"),
        must_not_assert=("causa confirmada",),
        acceptable_assessments=(
            EvidenceAssessment.INSUFFICIENT,
            EvidenceAssessment.LEANS_SUPPORTING,
        ),
        expected_reasoning_properties=(
            "Usar a seção documental recuperada sem converter o código em causa confirmada.",
        ),
        expected_check_properties=(
            "Recomendar verificação coerente de ventilação, corrente ou temperatura.",
        ),
    ),
)


TECHNICAL_EVALUATION_CASES = (
    TechnicalEvaluationCase(
        case_id="T1",
        title="Evidência forte sem confirmação",
        model_input=TechnicalCaseInput(
            domain="motor elétrico",
            equipment_name="Motor do transportador sintético",
            equipment_code="MTR-SYN-01",
            equipment_description="Motor de indução de bancada, 30 kW.",
            incident_description=(
                "Ruído e vibração acima do padrão no lado do acionamento, "
                "sem parada automática."
            ),
            request=(
                "Investigue a ocorrência com separação explícita entre fatos, "
                "hipóteses e próximos checks."
            ),
            facts=(
                TechnicalFactFixture("fact:t1:1", "Rotação estável em 1480 rpm."),
            ),
            evidence=(
                TechnicalEvidenceFixture(
                    "evidence:t1:1",
                    "Vibração radial RMS medida em 7,8 mm/s; referência histórica da mesma posição: 3,1 mm/s.",
                    "Medição sintética VIB-T1",
                ),
            ),
            hypotheses=(
                TechnicalHypothesisFixture(
                    "hypothesis:t1:1",
                    "Degradação do rolamento do lado do acionamento.",
                    supporting_evidence_locators=("evidence:t1:1",),
                ),
            ),
        ),
        expectations=TechnicalCaseExpectations(
            must_preserve_facts=("1480 rpm", "7,8 mm/s", "3,1 mm/s"),
            must_cite_locators=("fact:t1:1", "evidence:t1:1"),
            must_not_assert=("causa confirmada", "rolamento confirmado"),
            acceptable_assessments=(
                EvidenceAssessment.LEANS_SUPPORTING,
                EvidenceAssessment.MIXED,
                EvidenceAssessment.INSUFFICIENT,
            ),
            expected_reasoning_properties=(
                "Tratar a vibração elevada como suporte, não como diagnóstico confirmado.",
                "Separar a rotação estável da hipótese de rolamento.",
            ),
            expected_check_properties=(
                "Propor verificação discriminante de vibração/rolamento.",
                "Explicar como o check reduz a incerteza.",
            ),
        ),
    ),
    TechnicalEvaluationCase(
        case_id="T2",
        title="Medições contraditórias",
        model_input=TechnicalCaseInput(
            domain="sistema hidráulico",
            equipment_name="Unidade hidráulica sintética",
            equipment_code="HYD-SYN-02",
            equipment_description="Unidade hidráulica de bancada com sensor e manômetro local.",
            incident_description="Alarme de pressão baixa indicado pelo PLC.",
            request="Investigue sem escolher arbitrariamente entre medições conflitantes.",
            evidence=(
                TechnicalEvidenceFixture(
                    "evidence:t2:1",
                    "Manômetro local indicou 145 bar durante o alarme; etiqueta de calibração dentro da validade.",
                    "Leitura sintética MAN-T2",
                ),
                TechnicalEvidenceFixture(
                    "evidence:t2:2",
                    "Transmissor exibido no PLC indicou 92 bar no mesmo instante; autodiagnóstico sem falha.",
                    "Leitura sintética PLC-T2",
                ),
            ),
            hypotheses=(
                TechnicalHypothesisFixture(
                    "hypothesis:t2:1",
                    "Desvio no canal de medição do transmissor de pressão.",
                    supporting_evidence_locators=("evidence:t2:1",),
                    opposing_evidence_locators=("evidence:t2:2",),
                ),
            ),
        ),
        expectations=TechnicalCaseExpectations(
            must_preserve_facts=("145 bar", "92 bar"),
            must_cite_locators=("evidence:t2:1", "evidence:t2:2"),
            must_not_assert=("manômetro está correto", "transmissor está defeituoso"),
            acceptable_assessments=(
                EvidenceAssessment.MIXED,
                EvidenceAssessment.INSUFFICIENT,
            ),
            expected_reasoning_properties=(
                "Preservar ambas as medições e reconhecer o conflito.",
                "Não declarar uma medição verdadeira sem teste discriminante.",
            ),
            expected_check_properties=(
                "Comparar os canais com referência independente e segura.",
            ),
            minimum_contradictions=1,
        ),
    ),
    TechnicalEvaluationCase(
        case_id="T3",
        title="Evidência insuficiente",
        model_input=TechnicalCaseInput(
            domain="PLC",
            equipment_name="Painel PLC sintético",
            equipment_code="PLC-SYN-03",
            equipment_description="Painel de treinamento com módulo de entradas digitais.",
            incident_description="Parada esporádica com um único registro de falha de I/O.",
            request="Investigue reconhecendo explicitamente as lacunas de informação.",
            facts=(
                TechnicalFactFixture(
                    "fact:t3:1",
                    "Há somente um evento registrado; não há medições de tensão, inspeção de conectores ou temperatura do módulo.",
                ),
            ),
            hypotheses=(
                TechnicalHypothesisFixture(
                    "hypothesis:t3:1",
                    "Mau contato intermitente no circuito de entrada.",
                ),
            ),
        ),
        expectations=TechnicalCaseExpectations(
            must_preserve_facts=("um único registro", "não há medições"),
            must_cite_locators=("fact:t3:1",),
            must_not_assert=("mau contato confirmado", "módulo defeituoso"),
            acceptable_assessments=(EvidenceAssessment.INSUFFICIENT,),
            expected_reasoning_properties=(
                "Admitir que um evento isolado não determina a causa.",
                "Não preencher medições ausentes.",
            ),
            expected_check_properties=(
                "Coletar tensão, estado dos conectores ou recorrência do evento.",
            ),
        ),
    ),
    TechnicalEvaluationCase(
        case_id="T4",
        title="Histórico semelhante enganoso",
        model_input=TechnicalCaseInput(
            domain="equipamento térmico",
            equipment_name="Forno sintético de processo",
            equipment_code="OVN-SYN-04",
            equipment_description="Forno elétrico de bancada com acionamento por relé.",
            incident_description="Temperatura da câmara subiu mais lentamente que o esperado.",
            request="Investigue o evento atual sem copiar a causa do histórico.",
            evidence=(
                TechnicalEvidenceFixture(
                    "evidence:t4:history",
                    "Ocorrência anterior semelhante teve relé do aquecedor aberto como causa confirmada naquela ocorrência.",
                    "Histórico sintético HIST-T4",
                    "PAST_INCIDENT",
                ),
                TechnicalEvidenceFixture(
                    "evidence:t4:current",
                    "Na ocorrência atual, há comando no relé e corrente medida de 18,2 A no circuito do aquecedor.",
                    "Medição sintética CUR-T4",
                ),
            ),
            hypotheses=(
                TechnicalHypothesisFixture(
                    "hypothesis:t4:1",
                    "Relé do aquecedor aberto na ocorrência atual.",
                    supporting_evidence_locators=("evidence:t4:history",),
                    opposing_evidence_locators=("evidence:t4:current",),
                ),
            ),
        ),
        expectations=TechnicalCaseExpectations(
            must_preserve_facts=("18,2 A", "ocorrência anterior"),
            must_cite_locators=("evidence:t4:history", "evidence:t4:current"),
            must_not_assert=("relé aberto confirmado", "mesma causa confirmada"),
            acceptable_assessments=(
                EvidenceAssessment.MIXED,
                EvidenceAssessment.LEANS_OPPOSING,
                EvidenceAssessment.INSUFFICIENT,
            ),
            expected_reasoning_properties=(
                "Usar o histórico como pista, não como prova do evento atual.",
                "Explicitar que a corrente atual se opõe ao relé aberto.",
            ),
            expected_check_properties=(
                "Testar a entrega térmica ou outro discriminante do evento atual.",
            ),
            minimum_contradictions=1,
        ),
    ),
    TechnicalEvaluationCase(
        case_id="T5",
        title="Documentação técnica como contexto",
        model_input=TechnicalCaseInput(
            domain="sistema pneumático",
            equipment_name="Secador pneumático sintético",
            equipment_code="PNE-SYN-05",
            equipment_description="Secador de ar de bancada com indicação de código.",
            incident_description="Controlador apresenta código E17 e desempenho de secagem reduzido.",
            request=(
                "Investigue consultando a documentação vinculada quando necessário; "
                "não trate o manual como confirmação automática da causa."
            ),
            facts=(TechnicalFactFixture("fact:t5:1", "Código exibido: E17."),),
            documents=(
                TechnicalDocumentFixture(
                    "document:t5:1",
                    "Manual sintético do secador PNE-SYN-05",
                    17,
                    "Código E17: diferencial de pressão elevado. Procedimento: verificar a leitura com instrumento de referência e inspecionar obstrução somente após isolar e despressurizar o equipamento conforme procedimento autorizado.",
                ),
            ),
        ),
        expectations=TechnicalCaseExpectations(
            must_preserve_facts=("E17",),
            must_cite_locators=("fact:t5:1",),
            must_not_assert=("obstrução confirmada", "filtro confirmado"),
            acceptable_assessments=(
                EvidenceAssessment.INSUFFICIENT,
                EvidenceAssessment.LEANS_SUPPORTING,
            ),
            expected_reasoning_properties=(
                "Usar o manual como significado/procedimento, não como prova da causa.",
            ),
            expected_check_properties=(
                "Recomendar verificação coerente com o manual e preservar a condição de segurança.",
            ),
        ),
    ),
)


def get_technical_evaluation_cases() -> tuple[TechnicalEvaluationCase, ...]:
    return TECHNICAL_EVALUATION_CASES
