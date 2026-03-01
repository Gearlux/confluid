from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

import yaml

from confluid.merger import deep_merge


def load_config(path: Union[str, Path], _included: Optional[Set[Path]] = None) -> Dict[str, Any]:
    """
    Load configuration from a YAML file, recursively processing 'include:' directives.
    """
    path = Path(path).resolve()

    if _included is None:
        _included = set()

    if path in _included:
        raise ValueError(f"Circular include detected: {path}")

    _included.add(path)

    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")

    with open(path, "r") as f:
        data = yaml.safe_load(f) or {}

    # Process inclusions
    if "include" in data:
        includes = data.pop("include")
        if isinstance(includes, str):
            includes = [includes]

        merged_base: Dict[str, Any] = {}
        for inc_path in includes:
            # Resolve relative to current file
            full_inc_path = path.parent / inc_path
            inc_data = load_config(full_inc_path, _included=_included)
            deep_merge(merged_base, inc_data)

        # Overlay current file data on top of merged inclusions
        data = deep_merge(merged_base, data)

    return data


def load(data: Any, scopes: Optional[List[str]] = None) -> Any:
    """
    Reconstruct an object hierarchy from configuration data.

    Args:
        data: Dict, YAML string, or path to a config file.
        scopes: Optional list of scopes to activate.
    """
    from confluid.resolver import Resolver
    from confluid.scopes import resolve_scopes

    # 1. Resolve raw data if it's a file path
    if isinstance(data, (str, Path)) and Path(str(data)).exists():
        data = load_config(data)
    elif isinstance(data, str) and ("\n" in data or ":" in data):
        # YAML string
        data = yaml.safe_load(data)

    # 2. Resolve scopes if requested or declared in data
    active_scopes = scopes or data.get("scopes", []) if isinstance(data, dict) else []
    if active_scopes and isinstance(data, dict):
        data = resolve_scopes(data, active_scopes)

    # 3. Use resolver to turn strings into objects/Fluid
    resolver = Resolver(context=data if isinstance(data, dict) else None)
    resolved = resolver.resolve(data)

    # 4. Recursively flow the resolved data
    return _flow_recursive(resolved)


def _flow_recursive(data: Any) -> Any:
    """Recursively flow objects in dicts and lists."""
    from confluid.registry import get_registry

    if isinstance(data, dict):
        # Check if this dict represents a single configurable class: {"Class": {...}}
        if len(data) == 1:
            cls_name = list(data.keys())[0]
            cls = get_registry().get_class(cls_name)
            if cls:
                # Recurse into arguments first
                kwargs = _flow_recursive(data[cls_name])
                return cls(**kwargs)

        return {k: _flow_recursive(v) for k, v in data.items()}

    if isinstance(data, list):
        return [_flow_recursive(item) for item in data]

    return data
