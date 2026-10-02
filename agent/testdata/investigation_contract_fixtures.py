"""Provider-free InvestigationResultV1 fixtures used by contract tests."""

from copy import deepcopy

from agent.investigation_contracts import (
    RegisteredHypothesisCatalog,
    SourceCatalog,
    SourceCatalogEntry,
)


SOURCE_CATALOG = SourceCatalog(
    SourceCatalogEntry(f"src_{index}", "TEST_SOURCE", f"Fonte autorizada {index}")
    for index in range(1, 16)
)
REGISTERED_HYPOTHESIS_CATALOG = RegisteredHypothesisCatalog(
    {"ctx_hypothesis_1", "ctx_hypothesis_2", "ctx_hypothesis_3"}
)


def _result(problem, summary):
    return {
        "schema_version": "1.0",
        "problem": {"statement": problem, "source_refs": ["src_1"]},
        "summary": summary,
        "known_facts": [],
        "evidence": [],
        "contradictions": [],
        "hypotheses": [],
        "checks": [],
    }


def _fact(identifier, statement, source="src_1"):
    return {"id": identifier, "statement": statement, "source_refs": [source]}


def _evidence(identifier, statement, significance, source="src_2"):
    return {
        "id": identifier,
        "statement": statement,
        "significance": significance,
        "source_refs": [source],
    }


def _contradiction(identifier, left, right, explanation, implication):
    return {
        "id": identifier,
        "finding_refs": [left, right],
        "explanation": explanation,
        "implication": implication,
    }


def _hypothesis(
    identifier,
    statement,
    assessment,
    supporting=(),
    opposing=(),
    *,
    origin="PROPOSED",
    registered_ref=None,
    rationale="A avaliação decorre dos findings explicitamente relacionados.",
):
    item = {
        "id": identifier,
        "origin": origin,
        "statement": statement,
        "evidence_assessment": assessment,
        "supporting_finding_refs": list(supporting),
        "opposing_finding_refs": list(opposing),
        "rationale": rationale,
    }
    if registered_ref is not None:
        item["registered_ref"] = registered_ref
    return item


def _check(action, reason, basis, outcomes=()):
    return {
        "action": action,
        "reason": reason,
        "basis_refs": list(basis),
        "possible_outcomes": [
            {"observation": observation, "implication": implication}
            for observation, implication in outcomes
        ],
    }


ELECTRICAL = _result(
    "Proteção atua durante operação com corrente acima do valor de referência.",
    "Há sobrecorrente observada, mas sua origem ainda precisa ser diferenciada.",
)
ELECTRICAL["known_facts"] = [
    _fact("fact_1", "A proteção interrompeu a operação."),
]
ELECTRICAL["evidence"] = [
    _evidence(
        "evidence_1",
        "A corrente medida ficou acima do valor de referência.",
        "A medição sustenta a existência de uma condição elétrica anormal.",
    )
]
ELECTRICAL["hypotheses"] = [
    _hypothesis(
        "hypothesis_1",
        "A carga demandada pode estar elevando a corrente.",
        "LEANS_SUPPORTING",
        ["evidence_1"],
        origin="REGISTERED",
        registered_ref="ctx_hypothesis_1",
    )
]
ELECTRICAL["checks"] = [
    _check(
        "Registrar corrente e condição de carga durante um ciclo controlado.",
        "A correlação diferencia sobrecarga persistente de evento transitório.",
        ["evidence_1", "hypothesis_1"],
        [
            ("Corrente acompanha o aumento da carga.", "A hipótese ganha sustentação."),
            ("Corrente permanece normal.", "É necessário investigar outra origem."),
        ],
    )
]


MECHANICAL = _result(
    "Foi observada vibração anormal durante a operação.",
    "O padrão observado é compatível com desgaste, ainda sem confirmação.",
)
MECHANICAL["known_facts"] = [
    _fact("fact_1", "A vibração aumentou em relação à condição habitual."),
]
MECHANICAL["evidence"] = [
    _evidence(
        "evidence_1",
        "A inspeção identificou folga em componente móvel.",
        "A folga pode explicar parte do padrão de vibração.",
    )
]
MECHANICAL["hypotheses"] = [
    _hypothesis(
        "hypothesis_1",
        "O desgaste do componente móvel contribui para a vibração.",
        "LEANS_SUPPORTING",
        ["fact_1", "evidence_1"],
    )
]
MECHANICAL["checks"] = [
    _check(
        "Comparar o padrão de vibração antes e depois de eliminar a folga.",
        "A comparação verifica a contribuição do desgaste observado.",
        ["hypothesis_1"],
    )
]


