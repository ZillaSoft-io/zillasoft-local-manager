"""Bearer-token auth for the local web server.

The token (LOCAL_MANAGER_AUTH_TOKEN) is auto-generated on first run and stored
in .env. Every API request (except the public /health probe) must send
`Authorization: Bearer <token>`. The token is read from config per request so
rotation takes effect without a restart.
"""
from __future__ import annotations

import hmac
import logging

from fastapi import Header, HTTPException, status

from .config import ConfigHandler

logger = logging.getLogger(__name__)


def make_auth_dependency(config: ConfigHandler):
    """Build a FastAPI dependency that validates the bearer token."""

    async def verify_token(authorization: str | None = Header(default=None)):
        expected = config.get_raw("LOCAL_MANAGER_AUTH_TOKEN")
        if not config.is_set(expected):
            # Should never happen — ensure_auth_token() runs at startup.
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Auth token not configured on the server.",
            )
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing or malformed Authorization header.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        provided = authorization[len("Bearer "):]
        # Constant-time comparison to avoid timing leaks.
        if not hmac.compare_digest(provided, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid auth token.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return True

    return verify_token
