# Deployment Runbook

## 1) Local Development

### Backend
```bash
uvicorn backend.app.main:app --reload
```

### Frontend
```bash
cd frontend
npm install
npm run dev
```

Default local addresses:
- Frontend: `http://127.0.0.1:5173`
- Backend API: `http://127.0.0.1:8000/api/v1`

## 2) Production (Docker Compose)

### Prepare environment
1. Set production secrets in your shell (or `.env` loaded by Docker Compose):
```bash
export JWT_SECRET="replace-with-a-long-random-secret"
export CORS_ALLOW_ORIGINS="https://your-domain.com"
export TRUSTED_HOSTS="your-domain.com,www.your-domain.com"
export DeepSeek_API_KEY="your-deepseek-key-if-needed"
```

2. Build and start:
```bash
docker compose up -d --build
```

3. Check health:
```bash
curl http://127.0.0.1/health
```

In this setup:
- `frontend` serves SPA via Nginx on port `80`.
- `/api/*` is reverse-proxied to `backend`.
- SQLite data is persisted in Docker volume `tourist_agent_data`.

## 3) Security Checklist
- Use a strong `JWT_SECRET` in production.
- Keep `DOCS_ENABLED=false` in production.
- Keep `ENABLE_TRUSTED_HOST=true` and set exact `TRUSTED_HOSTS`.
- Set `CORS_ALLOW_ORIGINS` to exact domain list, not wildcard.
- Terminate TLS at cloud load balancer or reverse proxy.
- Keep `HSTS_ENABLED=true` only when HTTPS is fully enforced.

## 4) SQLite Backup and Restore

### Backup
```bash
python scripts/backup_sqlite.py --db tourist_agent.db --out-dir backups --keep 20
```

### Restore
```bash
python scripts/restore_sqlite.py --backup backups/tourist_agent_backup_YYYYMMDD_HHMMSS.db --db tourist_agent.db --force
```

Recommended:
- Run backup daily (cron/Task Scheduler).
- Keep encrypted offsite copies for disaster recovery.

## 5) Zero-Downtime-ish Update Flow
1. Backup DB before upgrade.
2. Pull latest code.
3. Rebuild containers:
```bash
docker compose up -d --build
```
4. Verify `/health`.
5. Roll back by restoring the latest backup if needed.
