from copy import deepcopy
from typing import Any, Dict, cast


def deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """
    Recursively merge overlay into base.
    Returns a new dictionary.

    Live Fluid markers (Class/Instance/Reference/Clone) are preserved by
    identity so that ``!ref:`` resolution stays consistent across contexts.
    Other values are deep-copied for safety.
    """
    result: Dict[str, Any] = cast(
        Dict[str, Any],
        _preserve_identity_copy(base) if isinstance(base, dict) else deepcopy(base),
    )
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = _preserve_identity_copy(value)
    return result


def _preserve_identity_copy(value: Any) -> Any:
    """Deepcopy ordinary containers but preserve identity of live Fluid objects.

    Fluid markers (Class, Instance, Reference, Clone) represent *one* logical
    configuration citizen. Deep-copying them here would undo the Resolver's
    ``!ref:`` resolution, causing two references to the same Fluid to produce
    two separate live instances downstream. We keep identity intact and let
    ``!clone:`` opt into explicit deepcopy when independence is wanted.
    """
    from confluid.fluid import Fluid

    if isinstance(value, Fluid):
        return value
    if isinstance(value, dict):
        return {k: _preserve_identity_copy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_preserve_identity_copy(item) for item in value]
    return deepcopy(value)


def expand_dotted_keys(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Expand flat dictionary with dotted keys into a nested dictionary.
    Ensures that existing keys are NOT shadowed but merged.
    """
    # 1. Start with all non-dotted keys
    result = {k: _preserve_identity_copy(v) for k, v in data.items() if "." not in k}

    # 2. Process dotted keys
    dotted_keys = sorted([k for k in data.keys() if "." in k], key=lambda k: (k.count("."), k))

    from confluid.fluid import Fluid

    for key in dotted_keys:
        value = data[key]
        parts = key.split(".")
        current = result

        for part in parts[:-1]:
            if part in current:
                entry = current[part]
                if isinstance(entry, Fluid):
                    # Traverse into Fluid kwargs
                    current = entry.kwargs
                    continue
                elif isinstance(entry, dict):
                    current = entry
                    continue
            current[part] = {}
            current = current[part]

        last_part = parts[-1]
        if last_part in current and isinstance(current[last_part], dict) and isinstance(value, dict):
            current[last_part] = deep_merge(current[last_part], value)
        else:
            current[last_part] = _preserve_identity_copy(value)

    return result
