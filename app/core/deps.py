import uuid
from typing import Annotated

from fastapi import Cookie, Depends, Header
from sqlalchemy.orm import Session

from .database import get_db
from .exceptions import UnauthorizedError
from .security import decode_token

# ImportÃ© ici pour Ã©viter les imports circulaires (les modÃ¨les importent Base depuis database)
def get_current_user_id(
    access_token: Annotated[str | None, Cookie(alias="99eange_jwt")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> uuid.UUID:
    """Extrait l'UUID de l'utilisateur depuis le cookie OU le header Authorization Bearer."""
    token = access_token
    if not token and authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
    if not token:
        raise UnauthorizedError("Authentification requise (cookie ou Bearer manquant).")
    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            raise UnauthorizedError("Type de token invalide.")
        return uuid.UUID(payload["sub"])
    except (ValueError, KeyError):
        raise UnauthorizedError("Token invalide ou expirÃ©.")


def get_current_user(
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
    db: Annotated[Session, Depends(get_db)],
):
    """Retourne l'utilisateur authentifiÃ© ou lÃ¨ve UnauthorizedError."""
    # Import local pour Ã©viter les imports circulaires au dÃ©marrage
    from app.models.user import User  # noqa: PLC0415

    user = db.get(User, user_id)
    if not user or not user.is_active:
        raise UnauthorizedError("Utilisateur introuvable ou inactif.")
    return user


def extract_profile(user) -> dict:
    """Extrait le profil utilisateur en dict pour les agents.

    Inclut tous les champs déclarés par l'utilisateur (inscription + paramètres)
    pour que les agents disposent du contexte complet : domaine, filière, centres
    d'intérêt, compétences. Tous ces champs influencent les réponses et le matching.
    """
    if not user.profile:
        return {}
    return {
        "first_name":        user.profile.first_name,
        "last_name":         user.profile.last_name,
        "gender":            user.profile.gender,
        "birth_year":        user.profile.birth_year,
        "country":           user.profile.country,
        "primary_role":      user.profile.primary_role,
        "domain":            user.profile.domain,
        "field_of_study":    user.profile.field_of_study,
        "current_status":    user.profile.current_status,
        "preferred_content": user.profile.preferred_content,
        "skills":            user.profile.skills,
    }


def get_admin_user(user: Annotated["User", Depends(get_current_user)]) -> "User":
    """Retourne l utilisateur admin authentifie, sinon 403."""
    from fastapi import HTTPException
    if not user.role or user.role.value != "admin":
        raise HTTPException(status_code=403, detail="Acces administrateur requis.")
    return user
