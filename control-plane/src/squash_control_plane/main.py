import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import text

from . import models  # noqa: F401
from .agent_channel import router as agent_channel_router
from .api_clients import router as api_clients_router
from .audit_api import router as audit_router
from .auth import Caller
from .config import get_settings
from .connections import ConnectionManager
from .console import router as console_router
from .database import Database
from .devices import router as devices_router
from .enrollment import router as enrollment_router
from .event_logs import router as event_logs_router
from .executions import expire_jobs
from .executions import router as executions_router
from .inventory import router as inventory_router
from .rate_limit import FixedWindowRateLimiter
from .schemas import HealthResponse
from .sensitive import configure_sensitive_fields


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    settings.validate_security()
    configure_sensitive_fields(
        settings.data_encryption_key,
        allow_plaintext=settings.environment.lower() == "development",
    )
    database = Database(settings.database_url)
    app.state.settings = settings
    app.state.database = database
    if settings.environment.lower() == "development":
        database.create_development_schema()

    app.state.connections = ConnectionManager(database, settings.output_limit_bytes_per_stream)

    async def lifecycle_loop() -> None:
        while True:
            await asyncio.to_thread(
                expire_jobs,
                database,
                settings.execution_timeout_grace_seconds,
            )
            await asyncio.sleep(1)

    lifecycle_task = asyncio.create_task(lifecycle_loop())
    try:
        yield
    finally:
        lifecycle_task.cancel()
        await asyncio.gather(lifecycle_task, return_exceptions=True)
        database.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Squash RMM Control Plane",
        version="0.1.0",
        description="Authenticated execution channel for enrolled Windows endpoints.",
        lifespan=lifespan,
    )
    rate_limiter = FixedWindowRateLimiter(get_settings().api_rate_limit_per_minute)
    app.include_router(agent_channel_router)
    app.include_router(api_clients_router)
    app.include_router(audit_router)
    app.include_router(console_router)
    app.include_router(devices_router)
    app.include_router(enrollment_router)
    app.include_router(event_logs_router)
    app.include_router(executions_router)
    app.include_router(inventory_router)

    @app.get("/", include_in_schema=False)
    async def index() -> RedirectResponse:
        return RedirectResponse(url="/console", status_code=307)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > 5 * 1024 * 1024:
            return JSONResponse(status_code=413, content={"detail": "Request body is too large"})
        if request.url.path.startswith("/v1/"):
            identity = (
                request.headers.get("x-api-key")
                or request.headers.get("authorization")
                or (request.client.host if request.client else "unknown")
            )
            if not await rate_limiter.allow(identity):
                return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'"
        )
        return response

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    async def health() -> HealthResponse:
        return HealthResponse()

    @app.get("/ready", response_model=HealthResponse, tags=["system"])
    async def ready(request: Request) -> HealthResponse:
        with request.app.state.database.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return HealthResponse()

    @app.get(
        "/v1/auth/check",
        tags=["system"],
        dependencies=[Depends(get_settings)],
    )
    async def auth_check(caller: Caller) -> dict[str, str]:
        return {"status": "authenticated"}

    return app


app = create_app()
