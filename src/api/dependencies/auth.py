"""FastAPI dependency injection for authentication and authorization."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any

import structlog
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from redis.asyncio import Redis

logger: structlog.BoundLogger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# OAuth2 scheme — the token URL is informational only for OpenAPI docs.
# ---------------------------------------------------------------------------
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/v1/auth/token", auto_error=False)

# ---------------------------------------------------------------------------
# JWT configuration from environment
# ---------------------------------------------------------------------------
_JWT_SECRET: str = os.environ.get("JWT_SECRET", "dev-insecure-secret-change-me")
_JWT_ALGORITHM: str = os.environ.get("JWT_ALGORITHM", "HS256")
_APP_ENV: str = os.environ.get("APP_ENV", "development")

_DEV_TEST_USER: dict[str, Any] = {
    "id": "dev-user-00000000",
    "email": "dev@matecos.local",
    "scopes": ["read", "write", "admin"],
    "is_dev": True,
}


# ---------------------------------------------------------------------------
# API-key authentication
# ---------------------------------------------------------------------------


async def verify_api_key(request: Request) -> dict[str, Any]:
    """Read and validate the ``X-API-Key`` request header.

    In development mode (``APP_ENV=development``) any non-empty key is
    accepted and a synthetic test-user dict is returned.  In all other
    environments the key is matched against the ``API_KEY`` environment
    variable (placeholder until DB-backed key store is wired in).

    Args:
        request: The incoming FastAPI request.

    Returns:
        A dict with at minimum ``id`` and ``scopes`` keys.

    Raises:
        HTTPException: 401 when the header is missing or the key is invalid.
    """
    api_key: str | None = request.headers.get("X-API-Key")

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-API-Key header is required",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    log = logger.bind(path=str(request.url.path))

    if _APP_ENV == "development":
        log.debug("api_key.dev_mode_accept")
        return {**_DEV_TEST_USER, "api_key_prefix": api_key[:8]}

    # Production: compare against env-var key (stub — replace with DB lookup)
    expected: str | None = os.environ.get("API_KEY")
    if not expected or api_key != expected:
        log.warning("api_key.invalid_key")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    return {
        "id": "apikey-user",
        "scopes": ["read", "write"],
        "api_key_prefix": api_key[:8],
    }


# ---------------------------------------------------------------------------
# JWT / Bearer-token authentication
# ---------------------------------------------------------------------------


async def get_current_user(
    token: str | None = Depends(oauth2_scheme),
) -> dict[str, Any]:
    """Validate a JWT Bearer token and return the decoded user payload.

    In development mode (``APP_ENV=development``) the dependency short-circuits
    and returns a synthetic test user so that the stack can be exercised without
    a real identity provider.

    Args:
        token: Bearer token extracted from the ``Authorization`` header.

    Returns:
        A dict with at minimum ``id`` and ``scopes`` keys.

    Raises:
        HTTPException: 401 when the token is missing, expired, or invalid.
    """
    if _APP_ENV == "development":
        logger.debug("jwt.dev_mode_skip_validation")
        return _DEV_TEST_USER

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token is required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            _JWT_SECRET,
            algorithms=[_JWT_ALGORITHM],
        )
    except JWTError as exc:
        logger.warning("jwt.invalid_token", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user_id: str | None = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token payload missing 'sub' claim",
            headers={"WWW-Authenticate": "Bearer"},
        )

    scopes: list[str] = payload.get("scopes", [])
    logger.debug("jwt.validated", user_id=user_id, scopes=scopes)
    return {
        "id": user_id,
        "email": payload.get("email"),
        "scopes": scopes,
    }


# ---------------------------------------------------------------------------
# Scope-based authorisation
# ---------------------------------------------------------------------------


def require_scope(scope: str) -> Callable[..., Any]:
    """Return a FastAPI dependency that asserts the current user has ``scope``.

    Args:
        scope: The required scope string, e.g. ``'admin'`` or ``'write'``.

    Returns:
        A FastAPI-compatible async dependency callable.

    Example::

        @router.delete("/tools/{tool_id}")
        async def delete_tool(_: dict = Depends(require_scope("admin"))):
            ...
    """

    async def _check(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
        """Assert the authenticated user holds the required scope.

        Args:
            user: Current-user dict injected by :func:`get_current_user`.

        Returns:
            The user dict unchanged (so callers can optionally capture it).

        Raises:
            HTTPException: 403 when the user lacks the required scope.
        """
        user_scopes: list[str] = user.get("scopes", [])
        if scope not in user_scopes:
            logger.warning(
                "authz.scope_denied",
                user_id=user.get("id"),
                required_scope=scope,
                user_scopes=user_scopes,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Scope '{scope}' is required for this operation",
            )
        return user

    return _check


# ---------------------------------------------------------------------------
# Redis-backed token-bucket rate limiter
# ---------------------------------------------------------------------------


class RateLimiter:
    """Redis-backed token-bucket rate limiter.

    Uses a sliding-window counter stored in Redis to enforce per-key request
    limits.  If Redis is unavailable the limiter fails open (allows the
    request) and logs a warning, preventing Redis outages from taking down
    the API.

    Attributes:
        redis: An async Redis client instance.
    """

    def __init__(self, redis: Redis) -> None:  # type: ignore[type-arg]
        """Initialise the limiter with a connected Redis client.

        Args:
            redis: Async Redis client (e.g. from ``redis.asyncio``).
        """
        self._redis = redis

    async def check(self, key: str, limit: int, window_seconds: int) -> None:
        """Assert that ``key`` has not exceeded ``limit`` calls in ``window_seconds``.

        Uses an atomic Lua script to increment a sliding-window counter and
        set its expiry in one round-trip.

        Args:
            key: Unique bucket identifier, e.g. ``f"rl:{user_id}:{endpoint}"``.
            limit: Maximum number of allowed calls in the window.
            window_seconds: Length of the sliding window in seconds.

        Raises:
            HTTPException: 429 Too Many Requests when the limit is exceeded.
        """
        redis_key = f"matecos:rl:{key}"
        try:
            lua_script = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return current
"""
            count: int = await self._redis.eval(  # type: ignore[misc]
                lua_script, 1, redis_key, str(window_seconds)
            )
            if count > limit:
                logger.warning(
                    "rate_limit.exceeded",
                    key=key,
                    count=count,
                    limit=limit,
                    window_seconds=window_seconds,
                )
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Rate limit exceeded: {limit} requests per {window_seconds}s",
                    headers={"Retry-After": str(window_seconds)},
                )
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            # Fail open — Redis unavailability must not block legitimate traffic.
            logger.error("rate_limit.redis_error", error=str(exc), key=key)


