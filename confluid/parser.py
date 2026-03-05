from typing import Any

import yaml


def parse_value(value: str) -> Any:
    """
    Parse a string value into a Python type, using YAML for complex types (lists, dicts).

    Examples:
        "42" -> 42
        "3.14" -> 3.14
        "true" -> True
        "[1, 2]" -> [1, 2]
        "{a: 1}" -> {"a": 1}
    """
    # 1. Handle common primitives first for speed
    val_lower = value.lower()
    if val_lower == "true":
        return True
    if val_lower == "false":
        return False
    if val_lower == "null" or val_lower == "none":
        return None

    # 2. Use YAML to parse complex or numeric types
    try:
        # We wrap in a way that ensures YAML parses it correctly as a value
        # safe_load is safe for CLI inputs
        parsed = yaml.safe_load(value)

        # If it parsed as a string but looks like it might be a list/dict,
        # it was likely malformed YAML. We return as is.
        return parsed
    except Exception:
        # Fallback to raw string
        return value
