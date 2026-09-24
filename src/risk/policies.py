"""
policies.py
===========
Pre-configured risk policies for different deployment profiles.
"""

from __future__ import annotations

from src.risk.engine import RiskPolicy

# Conservative — default for production
CONSERVATIVE = RiskPolicy(
    low_threshold=0.15,
    medium_threshold=0.4,
    high_threshold=0.7,
    auto_approve_low=True,
    auto_approve_medium=False,
    deny_critical=True,
)

# Balanced — for staging/testing environments
BALANCED = RiskPolicy(
    low_threshold=0.2,
    medium_threshold=0.5,
    high_threshold=0.8,
    auto_approve_low=True,
    auto_approve_medium=True,
    deny_critical=True,
)

# Permissive — for development only
PERMISSIVE = RiskPolicy(
    low_threshold=0.3,
    medium_threshold=0.6,
    high_threshold=0.9,
    auto_approve_low=True,
    auto_approve_medium=True,
    deny_critical=False,
)

POLICY_MAP: dict[str, RiskPolicy] = {
    "conservative": CONSERVATIVE,
    "balanced": BALANCED,
    "permissive": PERMISSIVE,
}


def get_risk_policy(name: str) -> RiskPolicy:
    """Look up a named risk policy.

    Args:
        name: Policy name (conservative, balanced, permissive).

    Returns:
        The corresponding ``RiskPolicy``.

    Raises:
        KeyError: If the policy name is unknown.
    """
    key = name.lower()
    if key not in POLICY_MAP:
        raise KeyError(
            f"Unknown risk policy '{name}'. Available: {sorted(POLICY_MAP.keys())}"
        )
    return POLICY_MAP[key]