# ---------------------------------------------------------------------------
# FastAPI dependency: resolves a RateLimiter from the app state
# ---------------------------------------------------------------------------


async def get_rate_limiter(request: Request) -> RateLimiter:
    """FastAPI dependency that resolves a :class:`RateLimiter` from app state.

    The application lifespan must store a connected Redis client at
    ``app.state.redis``.  If it is absent the limiter is created with a
    no-op stub that always passes.

    Args:
        request: The incoming FastAPI request (injected by FastAPI).

    Returns:
        A ready-to-use :class:`RateLimiter` instance.
    """
    redis_client: Redis | None = getattr(request.app.state, "redis", None)  # type: ignore[type-arg]
    if redis_client is None:
        logger.warning("rate_limiter.no_redis_in_state")
        # Return a limiter that will fail open (Redis unavailable ⇒ allow all).
        # We pass a minimal stub that satisfies the Redis interface.
        redis_client = _NullRedis()  # type: ignore[assignment]
    return RateLimiter(redis_client)


class _NullRedis:
    """Minimal no-op Redis stub used when Redis is not configured.

    All operations succeed without contacting any server, causing the
    :class:`RateLimiter` to always allow requests (fail-open behaviour).
    """

    async def eval(self, *args: Any, **kwargs: Any) -> int:  # noqa: ANN401
        """Return 1 so that the rate-limit counter never exceeds any limit."""
        return 1
