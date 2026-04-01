# M1 API Contract (Draft v1)

Base prefix: `/api/v1`

All JSON responses use:
- `success`: boolean
- `data`: object or list
- `error`: null or object `{ code, message }`

## 1. Auth

### `POST /auth/register`
Create account.

Request:
```json
{
  "email": "user@example.com",
  "username": "travel_user",
  "password": "StrongPassword123!"
}
```

Response:
```json
{
  "success": true,
  "data": {
    "user_id": "uuid",
    "email": "user@example.com",
    "username": "travel_user",
    "created_at": "2026-04-01T00:00:00Z"
  },
  "error": null
}
```

### `POST /auth/login`
Get access and refresh tokens.

Request:
```json
{
  "email": "user@example.com",
  "password": "StrongPassword123!"
}
```

Response:
```json
{
  "success": true,
  "data": {
    "access_token": "jwt",
    "refresh_token": "jwt",
    "token_type": "bearer",
    "expires_in": 1800
  },
  "error": null
}
```

### `POST /auth/refresh`
Refresh access token.

Request:
```json
{
  "refresh_token": "jwt"
}
```

Response:
```json
{
  "success": true,
  "data": {
    "access_token": "jwt",
    "token_type": "bearer",
    "expires_in": 1800
  },
  "error": null
}
```

### `POST /auth/logout`
Invalidate refresh token/session.

Request:
```json
{
  "refresh_token": "jwt"
}
```

Response:
```json
{
  "success": true,
  "data": {
    "logged_out": true
  },
  "error": null
}
```

## 2. Profile and Preferences

### `GET /me`
Return current user profile.

### `GET /preferences`
Return user preference document.

### `PATCH /preferences`
Update user preference fields.

Request (partial):
```json
{
  "language": "en",
  "timezone": "Asia/Shanghai",
  "budget_level": "medium",
  "interests": ["food", "culture", "nature"]
}
```

## 3. Conversations and Messages

### `POST /conversations`
Create a conversation.

Request:
```json
{
  "title": "Kyoto trip planning"
}
```

### `GET /conversations`
List current user's conversations.

Query:
- `limit` (default 20, max 100)
- `cursor` (optional pagination cursor)

### `GET /conversations/{conversation_id}`
Get conversation metadata.

### `GET /conversations/{conversation_id}/messages`
Get message history.

Query:
- `limit` (default 50)
- `before` (optional message id or timestamp cursor)

### `POST /conversations/{conversation_id}/messages`
Create a user message and return assistant output (non-streaming).

Request:
```json
{
  "content": "Plan a 3-day Kyoto trip under 500 USD",
  "client_message_id": "optional-idempotency-key"
}
```

Response:
```json
{
  "success": true,
  "data": {
    "user_message_id": "uuid",
    "assistant_message_id": "uuid",
    "assistant_content": "..."
  },
  "error": null
}
```

### `POST /conversations/{conversation_id}/stream`
Streaming assistant output (SSE).

Request:
```json
{
  "content": "Plan a 3-day Kyoto trip under 500 USD"
}
```

SSE events:
- `message_start`
- `token`
- `tool_call`
- `message_end`
- `error`

## 4. Itineraries

### `GET /itineraries`
List user itineraries.

### `GET /itineraries/{itinerary_id}`
Get itinerary detail.

### `POST /itineraries`
Create/save itinerary manually from frontend or pipeline.

Request:
```json
{
  "conversation_id": "uuid",
  "title": "Kyoto 2-day budget plan",
  "destination": "Kyoto",
  "summary": "Day-by-day plan...",
  "total_budget": 480,
  "currency": "USD",
  "raw_plan_json": {}
}
```

## 5. Status and Error Codes

Common error codes:
- `AUTH_INVALID_CREDENTIALS`
- `AUTH_TOKEN_EXPIRED`
- `AUTH_FORBIDDEN`
- `RESOURCE_NOT_FOUND`
- `VALIDATION_ERROR`
- `RATE_LIMITED`
- `INTERNAL_ERROR`
