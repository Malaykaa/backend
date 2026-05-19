"""Configuration centralisée du logging structuré (structlog + stdlib).

Un seul appel — configure_logging() — à faire au démarrage (main.py).

Produit :
- En développement : logs colorés lisibles par un humain
- En production    : logs JSON ligne-par-ligne (compatible Datadog, Loki, CloudWatch)

Le module expose aussi bind_request_context() pour que le middleware HTTP
attache méthode, path et request_id à chaque log de la requête courante.
"""

from __future__ import annotations

import logging
import logging.config
import sys
import uuid

import structlog


def configure_logging(environment: str = "local") -> None:
    """Configure structlog et le logging stdlib de façon cohérente.

    À appeler une seule fois au démarrage de l'application.
    """
    shared_processors: list = [
        structlog.contextvars.merge_contextvars,          # injecte les contextvars (user_id, thread_id…)
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    is_prod = environment in {"staging", "prod"}

    if is_prod:
        # JSON compact pour les agrégateurs de logs (Datadog, Loki, etc.)
        renderer = structlog.processors.JSONRenderer()
    else:
        # Console colorée pour le développement
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Redirige le logging stdlib vers structlog pour que les librairies tierces
    # (SQLAlchemy, uvicorn, etc.) passent par la même sortie formatée.
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(logging.INFO)

    # Réduire le bruit des librairies très verboses
    for noisy in ("sqlalchemy.engine", "apscheduler", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def bind_request_context(
    method: str,
    path: str,
    user_id: str | None = None,
) -> str:
    """Lie le contexte de la requête aux logs structurés.

    Retourne le request_id généré pour pouvoir le mettre dans les headers.
    Doit être appelé en début de requête, APRÈS clear_contextvars().
    """
    request_id = str(uuid.uuid4())[:8]
    structlog.contextvars.clear_contextvars()
    ctx: dict = {"request_id": request_id, "method": method, "path": path}
    if user_id:
        ctx["user_id"] = user_id
    structlog.contextvars.bind_contextvars(**ctx)
    return request_id


def bind_chat_context(thread_id: str, user_id: str | None = None) -> None:
    """Lie thread_id (et optionnellement user_id) au contexte courant.

    À appeler dans ChatService.stream_message() / handle_message().
    """
    ctx: dict = {"thread_id": thread_id}
    if user_id:
        ctx["user_id"] = user_id
    structlog.contextvars.bind_contextvars(**ctx)
