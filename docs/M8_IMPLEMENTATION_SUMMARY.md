# M8 Implementation Summary (Preference-Aware Planning)

## Delivered Scope

### 1) Preference Injection Into Pipeline
- Conversation APIs now pass authenticated user's profile preferences into assistant pipeline:
  - `language`
  - `timezone`
  - `budget_level`
  - `interests`
  - `dietary`
  - `mobility_notes`
- Updated files:
  - `backend/app/api/conversations.py`
  - `backend/app/services/assistant_service.py`

### 2) Graph/Planner Personalization
- Graph state now carries `user_preferences`.
- Planner objective is augmented with a preference profile block.
- Final response now includes an explicit line showing personalization profile that was applied.
- Planner prompt updated to explicitly adapt steps using preference profile.
- Updated files:
  - `graph.py`
  - `planner.py`

### 3) Frontend Visible Signal
- Stream UI now shows a live status tag when personalization is applied:
  - `Personalization profile applied`
- Updated file:
  - `frontend/src/App.tsx`

### 4) Regression Tests + CI
- Added backend tests validating preference payload forwarding to pipeline for both sync and stream endpoints:
  - `backend/tests/test_m8_personalization.py`
- Added M8 tests to CI workflow:
  - `.github/workflows/ci.yml`

## Outcome
- User profile data is no longer just stored; it now actively influences itinerary planning flow.
- The effect is observable both in backend response content and frontend realtime status logs.
