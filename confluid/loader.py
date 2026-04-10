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
                    target_path = Path(inc_path).resolve()

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
        result = _flow_recursive(data, context=context, path_prefix="")
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


def _flow_recursive(data: Any, context: Optional[Dict[str, Any]] = None, path_prefix: str = "") -> Any:
    from confluid.fluid import Class, Fluid, Instance, Reference

    # 1. Convert marker dictionaries to Class/Reference citizens
    if isinstance(data, dict):
        if "_confluid_class_" in data:
            cls_name = data.pop("_confluid_class_")
            if cls_name.endswith("()"):
                cls_name = cls_name[:-2]
            data = Instance(cls_name, **data)
        elif "_confluid_ref_" in data:
            data = Reference(data["_confluid_ref_"])

    # 2. Class/Instance — resolve kwargs recursively, attach context
    if isinstance(data, (Class, Instance)):
        resolver = Resolver(context=context)
        resolved_kwargs = {
            k: _flow_recursive(resolver.resolve(v), context=context, path_prefix=path_prefix)
            for k, v in data.kwargs.items()
        }
        res_obj = copy(data)
        res_obj.kwargs = resolved_kwargs
        res_obj.context = context
        return res_obj

        # Materialize if configurable AND marked as automatic (from YAML tag)
        if data.automatic and cls and get_registry().is_configurable(cls):
            instance_name = data.kwargs.get("name")
            new_prefix = path_prefix
            if instance_name and isinstance(instance_name, str):
                new_prefix = f"{path_prefix}.{instance_name}" if path_prefix else instance_name

            resolver = Resolver(context=context)
            final_kwargs = {}

            try:
                sig = inspect.signature(cls.__init__)  # type: ignore[misc]
                valid_params = [p for p in sig.parameters if p not in ("self", "cls")]
                for p_name, p_obj in sig.parameters.items():
                    if p_name in ("self", "cls"):
                        continue
                    if p_obj.default is not inspect.Parameter.empty:
                        final_kwargs[p_name] = p_obj.default
            except (ValueError, TypeError):
                valid_params = []

            for k, v in data.kwargs.items():
                res_v = resolver.resolve(v)
                final_kwargs[k] = _flow_recursive(res_v, context=context, path_prefix=new_prefix)

            if context:
                # Scoped settings: ClassName block (lower priority than explicit)
                scoped = context.get(cls_name)
                if isinstance(scoped, dict):
                    resolved_scoped = resolver.resolve(scoped)
                    for k, v in resolved_scoped.items():
                        if not k.startswith("_confluid_") and k not in data.kwargs:
                            final_kwargs[k] = v

                # Broadcasting: fill in remaining params from context root
                instance_name = data.kwargs.get("name")
                for param_name in (valid_params if valid_params else list(final_kwargs.keys())):
                    if param_name in data.kwargs:
                        continue
                    if isinstance(scoped, dict) and param_name in scoped:
                        continue
                    # Check candidates: instance.param, ClassName.param, param
                    candidates = [param_name]
                    if instance_name and isinstance(instance_name, str):
                        candidates.insert(0, f"{instance_name}.{param_name}")
                    for candidate in candidates:
                        if candidate in context:
                            val = context[candidate]
                            if isinstance(val, Fluid) or not isinstance(val, (dict, list)):
                                final_kwargs[param_name] = val
                                break

            if valid_params:
                final_kwargs = {k: v for k, v in final_kwargs.items() if k in valid_params}

            # Broadcast context into any deferred Fluid values in kwargs
            if context:
                for k, v in final_kwargs.items():
                    if isinstance(v, Class) and v.target:
                        target_cls = resolve_class(v.target) if isinstance(v.target, str) else v.target
                        if target_cls:
                            try:
                                tsig = inspect.signature(target_cls.__init__)  # type: ignore[misc]
                                for tp in tsig.parameters:
                                    if tp in ("self", "cls") or tp in v.kwargs:
                                        continue
                                    if tp in context and not isinstance(context[tp], (dict, list)):
                                        v.kwargs[tp] = context[tp]
                            except (ValueError, TypeError):
                                pass

            return cls(**final_kwargs)

        # Non-configurable but automatic (from YAML): instantiate directly
        if data.automatic and cls:
            resolved_kwargs = {}
            for k, v in data.kwargs.items():
                resolved_kwargs[k] = _flow_recursive(v, context=context, path_prefix=path_prefix)
            return cls(**resolved_kwargs)

        # Stays deferred if not automatic (code-created Class citizen)
        if context:
            data.context = context
            # Broadcast context values into deferred Fluid kwargs
            if isinstance(data, Class) and cls:
                try:
                    sig = inspect.signature(cls.__init__)  # type: ignore[misc]
                    for p in sig.parameters:
                        if p in ("self", "cls") or p in data.kwargs:
                            continue
                        if p in context and not isinstance(context[p], (dict, list)):
                            data.kwargs[p] = context[p]
                except (ValueError, TypeError):
                    pass
        return data

    # 3. Handle Reference Objects
    if isinstance(data, Reference):
        # Try context directly, then resolver for nested paths
        if context and data.target in context:
            return _flow_recursive(context[data.target], context=context, path_prefix=path_prefix)
        res_ref = copy(data)
        res_ref.context = context
        return res_ref

    # 4. Generic Fluid — attach context
    if isinstance(data, Fluid):
        if context:
            data.context = context
        return data

    # 5. Handle Lists
    if isinstance(data, list):
        return [_flow_recursive(item, context=context, path_prefix=path_prefix) for item in data]

    # 6. Handle Standard Dictionaries — use dict as local context for nested resolution
    if isinstance(data, dict):
        local_ctx = {**context, **data} if context else dict(data)
        return {k: _flow_recursive(v, context=local_ctx, path_prefix=path_prefix) for k, v in data.items()}

    return data
