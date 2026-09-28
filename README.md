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
