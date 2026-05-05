# Agent Role Boundary

This project enforces a strict role boundary:

## 1) LLM = Brain

LLM is responsible for:

- understanding raw user input
- inferring explicit and implicit requirements
- identifying missing information
- planning search tasks
- interpreting search results
- producing the final travel plan

## 2) Program Code = Executor

Program code is responsible for:

- context and conversation state management
- calling DeepSeek API
- validating LLM JSON schema
- executing tool calls from LLM plans
- collecting and storing tool results
- exception handling and fallback behavior
- returning structured payloads and markdown to frontend

Program code must not hardcode user-intent understanding logic.

## 3) Tools = Information Sources

Tools are responsible for fetching external data:

- web search
- map / POI search
- weather search
- hotel area search
- transport search

## Runtime Mapping (Current)

- Step 1: `backend/app/services/agent/intent_analyzer.py`
- Step 2: `backend/app/services/agent/task_planner.py`
- Step 3: `backend/app/services/agent/search_executor.py`
- Step 4: `backend/app/services/agent/result_synthesizer.py`
- Step 5: `backend/app/services/agent/itinerary_generator.py`

Schema validation entry:

- `backend/app/services/agent/llm_schemas.py`
