import inspect
from typing import Any, Dict, Set

import yaml


class CompactDumper(yaml.SafeDumper):
    """Custom YAML dumper that uses !class tag for @configurable objects."""

    pass


def _configurable_presenter(dumper: yaml.SafeDumper, data: Any) -> yaml.Node:
    """Presenter for objects marked as @configurable."""
    cls = data.__class__
    cls_name = getattr(cls, "__confluid_name__", cls.__name__)

    # 1. Collect attributes that are part of the constructor
    try:
        sig = inspect.signature(cls.__init__)
        params = [p for p in sig.parameters.keys() if p not in ("self", "cls")]
    except (ValueError, TypeError):
        params = []

    kwargs = {}
    for p in params:
        if hasattr(data, p):
            val = getattr(data, p)
            # Skip defaults or None to keep it compact
            if val is not None:
                kwargs[p] = val

    # 2. Use the !class tag with colon separator to avoid encoding issues
    tag = f"!class:{cls_name}"
    return dumper.represent_mapping(tag, kwargs)


def dump(obj: Any) -> str:
    """
    Export a configurable object hierarchy to a YAML string.
    Uses !class tags for professional, compact output.
    """
    from confluid.fluid import Fluid

    class _LocalDumper(CompactDumper):
        pass

    def _dict_representer(dumper: _LocalDumper, data: Dict[str, Any]) -> yaml.Node:
        if "_confluid_class_" in data:
            cls_name = data["_confluid_class_"]
            args = {k: v for k, v in data.items() if k != "_confluid_class_"}
            return dumper.represent_mapping("!class:" + cls_name, args)
        if "_confluid_ref_" in data:
            return dumper.represent_scalar("!ref:" + data["_confluid_ref_"], "")

        return dumper.represent_dict(data)

    def _fluid_representer(dumper: _LocalDumper, data: Fluid) -> yaml.Node:
        cls_name = data.target if isinstance(data.target, str) else data.target.__name__
        return dumper.represent_mapping("!class:" + cls_name, data.kwargs)

    _LocalDumper.add_representer(dict, _dict_representer)
    _LocalDumper.add_representer(Fluid, _fluid_representer)

    # 3. Identify and register representers for @configurable classes in the graph

    visited_ids: Set[int] = set()
    registered_classes: Set[type] = set()

    def _discover_and_register(target: Any) -> None:
        if target is None or id(target) in visited_ids:
            return
        visited_ids.add(id(target))

        cls = target.__class__
        if hasattr(cls, "__confluid_configurable__"):
            if cls not in registered_classes:
                _LocalDumper.add_representer(cls, _configurable_presenter)
                registered_classes.add(cls)

            # Recurse into configurable attributes
            for attr in dir(target):
                if not attr.startswith("_"):
                    try:
                        val = getattr(target, attr)
                        if not callable(val):
                            _discover_and_register(val)
                    except Exception:
                        pass

        # Recurse into containers
        if isinstance(target, (list, tuple)):
            for item in target:
                _discover_and_register(item)
        elif isinstance(target, dict):
            for val in target.values():
                _discover_and_register(val)

    _discover_and_register(obj)

    return yaml.dump(obj, Dumper=_LocalDumper, default_flow_style=False, sort_keys=False)
