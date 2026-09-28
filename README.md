# FaultTrace

Plataforma de investigação de falhas em equipamentos que conecta evidências,
históricos e documentação técnica para auxiliar equipes de manutenção.

## Desenvolvimento local

1. Crie o arquivo de ambiente:

   ```powershell
   Copy-Item .env.example .env
   ```

2. Substitua os valores de exemplo de `DJANGO_SECRET_KEY` e
   `POSTGRES_PASSWORD` no `.env`.

3. Construa e inicie os serviços:

   ```powershell
   docker compose up --build
   ```

A aplicação estará disponível em <http://localhost:8000/> e o health check em
<http://localhost:8000/health/>.

Para executar as validações dentro do container:

```powershell
docker compose exec web python manage.py check
docker compose exec web python manage.py test
docker compose exec web python manage.py makemigrations --check --dry-run
```

## Primeiro acesso administrativo

Como todo usuário pertence obrigatoriamente a uma empresa, crie primeiro a
organização pelo shell administrativo:

```powershell
docker compose exec web python manage.py shell -c "from accounts.models import Organization; Organization.objects.get_or_create(name='Empresa Exemplo')"
```

Em seguida, crie o superusuário e informe o ID dessa organização quando
solicitado:

```powershell
docker compose exec web python manage.py createsuperuser
```

O login da aplicação fica em <http://localhost:8000/accounts/login/>. Usuários
com papel `ADMIN` podem cadastrar equipamentos e documentos; usuários
`ADMIN` e `TECHNICIAN` podem registrar ocorrências, investigações e
intervenções.

> Ao atualizar uma instalação da Fase 1, use um banco vazio antes de aplicar
> estas migrations. O custom user foi introduzido agora, antes da existência de
> dados de domínio, e passa a ser dependência das migrations administrativas do
> Django.
