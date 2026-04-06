import json
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from .. import crud
from ..database import get_db
from ..models import User
from ..schemas import ChatResponse, ConversationCreateRequest, MessageCreateRequest
from ..services.assistant_service import generate_assistant_reply, stream_assistant_events
from .deps import get_current_user


router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.post("")
def create_conversation(
    payload: ConversationCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = crud.create_conversation(db, user_id=current_user.id, title=payload.title)
    return {
        "success": True,
        "data": {
            "id": conversation.id,
            "title": conversation.title,
            "is_archived": conversation.is_archived,
            "created_at": conversation.created_at,
            "updated_at": conversation.updated_at,
        },
        "error": None,
    }


@router.get("")
def list_conversations(
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    items = crud.list_user_conversations(db, user_id=current_user.id, limit=limit)
    data = [
        {
            "id": item.id,
            "title": item.title,
            "is_archived": item.is_archived,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
        }
        for item in items
    ]
    return {"success": True, "data": data, "error": None}


@router.get("/{conversation_id}")
def get_conversation(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = crud.get_user_conversation(db, user_id=current_user.id, conversation_id=conversation_id)
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found.",
        )
    return {
        "success": True,
        "data": {
            "id": conversation.id,
            "title": conversation.title,
            "is_archived": conversation.is_archived,
            "created_at": conversation.created_at,
            "updated_at": conversation.updated_at,
        },
        "error": None,
    }


@router.get("/{conversation_id}/messages")
def list_messages(
    conversation_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = crud.get_user_conversation(db, user_id=current_user.id, conversation_id=conversation_id)
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found.",
        )

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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found.",
        )

    user_msg = crud.create_message(
        db=db,
        user_id=current_user.id,
        conversation_id=conversation_id,
        role="user",
        content=payload.content,
    )

    preference_payload = _build_user_preference_payload(current_user)
    assistant_content = generate_assistant_reply(payload.content, preference_payload)
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


def _extract_destination_from_text(text: str) -> str:
    match = re.search(r"\bto\s+([A-Za-z][A-Za-z\s\-]{1,60})", text, re.IGNORECASE)
    if not match:
        return "Unknown"
    destination = match.group(1).strip(" .,")
    for stop_word in [" with ", " for ", " on ", " including "]:
        idx = destination.lower().find(stop_word)
        if idx != -1:
            destination = destination[:idx].strip()
    return destination or "Unknown"


def _build_user_preference_payload(current_user: User) -> dict:
    pref = current_user.preference
    if not pref:
        return {}
    return {
        "language": pref.language,
        "timezone": pref.timezone,
        "budget_level": pref.budget_level,
        "interests": pref.interests_json or [],
        "dietary": pref.dietary_json or [],
        "mobility_notes": pref.mobility_notes or "",
    }


def _save_auto_itinerary(
    db: Session,
    user_id: str,
    conversation_id: str,
    user_input: str,
    assistant_content: str,
) -> None:
    try:
        destination = _extract_destination_from_text(user_input)
        title = f"Auto itinerary {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}"
        crud.create_itinerary(
            db=db,
            user_id=user_id,
            conversation_id=conversation_id,
            title=title,
            destination=destination,
            summary=assistant_content[:3000],
            raw_plan_json={"source": "graph_pipeline", "input": user_input},
        )
    except Exception:
        # Itinerary auto-save failure should not block chat response.
        return


def _format_sse(event_type: str, data: dict) -> str:
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found.",
        )

    crud.create_message(
        db=db,
        user_id=current_user.id,
        conversation_id=conversation_id,
        role="user",
        content=payload.content,
    )

    preference_payload = _build_user_preference_payload(current_user)

    def event_generator():
        final_response = ""
        for event in stream_assistant_events(payload.content, preference_payload):
            event_type = event.get("type", "message")
            event_data = event.get("data", {})
            if event_type == "message_end":
                final_response = event_data.get("response", "")
            yield _format_sse(event_type, event_data)

        if final_response:
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
