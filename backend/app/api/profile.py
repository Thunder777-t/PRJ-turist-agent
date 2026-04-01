from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User
from ..schemas import PreferencePatchRequest
from .deps import get_current_user


router = APIRouter(tags=["profile"])


@router.get("/me")
def get_me(current_user: User = Depends(get_current_user)):
    return {
        "success": True,
        "data": {
            "user_id": current_user.id,
            "email": current_user.email,
            "username": current_user.username,
            "is_active": current_user.is_active,
            "created_at": current_user.created_at,
        },
        "error": None,
    }


@router.get("/preferences")
def get_preferences(current_user: User = Depends(get_current_user)):
    pref = current_user.preference
    if not pref:
        return {"success": True, "data": None, "error": None}
    return {
        "success": True,
        "data": {
            "language": pref.language,
            "timezone": pref.timezone,
            "budget_level": pref.budget_level,
            "interests": pref.interests_json or [],
            "dietary": pref.dietary_json or [],
            "mobility_notes": pref.mobility_notes,
            "updated_at": pref.updated_at,
        },
        "error": None,
    }


@router.patch("/preferences")
def patch_preferences(
    payload: PreferencePatchRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    pref = current_user.preference
    if pref is None:
        return {"success": False, "data": None, "error": {"code": "PREFERENCE_NOT_FOUND", "message": "Preference record missing."}}

    if payload.language is not None:
        pref.language = payload.language
    if payload.timezone is not None:
        pref.timezone = payload.timezone
    if payload.budget_level is not None:
        pref.budget_level = payload.budget_level
    if payload.interests is not None:
        pref.interests_json = payload.interests
    if payload.dietary is not None:
        pref.dietary_json = payload.dietary
    if payload.mobility_notes is not None:
        pref.mobility_notes = payload.mobility_notes

    db.commit()
    db.refresh(pref)
    return {
        "success": True,
        "data": {
            "language": pref.language,
            "timezone": pref.timezone,
            "budget_level": pref.budget_level,
            "interests": pref.interests_json or [],
            "dietary": pref.dietary_json or [],
            "mobility_notes": pref.mobility_notes,
            "updated_at": pref.updated_at,
        },
        "error": None,
    }

