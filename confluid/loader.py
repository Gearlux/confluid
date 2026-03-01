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
    from confluid.resolver import Resolver

    # 1. Resolve raw data if it's a file path
    if isinstance(data, (str, Path)) and Path(str(data)).exists():
        data = load_config(data)

    # 2. Use resolver to turn strings into objects/Fluid
    resolver = Resolver()
    resolved = resolver.resolve(data)

    # 3. If the top-level is a dict representing a single class (e.g. {"Model": {...}})
    # we can optionally 'flow' it if we can find the class in registry.
    if isinstance(resolved, dict) and len(resolved) == 1:
        cls_name = list(resolved.keys())[0]
        from confluid.registry import get_registry

        cls = get_registry().get_class(cls_name)
        if cls:
            return cls(**resolved[cls_name])

    return resolved
