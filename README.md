# TouristAgent

## Correct Entry Points

- Web app launcher (recommended): `main.py`
- Backend app module: `backend/app/main.py`
- Frontend app: `frontend/`
- Planning pipeline core: `graph.py` + `planner.py`

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
