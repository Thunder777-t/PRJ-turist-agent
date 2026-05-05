from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from queue import Empty, Queue
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from .. import crud
from ..database import get_db
from ..models import User
from ..schemas import (
    ChatResponse,
    ConversationCreateRequest,
    ConversationPatchRequest,
    MessageCreateRequest,
)
from ..services.assistant_service import generate_assistant_reply, stream_assistant_events
from ..services.agent.intent_analyzer import analyze_intent
from .deps import get_current_user


router = APIRouter(prefix="/conversations", tags=["conversations"])


def _conversation_payload(conversation) -> dict[str, Any]:
    return {
        "id": conversation.id,
        "title": conversation.title,
        "is_archived": conversation.is_archived,
        "created_at": conversation.created_at,
        "updated_at": conversation.updated_at,
    }


def _build_conversation_history(
    db: Session,
    user_id: str,
    conversation_id: str,
    limit: int = 40,
) -> list[dict[str, str]]:
    messages = crud.list_user_messages(
        db=db,
        user_id=user_id,
        conversation_id=conversation_id,
        limit=limit,
    )
    history: list[dict[str, str]] = []
    for msg in messages:
        role = msg.role if msg.role in {"user", "assistant"} else "assistant"
        history.append({"role": role, "content": msg.content})
    return history


