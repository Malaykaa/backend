"""Noms des cookies HTTP utilisés par l'authentification.

Un seul endroit à modifier si les noms changent en production.
Importé par : auth.py (set/delete), deps.py (lecture), rate_limit.py (lecture).
"""

ACCESS_COOKIE: str = "mlk_access"
REFRESH_COOKIE: str = "mlk_refresh"
