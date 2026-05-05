# Backend (M2)

This folder now includes M2 + M3 backend implementation:
- FastAPI app scaffold
- Auth endpoints (register/login/refresh/logout)
- SQLite persistence with SQLAlchemy models
- Alembic migration setup and first revision
- User-isolated conversation endpoints
- LLM-driven agent pipeline-backed assistant message endpoint
- SSE streaming endpoint for realtime assistant events

## LLM-Driven Agent Architecture

Runtime travel planning pipeline is in:

- `backend/app/services/assistant_service.py`
- `backend/app/services/agent/`

Pipeline flow:

1. Manage multi-turn context
2. LLM intent analysis
3. LLM slot extraction
4. LLM clarification planning
5. LLM search task planning
6. Tool execution (web/place/hotel/weather/transport)
7. LLM final synthesis and structured response output

No rule-based destination parser or keyword-template itinerary generator is used in the active backend path.

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
