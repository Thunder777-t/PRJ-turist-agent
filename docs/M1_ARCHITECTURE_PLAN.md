# M1 Architecture and Data Plan

## 1. Goal of M1
M1 defines the backend architecture baseline for the final web product:
- real-time travel assistant chat
- user account system
- per-user data isolation
- persistent chat and itinerary history

This milestone does not implement business logic yet. It locks the system design, data model, and API contract so M2 can start implementation directly.

## 2. Proposed Stack
- Backend framework: FastAPI
- ORM and migrations: SQLAlchemy + Alembic
- Storage: SQLite (single file database)
- Auth: JWT access + refresh tokens
- Password hashing: Argon2
- Realtime response: Server-Sent Events (SSE)

## 3. Why SQLite Instead of MySQL
- simpler setup and lower ops burden for student project scope
- easy local development and reproducible demos
- sufficient performance for expected workload
- supports secure setup when combined with:
  - hashed passwords
  - strict auth and authorization checks
  - optional field-level encryption for sensitive text
  - encrypted backups

## 4. High-Level Backend Modules
- `app/config.py`: environment settings
- `app/database.py`: engine/session/base
- `app/models.py`: SQLAlchemy models and relations
- `app/schemas.py`: API request/response schemas
- `app/api/*` (M2): route handlers
- `app/services/*` (M2): planner/chat/orchestration logic
- `app/security/*` (M2): auth and token handling

## 5. Runtime Data Flow (Target)
1. user registers or logs in
2. frontend sends message to backend with conversation id
3. backend stores user message
4. backend runs planner + executor pipeline
5. backend streams assistant response via SSE
6. backend stores assistant output and generated itinerary
7. frontend can load full conversation and itinerary history

## 6. Data Isolation Rules
- every read/write query must include `user_id` scope
- conversation access is restricted to conversation owner
- itinerary access is restricted to itinerary owner
- no cross-user joins without admin role (not needed for current scope)

## 7. Security Baseline (M1 Definition)
- store password hashes only (Argon2), never raw passwords
- use short-lived access tokens and refresh tokens
- validate all payloads with Pydantic schemas
- sanitize and cap input size for chat content
- do not log secrets or raw token values

## 8. M1 Deliverables
- SQLAlchemy data model definition for:
  - `users`
  - `conversations`
  - `messages`
  - `itineraries`
  - `user_preferences`
- API contract document for auth, chat, history endpoints
- ER diagram file for migration and implementation reference
