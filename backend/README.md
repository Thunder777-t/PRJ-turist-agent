# Backend (M2)

This folder now includes M2 + M3 backend implementation:
- FastAPI app scaffold
- Auth endpoints (register/login/refresh/logout)
- SQLite persistence with SQLAlchemy models
- Alembic migration setup and first revision
- User-isolated conversation endpoints
- Graph pipeline-backed assistant message endpoint
- SSE streaming endpoint for realtime assistant events

## Quick start

1. Install backend dependencies:
```powershell
.\.venv\Scripts\python -m pip install -r backend\requirements.txt
```

2. Apply migrations:
```powershell
.\.venv\Scripts\alembic upgrade head
```

3. Run API server:
```powershell
.\.venv\Scripts\uvicorn backend.app.main:app --reload
```

4. Open docs:
- `http://127.0.0.1:8000/docs`

## Test

```powershell
.\.venv\Scripts\python -m unittest discover -s backend\tests -v
```
