import importlib
from typing import Any, Generator


def _chunk_text(text: str, chunk_size: int = 120) -> Generator[str, None, None]:
    if not text:
        return
    for i in range(0, len(text), chunk_size):
        yield text[i : i + chunk_size]


def _fallback_response(user_input: str) -> str:
    return (
        "The full travel pipeline is temporarily unavailable. "
        "Your request has been received: "
        f"{user_input}"
    )


def _load_graph_module():
    return importlib.import_module("graph")


def generate_assistant_reply(user_input: str) -> str:
    try:
        graph_module = _load_graph_module()
        final_state = graph_module.app.invoke(
            {"input": user_input, "step_results": []},
            {"recursion_limit": 50},
        )
        response = final_state.get("response", "")
        return response or "No response generated."
    except Exception:
        return _fallback_response(user_input)


def stream_assistant_events(user_input: str) -> Generator[dict[str, Any], None, None]:
    yield {"type": "message_start", "data": {"input": user_input}}

    try:
        graph_module = _load_graph_module()
        stream = graph_module.app.stream(
            {"input": user_input, "step_results": []},
            {"recursion_limit": 50},
        )
        for event in stream:
            if "planner" in event:
                plan = event["planner"].get("plan", [])
                yield {
                    "type": "planner",
                    "data": {
                        "plan_count": len(plan),
                        "plan_preview": plan[:3],
                    },
                }
                continue

            if "executor" in event:
                step_results = event["executor"].get("step_results", [])
                if step_results:
                    last_step = step_results[-1]
                    yield {
                        "type": "tool_call",
                        "data": {
                            "step_id": last_step.get("step_id"),
                            "step": last_step.get("step"),
                            "tool": last_step.get("tool"),
                            "status": last_step.get("status"),
                            "confidence": last_step.get("confidence"),
                        },
                    }
                continue

            if "finalizer" in event:
                final_response = event["finalizer"].get("response", "") or "No response generated."
                for chunk in _chunk_text(final_response):
                    yield {"type": "token", "data": {"text": chunk}}
                yield {"type": "message_end", "data": {"response": final_response}}
                return

    except Exception as e:
        fallback = _fallback_response(user_input)
        yield {"type": "error", "data": {"message": str(e)}}
        for chunk in _chunk_text(fallback):
            yield {"type": "token", "data": {"text": chunk}}
        yield {"type": "message_end", "data": {"response": fallback}}

