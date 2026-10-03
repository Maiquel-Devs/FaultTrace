# FaultTrace

Plataforma Django para investigação de falhas em equipamentos, conectando fatos,
evidências com proveniência, histórico, documentação técnica, hipóteses,
contradições e próximos checks. O diagnóstico final continua pertencendo ao
técnico; o Agent não deve transformar pistas em causa confirmada.

## Documentação

Para continuidade assistida por IA, leia primeiro
[`docs/HANDOFF.md`](docs/HANDOFF.md).

- [Visão geral do projeto](docs/PROJECT_OVERVIEW.md)
- [Arquitetura do sistema](docs/ARCHITECTURE.md)
- [Arquitetura dos Agents](docs/AGENT_ARCHITECTURE.md)
- [Contrato InvestigationResultV1](docs/INVESTIGATION_CONTRACT_V1.md)
- [Histórico de desenvolvimento](docs/DEVELOPMENT_HISTORY.md)
- [Handoff e estado atual](docs/HANDOFF.md)

O Agent usado pela interface atual é o fluxo legado. O fluxo estruturado com
`InvestigationResultV1` ainda é experimental, não possui persistência/UI e não
deve substituir o legado sem nova decisão arquitetural.

## Desenvolvimento local

1. Crie o arquivo de ambiente e substitua os valores de exemplo:

   ```powershell
   Copy-Item .env.example .env
   ```

2. Construa e inicie os serviços:

   ```powershell
   docker compose up --build
   ```

A aplicação ficará em <http://localhost:8000/>, o health check em
<http://localhost:8000/health/> e o login em
<http://localhost:8000/accounts/login/>.

O código é copiado para a imagem, sem bind mount. Depois de alterar Python,
templates ou arquivos estáticos, reconstrua o serviço:

```powershell
docker compose up --build -d web
```

## Validação

```powershell
docker compose exec web python manage.py check
docker compose exec web python manage.py makemigrations --check --dry-run
docker compose exec web python manage.py migrate --check
docker compose exec web python manage.py test
```

O CI executa essas validações com PostgreSQL e providers mockados/fakes; nenhuma
API externa de LLM é chamada.

## Primeiro acesso administrativo

Todo usuário pertence obrigatoriamente a uma organização. Crie a organização e
depois o superusuário:

```powershell
docker compose exec web python manage.py shell -c "from accounts.models import Organization; Organization.objects.get_or_create(name='Empresa Exemplo')"
docker compose exec web python manage.py createsuperuser
```

Usuários `ADMIN` podem cadastrar equipamentos, documentos e configuração de IA.
Usuários autenticados da organização operam ocorrências e investigações conforme
as validações do domínio.

## Cenário de demonstração

```powershell
docker compose exec web python manage.py seed_demo --password "escolha-uma-senha-local"
```

O comando idempotente cria dados sintéticos do Compressor C-04, incluindo
histórico, conhecimento e PDF fictício. A senha é recebida pela linha de comando
e não deve ser versionada. Para usar o Agent legado, configure um provider pela
tela de Configurações com uma credencial própria.
