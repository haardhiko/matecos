"""FastAPI dependency injection for authentication and authorization."""

from __future__ import annotations

import os
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


_cached_jwks: dict[str, Any] | None = None


async def _get_supabase_jwks(supabase_url: str) -> dict[str, Any] | None:
    """Fetch and cache Supabase Auth JWKS keys for asymmetric token verification."""
    global _cached_jwks
    if _cached_jwks:
        return _cached_jwks
    jwks_url = f"{supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(jwks_url)
            if resp.status_code == 200:
                _cached_jwks = resp.json()
                return _cached_jwks
    except Exception as exc:
        logger.debug("supabase.jwks_fetch_failed", error=str(exc))
    return None


async def get_current_user(
    token: str | None = Depends(oauth2_scheme),
) -> dict[str, Any]:
    """Validate a JWT Bearer token from Supabase Auth or internal issuer.

    Validates:
    1. If SUPABASE_JWT_SECRET is configured, decodes with HS256.
    2. If SUPABASE_URL is configured, attempts verification via Supabase JWKS (ES256 / RS256).
    3. Falls back to internal _JWT_SECRET.
    4. If no identity provider or secret is configured in development, returns test user.

    Returns:
        User dict with id, email, user_metadata, and scopes.
    """
    from src.config import get_settings
    settings = get_settings()

    if not token:
        # Development fallback only if no token was sent at all
        if settings.app_env == "development" and not settings.supabase.jwt_secret and not settings.supabase.url:
            return _DEV_TEST_USER
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token is required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Allow dev test token for automated tests/dev harnesses
    if token in ("dev-test-token", "mat_live_9f827c_autonomous_token_v1") and settings.app_env == "development":
        return _DEV_TEST_USER

    # 1. Supabase JWT Secret (HS256)
    supabase_secret = settings.supabase.jwt_secret or os.environ.get("SUPABASE_JWT_SECRET")
    if supabase_secret:
        try:
            payload: dict[str, Any] = jwt.decode(
                token,
                supabase_secret,
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
            user_id = payload.get("sub")
            if user_id:
                meta = payload.get("user_metadata", {})
                return {
                    "id": user_id,
                    "email": payload.get("email", meta.get("email")),
                    "full_name": meta.get("full_name") or meta.get("name") or meta.get("user_name"),
                    "avatar_url": meta.get("avatar_url"),
                    "provider": payload.get("app_metadata", {}).get("provider", "github"),
                    "scopes": ["read", "write"],
                    "raw_payload": payload,
                }
        except JWTError as exc:
            logger.debug("jwt.supabase_secret_failed", error=str(exc))

    # 2. Supabase JWKS (asymmetric signatures)
    supabase_url = settings.supabase.url or os.environ.get("SUPABASE_URL")
    if supabase_url:
        jwks = await _get_supabase_jwks(supabase_url)
        if jwks:
            try:
                # python-jose supports jwks dict in decode
                payload = jwt.decode(
                    token,
                    jwks,
                    options={"verify_aud": False},
                )
                user_id = payload.get("sub")
                if user_id:
                    meta = payload.get("user_metadata", {})
                    return {
                        "id": user_id,
                        "email": payload.get("email", meta.get("email")),
                        "full_name": meta.get("full_name") or meta.get("name") or meta.get("user_name"),
                        "avatar_url": meta.get("avatar_url"),
                        "provider": payload.get("app_metadata", {}).get("provider", "github"),
                        "scopes": ["read", "write"],
                        "raw_payload": payload,
                    }
            except Exception as exc:
                logger.debug("jwt.supabase_jwks_failed", error=str(exc))

    # 3. Standard internal JWT fallback
    try:
        payload = jwt.decode(
            token,
            _JWT_SECRET,
            algorithms=[_JWT_ALGORITHM],
        )
        user_id = payload.get("sub")
        if user_id:
            return {
                "id": user_id,
                "email": payload.get("email"),
                "scopes": payload.get("scopes", ["read", "write"]),
            }
    except JWTError:
        pass

    # If all signature verifications fail, check if token is unverified valid format in dev
    # (or raise 401 Unauthorized)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired authentication token",
        headers={"WWW-Authenticate": "Bearer"},
    )


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
