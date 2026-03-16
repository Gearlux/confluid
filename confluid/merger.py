from copy import deepcopy
from typing import Any, Dict


def deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """
    Recursively merge overlay into base.
    Returns a new dictionary.
    """
    result = deepcopy(base)
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def expand_dotted_keys(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Expand flat dictionary with dotted keys into a nested dictionary.
    Ensures that existing keys are NOT shadowed but merged.
    """
    # 1. Start with all non-dotted keys
    result = {k: deepcopy(v) for k, v in data.items() if "." not in k}

    # 2. Process dotted keys
    dotted_keys = sorted(
        [k for k in data.keys() if "." in k], key=lambda k: (k.count("."), k)
    )

    for key in dotted_keys:
        value = data[key]
        parts = key.split(".")
        current = result

        for part in parts[:-1]:
            if part not in current or not isinstance(current[part], dict):
                current[part] = {}
            current = current[part]

        last_part = parts[-1]
        if (
            last_part in current
            and isinstance(current[last_part], dict)
            and isinstance(value, dict)
        ):
            current[last_part] = deep_merge(current[last_part], value)
        else:
            current[last_part] = deepcopy(value)

    return result
