import hashlib
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy import select

from .config import Settings, get_settings
from .models import ApiClient

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(
    supplied_key: Annotated[str | None, Security(api_key_header)],
    settings: Annotated[Settings, Depends(get_settings)],
    request: Request,
) -> str:
    if not settings.api_key_sha256:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API authentication is not configured",
        )

    supplied_digest = hashlib.sha256((supplied_key or "").encode("utf-8")).hexdigest()
    if secrets.compare_digest(supplied_digest, settings.api_key_sha256):
        return f"bootstrap:{supplied_digest[:16]}"

    database = getattr(request.app.state, "database", None)
    if database is not None:
        with database.session_factory() as session:
            client = session.scalar(
                select(ApiClient).where(
                    ApiClient.key_hash == supplied_digest,
                    ApiClient.revoked_at.is_(None),
                )
            )
            if client is not None:
                return f"api-client:{client.id}"

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid API credentials",
    )


Caller = Annotated[str, Depends(require_api_key)]