@router.post("")
def create_conversation(
    payload: ConversationCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = crud.create_conversation(db, user_id=current_user.id, title=payload.title)
    return {"success": True, "data": _conversation_payload(conversation), "error": None}


@router.get("")
def list_conversations(
    limit: int = Query(default=20, ge=1, le=100),
    include_archived: bool = Query(default=False),
    q: str | None = Query(default=None, min_length=1, max_length=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    items = crud.list_user_conversations(
        db,
        user_id=current_user.id,
        limit=limit,
        include_archived=include_archived,
        query=q.strip() if q else None,
    )
    data = [_conversation_payload(item) for item in items]
    return {"success": True, "data": data, "error": None}


@router.get("/{conversation_id}")
def get_conversation(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = crud.get_user_conversation(db, user_id=current_user.id, conversation_id=conversation_id)
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
    return {"success": True, "data": _conversation_payload(conversation), "error": None}


@router.patch("/{conversation_id}")
def patch_conversation(
    conversation_id: str,
    payload: ConversationPatchRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = crud.get_user_conversation(db, user_id=current_user.id, conversation_id=conversation_id)
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")

    updated = crud.update_conversation(
        db=db,
        conversation=conversation,
        title=payload.title,
        is_archived=payload.is_archived,
    )
    return {"success": True, "data": _conversation_payload(updated), "error": None}


@router.delete("/{conversation_id}")
def delete_conversation(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = crud.get_user_conversation(db, user_id=current_user.id, conversation_id=conversation_id)
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")

    crud.delete_conversation(db=db, conversation=conversation)
    return {"success": True, "data": {"deleted": True, "conversation_id": conversation_id}, "error": None}


@router.get("/{conversation_id}/messages")
def list_messages(
    conversation_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = crud.get_user_conversation(db, user_id=current_user.id, conversation_id=conversation_id)
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")

    messages = crud.list_user_messages(
        db=db,
        user_id=current_user.id,
        conversation_id=conversation_id,
        limit=limit,
    )
    data = [
        {
            "id": msg.id,
            "conversation_id": msg.conversation_id,
            "role": msg.role,
            "content": msg.content,
            "created_at": msg.created_at,
        }
        for msg in messages
    ]
    return {"success": True, "data": data, "error": None}


@router.post("/{conversation_id}/messages")
def create_message(
    conversation_id: str,
    payload: MessageCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = crud.get_user_conversation(db, user_id=current_user.id, conversation_id=conversation_id)
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")

    user_msg = crud.create_message(
        db=db,
        user_id=current_user.id,
        conversation_id=conversation_id,
        role="user",
        content=payload.content,
    )

    preference_payload = _build_user_preference_payload(current_user)
    request_speed_mode = _normalize_agent_speed_mode(payload.agent_speed_mode)
    if request_speed_mode:
        preference_payload["agent_speed_mode"] = request_speed_mode
    history = _build_conversation_history(
        db=db,
        user_id=current_user.id,
        conversation_id=conversation_id,
    )
    assistant_content = generate_assistant_reply(payload.content, preference_payload, history)
    assistant_msg = crud.create_message(
        db=db,
        user_id=current_user.id,
        conversation_id=conversation_id,
        role="assistant",
        content=assistant_content,
    )
    _save_auto_itinerary(
        db=db,
        user_id=current_user.id,
        conversation_id=conversation_id,
        user_input=payload.content,
        assistant_content=assistant_content,
        user_preferences=preference_payload,
    )

    return {
        "success": True,
        "data": ChatResponse(
            user_message_id=user_msg.id,
            assistant_message_id=assistant_msg.id,
            assistant_content=assistant_content,
        ),
        "error": None,
    }


def _build_user_preference_payload(current_user: User) -> dict[str, Any]:
    pref = current_user.preference
    if not pref:
        return {}
    payload = {
        "language": pref.language,
        "timezone": pref.timezone,
        "budget_level": pref.budget_level,
        "interests": pref.interests_json or [],
        "dietary": pref.dietary_json or [],
        "mobility_notes": pref.mobility_notes or "",
    }
    return payload


def _normalize_agent_speed_mode(value: str | None) -> str | None:
    mode = str(value or "").strip().lower()
    if not mode:
        return None
    if mode in {"fast", "aggressive", "极速"}:
        return "fast"
    if mode in {"quality", "high_quality", "accurate", "精确", "高质量"}:
        return "quality"
    return None


def _save_auto_itinerary(
    db: Session,
    user_id: str,
    conversation_id: str,
    user_input: str,
    assistant_content: str,
    user_preferences: dict[str, Any] | None = None,
) -> None:
    try:
        understanding = analyze_intent(
            user_input=user_input,
            conversation_history=None,
            user_preferences=user_preferences or {},
        )

        destination = "Unknown"
        explicit = understanding.get("explicit_requirements", [])
        if isinstance(explicit, list):
            for item in explicit:
                if not isinstance(item, dict):
                    continue
                req_type = str(item.get("type", "")).strip().lower()
                value = str(item.get("value", "")).strip()
                if req_type == "destination" and value:
                    destination = value
                    break

        title = f"Auto itinerary {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}"
        crud.create_itinerary(
            db=db,
            user_id=user_id,
            conversation_id=conversation_id,
            title=title,
            destination=destination,
            summary=assistant_content[:3000],
            raw_plan_json={
                "source": "travel_agent_pipeline",
                "input": user_input,
                "requirement_understanding": understanding,
            },
        )
    except Exception:
        return


def _format_sse(event_type: str, data: dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/{conversation_id}/stream")
def stream_message(
    conversation_id: str,
    payload: MessageCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = crud.get_user_conversation(db, user_id=current_user.id, conversation_id=conversation_id)
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")

    crud.create_message(
        db=db,
        user_id=current_user.id,
        conversation_id=conversation_id,
        role="user",
        content=payload.content,
    )

    preference_payload = _build_user_preference_payload(current_user)
    request_speed_mode = _normalize_agent_speed_mode(payload.agent_speed_mode)
    if request_speed_mode:
        preference_payload["agent_speed_mode"] = request_speed_mode
    history = _build_conversation_history(
        db=db,
        user_id=current_user.id,
        conversation_id=conversation_id,
    )

    def event_generator():
        final_response = ""
        client_disconnected = False
        heartbeat_interval_sec = 10.0
        queue: Queue[dict[str, Any] | object] = Queue()
        done = object()
        stop_event = threading.Event()

        def _produce_events() -> None:
            try:
                for event in stream_assistant_events(
                    payload.content,
                    preference_payload,
                    history,
                    cancel_event=stop_event,
                ):
                    if stop_event.is_set():
                        break
                    if isinstance(event, dict):
                        queue.put(event)
            except Exception as exc:
                queue.put({"type": "error", "data": {"message": str(exc)}})
            finally:
                queue.put(done)

        worker = threading.Thread(target=_produce_events, daemon=True)
        worker.start()

        try:
            while True:
                try:
                    item = queue.get(timeout=heartbeat_interval_sec)
                except Empty:
                    # Keep the SSE connection alive while the pipeline runs long tasks.
                    yield _format_sse(
                        "heartbeat",
                        {"ts": int(time.time()), "status": "processing"},
                    )
                    continue

                if item is done:
                    break
                if not isinstance(item, dict):
                    continue

                event_type = str(item.get("type", "message"))
                event_data = item.get("data", {})
                if not isinstance(event_data, dict):
                    event_data = {"raw": str(event_data)}
                if event_type == "message_end":
                    final_response = str(event_data.get("response", ""))
                yield _format_sse(event_type, event_data)
        except GeneratorExit:
            client_disconnected = True
            return
        finally:
            stop_event.set()

        if final_response and not client_disconnected:
            assistant_msg = crud.create_message(
                db=db,
                user_id=current_user.id,
                conversation_id=conversation_id,
                role="assistant",
                content=final_response,
            )
            _save_auto_itinerary(
                db=db,
                user_id=current_user.id,
                conversation_id=conversation_id,
                user_input=payload.content,
                assistant_content=final_response,
                user_preferences=preference_payload,
            )
            yield _format_sse("persisted", {"assistant_message_id": assistant_msg.id})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

