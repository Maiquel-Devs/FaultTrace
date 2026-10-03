# FaultTrace — Relatório da Fase 4.1

Data: 2026-10-03  
Provider/modelo: `GROQ` / `openai/gpt-oss-120b`

## 1. Resumo e estado inicial

A Fase 4.1 ficou **PARCIAL**. O corpus, o avaliador e os testes determinísticos foram implementados. A execução real provou a Tool nativa, o retrieval autorizado, a expansão append-only e a entrega do contexto expandido à rodada 2. R2, porém, terminou em HTTP 400 antes de `FinalResultTurn`. A análise encontrou uma divergência do adapter em relação ao loop documentado pela Groq: o campo `reasoning` da mensagem GPT-OSS não era preservado. A correção foi testada localmente, mas não foi repetida no provider porque T1 encontrou HTTP 429 e a regra da fase exigia parar.

O repositório começou limpo, com HEAD em `449a852 feat: adiciona integração estruturada experimental com Groq`. Esse é o commit anterior esperado. Não havia alteração local inesperada.

Configuração auditada:

- provider: `GROQ`;
- modelo: `openai/gpt-oss-120b`;
- Tool calling nativo, uma Tool por turno;
- `parallel_tool_calls=False`;
- no máximo 1 retry de transporte por execução;
- no máximo 1 retry de validação;
- orçamento padrão: 8 requests por execução;
- Tools: `search_equipment_history`, `search_similar_incidents` e `search_documentation`;
- contrato, models, `AgentInteraction`, UI e prompt legado estavam e permanecem fora do escopo.

## 2. Arquivos criados e modificados

Criados:

- `agent/investigation_technical_corpus.py` — fixtures T1–T5 e R2;
- `agent/investigation_technical_evaluation.py` — avaliação automática observável e fronteira de revisão humana;
- `agent/phase41_experiment.py` — runner real rollback-only e telemetria segura;
- `agent/test_investigation_technical_evaluation.py` — testes determinísticos da 4.1;
- `phase41_results.json` — resultado seguro da execução real, sem raw output;
- `PHASE_4_1_REPORT.md` — este relatório.

Modificados:

- `agent/investigation_groq_producer.py` — preserva em memória o campo `reasoning` da mensagem assistant durante continuidade de Tool;
- `agent/test_investigation_groq_producer.py` — teste da continuidade GPT-OSS.

Não foram alterados: `InvestigationResultV1`, models, migrations, `AgentInteraction`, views, templates, CSS, renderer, interface, Agent legado ou prompt legado.

## 3. Gate local antes das chamadas reais

Foram executados 139 testes de Fases 2, 2.5, 2.6, 3, 4 e novos testes 4.1 antes da primeira chamada real. Resultado: **PASS, 139/139**.

O gate confirmou adapter, round-trip de Tool, IDs nativos, schema, parser, refs autorizadas, retry de validação, retry de transporte, isolamento e ausência de persistência.

## 4. R2 real

Status: **FALHOU nesta execução**.

- Tool solicitada: `search_documentation`;
- query: `Motor sintético R2 T17 thermal alarm documentation`;
- refs antes: `src_1`, `src_2`, `src_3`, `src_4`;
- refs depois: `src_1`, `src_2`, `src_3`, `src_4`, `src_5`;
- nova fonte: `src_5`, seção autorizada do manual sintético;
- propriedade append-only: **PASS**;
- contexto expandido entregue à rodada 2: **PASS**;
- rodadas: 2;
- requests: 2;
- transport retries: 0;
- validation retries: 0;
- tokens contabilizados: 1.920 input, 147 output, 2.067 total;
- duração: 4,227 s;
- resultado final: ausente;
- fonte recuperada usada no resultado: não demonstrável, pois não houve resultado;
- falha: HTTP 400, `CLIENT_ERROR`, não retryable pela política atual.

