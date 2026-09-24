"""
classifiers.py
==============
Risk classifiers — deterministic rule-based classifiers for tool invocations.

Each classifier scores a specific risk dimension on a 0.0–1.0 scale.
The risk engine aggregates scores and maps to risk levels.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ClassifierResult:
    """Result from a single risk classifier.

    Attributes:
        classifier: Name of the classifier.
        score: Risk score between 0.0 (safe) and 1.0 (dangerous).
        reason: Human-readable explanation.
        metadata: Additional diagnostic data.
    """

    classifier: str
    score: float
    reason: str
    metadata: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Prompt Injection Classifier
# ---------------------------------------------------------------------------

_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"disregard\s+(all\s+)?prior",
    r"you\s+are\s+now\s+a",
    r"act\s+as\s+if",
    r"pretend\s+you\s+are",
    r"system\s*:\s*",
    r"<\s*script\s*>",
    r"javascript\s*:",
    r"eval\s*\(",
    r"exec\s*\(",
    r"__import__\s*\(",
    r"subprocess\.\s*(run|call|Popen)",
    r"os\.\s*(system|popen|exec)",
    r"\\x[0-9a-fA-F]{2}",  # hex escape sequences
]
_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)


def classify_prompt_injection(
    tool_id: str, tool_input: dict[str, Any], context: dict[str, Any]
) -> ClassifierResult:
    """Detect potential prompt injection in tool inputs.

    Scans all string values in ``tool_input`` for known injection patterns.

    Returns:
        ``ClassifierResult`` with score 0.0 (clean) to 1.0 (injection detected).
    """
    all_text = _extract_text_values(tool_input)
    matches = _INJECTION_RE.findall(all_text)

    if matches:
        score = min(1.0, len(matches) * 0.3)
        return ClassifierResult(
            classifier="prompt_injection",
            score=score,
            reason=f"Detected {len(matches)} injection pattern(s) in tool input.",
            metadata={"patterns_matched": len(matches)},
        )

    return ClassifierResult(
        classifier="prompt_injection",
        score=0.0,
        reason="No injection patterns detected.",
    )


# ---------------------------------------------------------------------------
# Data Exfiltration Classifier
# ---------------------------------------------------------------------------

_EXFIL_PATTERNS = [
    r"https?://\d+\.\d+\.\d+\.\d+",  # IP-based URLs
    r"https?://[a-z0-9]+\.ngrok",  # ngrok tunnels
    r"ftp://",
    r"curl\s+",
    r"wget\s+",
    r"requests\.post",
    r"send.*to.*external",
    r"upload.*to.*server",
]
_EXFIL_RE = re.compile("|".join(_EXFIL_PATTERNS), re.IGNORECASE)


def classify_data_exfiltration(
    tool_id: str, tool_input: dict[str, Any], context: dict[str, Any]
) -> ClassifierResult:
    """Detect potential data exfiltration in tool inputs."""
    all_text = _extract_text_values(tool_input)
    matches = _EXFIL_RE.findall(all_text)

    if matches:
        return ClassifierResult(
            classifier="data_exfiltration",
            score=min(1.0, len(matches) * 0.4),
            reason=f"Detected {len(matches)} potential exfiltration pattern(s).",
            metadata={"patterns_matched": len(matches)},
        )

    return ClassifierResult(
        classifier="data_exfiltration",
        score=0.0,
        reason="No exfiltration patterns detected.",
    )


# ---------------------------------------------------------------------------
# Privilege Escalation Classifier
# ---------------------------------------------------------------------------

_PRIVESC_PATTERNS = [
    r"sudo\s+",
    r"chmod\s+[0-7]*7[0-7]*",
    r"chown\s+root",
    r"su\s+-\s",
    r"--privileged",
    r"CAP_SYS_ADMIN",
    r"--cap-add",
    r"mount\s+",
    r"/etc/shadow",
    r"/etc/passwd",
    r"GRANT\s+ALL",
    r"ALTER\s+USER.*SUPERUSER",
]
_PRIVESC_RE = re.compile("|".join(_PRIVESC_PATTERNS), re.IGNORECASE)


def classify_privilege_escalation(
    tool_id: str, tool_input: dict[str, Any], context: dict[str, Any]
) -> ClassifierResult:
    """Detect potential privilege escalation attempts."""
    all_text = _extract_text_values(tool_input)
    matches = _PRIVESC_RE.findall(all_text)

    if matches:
        return ClassifierResult(
            classifier="privilege_escalation",
            score=min(1.0, len(matches) * 0.5),
            reason=f"Detected {len(matches)} privilege escalation pattern(s).",
            metadata={"patterns_matched": len(matches)},
        )

    return ClassifierResult(
        classifier="privilege_escalation",
        score=0.0,
        reason="No privilege escalation patterns detected.",
    )


# ---------------------------------------------------------------------------
# Output Integrity Classifier
# ---------------------------------------------------------------------------


def classify_output_integrity(
    tool_id: str, tool_input: dict[str, Any], context: dict[str, Any]
) -> ClassifierResult:
    """Assess risk of output being tampered with.

    Higher risk for tools that produce executable output or modify
    persistent state.
    """
    high_risk_tools = {"code.execute", "code.write", "code.deploy"}
    medium_risk_tools = {"data.write", "document.create", "web.http_post"}

    if tool_id in high_risk_tools:
        return ClassifierResult(
            classifier="output_integrity",
            score=0.7,
            reason=f"Tool '{tool_id}' produces executable/persistent output.",
        )
    if tool_id in medium_risk_tools:
        return ClassifierResult(
            classifier="output_integrity",
            score=0.4,
            reason=f"Tool '{tool_id}' modifies persistent state.",
        )

    return ClassifierResult(
        classifier="output_integrity",
        score=0.1,
        reason="Tool output is read-only or ephemeral.",
    )


# ---------------------------------------------------------------------------
# Sandbox Escape Classifier
# ---------------------------------------------------------------------------

_SANDBOX_PATTERNS = [
    r"docker\s+run",
    r"docker\s+exec",
    r"--net\s*=\s*host",
    r"--pid\s*=\s*host",
    r"-v\s+/:/",
    r"nsenter",
    r"chroot",
    r"/proc/self",
    r"breakout",
    r"escape.*sandbox",
]
_SANDBOX_RE = re.compile("|".join(_SANDBOX_PATTERNS), re.IGNORECASE)


def classify_sandbox_escape(
    tool_id: str, tool_input: dict[str, Any], context: dict[str, Any]
) -> ClassifierResult:
    """Detect potential sandbox escape attempts."""
    all_text = _extract_text_values(tool_input)
    matches = _SANDBOX_RE.findall(all_text)

    if matches:
        return ClassifierResult(
            classifier="sandbox_escape",
            score=min(1.0, len(matches) * 0.5),
            reason=f"Detected {len(matches)} sandbox escape pattern(s).",
            metadata={"patterns_matched": len(matches)},
        )

    return ClassifierResult(
        classifier="sandbox_escape",
        score=0.0,
        reason="No sandbox escape patterns detected.",
    )


# ---------------------------------------------------------------------------
# Content Safety Classifier
# ---------------------------------------------------------------------------

_CONTENT_PATTERNS = [
    r"password\s*[:=]",
    r"api[_-]?key\s*[:=]",
    r"secret[_-]?key\s*[:=]",
    r"access[_-]?token\s*[:=]",
    r"private[_-]?key\s*[:=]",
    r"BEGIN\s+RSA\s+PRIVATE\s+KEY",
    r"BEGIN\s+OPENSSH\s+PRIVATE\s+KEY",
    r"AWS[_-]?SECRET",
]
_CONTENT_RE = re.compile("|".join(_CONTENT_PATTERNS), re.IGNORECASE)


def classify_content_safety(
    tool_id: str, tool_input: dict[str, Any], context: dict[str, Any]
) -> ClassifierResult:
    """Detect potential secrets or sensitive content in tool inputs."""
    all_text = _extract_text_values(tool_input)
    matches = _CONTENT_RE.findall(all_text)

    if matches:
        return ClassifierResult(
            classifier="content_safety",
            score=min(1.0, len(matches) * 0.4),
            reason=f"Detected {len(matches)} potential secret/sensitive pattern(s).",
            metadata={"patterns_matched": len(matches)},
        )

    return ClassifierResult(
        classifier="content_safety",
        score=0.0,
        reason="No sensitive content patterns detected.",
    )


# ---------------------------------------------------------------------------
# Registry of all classifiers
# ---------------------------------------------------------------------------

ALL_CLASSIFIERS = [
    classify_prompt_injection,
    classify_data_exfiltration,
    classify_privilege_escalation,
    classify_output_integrity,
    classify_sandbox_escape,
    classify_content_safety,
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_text_values(obj: Any, max_depth: int = 10) -> str:
    """Recursively extract all string values from a nested dict/list.

    Returns a single concatenated string for pattern matching.
    """
    if max_depth <= 0:
        return ""
    parts: list[str] = []
    if isinstance(obj, str):
        parts.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            parts.append(_extract_text_values(v, max_depth - 1))
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            parts.append(_extract_text_values(item, max_depth - 1))
    return " ".join(parts)
