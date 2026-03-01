from copy import deepcopy
from typing import Any, Dict


def deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """
    Recursively merge overlay into base.
    Modifies base in place and returns it.
    """
    for key, value in overlay.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            deep_merge(base[key], value)
        else:
            base[key] = deepcopy(value)
    return base


def expand_dotted_keys(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Expand flat dictionary with dotted keys into a nested dictionary.
    Example: {"a.b": 1, "c": 2} -> {"a": {"b": 1}, "c": 2}
    """
    result: Dict[str, Any] = {}
    for key, value in data.items():
        if "." in key:
            parts = key.split(".")
            current = result
            for part in parts[:-1]:
                if part not in current or not isinstance(current[part], dict):
                    current[part] = {}
                current = current[part]
            current[parts[-1]] = value
        else:
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                deep_merge(result[key], value)
            else:
                result[key] = value
    return result