SENSOR = _result(
    "A leitura do sensor diverge de uma medição independente.",
    "As duas medições não podem ser tratadas como equivalentes sem validação.",
)
SENSOR["evidence"] = [
    _evidence(
        "evidence_1",
        "O sensor reportou valor acima da faixa esperada.",
        "A leitura é usada pelo controle para decidir a atuação.",
    ),
    _evidence(
        "evidence_2",
        "O instrumento independente indicou valor dentro da faixa esperada.",
        "A medição independente questiona a exatidão do sensor.",
        "src_3",
    ),
]
SENSOR["contradictions"] = [
    _contradiction(
        "contradiction_1",
        "evidence_1",
        "evidence_2",
        "As leituras simultâneas indicam condições incompatíveis.",
        "A condição real não pode ser determinada sem validar os instrumentos.",
    )
]
SENSOR["hypotheses"] = [
    _hypothesis(
        "hypothesis_1",
        "O sensor pode estar descalibrado.",
        "LEANS_SUPPORTING",
        ["evidence_2"],
        ["evidence_1"],
    )
]
SENSOR["checks"] = [
    _check(
        "Comparar o sensor com um padrão rastreável em mais de um ponto.",
        "A verificação resolve a contradição entre as leituras.",
        ["contradiction_1", "hypothesis_1"],
    )
]


TEMPERATURE = _result(
    "A temperatura aumenta progressivamente até ocorrer interrupção.",
    "O comportamento temporal indica acúmulo de calor, com origem ainda aberta.",
)
TEMPERATURE["known_facts"] = [
    _fact("fact_1", "A interrupção ocorre após um período de operação."),
]
TEMPERATURE["evidence"] = [
    _evidence(
        "evidence_1",
        "Medições sucessivas mostram aumento progressivo da temperatura.",
        "A progressão relaciona o tempo de operação ao evento.",
    )
]
TEMPERATURE["hypotheses"] = [
    _hypothesis(
        "hypothesis_1",
        "A dissipação pode ser insuficiente para a condição operacional.",
        "LEANS_SUPPORTING",
        ["fact_1", "evidence_1"],
    )
]
TEMPERATURE["checks"] = [
    _check(
        "Registrar temperatura e condição operacional no mesmo intervalo.",
        "A série conjunta permite testar a associação observada.",
        ["evidence_1", "hypothesis_1"],
    )
]


PRESSURE = _result(
    "A pressão medida permanece abaixo do valor esperado.",
    "O desvio é real, mas diferentes mecanismos ainda podem explicá-lo.",
)
PRESSURE["evidence"] = [
    _evidence(
        "evidence_1",
        "A medição repetida confirmou pressão abaixo da referência.",
        "O desvio persistente exige localizar perda ou geração insuficiente.",
    )
]
PRESSURE["hypotheses"] = [
    _hypothesis(
        "hypothesis_1",
        "Pode existir perda no circuito.",
        "INSUFFICIENT",
        ["evidence_1"],
        rationale="A pressão baixa é compatível, mas não localiza uma perda.",
    ),
    _hypothesis(
        "hypothesis_2",
        "A capacidade de geração pode estar reduzida.",
        "INSUFFICIENT",
        ["evidence_1"],
        rationale="A mesma evidência admite geração insuficiente como alternativa.",
    ),
]
PRESSURE["checks"] = [
    _check(
        "Comparar pressão em pontos sucessivos do circuito.",
        "A comparação ajuda a distinguir perda localizada de geração insuficiente.",
        ["hypothesis_1", "hypothesis_2"],
    )
]


AUTOMATION = _result(
    "Uma entrada digital alterna sem correspondência consistente no processo.",
    "A intermitência pode estar no campo, na conexão ou na leitura de controle.",
)
AUTOMATION["known_facts"] = [
    _fact("fact_1", "A lógica registra alternância da entrada digital."),
]
AUTOMATION["evidence"] = [
    _evidence(
        "evidence_1",
        "O estado físico observado nem sempre acompanha o registro lógico.",
        "A divergência delimita uma inconsistência entre campo e controle.",
    )
]
AUTOMATION["hypotheses"] = [
    _hypothesis(
        "hypothesis_1",
        "A cadeia do sinal apresenta uma falha intermitente.",
        "LEANS_SUPPORTING",
        ["fact_1", "evidence_1"],
    )
]
AUTOMATION["checks"] = [
    _check(
        "Observar o sinal em pontos sucessivos da cadeia durante a intermitência.",
        "A observação localiza onde os estados deixam de corresponder.",
        ["evidence_1", "hypothesis_1"],
    )
]


FEW_EVIDENCE = _result(
    "Foi reportada interrupção inesperada, sem outras observações registradas.",
    "Os dados atuais descrevem o sintoma, mas não sustentam uma causa.",
)
FEW_EVIDENCE["checks"] = [
    _check(
        "Registrar as condições presentes imediatamente antes da próxima ocorrência.",
        "É necessário obter findings antes de formular hipótese defensável.",
        ["fact_1"],
    )
]
FEW_EVIDENCE["known_facts"] = [
    _fact("fact_1", "Uma interrupção inesperada foi reportada."),
]


