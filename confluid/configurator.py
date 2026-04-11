import inspect
from typing import Any, Dict, List, Optional, Set

import yaml

from confluid.merger import expand_dotted_keys
from confluid.resolver import Resolver


def configure(*instances: Any, config: Any, context: Optional[Dict[str, Any]] = None) -> None:
    """Apply configuration to one or more existing object instances.

    Recursively walks the object graph and sets attributes based on matching
    class names, instance names, and dotted paths in the config.
    """
    if config is None:
        return

    if isinstance(config, str) and (":" in config or "\n" in config):
        config = yaml.safe_load(config)

    if not isinstance(config, dict):
        return

    resolved_context = context if context is not None else config
    resolver = Resolver(context=resolved_context)
    config = expand_dotted_keys(resolver.resolve(config))

    visited: Set[int] = set()
    for instance in instances:
        _walk(instance, config, resolved_context, "", visited)


def _walk(obj: Any, config: Dict[str, Any], context: Dict[str, Any], prefix: str, visited: Set[int]) -> None:
    """Recursively traverse the object graph and apply matching configuration."""
    if obj is None:
        return

    from confluid.fluid import flow

    obj = flow(obj)

    obj_id = id(obj)
    if obj_id in visited:
        return
    visited.add(obj_id)

    if isinstance(obj, (list, tuple)):
        for item in obj:
            _walk(item, config, context, prefix, visited)
        return

    if isinstance(obj, dict):
        for v in obj.values():
            _walk(v, config, context, prefix, visited)
        return

    cls = obj.__class__
    if getattr(cls, "__confluid_configurable__", False):
        instance_name = getattr(obj, "name", None)
        if isinstance(instance_name, str):
            prefix = f"{prefix}.{instance_name}" if prefix else instance_name
        _apply(obj, config, context, prefix)

    # Recurse into non-callable attributes
    for attr_name in dir(obj):
        if attr_name.startswith("_"):
            continue
        try:
            attr_val = getattr(obj, attr_name)
            if not callable(attr_val):
                _walk(attr_val, config, context, prefix, visited)
        except Exception:
            continue


def _apply(obj: Any, config: Dict[str, Any], context: Dict[str, Any], prefix: str) -> None:
    """Apply matching config values to a single configurable object."""
    cls = obj.__class__
    cls_name = getattr(cls, "__confluid_name__", cls.__name__)
    instance_name = getattr(obj, "name", None)

    # Build scoped overlay: ClassName > instance_name > ClassName.instance_name
    obj_config: Dict[str, Any] = {}
    for key in [cls_name, instance_name, f"{cls_name}.{instance_name}" if instance_name else None]:
        if key and key in config and isinstance(config[key], dict):
            obj_config.update(config[key])

    resolver = Resolver(context=context)

    for attr_name in _configurable_attrs(obj):
        val = _match(attr_name, cls_name, instance_name, config, obj_config, prefix)
        if val is None:
            continue

        resolved_val = resolver.resolve(val)
        if isinstance(resolved_val, str):
            from confluid.resolver import parse_value

            resolved_val = parse_value(resolved_val)

        # Materialize marker dicts into live instances
        if isinstance(resolved_val, dict) and "_confluid_class_" in resolved_val:
            from confluid.fluid import flow as _flow

            resolved_val = _flow(resolved_val)

        current_val = getattr(obj, attr_name, None)
        if isinstance(resolved_val, dict) and hasattr(
            getattr(current_val, "__class__", None), "__confluid_configurable__"
        ):
            _walk(current_val, resolved_val, context, prefix, set())
        else:
            setattr(obj, attr_name, resolved_val)


def _match(
    attr: str,
    cls_name: str,
    inst_name: Optional[str],
    config: Dict[str, Any],
    obj_config: Dict[str, Any],
    prefix: str,
) -> Any:
    """Find the best matching config value for an attribute (priority order)."""
    candidates = []
    if prefix:
        candidates.append(f"{prefix}.{attr}")
    if inst_name:
        candidates.append(f"{cls_name}.{inst_name}.{attr}")
    candidates.append(f"{cls_name}.{attr}")
    if inst_name:
        candidates.append(f"{inst_name}.{attr}")

    for path in candidates:
        val = _deep_get(config, path)
        if val is not None:
            return val

    if attr in obj_config:
        return obj_config[attr]

    # Broadcast: direct attribute in global config (non-dict only)
    if attr in config and not isinstance(config[attr], dict):
        return config[attr]

    return None


def _deep_get(data: Dict[str, Any], path: str) -> Any:
    """Get value from nested dict by dotted path."""
    if path in data:
        return data[path]
    current: Any = data
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _configurable_attrs(obj: Any) -> List[str]:
    """Get configurable attribute names from an object."""
    cls = obj.__class__
    attrs: List[str] = []

    try:
        sig = inspect.signature(cls.__init__)
        attrs.extend(p for p in sig.parameters if p not in ("self", "cls"))
    except (ValueError, TypeError):
        pass

    for name in dir(obj):
        if name.startswith("_") or callable(getattr(obj, name)):
            continue
        member = getattr(cls, name, None)
        if member and getattr(member, "__confluid_ignore__", False):
            continue
        if isinstance(member, property) and member.fset is None:
            continue
        if name not in attrs:
            attrs.append(name)

    return attrs
