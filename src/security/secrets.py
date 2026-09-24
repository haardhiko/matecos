"""
secrets.py
==========
Secrets management — safe storage and access to sensitive credentials.
"""

from __future__ import annotations

import os

import structlog

logger = structlog.get_logger(__name__)


class SecretNotFoundError(Exception):
    """Raised when a requested secret does not exist."""


class SecretsManager:
    """Manages access to sensitive credentials.

    In development, reads from environment variables.  In production,
    this should be backed by a dedicated secrets store (HashiCorp Vault,
    GCP Secret Manager, AWS Secrets Manager).

    Secrets are never logged, never included in error messages, and
    never passed to agents or tool outputs.
    """

    def __init__(self, prefix: str = "MATECOS_SECRET_") -> None:
        self._prefix = prefix
        self._cache: dict[str, str] = {}
        self._log = logger.bind(component="SecretsManager")

    def get(self, name: str) -> str:
        """Retrieve a secret by name.

        Looks up ``{prefix}{name}`` in environment variables.

        Args:
            name: The secret name (e.g. ``OPENAI_API_KEY``).

        Returns:
            The secret value.

        Raises:
            SecretNotFoundError: If the secret is not found.
        """
        if name in self._cache:
            return self._cache[name]

        env_key = f"{self._prefix}{name}"
        value = os.environ.get(env_key) or os.environ.get(name)

        if not value:
            raise SecretNotFoundError(
                f"Secret '{name}' not found. Set environment variable '{env_key}'."
            )

        self._cache[name] = value
        self._log.info("secrets.loaded", name=name)  # Never log the value!
        return value

    def get_or_default(self, name: str, default: str = "") -> str:
        """Retrieve a secret or return a default.

        Args:
            name: The secret name.
            default: Default value if not found.

        Returns:
            The secret value or the default.
        """
        try:
            return self.get(name)
        except SecretNotFoundError:
            return default

    def has(self, name: str) -> bool:
        """Check if a secret exists.

        Args:
            name: The secret name.

        Returns:
            ``True`` if the secret is available.
        """
        try:
            self.get(name)
            return True
        except SecretNotFoundError:
            return False

    def clear_cache(self) -> None:
        """Clear the in-memory secrets cache."""
        self._cache.clear()
        self._log.info("secrets.cache_cleared")