MANY_EVIDENCE = _result(
    "O desempenho se degrada durante operação prolongada.",
    "Medições, inspeções, histórico e documentação delimitam causas concorrentes.",
)
MANY_EVIDENCE["known_facts"] = [
    _fact("fact_1", "A degradação aparece após operação prolongada."),
    _fact("fact_2", "O comportamento foi reproduzido em dois ciclos.", "src_2"),
    _fact("fact_3", "Uma intervenção anterior tratou sintoma semelhante.", "src_3"),
    _fact("fact_4", "Não houve alteração de configuração registrada.", "src_4"),
]
MANY_EVIDENCE["evidence"] = [
    _evidence("evidence_1", "A medição A se afasta da referência.", "Indica desvio operacional.", "src_5"),
    _evidence("evidence_2", "A medição B permanece estável.", "Reduz a abrangência de uma hipótese geral.", "src_6"),
    _evidence("evidence_3", "A inspeção encontrou desgaste localizado.", "Oferece um mecanismo possível.", "src_7"),
    _evidence("evidence_4", "O histórico registra recorrência sob condição semelhante.", "Relaciona a condição atual a evento anterior sem provar a causa.", "src_8"),
    _evidence("evidence_5", "A documentação recomenda verificar o subsistema associado.", "Fornece procedimento técnico aplicável.", "src_9"),
    _evidence("evidence_6", "Uma medição independente não reproduziu o desvio C.", "Cria incerteza sobre a leitura original.", "src_10"),
]
MANY_EVIDENCE["contradictions"] = [
    _contradiction("contradiction_1", "evidence_1", "evidence_2", "As medições não indicam degradação uniforme.", "Uma causa global fica menos provável."),
    _contradiction("contradiction_2", "evidence_6", "fact_2", "A repetibilidade do evento não se repete na medição C.", "A qualidade da medição C precisa ser verificada."),
]
MANY_EVIDENCE["hypotheses"] = [
    _hypothesis("hypothesis_1", "O desgaste localizado contribui para a degradação.", "LEANS_SUPPORTING", ["evidence_3", "evidence_4"]),
    _hypothesis("hypothesis_2", "O desvio afeta todo o sistema.", "LEANS_OPPOSING", ["evidence_1"], ["evidence_2"]),
    _hypothesis("hypothesis_3", "A leitura C representa uma anomalia real.", "MIXED", ["fact_2"], ["evidence_6"]),
]
MANY_EVIDENCE["checks"] = [
    _check("Validar a medição C.", "Há conflito entre repetibilidade e medição independente.", ["contradiction_2"]),
    _check("Inspecionar a progressão do desgaste localizado.", "O desgaste é a hipótese mais sustentada no conjunto atual.", ["hypothesis_1"]),
    _check("Repetir as medições A e B sob a mesma condição.", "A comparação testa se o desvio é localizado.", ["contradiction_1", "hypothesis_2"]),
]


CONFLICTING = deepcopy(SENSOR)
CONFLICTING["problem"]["statement"] = "Duas fontes relevantes descrevem estados incompatíveis."
CONFLICTING["summary"] = "O conflito impede escolher uma interpretação sem nova verificação."


NO_CONTRADICTION = deepcopy(MECHANICAL)
NO_CONTRADICTION["problem"]["statement"] = "Há desgaste observado sem informação conflitante registrada."
NO_CONTRADICTION["contradictions"] = []


NO_HYPOTHESIS = _result(
    "Foi reportado comportamento anormal sem detalhes reproduzíveis.",
    "Não há base suficiente para formular hipótese defensável.",
)
NO_HYPOTHESIS["known_facts"] = [
    _fact("fact_1", "Um comportamento anormal foi reportado."),
]
NO_HYPOTHESIS["checks"] = [
    _check(
        "Obter descrição reproduzível e observações do evento.",
        "A informação disponível ainda não diferencia explicações.",
        ["fact_1"],
    )
]


NO_CHECKS = _result(
    "A condição observada foi explicada por evidências registradas.",
    "Não há verificação adicional recomendada pelo Agent neste resultado.",
)
NO_CHECKS["known_facts"] = [
    _fact("fact_1", "A condição foi reproduzida e registrada."),
]
NO_CHECKS["evidence"] = [
    _evidence(
        "evidence_1",
        "A intervenção registrada eliminou a condição observada.",
        "O resultado documentado explica o comportamento para esta análise.",
    )
]
NO_CHECKS["hypotheses"] = [
    _hypothesis(
        "hypothesis_1",
        "A condição registrada estava associada ao fator tratado.",
        "LEANS_SUPPORTING",
        ["evidence_1"],
        origin="REGISTERED",
        registered_ref="ctx_hypothesis_2",
    )
]
NO_CHECKS["checks"] = []


CORPUS_A_TO_L = {
    "A_electrical": ELECTRICAL,
    "B_mechanical": MECHANICAL,
    "C_sensor": SENSOR,
    "D_temperature": TEMPERATURE,
    "E_pressure": PRESSURE,
    "F_automation": AUTOMATION,
    "G_few_evidence": FEW_EVIDENCE,
    "H_many_evidence": MANY_EVIDENCE,
    "I_conflicting": CONFLICTING,
    "J_no_contradiction": NO_CONTRADICTION,
    "K_no_hypothesis": NO_HYPOTHESIS,
    "L_no_checks": NO_CHECKS,
}


def valid_payload():
    """Return a mutable, independent payload for adversarial tests."""

    return deepcopy(ELECTRICAL)
