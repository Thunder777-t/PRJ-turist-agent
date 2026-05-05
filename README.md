# TouristAgent

## Correct Entry Points

- Web app launcher (recommended): `main.py`
- Backend app module: `backend/app/main.py`
- Frontend app: `frontend/`
- LLM-driven travel agent pipeline core: `backend/app/services/agent/` + `backend/app/services/assistant_service.py`

## Project Structure

- `backend/`: FastAPI service, data models, agent orchestration, tests.
- `frontend/`: Vite + React UI.
- `docs/`: architecture notes and milestone summaries.
- `scripts/`: maintenance helpers (sqlite backup/restore).
- `experimental/ts-agent-pipeline/`: non-runtime TypeScript prototype modules for search pipeline experiments.

Notes:
- Runtime does **not** depend on `experimental/`.
- Main production path is Python backend + React frontend.

## Core Design (LLM-Driven Agent)

This project uses an LLM-driven architecture instead of a rule-based parser.

- User input is sent to the LLM for intent and requirement understanding.
- The LLM returns structured requirement slots and missing-info analysis.
- The LLM plans search tasks.
- The backend executes tools based on those LLM-generated tasks.
- Tool outputs are sent back to the LLM for synthesis and final answer generation.
- Backend code focuses on orchestration, context management, persistence, and API delivery.

Role boundary reference:
- `docs/AGENT_ROLE_BOUNDARY.md`

## Run Web App Locally

```powershell
.\.venv\Scripts\python.exe main.py
```

This command starts:
- FastAPI backend on `http://127.0.0.1:8000`
- Vite frontend on `http://127.0.0.1:5173`

Use `Ctrl+C` to stop both services.

## Quick Smoke Test

```powershell
.\.venv\Scripts\python.exe main.py --smoke-test --no-browser
```

If successful, the script exits after confirming both backend and frontend are reachable.
