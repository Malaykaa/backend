"""Rate-limiter partagé (slowapi).

Clé par IP distante. Le limiter est exposé pour être appliqué via décorateur
sur les endpoints sensibles (auth, OTP) et enregistré dans main.py via
app.state.limiter + handler RateLimitExceeded.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
