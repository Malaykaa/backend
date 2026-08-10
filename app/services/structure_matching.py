"""Normalisation de nom — utilisée pour le matching d'invitation/roster.

Une seule règle, réutilisée par l'invitation enseignant (Phase 1) et le
roster étudiant (Phase 2) : minuscules, sans accents, espaces compressés.
Évite que deux implémentations divergent avec le temps.
"""

from __future__ import annotations

import re
import unicodedata

_MULTI_SPACE_RE = re.compile(r"\s+")


def normalize_name(*parts: str) -> str:
    """Normalise un nom pour comparaison.

    normalize_name("Kofé", "MENSAH") == normalize_name("kofe", "  mensah") == "kofe mensah"
    """
    joined = " ".join(p.strip() for p in parts if p)
    nfkd = unicodedata.normalize("NFD", joined.lower())
    without_accents = "".join(c for c in nfkd if not unicodedata.combining(c))
    return _MULTI_SPACE_RE.sub(" ", without_accents).strip()
