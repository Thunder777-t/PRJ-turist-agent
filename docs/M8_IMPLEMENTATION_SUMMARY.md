# M8 Implementation Summary (Preference-Aware Planning)

## Historical Note

This milestone originally referenced a `graph.py` + `planner.py` path.
The project has since been refactored to a single LLM-driven agent pipeline.

## Current Effective Path

- `backend/app/services/assistant_service.py`
- `backend/app/services/agent/`
- `backend/app/api/conversations.py`

User preferences (`language`, `timezone`, `budget_level`, `interests`, `dietary`, `mobility_notes`) are injected into the active LLM-driven pipeline and influence intent understanding, slot extraction, task planning, and final synthesis.
