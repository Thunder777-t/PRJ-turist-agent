# AI Travel Agent Frontend Development Rules

## 1. Product Positioning

This frontend is for an **AI Travel Agent** product.

It is **not**:
- a generic tourism marketing website
- a keyword-based Q&A bot UI
- a plain markdown chat shell

The UI must clearly present both:
- conversational experience
- agent reasoning/progress and structured travel outputs

## 2. Experience Goals

Frontend work must align with these goals:
1. ChatGPT-like conversational usability (interaction quality, not brand copying)
2. Fixed bottom composer (input area stays stable while reading conversation)
3. Streaming response rendering
4. Agent process visibility (steps/status/progress)
5. Structured travel plan cards (not markdown-only)
6. Source/citation link display
7. Mobile-responsive experience

## 3. Brand and Design Constraints

Allowed:
- modern AI product feel
- clear information hierarchy
- polished interaction and motion

Not allowed:
- copying ChatGPT brand, logo, wording, or near-identical visual details
- pixel-level cloning of ChatGPT UI

## 4. Frontend Architecture Rules

- Do not place all UI and logic in one component.
- Separate chat, composer, trace/agent steps, sources, and travel plan into independent components.
- Keep data parsing/mapping logic out of visual-only components.
- Prefer typed interfaces for all agent payloads and stream events.

Recommended split:
- `AppShell`
- `ConversationSidebar`
- `ChatViewport` / `MessageList`
- `Composer`
- `AgentStepsPanel`
- `TravelPlanPanel`
- `SourcesPanel`

## 5. Data Contract Requirements

Frontend must be able to consume backend outputs for:
- `travelPlan`
- `agentSteps`
- `sources`

Rules:
- Do not rely only on `markdown_answer` for final presentation.
- Structured payload takes priority for rendering plan cards.
- If structured payload is missing, show a clear fallback state and keep chat usable.

## 6. Streaming and Interaction Rules

- Must support streaming token updates in assistant messages.
- Must support stop/cancel while generating.
- Input area must stay fixed at bottom during generation.
- Show visible running state so users know the agent is working.
- Agent process area should reflect step-level state changes where available.

## 7. Travel Plan Rendering Rules

- Do not render travel plans as raw markdown only.
- Render itinerary by day with structured sections (morning/afternoon/evening, transport, food, notes).
- Support assumptions/missing info/follow-up questions in dedicated UI blocks.
- Budget and hotel-area suggestions should have dedicated visual sections when present.

## 8. Source Rendering Rules

- Sources must show at least: title + URL.
- Prefer showing snippet/platform/domain when available.
- Deduplicate visually repeated sources if backend sends overlaps.
- Source links must be clearly distinguishable from assistant narrative text.

## 9. Content Safety Rules for Frontend

- Do not hardcode Chengdu or any city as default real answer data.
- Do not ship static fake itinerary content as if it were live model output.
- Any mock/demo content must be clearly marked as placeholder/test data.

## 10. Responsive Rules

- Must work on desktop and mobile.
- Composer remains reachable and usable on small screens.
- Panels (steps/sources/plan) should collapse or stack appropriately on narrow viewports.

## 11. Definition of Done (Frontend)

A frontend change is not complete unless:
- streaming still works
- `travelPlan`, `agentSteps`, and `sources` can all be consumed and rendered
- composer is fixed-bottom and usable
- agent running/progress state is visible
- mobile layout remains usable
- no ChatGPT branding or visual cloning is introduced
