"""
validators.py
=============
Input and schema validators for MATECOS.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class ValidationError(Exception):
    """Raised when input validation fails."""

    def __init__(self, field: str, message: str) -> None:
        self.field = field
        self.message = message
        super().__init__(f"Validation error on '{field}': {message}")


class InputValidator:
    """Validates user inputs and tool parameters."""

    MAX_GOAL_LENGTH = 10_000
    MAX_ATTACHMENT_COUNT = 10
    MIN_GOAL_LENGTH = 10

    def validate_goal(self, text: str) -> str:
        """Validate and sanitise a goal text.

        Args:
            text: The raw goal text.

        Returns:
            Sanitised goal text.

        Raises:
            ValidationError: If the text is invalid.
        """
        if not text or not text.strip():
            raise ValidationError("text", "Goal text cannot be empty.")

        text = text.strip()

        if len(text) < self.MIN_GOAL_LENGTH:
            raise ValidationError(
                "text",
                f"Goal text must be at least {self.MIN_GOAL_LENGTH} characters.",
            )

        if len(text) > self.MAX_GOAL_LENGTH:
            raise ValidationError(
                "text",
                f"Goal text exceeds maximum length of {self.MAX_GOAL_LENGTH} characters.",
            )

        return text

    def validate_tool_input(
        self, tool_id: str, payload: dict[str, Any], schema: dict[str, Any]
    ) -> list[str]:
        """Validate tool input against its JSON schema.

        Args:
            tool_id: The tool identifier.
            payload: The input to validate.
            schema: The JSON schema to validate against.

        Returns:
            List of validation error messages (empty if valid).
        """
        errors: list[str] = []

        # Check required fields
        required = schema.get("required", [])
        for field_name in required:
            if field_name not in payload:
                errors.append(f"Missing required field: '{field_name}'")

        # Check types for known properties
        properties = schema.get("properties", {})
        for field_name, value in payload.items():
            if field_name in properties:
                expected_type = properties[field_name].get("type")
                if expected_type and not self._type_matches(value, expected_type):
                    errors.append(
                        f"Field '{field_name}': expected type '{expected_type}', "
                        f"got '{type(value).__name__}'"
                    )

        if errors:
            logger.warning(
                "validator.tool_input_invalid",
                tool_id=tool_id,
                error_count=len(errors),
            )

        return errors

    @staticmethod
    def _type_matches(value: Any, expected_type: str) -> bool:
        """Check if a Python value matches a JSON schema type."""
        type_map = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
            "array": list,
            "object": dict,
        }
        expected = type_map.get(expected_type)
        if expected is None:
            return True
        return isinstance(value, expected)
