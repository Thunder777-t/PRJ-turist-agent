from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from .. import crud
from ..database import get_db
from ..models import User
from ..schemas import ChatResponse, ConversationCreateRequest, MessageCreateRequest
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

    # M2 placeholder assistant output.
    assistant_content = (
        "Message received. Travel planning pipeline integration will be connected in M3."
    )
    assistant_msg = crud.create_message(
        db=db,
        user_id=current_user.id,
        conversation_id=conversation_id,
        role="assistant",
        content=assistant_content,
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

