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
