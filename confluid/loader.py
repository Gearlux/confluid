import importlib
import re
import threading
from copy import copy
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union, cast

import yaml
from logflow import get_logger

from confluid.merger import deep_merge, expand_dotted_keys
from confluid.resolver import Resolver
from confluid.scopes import resolve_scopes

logger = get_logger("confluid.loader")

# Thread-local storage for materialization context
_state = threading.local()


def get_active_context() -> Optional[Dict[str, Any]]:
    return getattr(_state, "context", None)


def _register_constructors() -> None:
    """Register YAML constructors for !ref: and !class: tags."""
    from confluid.fluid import Class, Instance, Reference

    def _parse_inline_kwargs(args_str: str) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if args_str and args_str.strip():
            for pair in args_str.split(","):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    kwargs[k.strip()] = v.strip()
        return kwargs

    def ref_constructor(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.nodes.Node) -> Any:
        return Reference(tag_suffix)

    def class_constructor(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.nodes.Node) -> Any:
        instant = re.match(r"^([\w_.]+)\((.*)\)$", tag_suffix)
        factory = Instance if instant else Class
        name = instant.group(1) if instant else tag_suffix

        if isinstance(node, yaml.nodes.MappingNode):
            mapping: dict[str, Any] = {str(k): v for k, v in loader.construct_mapping(node, deep=True).items()}
            return factory(name, **mapping)

        if isinstance(node, yaml.nodes.ScalarNode) and instant:
            return factory(name, **_parse_inline_kwargs(instant.group(2)))

        return Class(tag_suffix)

    yaml.SafeLoader.add_multi_constructor("!ref:", ref_constructor)
    yaml.SafeLoader.add_multi_constructor("!class:", class_constructor)

    def ref_compat(loader: yaml.SafeLoader, node: Any) -> Any:
        return Reference(loader.construct_scalar(node))

    def class_compat(loader: yaml.SafeLoader, node: Any) -> Any:
        val = loader.construct_scalar(node)
        instant = re.match(r"^([\w_.]+)\((.*)\)$", val)
        if instant:
            return Instance(instant.group(1), **_parse_inline_kwargs(instant.group(2)))
        return Class(val)

    yaml.SafeLoader.add_constructor("!ref", ref_compat)
    yaml.SafeLoader.add_constructor("!class", class_compat)


def load_config(path: Union[str, Path], _included: Optional[Set[Path]] = None) -> Dict[str, Any]:
    """Load raw YAML with markers and recursive includes."""
    path = Path(path).resolve()
    if _included is None:
        _included = set()
    if path in _included:
        raise ValueError(f"Circular include: {path}")
    _included.add(path)

    if not path.exists():
        raise FileNotFoundError(f"Not found: {path}")

    _register_constructors()
    with open(path, "r") as f:
        data = yaml.safe_load(f) or {}

    data = _process_imports(data)
    data = cast(Dict[str, Any], _process_includes_recursive(data, path, _included))
    return data


def _process_imports(data: Dict[str, Any]) -> Dict[str, Any]:
    if "import" in data:
        imports = data.pop("import")
        if imports:
            if isinstance(imports, str):
                imports = [imports]
            for m in imports:
                try:
                    importlib.import_module(m)
                except ImportError:
                    pass
    return data


def _process_includes_recursive(data: Any, current_path: Path, _included: Set[Path]) -> Any:
    from confluid.fluid import Fluid

    if isinstance(data, list):
        return [_process_includes_recursive(item, current_path, _included) for item in data]

    # Traverse into Class/Fluid kwargs
    if isinstance(data, Fluid):
        data.kwargs = {k: _process_includes_recursive(v, current_path, _included) for k, v in data.kwargs.items()}
        return data

    if not isinstance(data, dict):
        return data

    processed_dict: Dict[str, Any] = {
        str(k): _process_includes_recursive(v, current_path, _included) for k, v in data.items()
    }

    if "include" in processed_dict:
        includes = processed_dict.pop("include")
        if isinstance(includes, str):
            includes = [includes]

        if isinstance(includes, list):
            merged_base: Dict[str, Any] = {}
            for inc_path in includes:
                if not isinstance(inc_path, str):
                    continue
                target_path = current_path.parent / inc_path
                if not target_path.exists():
                    target_path = Path(inc_path)

                inc_data = load_config(target_path, _included=set(_included))
                merged_base = deep_merge(merged_base, inc_data)
            processed_dict = deep_merge(merged_base, processed_dict)

    return processed_dict


