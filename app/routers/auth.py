"""Routes d'authentification â€” couche mince HTTP."""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Cookie, Depends, Request, Response
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.cookies import ACCESS_COOKIE, REFRESH_COOKIE
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.rate_limit import limiter
from app.models.user import User
from app.schemas.auth import (
    AuthResultResponse,
    ChangePasswordRequest,
    CheckPhoneRequest,
    ForgotPasswordRequest,
    LoginPhoneRequest,
    LoginRequest,
    MeResponse,
    RegisterPhoneRequest,
    RegisterRequest,
    ResetPasswordRequest,
)
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()

_COOKIE_KWARGS: dict = {
    "httponly": True,
    "samesite": "lax",
    "secure": settings.environment != "local",
    "path": "/",
}


def _set_auth_cookies(response: Response, access: str, refresh: str) -> None:
    response.set_cookie(
        ACCESS_COOKIE,
        access,
        max_age=settings.access_token_expire_minutes * 60,
        **_COOKIE_KWARGS,
    )
    response.set_cookie(
        REFRESH_COOKIE,
        refresh,
        max_age=settings.refresh_token_expire_days * 86400,
        **_COOKIE_KWARGS,
    )


def _auth_result(user: User, access_token: str) -> dict:
    """Construit la reponse auth."""
    return {
        "accessToken": access_token,
        "user": {
            "id": str(user.id),
            "email": user.email,
            "phone": user.phone,
            "role": user.role.value if user.role else "b2c",
            "is_active": user.is_active,
            "created_at": user.created_at.isoformat(),
        },
    }


# â”€â”€ Email auth (option secondaire) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


@router.post("/register", response_model=AuthResultResponse, status_code=201)
@limiter.limit("5/15minute")
def register(
    request: Request,
    body: RegisterRequest,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
):
    service = AuthService(db)
    user, access, refresh = service.register(body)
    db.commit()
    _set_auth_cookies(response, access, refresh)
    return _auth_result(user, access)


@router.post("/login", response_model=AuthResultResponse)
@limiter.limit("5/minute")
def login(
    request: Request,
    body: LoginRequest,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
):
    service = AuthService(db)
    user, access, refresh = service.login(body.email, body.password)
    _set_auth_cookies(response, access, refresh)
    return _auth_result(user, access)


# â”€â”€ Phone auth (flux principal) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


@router.post("/register-phone", response_model=AuthResultResponse, status_code=201)
@limiter.limit("5/15minute")
def register_phone(
    request: Request,
    body: RegisterPhoneRequest,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
):
    """Inscription par numéro WhatsApp — sans OTP.

    Le numéro est auto-déclaré : on incite l'utilisateur à renseigner son vrai
    numéro WhatsApp via la promesse "recevoir les opportunités" (cf. copie
    onboarding côté frontend), plutôt que de dépendre de la fiabilité de
    livraison WhatsApp pour vérifier sa possession à l'inscription.
    """
    service = AuthService(db)
    user, access, refresh = service.register_phone(body)
    db.commit()
    _set_auth_cookies(response, access, refresh)
    return _auth_result(user, access)


@router.post("/check-phone")
@limiter.limit("10/minute")
def check_phone(
    request: Request,
    body: CheckPhoneRequest,
    db: Annotated[Session, Depends(get_db)],
):
    """Vérifie si un numéro de téléphone est déjà associé à un compte.

    Utilisé à l'étape 1 de l'inscription pour informer l'utilisateur
    avant l'envoi de l'OTP, sans révéler d'informations sensibles au-delà
    de l'existence du compte.
    """
    from app.repositories.user_repo import UserRepository
    normalized = AuthService._normalize_phone(body.phone)
    exists = UserRepository(db).get_by_phone(normalized) is not None
    return {"exists": exists}


@router.post("/forgot-password")
@limiter.limit("3/15minute")
async def forgot_password(
    request: Request,
    body: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
):
    """Envoie un OTP WhatsApp pour réinitialiser le mot de passe.

    Retourne toujours {"ok": true} même si le numéro n'existe pas en base
    afin d'éviter l'énumération de comptes (account enumeration).
    """
    return await AuthService.send_otp_static(body.phone, background_tasks)


@router.post("/reset-password")
@limiter.limit("5/15minute")
def reset_password(
    request: Request,
    body: ResetPasswordRequest,
    db: Annotated[Session, Depends(get_db)],
):
    """Réinitialise le mot de passe après vérification de l'OTP WhatsApp."""
    service = AuthService(db)
    service.reset_password(body.phone, body.code, body.new_password)
    db.commit()
    return {"ok": True}


@router.post("/login-phone", response_model=AuthResultResponse)
@limiter.limit("5/minute")
def login_phone(
    request: Request,
    body: LoginPhoneRequest,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
):
    """Connexion par tÃ©lÃ©phone + mot de passe."""
    service = AuthService(db)
    user, access, refresh = service.login_phone(body.phone, body.password)
    _set_auth_cookies(response, access, refresh)
    return _auth_result(user, access)


# â”€â”€ Session commune â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


@router.post("/refresh")
@limiter.limit("10/minute")
def refresh_token(
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    refresh_token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None,
):
    from app.core.exceptions import UnauthorizedError

    if not refresh_token:
        raise UnauthorizedError(f"Cookie {REFRESH_COOKIE} manquant.")
    service = AuthService(db)
    new_access, new_refresh = service.refresh(refresh_token)
    _set_auth_cookies(response, new_access, new_refresh)
    # Retourner le nouvel access token dans le corps pour que le frontend
    # puisse mettre à jour son token en mémoire (sans relire le cookie httpOnly).
    return {"accessToken": new_access}

@router.get("/me", response_model=MeResponse)
def get_me(
    current_user: Annotated[User, Depends(get_current_user)],
):
    return {"user": {"id": str(current_user.id), "email": current_user.email, "phone": current_user.phone, "role": current_user.role.value if current_user.role else "b2c", "is_active": current_user.is_active, "created_at": current_user.created_at.isoformat()}}


@router.post("/change-password")
@limiter.limit("5/hour")
def change_password(
    request: Request,
    body: ChangePasswordRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Change le mot de passe de l'utilisateur connecté."""
    service = AuthService(db)
    service.change_password(current_user, body.old_password, body.new_password)
    db.commit()
    return {"detail": "Mot de passe modifié."}


@router.post("/logout")
def logout(
    response: Response,
    refresh_token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None,
):
    if refresh_token:
        from app.core.security import decode_token
        from app.services.token_blacklist import revoke
        try:
            payload = decode_token(refresh_token)
            revoke(refresh_token, exp=int(payload.get("exp", 0)))
        except Exception:
            pass  # token invalide ou expiré — les cookies sont supprimés dans tous les cas
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path="/")
    return {"detail": "Déconnecté."}


@router.delete("/me", status_code=204)
def delete_account(
    response: Response,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    refresh_token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None,
):
    """Supprime définitivement le compte et toutes les données associées.

    La suppression cascade (goals, threads, messages, documents, intentions,
    notifications, feedbacks) est assurée par les relations SQLAlchemy avec
    cascade='all, delete-orphan' sur le modèle User.
    Les cookies de session sont effacés immédiatement.
    """
    # Invalider le refresh token si présent
    if refresh_token:
        from app.core.security import decode_token
        from app.services.token_blacklist import revoke
        try:
            payload = decode_token(refresh_token)
            revoke(refresh_token, exp=int(payload.get("exp", 0)))
        except Exception:
            pass

    # Supprimer le compte (cascade sur toutes les données)
    db.delete(current_user)
    db.commit()

    # Effacer les cookies de session
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path="/")

