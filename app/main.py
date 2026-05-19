from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from structlog import get_logger

from app.core.config import get_settings, validate_security_settings
from app.core.logging_config import bind_request_context, configure_logging
from app.core.rate_limit import limiter
from app.routers import auth, documents, files, goals, opportunities, plans, tracking, users
from app.routers import action_adapter, admin_scraping, admin_router, chat, recommendations_router

settings = get_settings()

# Configurer structlog dÃ¨s que possible â€” avant toute instanciation de logger.
configure_logging(environment=settings.environment)

logger = get_logger(__name__)

# Fail-fast au boot : refuse de dÃ©marrer en staging/prod si la config est dangereuse.
validate_security_settings(settings)

app = FastAPI(
    title="Malayka API",
    version="0.1.0",
    description="Moteur d'exÃ©cution et d'opportunitÃ©s â€” API Backend",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    # Bind le contexte de la requÃªte â€” tous les logs dans ce scope l'hÃ©ritent.
    request_id = bind_request_context(
        method=request.method,
        path=request.url.path,
    )
    response = await call_next(request)
    logger.info(
        "request_completed",
        status_code=response.status_code,
    )
    # Expose le request_id dans les headers pour faciliter le debug cÃ´tÃ© client.
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error("unhandled_exception", path=request.url.path, error=str(exc))
    return JSONResponse(status_code=500, content={"detail": "Erreur interne du serveur."})


app.include_router(auth.router)
app.include_router(users.router)
# Router /chat unifiÃ© â€” sert Ã  la fois les routes de base et les routes
# enrichies (presetKey, plan, pour-moiâ€¦) historiquement portÃ©es par
# frontend_adapter, dÃ©sormais consolidÃ©es dans un seul module.
app.include_router(chat.router)
# Adapter actions/livrables : expose les endpoints SSE /ai/actions/* pour le frontend
app.include_router(action_adapter.router)
app.include_router(documents.router)
app.include_router(files.router)
app.include_router(goals.router)
app.include_router(opportunities.router)
app.include_router(plans.router)
app.include_router(tracking.router)
# Admin backoffice complet
app.include_router(admin_router.router)
# Admin scraping : endpoints de dÃ©clenchement manuel + stats
app.include_router(admin_scraping.router)
app.include_router(recommendations_router.router)


@app.on_event("startup")
async def _startup() -> None:
    from app.services.scraping.scheduler import start_scheduler
    start_scheduler()


@app.on_event("shutdown")
async def _shutdown() -> None:
    from app.services.scraping.scheduler import stop_scheduler
    stop_scheduler()


@app.get("/health", tags=["system"])
def health_check() -> dict[str, str]:
    """Liveness probe — vérifie que le process répond. Ne teste pas les dépendances."""
    return {"status": "ok", "environment": settings.environment}


@app.get("/health/ready", tags=["system"])
def health_ready():
    """Readiness probe — vérifie que DB et Redis sont accessibles.

    Retourne 200 si tout est opérationnel, 503 (degraded) sinon.
    Utilisé par les load balancers et les orchestrateurs (Docker, K8s…)
    pour sortir un nœud de la rotation si ses dépendances sont hors-ligne.
    """
    from app.core.database import engine
    import redis as redis_lib

    checks: dict[str, str] = {}

    # ── PostgreSQL ──────────────────────────────────────────────────────────
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = f"error: {exc}"

    # ── Redis ───────────────────────────────────────────────────────────────
    try:
        client = redis_lib.from_url(settings.redis_url, socket_timeout=2, socket_connect_timeout=2)
        client.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {exc}"

    all_ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if all_ok else 503,
        content={"status": "ok" if all_ok else "degraded", "checks": checks},
    )

