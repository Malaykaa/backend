"""Service d'embeddings — wrapper Perplexity pplx-embed-v1-4b.

L'API Perplexity expose POST /v1/embeddings, compatible avec le format
OpenAI (Bearer + body {model, input}). On utilise httpx directement
plutôt que le SDK OpenAI pour éviter le coupling.

Format de réponse réel (vérifié expérimentalement) :
    Le champ `embedding` de chaque item est une chaîne base64 encodant
    2560 entiers int8 signés (quantification 8 bits). On décode :
        base64 → bytes (2560 octets) → struct.unpack('2560b') → list[int]
        puis normalise vers float en divisant par 128.0 → list[float]
    La similarité cosinus fonctionne directement sur ces valeurs normalisées.

Comportement défensif :
- PERPLEXITY_API_KEY absent → log warning + return None (jamais crash).
- Format inconnu → log warning + return None.
- Erreur réseau / HTTP → log + return None.
"""

from __future__ import annotations

import base64
import logging
import struct
from typing import Any, Iterable

import httpx

from app.core.config import get_settings
from app.models.scraped_offer import EMBEDDING_DIM

logger = logging.getLogger(__name__)

PERPLEXITY_EMBEDDINGS_URL = "https://api.perplexity.ai/v1/embeddings"
PERPLEXITY_MODEL = "pplx-embed-v1-4b"

_HTTP_TIMEOUT = 15.0
_MAX_BATCH_SIZE = 64
# Facteur de normalisation int8 → float (max int8 = 127 ≈ 128)
_INT8_SCALE = 128.0


class EmbeddingService:
    """Singleton ; instancier via get_embedding_service()."""

    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.perplexity_api_key
        self._available = bool(self._api_key)
        if not self._available:
            logger.warning(
                "[Embeddings] PERPLEXITY_API_KEY absent — embeddings désactivés. "
                "La recherche sémantique retombera sur ILIKE."
            )

    @property
    def available(self) -> bool:
        return self._available

    @property
    def dimension(self) -> int:
        return EMBEDDING_DIM

    async def embed(self, text: str) -> list[float] | None:
        """Embedding d'un texte unique. None si provider indisponible / erreur."""
        if not self._available:
            return None
        text = (text or "").strip()
        if not text:
            return None
        results = await self._call([text])
        if results is None or not results:
            return None
        return results[0]

    async def embed_batch(self, texts: Iterable[str]) -> list[list[float] | None]:
        """Embeddings d'une liste de textes (None par item en cas d'échec).

        Découpe en sous-batches de _MAX_BATCH_SIZE pour ne pas dépasser les
        limites du provider. Préserve l'ordre d'entrée.
        """
        cleaned = [(i, (t or "").strip()) for i, t in enumerate(texts)]
        if not cleaned:
            return []

        results: list[list[float] | None] = [None] * len(cleaned)
        if not self._available:
            return results

        # On envoie uniquement les textes non vides au provider, mais on
        # remplit les résultats à l'index original pour préserver l'ordre.
        non_empty = [(i, t) for i, t in cleaned if t]
        for chunk_start in range(0, len(non_empty), _MAX_BATCH_SIZE):
            chunk = non_empty[chunk_start : chunk_start + _MAX_BATCH_SIZE]
            chunk_texts = [t for _, t in chunk]
            chunk_vectors = await self._call(chunk_texts)
            if chunk_vectors is None:
                continue  # tous les items du chunk restent à None
            for (orig_idx, _), vec in zip(chunk, chunk_vectors):
                results[orig_idx] = vec
        return results

    async def _call(self, inputs: list[str]) -> list[list[float]] | None:
        """POST /embeddings ; retourne la liste de vecteurs ou None sur erreur."""
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {"model": PERPLEXITY_MODEL, "input": inputs}
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                response = await client.post(
                    PERPLEXITY_EMBEDDINGS_URL, json=payload, headers=headers,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "[Embeddings] HTTP %s sur Perplexity : %s",
                exc.response.status_code, exc.response.text[:200],
            )
            return None
        except httpx.HTTPError as exc:
            logger.error("[Embeddings] Erreur réseau Perplexity : %s", exc)
            return None
        except ValueError as exc:  # JSON invalide
            logger.error("[Embeddings] Réponse non-JSON : %s", exc)
            return None

        items = data.get("data") or []
        items_sorted = sorted(items, key=lambda x: x.get("index", 0))

        vectors: list[list[float] | None] = []
        for item in items_sorted:
            raw = item.get("embedding")
            vec = _decode_embedding(raw)
            vectors.append(vec)

        if any(v is None for v in vectors) or len(vectors) != len(inputs):
            logger.warning(
                "[Embeddings] Réponse incomplète : %d vecteurs décodés pour %d entrées",
                sum(v is not None for v in vectors), len(inputs),
            )
            return None
        return vectors  # type: ignore[return-value]


def _decode_embedding(raw: Any) -> list[float] | None:
    """Décode l'embedding retourné par Perplexity.

    Format actuel : chaîne base64 → 2560 octets int8 signés → floats normalisés.
    Fallback : si `raw` est déjà une liste de floats, on la retourne directement
    (rétro-compatibilité si l'API change à nouveau de format).
    """
    if raw is None:
        return None

    # Cas déjà décodé (liste de nombres)
    if isinstance(raw, list):
        if not raw:
            return None
        try:
            return [float(x) for x in raw]
        except (TypeError, ValueError):
            logger.warning("[Embeddings] liste non-numérique dans embedding")
            return None

    # Cas base64 binaire (format actuel pplx-embed-v1-4b)
    if isinstance(raw, str):
        try:
            data = base64.b64decode(raw)
        except Exception:
            logger.warning("[Embeddings] base64 invalide dans embedding")
            return None

        n = len(data)
        if n != EMBEDDING_DIM:
            logger.warning(
                "[Embeddings] Dimension inattendue : %d octets (attendu %d). "
                "Vérifier EMBEDDING_DIM ou le modèle Perplexity.",
                n, EMBEDDING_DIM,
            )
            # On tente quand même le décodage si la taille est cohérente
            if n == 0:
                return None

        try:
            ints = struct.unpack(f"{n}b", data)   # int8 signés
            return [float(x) / _INT8_SCALE for x in ints]
        except struct.error as exc:
            logger.warning("[Embeddings] struct.unpack int8 failed: %s", exc)
            return None

    logger.warning("[Embeddings] type inattendu pour embedding: %s", type(raw).__name__)
    return None


_singleton: EmbeddingService | None = None


def get_embedding_service() -> EmbeddingService:
    """Accès singleton — reflète la valeur de PERPLEXITY_API_KEY au boot."""
    global _singleton
    if _singleton is None:
        _singleton = EmbeddingService()
    return _singleton
