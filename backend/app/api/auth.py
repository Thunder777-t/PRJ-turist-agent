from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .. import crud
from ..database import get_db
from ..schemas import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    UserResponse,
)
from ..security import (
    create_access_token,
    create_refresh_token,
    decode_token_safely,
    hash_refresh_token,
)


router = APIRouter(prefix="/auth", tags=["auth"])


def _issue_token_pair(
    db: Session,
    user_id: str,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> TokenPair:
    session_id = str(uuid4())
    refresh_token, refresh_expires_at = create_refresh_token(user_id=user_id, session_id=session_id)
    refresh_hash = hash_refresh_token(refresh_token)
    crud.create_auth_session(
        db,
        user_id=user_id,
        refresh_token_hash=refresh_hash,
        expires_at=refresh_expires_at,
        session_id=session_id,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    access_token, expires_in = create_access_token(user_id=user_id)
    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=expires_in,
    )


@router.post("/register")
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    existing_email = crud.get_user_by_email(db, payload.email)
    if existing_email:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered.",
        )
    existing_username = crud.get_user_by_username(db, payload.username)
    if existing_username:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already registered.",
        )

    user = crud.create_user(
        db,
        email=payload.email,
        username=payload.username,
        password=payload.password,
    )
    return {
        "success": True,
        "data": UserResponse(
            user_id=user.id,
            email=user.email,
            username=user.username,
            created_at=user.created_at,
        ),
        "error": None,
    }


@router.post("/login")
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    user = crud.authenticate_user(db, email=payload.email, password=payload.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    tokens = _issue_token_pair(
        db=db,
        user_id=user.id,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    return {"success": True, "data": tokens, "error": None}


@router.post("/refresh")
def refresh_token(payload: RefreshRequest, request: Request, db: Session = Depends(get_db)):
    decoded = decode_token_safely(payload.refresh_token)
    if not decoded or decoded.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token.",
        )

    user_id = decoded.get("sub")
    session_id = decoded.get("sid")
    refresh_hash = hash_refresh_token(payload.refresh_token)
    session = crud.get_active_auth_session_by_token_hash(db, refresh_hash)
    if not session or session.id != session_id or session.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh session not found or expired.",
        )

    crud.revoke_auth_session(db, session)
    tokens = _issue_token_pair(
        db=db,
        user_id=user_id,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    return {"success": True, "data": tokens, "error": None}


@router.post("/logout")
def logout(payload: LogoutRequest, db: Session = Depends(get_db)):
    refresh_hash = hash_refresh_token(payload.refresh_token)
    session = crud.get_active_auth_session_by_token_hash(db, refresh_hash)
    if session:
        crud.revoke_auth_session(db, session)
    return {"success": True, "data": {"logged_out": True}, "error": None}

