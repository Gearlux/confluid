import inspect
from typing import Any, Optional, Set

import yaml


class CompactDumper(yaml.SafeDumper):
    """Custom YAML dumper that uses !class tag for @configurable objects."""

    pass


def _configurable_presenter(dumper: yaml.SafeDumper, data: Any) -> Any:
    """Represent @configurable objects as !class mappings."""
    cls_name = getattr(data, "__confluid_name__", data.__class__.__name__)

    # Use inspect to find which fields are actually constructor parameters
    try:
        sig = inspect.signature(data.__class__.__init__)
        params = [p for p in sig.parameters if p not in ("self", "cls")]
    except (ValueError, TypeError):
        params = []

    kwargs = {}
    for p in params:
        if hasattr(data, p):
            val = getattr(data, p)
            # Skip defaults or None to keep it compact
            if val is not None:
                # SPECIAL HANDLING: If the value is a CLASS, represent it as a string or !class tag
                if isinstance(val, type):
                    # If it's a configurable class, use the !class notation
                    if hasattr(val, "__confluid_configurable__"):
                        val = f"!class:{getattr(val, '__confluid_name__', val.__name__)}"
                    else:
                        # Fallback to full name
                        val = f"{val.__module__}.{val.__name__}"
                kwargs[p] = val

    # 2. Use the !class tag with colon separator to avoid encoding issues
    tag = f"!class:{cls_name}"
    return dumper.represent_mapping(tag, kwargs)


def _fluid_presenter(dumper: yaml.SafeDumper, data: Any) -> Any:
    """Represent Fluid/Class/Reference citizens back to YAML tags."""
    from confluid.fluid import Class, Reference

    if isinstance(data, Reference):
        return dumper.represent_scalar("!ref", data.target)

    if isinstance(data, Class):
        target = data.target
        if isinstance(target, type):
            name = f"{target.__module__}.{target.__qualname__}"
        else:
            name = str(target)
        tag = f"!class:{name}"
        return dumper.represent_mapping(tag, data.kwargs)

    return dumper.represent_data(data.target)


def dump(obj: Any) -> str:
    """
    Serialize a (potentially nested) object tree to YAML.
    Supports @configurable objects via !class tags.
    """

    class _LocalDumper(CompactDumper):
        pass

    # Walk the object tree to find all unique configurable classes
    def _discover_and_register(target: Any, visited: Optional[Set[int]] = None) -> None:
        if visited is None:
            visited = set()
        if id(target) in visited:
            return
        visited.add(id(target))

        from confluid.fluid import Fluid

        if isinstance(target, Fluid):
            _LocalDumper.add_representer(type(target), _fluid_presenter)
            return

        if hasattr(target.__class__, "__confluid_configurable__"):
            _LocalDumper.add_representer(target.__class__, _configurable_presenter)
            # Recurse into constructor-passed attributes
            try:
                sig = inspect.signature(target.__class__.__init__)
                for p in sig.parameters:
                    if hasattr(target, p):
                        _discover_and_register(getattr(target, p), visited)
            except (ValueError, TypeError):
                pass
        elif isinstance(target, list):
            for item in target:
                _discover_and_register(item, visited)
        elif isinstance(target, dict):
            for val in target.values():
                _discover_and_register(val, visited)

    _discover_and_register(obj)

    return yaml.dump(obj, Dumper=_LocalDumper, default_flow_style=False, sort_keys=False)
