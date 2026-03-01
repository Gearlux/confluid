from pathlib import Path
from typing import Any, Dict, Union

import yaml


def load_config(path: Union[str, Path]) -> Dict[str, Any]:
    """
    Load configuration from a YAML file.

    Args:
        path: Path to the configuration file.

    Returns:
        The configuration dictionary.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")

    with open(path, "r") as f:
        data = yaml.safe_load(f)
        return data or {}


def load(data: Any) -> Any:
    """
    Reconstruct an object hierarchy from configuration data.

    Args:
        data: Dict, YAML string, or path to a config file.
    """
    from confluid.fluid import flow
    from confluid.resolver import Resolver

    # 1. Resolve raw data if it's a file path
    if isinstance(data, (str, Path)) and Path(str(data)).exists():
        data = load_config(data)
    elif isinstance(data, str) and ("\n" in data or ":" in data):
        # YAML string
        data = yaml.safe_load(data)

    # 2. Use resolver to turn strings into objects/Fluid
    resolver = Resolver()
    resolved = resolver.resolve(data)

    # 3. Recursively flow the resolved data
    return _flow_recursive(resolved)


def _flow_recursive(data: Any) -> Any:
    """Recursively flow objects in dicts and lists."""
    from confluid.fluid import flow
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
