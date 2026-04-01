from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str


class UserResponse(BaseModel):
    user_id: str
    email: EmailStr
    username: str
    created_at: datetime


class ConversationCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ConversationResponse(BaseModel):
    id: str
    title: str
    is_archived: bool
    created_at: datetime
    updated_at: datetime


class MessageCreateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=8000)
    client_message_id: str | None = None


class MessageResponse(BaseModel):
    id: str
    conversation_id: str
    role: str
    content: str
    created_at: datetime


class PreferencePatchRequest(BaseModel):
    language: str | None = Field(default=None, max_length=16)
    timezone: str | None = Field(default=None, max_length=64)
    budget_level: str | None = Field(default=None, max_length=32)
    interests: list[str] | None = None
    dietary: list[str] | None = None
    mobility_notes: str | None = Field(default=None, max_length=255)


class ItineraryCreateRequest(BaseModel):
    conversation_id: str | None = None
    title: str = Field(min_length=1, max_length=200)
    destination: str = Field(min_length=1, max_length=120)
    start_date: date | None = None
    end_date: date | None = None
    total_budget: float | None = None
    currency: str = Field(default="USD", min_length=3, max_length=8)
    summary: str = Field(min_length=1)
    raw_plan_json: dict[str, Any] | None = None


class ItineraryResponse(BaseModel):
    id: str
    conversation_id: str | None
    title: str
    destination: str
    start_date: date | None
    end_date: date | None
    total_budget: float | None
    currency: str
    summary: str
    raw_plan_json: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime

