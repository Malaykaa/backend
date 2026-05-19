"""Service WhatsApp — envoi d'OTP via OneMessage (provider principal).

Mode DEV  : si aucun provider configuré, l'OTP est loggé en console.
Mode PROD : OneMessage est obligatoire (validé au boot via validate_security_settings).

Provider Twilio conservé en fallback pour rollback rapide en cas
d'incident OneMessage (cf. _send_via_twilio plus bas).
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Délai d'attente HTTP (en secondes). Le provider OneMessage est synchrone
# et peut prendre ~80s à confirmer un envoi en mode dégradé. L'envoi étant
# lancé via FastAPI BackgroundTasks après le 200 client, ce timeout n'impacte
# pas l'UX — il borne juste la durée de vie de la task background.
_HTTP_TIMEOUT = 90.0


class WhatsAppService:
    def __init__(self) -> None:
        settings = get_settings()
        self._has_onemessage = bool(
            settings.onemessage_base_url and settings.onemessage_api_key
        )
        self._has_twilio = bool(
            settings.twilio_account_sid
            and settings.twilio_auth_token
            and settings.twilio_whatsapp_from
        )
        self._mock_accept_any = settings.otp_mock_accept_any

        if self._mock_accept_any:
            logger.warning(
                "[OTP_MOCK] OTP_MOCK_ACCEPT_ANY=true — tout code à 6 chiffres "
                "est accepté. Désactiver dès que OneMessage est configuré."
            )

    @property
    def mock_accept_any(self) -> bool:
        return self._mock_accept_any

    @property
    def has_onemessage(self) -> bool:
        return self._has_onemessage

    @property
    def has_twilio(self) -> bool:
        return self._has_twilio

    async def send_otp(self, phone: str, code: str) -> None:
        """Envoie un OTP. OneMessage en prod, fallback Twilio si activé, log en dev."""
        body = (
            f"Votre code de vérification Malayka : {code}. "
            "Valable 10 minutes."
        )
        if self._has_onemessage:
            await self._send_via_onemessage(phone, body)
            return
        if self._has_twilio:
            await self._send_via_twilio(phone, body)
            return
        logger.info("[OTP-DEV] Code pour %s : %s", phone, code)

    async def send_message(self, phone: str, body: str) -> None:
        """Envoie un message WhatsApp libre (notifications, recommandations…).

        Même politique de routage que send_otp : OneMessage > Twilio > log.
        """
        if self._has_onemessage:
            await self._send_via_onemessage(phone, body)
            return
        if self._has_twilio:
            await self._send_via_twilio(phone, body)
            return
        logger.info("[WA-DEV] Message pour %s : %s", phone, body)

    async def _send_via_onemessage(self, phone: str, body: str) -> None:
        """POST {base_url}/api/send avec header X-API-Key.

        Body attendu par l'API : {"to": phone, "text": body}.

        Le format ``to`` doit être en chiffres uniquement (sans ``+``) sinon
        OneMessage acquitte HTTP 200 mais ne livre pas réellement le message
        (et la requête met ~77s à répondre au lieu de ~5s).
        """
        import re  # noqa: PLC0415

        settings = get_settings()
        url = f"{settings.onemessage_base_url.rstrip('/')}/api/send"
        headers = {"X-API-Key": settings.onemessage_api_key}
        payload = {"to": re.sub(r"\D", "", phone), "text": body}

        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # On ne log PAS le payload pour ne pas écrire le code OTP en clair.
            logger.error(
                "[OneMessage] Échec envoi à %s : HTTP %s — %s",
                phone, exc.response.status_code, exc.response.text[:200],
            )
            raise
        except httpx.TimeoutException as exc:
            # Best effort : si OneMessage rame/est down, l'OTP est déjà stocké
            # dans Redis (cf. AuthService.send_otp_static), donc le flow de
            # vérification reste valide. On évite juste de propager un 500
            # qui bloquerait l'utilisateur côté frontend.
            logger.warning(
                "[OneMessage] Timeout pour %s (best effort, OTP déjà en Redis) : %s",
                phone, exc,
            )
            return
        except httpx.HTTPError as exc:
            logger.error("[OneMessage] Erreur réseau pour %s : %s", phone, exc)
            raise
        logger.info("[OneMessage] Message envoyé à %s", phone)

    async def _send_via_twilio(self, phone: str, body: str) -> None:
        """Fallback Twilio — exécuté hors event loop (HTTP bloquant).

        Conservé pour rollback rapide ; ne pas supprimer sans confirmation
        que OneMessage est stable en production.
        """
        import asyncio  # noqa: PLC0415
        from twilio.base.exceptions import TwilioRestException  # noqa: PLC0415
        from twilio.rest import Client  # noqa: PLC0415

        settings = get_settings()
        client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
        try:
            await asyncio.to_thread(
                client.messages.create,
                body=body,
                from_=settings.twilio_whatsapp_from,
                to=f"whatsapp:{phone}",
            )
        except TwilioRestException as exc:
            logger.error("[Twilio] Échec envoi à %s : %s", phone, exc)
            raise
        logger.info("[Twilio] Message envoyé à %s", phone)


whatsapp_service = WhatsAppService()
