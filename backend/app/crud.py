from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models
from .security import hash_password, verify_password


def get_user_by_email(db: Session, email: str) -> models.User | None:
    stmt = select(models.User).where(models.User.email == email)
    return db.scalar(stmt)


def get_user_by_username(db: Session, username: str) -> models.User | None:
    stmt = select(models.User).where(models.User.username == username)
    return db.scalar(stmt)


def get_user_by_id(db: Session, user_id: str) -> models.User | None:
    stmt = select(models.User).where(models.User.id == user_id)
    return db.scalar(stmt)


def create_user(db: Session, email: str, username: str, password: str) -> models.User:
    user = models.User(
        email=email,
        username=username,
        password_hash=hash_password(password),
        is_active=True,
    )
    db.add(user)
    db.flush()

    preference = models.UserPreference(
        user_id=user.id,
        language="en",
        timezone="UTC",
        budget_level="medium",
    )
    db.add(preference)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, email: str, password: str) -> models.User | None:
    user = get_user_by_email(db, email=email)
    if not user:
        return None
    if not verify_password(password, user.password_hash):
        return None
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    return user


def create_auth_session(
    db: Session,
    user_id: str,
    refresh_token_hash: str,
    expires_at: datetime,
    session_id: str | None = None,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> models.AuthSession:
    payload = {
        "user_id": user_id,
        "refresh_token_hash": refresh_token_hash,
        "expires_at": expires_at,
        "user_agent": user_agent,
        "ip_address": ip_address,
    }
    if session_id:
        payload["id"] = session_id
    session = models.AuthSession(**payload)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def get_active_auth_session_by_token_hash(
    db: Session, refresh_token_hash: str
) -> models.AuthSession | None:
    now = datetime.now(timezone.utc)
    stmt = (
        select(models.AuthSession)
        .where(models.AuthSession.refresh_token_hash == refresh_token_hash)
        .where(models.AuthSession.revoked_at.is_(None))
        .where(models.AuthSession.expires_at > now)
    )
    return db.scalar(stmt)


def revoke_auth_session(db: Session, session: models.AuthSession) -> None:
    session.revoked_at = datetime.now(timezone.utc)
    db.commit()


def create_conversation(db: Session, user_id: str, title: str) -> models.Conversation:
    conversation = models.Conversation(user_id=user_id, title=title, is_archived=False)
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


def list_user_conversations(db: Session, user_id: str, limit: int = 20) -> list[models.Conversation]:
    stmt = (
        select(models.Conversation)
        .where(models.Conversation.user_id == user_id)
        .order_by(models.Conversation.updated_at.desc())
        .limit(limit)
    )
    return list(db.scalars(stmt).all())


def get_user_conversation(
    db: Session, user_id: str, conversation_id: str
) -> models.Conversation | None:
    stmt = (
        select(models.Conversation)
        .where(models.Conversation.id == conversation_id)
        .where(models.Conversation.user_id == user_id)
    )
    return db.scalar(stmt)


def create_message(
    db: Session,
    user_id: str,
    conversation_id: str,
    role: str,
    content: str,
) -> models.Message:
    message = models.Message(
        user_id=user_id,
        conversation_id=conversation_id,
        role=role,
        content=content,
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def list_user_messages(
    db: Session,
    user_id: str,
    conversation_id: str,
    limit: int = 50,
) -> list[models.Message]:
    stmt = (
        select(models.Message)
        .where(models.Message.user_id == user_id)
        .where(models.Message.conversation_id == conversation_id)
        .order_by(models.Message.created_at.asc())
        .limit(limit)
    )
    return list(db.scalars(stmt).all())


def create_itinerary(
    db: Session,
    user_id: str,
    conversation_id: str | None,
    title: str,
    destination: str,
    summary: str,
    total_budget: float | None = None,
    currency: str = "USD",
    raw_plan_json: dict | None = None,
) -> models.Itinerary:
    itinerary = models.Itinerary(
        user_id=user_id,
        conversation_id=conversation_id,
        title=title,
        destination=destination,
        summary=summary,
        total_budget=total_budget,
        currency=currency,
        raw_plan_json=raw_plan_json,
    )
    db.add(itinerary)
    db.commit()
    db.refresh(itinerary)
    return itinerary
