# Database Migrations

Alembic is the only supported schema mutation path.

```powershell
uv run alembic upgrade head
uv run alembic check
```

Application containers never create tables at runtime. Compose and deployment
manifests run a dedicated migration job before API, Worker, and Scheduler roll out.
