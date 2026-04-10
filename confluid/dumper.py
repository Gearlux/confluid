import inspect
from typing import Any, Optional, Set

import yaml


class CompactDumper(yaml.SafeDumper):
    """Custom YAML dumper with !class tag support."""

    pass


def _represent_object(dumper: yaml.SafeDumper, data: Any) -> Any:
    """Represent @configurable objects and Fluid citizens as YAML tags."""
    from confluid.fluid import Class, Reference

    # Fluid citizens: Class and Reference
    if isinstance(data, Reference):
        return dumper.represent_scalar("!ref", data.target)

    if isinstance(data, Class):
        target = data.target
        if isinstance(target, type):
            name = f"{target.__module__}.{target.__qualname__}"
        else:
            name = str(target)
        return dumper.represent_mapping(f"!class:{name}", data.kwargs)

    # @configurable instances
    cls_name = getattr(data, "__confluid_name__", data.__class__.__name__)
    try:
        sig = inspect.signature(data.__class__.__init__)
        params = [p for p in sig.parameters if p not in ("self", "cls")]
    except (ValueError, TypeError):
        params = []

    kwargs = {}
    for p in params:
        if hasattr(data, p):
            val = getattr(data, p)
            if val is not None:
                if isinstance(val, type):
                    if hasattr(val, "__confluid_configurable__"):
                        val = f"!class:{getattr(val, '__confluid_name__', val.__name__)}"
                    else:
                        val = f"{val.__module__}.{val.__name__}"
                kwargs[p] = val

    return dumper.represent_mapping(f"!class:{cls_name}", kwargs)


def dump(obj: Any) -> str:
    """Serialize a (potentially nested) object tree to YAML."""

    class _LocalDumper(CompactDumper):
        pass

    def _discover_and_register(target: Any, visited: Optional[Set[int]] = None) -> None:
        if visited is None:
            visited = set()
        if id(target) in visited:
            return
        visited.add(id(target))

        from confluid.fluid import Fluid

        if isinstance(target, Fluid):
            _LocalDumper.add_representer(type(target), _represent_object)
            return

        if hasattr(target.__class__, "__confluid_configurable__"):
            _LocalDumper.add_representer(target.__class__, _represent_object)
            try:
                sig = inspect.signature(target.__class__.__init__)
                for p in sig.parameters:
                    if hasattr(target, p):
                        _discover(getattr(target, p), visited)
            except (ValueError, TypeError):
                pass
        elif isinstance(target, list):
            for item in target:
                _discover(item, visited)
        elif isinstance(target, dict):
            for val in target.values():
                _discover_and_register(val, visited)

    _discover_and_register(obj)
    return yaml.dump(obj, Dumper=_LocalDumper, default_flow_style=False, sort_keys=False)
