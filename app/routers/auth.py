"""Routes d'authentification â€” couche mince HTTP."""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Cookie, Depends, Request, Response
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.rate_limit import limiter
from app.models.user import User
from app.schemas.auth import (
    AuthResultResponse,
    LoginPhoneRequest,
    LoginRequest,
    MeResponse,
    RegisterRequest,
    SendOtpRequest,
    VerifyOtpRegisterRequest,
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
        "99eange_jwt",
        access,
        max_age=settings.access_token_expire_minutes * 60,
        **_COOKIE_KWARGS,
    )
    response.set_cookie(
        "99eange_rt",
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


# â”€â”€ Phone OTP auth (flux principal) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


@router.post("/send-otp")
@limiter.limit("3/15minute")
async def send_otp(
    request: Request,
    body: SendOtpRequest,
    background_tasks: BackgroundTasks,
):
    """Envoie un OTP par WhatsApp â€” l'envoi WhatsApp est diffÃ©rÃ© en background
    pour rÃ©pondre 200 immÃ©diatement mÃªme si le provider rame. Le code OTP
    est stockÃ© dans Redis avant le scheduling, donc /verify-otp reste valide.
    """
    from app.services.auth_service import AuthService  # noqa: PLC0415

    return await AuthService.send_otp_static(body.phone, background_tasks)


@router.post("/verify-otp-register", response_model=AuthResultResponse, status_code=201)
@limiter.limit("10/minute")
def verify_otp_register(
    request: Request,
    body: VerifyOtpRegisterRequest,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
):
    """VÃ©rifie l'OTP et crÃ©e le compte utilisateur."""
    service = AuthService(db)
    user, access, refresh = service.verify_otp_register(body)
    db.commit()
    _set_auth_cookies(response, access, refresh)
    return _auth_result(user, access)


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
    refresh_token: Annotated[str | None, Cookie(alias="99eange_rt")] = None,
):
    from app.core.exceptions import UnauthorizedError

    if not refresh_token:
        raise UnauthorizedError("Cookie 99eange_rt manquant.")
    service = AuthService(db)
    new_access, new_refresh = service.refresh(refresh_token)
    _set_auth_cookies(response, new_access, new_refresh)
    return {"detail": "Token rafraîchi."}

@router.get("/me", response_model=MeResponse)
def get_me(
    current_user: Annotated[User, Depends(get_current_user)],
):
    return {"user": {"id": str(current_user.id), "email": current_user.email, "phone": current_user.phone, "role": current_user.role.value if current_user.role else "b2c", "is_active": current_user.is_active, "created_at": current_user.created_at.isoformat()}}


@router.post("/logout")
def logout(
    response: Response,
    refresh_token: Annotated[str | None, Cookie(alias="99eange_rt")] = None,
):
    if refresh_token:
        from app.core.security import decode_token
        from app.services.token_blacklist import revoke
        try:
            payload = decode_token(refresh_token)
            revoke(refresh_token, exp=int(payload.get("exp", 0)))
        except Exception:
            pass  # token invalide ou expiré — les cookies sont supprimés dans tous les cas
    response.delete_cookie("99eange_jwt", path="/")
    response.delete_cookie("99eange_rt", path="/")
    return {"detail": "Déconnecté."}

