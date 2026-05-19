"""Routes profil utilisateur — GET et PATCH."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.exceptions import NotFoundError
from app.models.user import User
from app.repositories.user_repo import UserRepository
from app.schemas.auth import ProfileResponse, WrappedProfileResponse
from app.schemas.user import ProfileUpdate

router = APIRouter(tags=["profile"])


def _profile_with_user_fields(user: User) -> dict:
    """Construit la réponse profil en incluant phone et email depuis la table users."""
    data = ProfileResponse.model_validate(user.profile).model_dump()
    data["phone"] = user.phone
    data["email"] = user.email
    return data


@router.get("/profile", response_model=WrappedProfileResponse)
def get_profile(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    repo = UserRepository(db)
    user = repo.get_with_profile(current_user.id)
    if not user or not user.profile:
        return {"profile": None}
    return {"profile": _profile_with_user_fields(user)}


@router.patch("/profile", response_model=WrappedProfileResponse)
def update_profile(
    body: ProfileUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    repo = UserRepository(db)

    # phone vit sur la table users, pas profiles
    if body.phone is not None:
        current_user.phone = body.phone
        db.flush()

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        user = repo.get_with_profile(current_user.id)
        if not user or not user.profile:
            return {"profile": None}
        return {"profile": _profile_with_user_fields(user)}

    profile = repo.update_profile(current_user.id, **updates)
    if not profile:
        raise NotFoundError("Profil")
    db.commit()
    user = repo.get_with_profile(current_user.id)
    return {"profile": _profile_with_user_fields(user)}
