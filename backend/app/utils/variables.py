"""
Jinja2 environment factory and variable extraction utilities.
"""

import re
from datetime import datetime, timezone
from typing import Any

from jinja2 import (
    ChainableUndefined,
    StrictUndefined,
)
from jinja2.sandbox import SandboxedEnvironment

from app.utils.cable_test import parse_cable_test_filter


# ── Custom Jinja2 filters ───────────────────────────────────────────────────


def _datetimeformat(value: Any, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Format a Unix timestamp or ISO date string to a human-readable date/time."""
    try:
        if isinstance(value, (int, float)):
            dt = datetime.fromtimestamp(value, tz=timezone.utc)
        elif isinstance(value, str):
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        elif isinstance(value, datetime):
            dt = value
        else:
            return str(value)
        return dt.strftime(fmt)
    except (ValueError, TypeError, OSError):
        return str(value)


def create_jinja_env(strict: bool = False) -> SandboxedEnvironment:
    """Create a SandboxedEnvironment with custom filters registered."""
    if strict:
        env = SandboxedEnvironment(undefined=StrictUndefined)
    else:
        env = SandboxedEnvironment(undefined=ChainableUndefined)
    env.filters["datetimeformat"] = _datetimeformat
    env.filters["parse_cable_test"] = parse_cable_test_filter
    return env


def extract_variables(template: str) -> list[str]:
    """
    Extract all variable names from a template string.

    Args:
        template: Template string with {{variable}} placeholders

    Returns:
        List of variable names found in template
    """
    if not template:
        return []

    # Match {{variable}} patterns
    pattern = r"\{\{\s*([^}]+?)\s*\}\}"
    matches = re.findall(pattern, template)

    # Clean up variable names (remove filters and whitespace)
    variables = []
    for match in matches:
        # Split on | to remove Jinja2 filters
        var_name = match.split("|")[0].strip()
        if var_name and var_name not in variables:
            variables.append(var_name)

    return variables
