# M5 Implementation Summary (Security + Deployment)

## Delivered Scope

### 1. Backend Production Hardening
- Added environment-driven security/runtime configuration in `backend/app/config.py`:
  - `CORS_ALLOW_ORIGINS`
  - `ENABLE_TRUSTED_HOST`
  - `TRUSTED_HOSTS`
  - `CREATE_TABLES_ON_STARTUP`
  - `DOCS_ENABLED`
  - `SECURITY_HEADERS_ENABLED`
  - `HSTS_ENABLED`
- Updated app bootstrap in `backend/app/main.py`:
  - optional docs/openapi disable in production
  - restricted CORS methods/headers
  - optional Trusted Host middleware
  - security headers middleware (`X-Frame-Options`, `X-Content-Type-Options`, etc.)
  - optional HSTS
  - startup table auto-create behind env switch

### 2. Containerized Web Deployment
- Added backend container build:
  - `backend/Dockerfile`
- Added frontend container build and reverse proxy:
  - `frontend/Dockerfile`
  - `frontend/nginx.conf`
- Added unified production orchestration:
  - `docker-compose.yml`
  - frontend exposed on `:80`, API reverse-proxied to backend
  - persistent SQLite data volume

### 3. Data Safety Operations (SQLite)
- Added backup/restore scripts:
  - `scripts/backup_sqlite.py`
  - `scripts/restore_sqlite.py`
- Added retention policy support in backup script (`--keep`).

### 4. Ops Documentation
- Added full deployment runbook:
  - `docs/DEPLOYMENT_RUNBOOK.md`
- Added backend environment template:
  - `backend/.env.example`

## Verification
- Backend API tests passed:
  - `.venv\Scripts\python.exe -m unittest backend/tests/test_m2_api.py`
- Frontend production build passed:
  - `cd frontend && npm run build`
