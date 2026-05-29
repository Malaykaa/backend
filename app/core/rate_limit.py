"""Rate-limiter partagé (slowapi).

Deux clés disponibles :
- get_remote_address  (IP distante) — pour les endpoints auth/OTP où l'on
  veut bloquer les attaques distribuées avant authentification.
- get_user_key        (user_id JWT, fallback IP) — pour les endpoints LLM/SSE
  où l'on veut limiter la consommation par compte authentifié.
"""

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.cookies import ACCESS_COOKIE

limiter = Limiter(key_func=get_remote_address)


def get_user_key(request: Request) -> str:
    """Clé de rate-limit basée sur l'user_id extrait du JWT.

    Lit le cookie ACCESS_COOKIE, ou le header Authorization Bearer.
    Retourne 'user:{sub}' si le token est valide, sinon l'IP distante.
    Utilisé pour les endpoints LLM/SSE afin de limiter par compte.
    """
    from app.core.security import decode_token  # import local — évite circularité au boot

    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    if token:
        try:
            payload = decode_token(token)
            sub = payload.get("sub")
            if sub:
                return f"user:{sub}"
        except Exception:
            pass
    return get_remote_address(request)
