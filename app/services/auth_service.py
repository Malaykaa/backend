"""Service d'authentification — logique métier (pas de SQL ici)."""

import re
import uuid

from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError, ConflictError, UnauthorizedError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.user import User
from app.repositories.user_repo import UserRepository
from app.schemas.auth import RegisterRequest, VerifyOtpRegisterRequest
from app.services.otp_service import otp_service
from app.services.whatsapp_service import whatsapp_service


class AuthService:
    def __init__(self, db: Session) -> None:
        self.user_repo = UserRepository(db)

    # ── Utilitaires ──────────────────────────────────────

    @staticmethod
    def _normalize_phone(phone: str) -> str:
        return re.sub(r"\D", "", phone)

    @staticmethod
    def _phone_to_email(phone: str) -> str:
        """Génère un email synthétique pour les utilisateurs inscrits par téléphone."""
        return f"{AuthService._normalize_phone(phone)}@malaykaa.app"

    def _generate_tokens(self, user_id: str) -> tuple[str, str]:
        return create_access_token(user_id), create_refresh_token(user_id)

    # ── Inscription par email (option secondaire) ────────

    def register(self, data: RegisterRequest) -> tuple[User, str, str]:
        """Inscription par email — retourne (user, access_token, refresh_token)."""
        existing = self.user_repo.get_by_email(data.email)
        if existing:
            raise ConflictError("Un compte avec cet email existe déjà.")

        hashed = hash_password(data.password)
        user = self.user_repo.create_with_profile(
            email=data.email,
            password_hash=hashed,
            first_name=data.first_name,
            last_name=data.last_name,
        )

        access, refresh = self._generate_tokens(str(user.id))
        return user, access, refresh

    # ── Connexion par email ──────────────────────────────

    def login(self, email: str, password: str) -> tuple[User, str, str]:
        """Connexion par email — retourne (user, access_token, refresh_token)."""
        user = self.user_repo.get_by_email(email)
        if not user or not verify_password(password, user.password_hash):
            raise UnauthorizedError("Email ou mot de passe incorrect.")
        if not user.is_active:
            raise UnauthorizedError("Compte désactivé.")

        user = self.user_repo.get_with_profile(user.id) or user

        access, refresh = self._generate_tokens(str(user.id))
        return user, access, refresh

    # ── OTP WhatsApp (flux principal) ────────────────────

    @staticmethod
    async def send_otp_static(
        phone: str,
        background_tasks: BackgroundTasks | None = None,
    ) -> dict:
        """Génère un OTP, le stocke en Redis, et envoie par WhatsApp.

        Si ``background_tasks`` est fourni (via le router HTTP), l'envoi
        WhatsApp est différé après la réponse 200 — utile quand le provider
        rame (le code est déjà en Redis donc /verify-otp marche tout de suite).
        Sans BackgroundTasks (appels directs en tests/CLI), comportement
        synchrone d'origine conservé.
        """
        normalized = AuthService._normalize_phone(phone)
        if len(normalized) < 8:
            raise BadRequestError("Numéro de téléphone invalide.")

        otp_service.check_send_rate(phone)  # max 3 envois/heure par numéro
        code = otp_service.generate_code()
        otp_service.store(phone, code)
        if background_tasks is not None:
            background_tasks.add_task(whatsapp_service.send_otp, phone, code)
        else:
            await whatsapp_service.send_otp(phone, code)
        return {"ok": True}

    def verify_otp_register(self, data: VerifyOtpRegisterRequest) -> tuple[User, str, str]:
        """Vérifie l'OTP et crée le compte — retourne (user, access_token, refresh_token)."""
        normalized = self._normalize_phone(data.phone)

        # Vérification du code OTP
        code_valid = self._verify_otp_code(data.phone, data.code)
        if not code_valid:
            raise BadRequestError("Code OTP invalide ou expiré.")

        # Vérifier que le numéro n'est pas déjà utilisé
        existing = self.user_repo.get_by_phone(normalized)
        if existing:
            raise ConflictError("Ce numéro est déjà utilisé.")

        hashed = hash_password(data.password)
        user = self.user_repo.create_with_profile(
            email=None,
            password_hash=hashed,
            phone=normalized,
            first_name=data.first_name,
            last_name=data.last_name,
            gender=data.gender.value if data.gender else None,
            birth_year=data.birth_year,
            country=data.country,
            primary_role=data.primary_role.value if data.primary_role else None,
        )

        access, refresh = self._generate_tokens(str(user.id))
        return user, access, refresh

    def login_phone(self, phone: str, password: str) -> tuple[User, str, str]:
        """Connexion par téléphone + mot de passe."""
        normalized = self._normalize_phone(phone)
        user = self.user_repo.get_by_phone(normalized)
        if not user or not verify_password(password, user.password_hash):
            raise UnauthorizedError("Téléphone ou mot de passe incorrect.")
        if not user.is_active:
            raise UnauthorizedError("Compte désactivé.")

        user = self.user_repo.get_with_profile(user.id) or user

        access, refresh = self._generate_tokens(str(user.id))
        return user, access, refresh

    # ── Refresh & profil ─────────────────────────────────

    def refresh(self, refresh_token: str) -> tuple[str, str]:
        """Rafraîchit la session par rotation du refresh token.

        Révoque l'ancien refresh token, émet un nouveau couple
        (access_token, refresh_token). Un token volé ne peut donc
        être utilisé qu'une seule fois avant invalidation.

        Retourne : (new_access_token, new_refresh_token)
        """
        from app.services.token_blacklist import is_revoked, revoke

        try:
            payload = decode_token(refresh_token)
        except ValueError:
            raise UnauthorizedError("Refresh token invalide ou expiré.")

        if payload.get("type") != "refresh":
            raise UnauthorizedError("Type de token invalide.")

        if is_revoked(refresh_token):
            raise UnauthorizedError("Session expirée. Veuillez vous reconnecter.")

        user_id = payload.get("sub")
        if not user_id:
            raise UnauthorizedError("Token invalide.")

        user = self.user_repo.get_by_id(uuid.UUID(user_id))
        if not user or not user.is_active:
            raise UnauthorizedError("Utilisateur introuvable ou inactif.")

        # Rotation : révoquer l'ancien token avant d'émettre le nouveau.
        # Non bloquant si Redis est down — la rotation côté cookie a lieu dans tous les cas.
        try:
            revoke(refresh_token, exp=int(payload.get("exp", 0)))
        except Exception:
            pass

        new_access = create_access_token(str(user.id))
        new_refresh = create_refresh_token(str(user.id))
        return new_access, new_refresh

    def get_user_with_profile(self, user_id: uuid.UUID) -> User:
        """Retourne l'utilisateur avec son profil chargé."""
        user = self.user_repo.get_with_profile(user_id)
        if not user:
            raise UnauthorizedError("Utilisateur introuvable.")
        return user

    def change_password(self, user: User, old_password: str, new_password: str) -> None:
        """Change le mot de passe d'un utilisateur authentifié.

        Vérifie l'ancien mot de passe avant d'accepter le nouveau.
        L'appelant est responsable du db.commit().
        """
        if not verify_password(old_password, user.password_hash):
            raise UnauthorizedError("Mot de passe actuel incorrect.")
        user.password_hash = hash_password(new_password)

    def reset_password(self, phone: str, code: str, new_password: str) -> None:
        """Réinitialise le mot de passe via OTP WhatsApp (utilisateur non authentifié).

        Flow : /auth/forgot-password envoie l'OTP → l'utilisateur le saisit ici
        avec son nouveau mot de passe. Retourne toujours la même erreur générique
        que l'OTP soit invalide ou que le numéro n'existe pas (évite l'énumération).
        L'appelant est responsable du db.commit().
        """
        if not self._verify_otp_code(phone, code):
            raise BadRequestError("Code OTP invalide ou expiré.")

        normalized = self._normalize_phone(phone)
        user = self.user_repo.get_by_phone(normalized)
        if not user:
            raise BadRequestError("Code OTP invalide ou expiré.")

        if not user.is_active:
            raise UnauthorizedError("Compte désactivé.")

        user.password_hash = hash_password(new_password)

    # ── Privé ────────────────────────────────────────────

    def _verify_otp_code(self, phone: str, code: str) -> bool:
        """Vérifie le code OTP.

        En dev avec OTP_MOCK_ACCEPT_ANY=true, accepte tout code à 6 chiffres.
        Sinon (et toujours en prod via le fail-fast au boot), vérifie le code
        réellement émis et stocké dans Redis.
        """
        if whatsapp_service.mock_accept_any:
            return bool(re.fullmatch(r"\d{6}", code))
        return otp_service.verify(phone, code)