def load(
    data: Any,
    scopes: Optional[List[str]] = None,
    flow: bool = True,
    context: Optional[Dict[str, Any]] = None,
) -> Any:
    _register_constructors()

    if isinstance(data, (str, Path)):
        str_data = str(data)
        if "\n" not in str_data and ":" not in str_data and len(str_data) < 255 and Path(str_data).exists():
            data = load_config(data)
        else:
            data = cast(Dict[str, Any], yaml.safe_load(str_data) or {})
            data = _process_includes_recursive(data, Path.cwd() / "string.yaml", set())

    # Handle root-level Fluid objects (e.g., YAML starting with !class:)
    from confluid.fluid import Fluid

    if isinstance(data, Fluid):
        if flow:
            return _deep_flow(data)
        return data

    if not isinstance(data, dict):
        return data

    data = cast(Dict[str, Any], _process_imports(data))
    active_scopes = scopes or data.get("scopes", [])
    if active_scopes:
        data = resolve_scopes(data, active_scopes)

    resolver = Resolver(context=context or data)
    data = resolver.resolve(data)
    data = expand_dotted_keys(data)

    if not flow:
        return data

    return materialize(data, context=context or data)


def materialize(data: Any, context: Optional[Dict[str, Any]] = None) -> Any:
    """Resolve config data and instantiate all Class objects recursively."""
    if context:
        context = expand_dotted_keys(context)
    old_ctx = getattr(_state, "context", None)
    _state.context = context
    try:
        result = _flow_recursive(data, parent_context=context)
        return _deep_flow(result)
    finally:
        _state.context = old_ctx


def _deep_flow(data: Any) -> Any:
    """Flow the top-level Fluid + any Instance objects in the tree."""
    from confluid.fluid import Fluid, Instance
    from confluid.fluid import flow as _flow

    if isinstance(data, Fluid):
        return _flow(data)
    if isinstance(data, dict):
        return {k: (_flow(v) if isinstance(v, Instance) else v) for k, v in data.items()}
    if isinstance(data, list):
        return [(_flow(item) if isinstance(item, Instance) else item) for item in data]
    return data


def _prepare_kwargs(marker_dict: Dict[str, Any], parent_context: Dict[str, Any]) -> Dict[str, Any]:
    """Merge broadcast values and scoped blocks into a class marker dict.

    Priority: explicit kwargs > class-scoped block > instance-scoped block > broadcast scalars.
    """
    from confluid.fluid import Fluid

    cls_name = marker_dict.get("_confluid_class_", "")
    if cls_name.endswith("()"):
        cls_name = cls_name[:-2]
    instance_name = marker_dict.get("name")

    merged: Dict[str, Any] = {}

    # 1. Broadcast: non-dict, non-list, non-Fluid scalars from parent
    for k, v in parent_context.items():
        if not isinstance(v, (dict, list, Fluid)):
            merged[k] = v

    # 2. Class-scoped and instance-scoped blocks
    for key in [cls_name, instance_name]:
        block = parent_context.get(key) if key else None
        if isinstance(block, dict):
            merged.update(block)

    # 3. Explicit kwargs win
    merged.update(marker_dict)

    return merged


def _flow_recursive(data: Any, parent_context: Optional[Dict[str, Any]] = None) -> Any:
    from confluid.fluid import Class, Fluid, Instance, Reference

    # 1. Marker dictionaries → Fluid citizens with broadcasting applied
    if isinstance(data, dict):
        if "_confluid_class_" in data:
            if parent_context:
                data = _prepare_kwargs(data, parent_context)

            cls_name = data.pop("_confluid_class_")
            if cls_name.endswith("()"):
                cls_name = cls_name[:-2]

            # Child context: parent context merges into data (context values fill gaps)
            child_ctx = deep_merge(data, parent_context) if parent_context else dict(data)
            resolved_kwargs = {k: _flow_recursive(v, parent_context=child_ctx) for k, v in data.items()}
            return Instance(cls_name, **resolved_kwargs)

        if "_confluid_ref_" in data:
            data = Reference(data["_confluid_ref_"])
        else:
            # Plain dict — pass merged context down
            local_ctx = {**parent_context, **data} if parent_context else dict(data)
            return {k: _flow_recursive(v, parent_context=local_ctx) for k, v in data.items()}

    # 2. Class/Instance from YAML tags — apply broadcasting to kwargs
    if isinstance(data, (Class, Instance)):
        if parent_context:
            target_name = (
                data.target
                if isinstance(data.target, str)
                else getattr(data.target, "__confluid_name__", getattr(data.target, "__name__", ""))
            )
            synthetic = {**data.kwargs, "_confluid_class_": target_name}
            merged_kwargs = _prepare_kwargs(synthetic, parent_context)
            merged_kwargs.pop("_confluid_class_", None)
        else:
            merged_kwargs = dict(data.kwargs)

        child_ctx = deep_merge(merged_kwargs, parent_context) if parent_context else dict(merged_kwargs)
        resolved_kwargs = {k: _flow_recursive(v, parent_context=child_ctx) for k, v in merged_kwargs.items()}
        res_obj = copy(data)
        res_obj.kwargs = resolved_kwargs
        return res_obj

    # 3. Reference — resolve against parent context
    if isinstance(data, Reference):
        if parent_context and data.target in parent_context:
            return _flow_recursive(parent_context[data.target], parent_context=parent_context)
        return data

    # 4. Generic Fluid — pass through
    if isinstance(data, Fluid):
        return data

    # 5. Lists
    if isinstance(data, list):
        return [_flow_recursive(item, parent_context=parent_context) for item in data]

    return data