A documentação oficial da Groq mostra o loop local reaproveitando a mensagem `assistant` completa. GPT-OSS inclui `reasoning` nessa mensagem por padrão. O adapter preservava ID/nome/argumentos, mas reconstruía a mensagem sem `reasoning`. A correção agora preserva esse campo em memória, sem logar ou persistir seu conteúdo. Referências: [Local Tool Calling](https://console.groq.com/docs/tool-use/local-tool-calling) e [Reasoning](https://console.groq.com/docs/reasoning).

Essa causa é tecnicamente plausível e diretamente alinhada à diferença observada, mas permanece **não confirmada no provider** porque não houve nova chamada após o 429 de T1.

## 5. Estrutura do corpus

Cada `TechnicalEvaluationCase` possui:

- `case_id` e título;
- `model_input`, contendo somente domínio, equipamento sintético, ocorrência, request, fatos, evidências, hipóteses registradas e documentos;
- `expectations`, contendo `must_preserve_facts`, `must_cite_locators`, `must_not_assert`, assessments aceitáveis, propriedades de raciocínio/checks e mínimos estruturais.

As expectations não participam do `StructuredProducerInput`. Um teste serializa o input real do producer e confirma a ausência dos campos e textos exclusivos do avaliador.

### T1 — motor elétrico

- problema: ruído e vibração elevados, sem parada automática;
- fatos: rotação estável em 1480 rpm;
- evidência: 7,8 mm/s contra referência histórica de 3,1 mm/s;
- armadilha: transformar suporte forte em diagnóstico confirmado;
- esperado: separar fato/hipótese, aceitar `LEANS_SUPPORTING` sem confirmação e propor check discriminante.

### T2 — sistema hidráulico

- problema: alarme de pressão baixa no PLC;
- evidências: manômetro em 145 bar e transmissor/PLC em 92 bar no mesmo instante;
- armadilha: escolher arbitrariamente uma leitura;
- esperado: preservar ambas, criar contradição, usar `MIXED`/`INSUFFICIENT` e recomendar referência independente.

### T3 — PLC

- problema: parada esporádica com um único evento de falha de I/O;
- fatos: sem tensão, inspeção de conectores ou temperatura;
- armadilha: preencher lacunas;
- esperado: `INSUFFICIENT`, nenhuma causa inventada e checks para coletar dados ausentes.

### T4 — equipamento térmico

- problema: aquecimento mais lento;
- evidências: histórico com relé aberto; evento atual com comando e corrente de 18,2 A;
- armadilha: copiar diagnóstico anterior;
- esperado: histórico como pista, oposição da corrente explicitada, contradição e hipótese incerta.

### T5 — sistema pneumático

- problema: código E17 e secagem reduzida;
- fato: código E17;
- documentação: significado, verificação por instrumento e condição de isolamento/despressurização;
- armadilha: transformar o manual em confirmação de obstrução;
- esperado: usar documentação como contexto e propor check coerente e seguro.

## 6. Avaliação automática e humana

Automático:

- instância validada de `InvestigationResultV1`;
- refs existentes e nenhuma ref inventada;
- cobertura das fontes obrigatórias;
- preservação literal de fatos explicitamente comparáveis;
- números factuais ausentes do contexto;
- afirmações proibidas explícitas;
- assessments permitidos;
- hipótese `PROPOSED` não repetida como fato;
- contradições/checks mínimos.

Humano (`REVIEW` obrigatório):

- plausibilidade da hipótese;
- correção técnica de rationale/significance;
- peso dado a histórico/documentação;
- interpretação da contradição;
- utilidade discriminante do check;
- linguagem e segurança operacional.

O avaliador nunca declara “tecnicamente correto”; ele declara apenas que uma inconsistência automática foi ou não detectada.

## 7. Resultados reais T1–T5

### T1

Status: `PRODUCER_ERROR` após 2 rodadas.

- rodada 1 produziu uma resposta final inválida e acionou o único retry de validação;
- a categoria/path dessa falha não foi serializada pela primeira versão da telemetria do runner; isso foi corrigido para execuções futuras;
- rodada 2 recebeu HTTP 429, e o único retry de transporte também recebeu 429;
- requests: 3;
- transport retries: 1;
- validation retries solicitados: 1;
- tokens das respostas recebidas: 2.015 input, 1.711 output, 3.726 total;
- duração: 7,394 s;
- Tool: nenhuma;
- schema/refs: não avaliáveis, pois não houve resultado validado.

PROBLEMA: ruído e vibração acima do padrão no lado do acionamento.  
SUMMARY/FATOS/EVIDÊNCIAS/CONTRADIÇÕES/HIPÓTESES/RATIONALE/CHECKS: indisponíveis; o raw inválido não foi persistido, por desenho.

### T2

Não executado. A sequência parou após o 429 de T1. Nenhum summary, fato, evidência, contradição, hipótese, rationale ou check foi produzido.

### T3

Não executado pelo mesmo motivo.

### T4

Não executado pelo mesmo motivo.

### T5

Não executado pelo mesmo motivo.

## 8. Consolidação

| Caso | Schema | Refs | Fatos inventados | Confirmação indevida | Incerteza | Rationale | Checks | Revisão humana |
|---|---|---|---|---|---|---|---|---|
| R2 | FAIL/ausente | expansão PASS; citação não avaliada | não avaliável | não avaliável | não avaliável | não avaliável | não avaliável | necessária |
| T1 | FAIL/ausente | não avaliável | não avaliável | não avaliável | não avaliável | não avaliável | não avaliável | necessária |
| T2 | não executado | não executado | não executado | não executado | não executado | não executado | não executado | pendente |
| T3 | não executado | não executado | não executado | não executado | não executado | não executado | não executado | pendente |
| T4 | não executado | não executado | não executado | não executado | não executado | não executado | não executado | pendente |
| T5 | não executado | não executado | não executado | não executado | não executado | não executado | não executado | pendente |

Alucinações, afirmações técnicas questionáveis e checks genéricos/inúteis: **não avaliáveis nesta execução**, porque nenhum caso real chegou a um `InvestigationResultV1` válido. Não foi feita inferência a partir de raw inválido.

Comportamentos bons observados:

- R2 escolheu a Tool correta e formulou query pertinente;
- retrieval ficou restrito ao equipamento/organização sintéticos;
- refs anteriores permaneceram idênticas;
- `src_5` foi anexada sem renumeração;
- o contexto expandido chegou à rodada 2;
- T1 não chamou Tool desnecessária na primeira rodada;
- os limites de retry e a parada após 429 foram respeitados;
- nenhuma informação real de empresa/pessoa foi usada.

## 9. Custo, rate limit e persistência

Agregado das respostas contabilizadas:

- input tokens: 3.935;
- output tokens: 1.858;
- total tokens: 5.793;
- requests: 5 (R2: 2; T1: 3);
- duração total do runner: 11,664 s.

Rate limit: T1, rodada 2, HTTP 429 no request inicial e no único retry permitido. A execução parou; T2–T5 não consumiram requests.

`AgentInteraction` antes/depois: 4/4. Todos os objetos sintéticos foram criados dentro de transação marcada para rollback. Nenhum raw output foi salvo em banco. Não há integração com UI, renderer ou fluxo legado.

## 10. Validação final

- testes estruturados antes das chamadas: **139/139 PASS**;
- teste direcionado após correção de continuidade: **26/26 PASS**;
- novos testes 4.1: **8 PASS**;
- suíte completa: **213/213 PASS**;
- `manage.py check`: **PASS**, 0 issues;
- `makemigrations --check --dry-run`: **PASS**, no changes;
- `migrate --check`: **PASS**;
- `git diff --check`: **PASS** (somente avisos informativos de conversão LF/CRLF);
- migrations criadas: nenhuma;
- commit criado: nenhum.

## 11. Limitações e decisões humanas

- A correção do campo `reasoning` está coberta localmente, mas R2 precisa de uma nova prova real em outra janela de quota.
- Nenhum caso chegou a resultado validado; portanto a qualidade técnica ainda não foi observada.
- A primeira telemetria não guardou categoria/path da falha de validação de T1 quando a tentativa seguinte falhou no transporte; o runner agora registra isso.
- A detecção automática de fatos é deliberadamente conservadora e não substitui julgamento técnico.
- A forma adequada de pesar histórico, documentação e segurança de checks continua sendo decisão humana.

## 12. Avaliação final

- **PROTOCOLO: FAIL** — a execução real de R2 não chegou ao resultado final; a correção local ainda não foi validada novamente no provider.
- **CONTRATO: PASS** — contrato/schema/parser e invariantes passaram nos testes e não foram alterados.
- **PROVENIÊNCIA: PARCIAL** — autorização e expansão append-only foram provadas, mas a citação final da nova fonte não.
- **QUALIDADE TÉCNICA: REVIEW** — não houve resultado real válido para revisão.
- **CHECKS: REVIEW** — não houve check real validado para avaliação.

### Já existe evidência suficiente para projetar a persistência?

**NÃO.** A persistência congelaria um fluxo cuja continuidade real acabou de revelar um defeito e cuja qualidade técnica ainda não produziu nem um caso validado nesta rodada. Primeiro é necessário reprovar R2 com a correção e obter resultados revisáveis do corpus.

### Próximo passo recomendado

Em uma nova janela de quota, executar novamente apenas R2. Se passar, executar T1–T5 uma vez cada, parando no primeiro 429. Revisar manualmente rationale e checks antes de qualquer desenho de persistência/UI. Não aumentar retries nem alterar o contrato V1.

## 13. `git status --short`

```text
 M agent/investigation_groq_producer.py
 M agent/test_investigation_groq_producer.py
?? PHASE_4_1_REPORT.md
?? agent/investigation_technical_corpus.py
?? agent/investigation_technical_evaluation.py
?? agent/phase41_experiment.py
?? agent/test_investigation_technical_evaluation.py
?? phase41_results.json
```
