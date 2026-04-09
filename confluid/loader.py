import importlib
import inspect
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union, cast

import yaml
from logflow import get_logger

from confluid.merger import deep_merge, expand_dotted_keys
from confluid.registry import get_registry, resolve_class
from confluid.resolver import Resolver
from confluid.scopes import resolve_scopes

logger = get_logger("confluid.loader")

# Thread-local storage for materialization context
_state = threading.local()


def get_active_context() -> Optional[Dict[str, Any]]:
    return getattr(_state, "context", None)


def _register_constructors() -> None:
    """Register custom YAML constructors for !ref and !class to return Class/Reference citizens."""
    from confluid.fluid import Class, Reference

    def ref_constructor(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.nodes.Node) -> Any:
        # YAML tags are marked as automatic=True so materialize() resolves them
        return Reference(tag_suffix, automatic=True)

    def class_constructor(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.nodes.Node) -> Any:
        if isinstance(node, yaml.nodes.MappingNode):
            kwargs: dict[str, Any] = {str(k): v for k, v in loader.construct_mapping(node, deep=True).items()}
            return Class(tag_suffix, automatic=True, **kwargs)
        if isinstance(node, yaml.nodes.ScalarNode):
            match = re.match(r"^([\w_]+)(?:\((.*)\))?$", tag_suffix)
            if match:
                cls_name, args_str = match.groups()
                str_kwargs: dict[str, Any] = {}
                if args_str and args_str.strip():
                    for pair in args_str.split(","):
                        if "=" in pair:
                            k, v = pair.split("=", 1)
                            str_kwargs[k.strip()] = v.strip()
                return Class(cls_name, automatic=True, **str_kwargs)
        return Class(tag_suffix, automatic=True)

    yaml.SafeLoader.add_multi_constructor("!ref:", ref_constructor)
    yaml.SafeLoader.add_multi_constructor("!class:", class_constructor)

    def ref_compat(loader: yaml.SafeLoader, node: Any) -> Any:
        return Reference(loader.construct_scalar(node), automatic=True)

    def class_compat(loader: yaml.SafeLoader, node: Any) -> Any:
        val = loader.construct_scalar(node)
        if "(" in val and val.endswith(")"):
            match = re.match(r"^([\w_]+)\((.*)\)$", val)
            if match:
                cls_name, args_str = match.groups()
                compat_kwargs: dict[str, Any] = {}
                if args_str and args_str.strip():
                    for pair in args_str.split(","):
                        if "=" in pair:
                            k, v = pair.split("=", 1)
                            compat_kwargs[k.strip()] = v.strip()
                return Class(cls_name, automatic=True, **compat_kwargs)
        return Class(val, automatic=True)

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
        curr = Path.cwd().resolve()
        source_root = None
        for _ in range(6):
            if (curr / ".gitmodules").exists():
                source_root = curr
                break
            curr = curr.parent

        if source_root:
            path_str = str(path)
            if "/source/" in path_str:
                suffix = path_str.split("/source/")[-1]
                candidate = source_root / suffix
                if candidate.exists():
                    path = candidate

            if not path.exists():
                parts = Path(path).parts
                projects = [
                    "waivefront",
                    "logflow",
                    "confluid",
                    "liquify",
                    "dataflux",
                    "torpedo",
                    "navigaitor",
                    "aisland",
                    "marainer",
                ]
                for i, p in enumerate(parts):
                    if p in projects:
                        candidate = source_root / Path(*parts[i:])
                        if candidate.exists():
                            path = candidate
                            break

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
            return materialize(data, context=context)
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
    old_ctx = getattr(_state, "context", None)
    _state.context = context
    try:
        return _flow_recursive(data, context=context, path_prefix="")
    finally:
        _state.context = old_ctx


def _flow_recursive(data: Any, context: Optional[Dict[str, Any]] = None, path_prefix: str = "") -> Any:
    from confluid.fluid import Class, Fluid, Reference

    # 1. Convert marker dictionaries to citizens immediately (legacy support)
    if isinstance(data, dict):
        if "_confluid_class_" in data:
            cls_name = data.pop("_confluid_class_")
            data = Class(cls_name, automatic=True, **data)
        elif "_confluid_ref_" in data:
            ref_path = data["_confluid_ref_"]
            data = Reference(ref_path, automatic=True)

    # 2. Handle Class Objects
    if isinstance(data, Class):
        cls_name = data.target
        cls = resolve_class(cls_name)

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
                # Prioritized Broadcasting
                for param_name in (valid_params if valid_params else final_kwargs.keys()):
                    full_path = f"{new_prefix}.{param_name}" if new_prefix else param_name
                    parts = full_path.split(".")
                    found_val = None
                    for start_idx in range(len(parts)):
                        candidate_path = ".".join(parts[start_idx:])
                        if candidate_path in context:
                            found_val = context[candidate_path]
                            if not isinstance(found_val, (dict, list)) or _is_marker(found_val):
                                break
                            found_val = None

                    if found_val is not None and param_name not in data.kwargs:
                        if isinstance(found_val, Fluid):
                            # Context value is a complete replacement (e.g., YAML !class:)
                            final_kwargs[param_name] = found_val
                        elif isinstance(final_kwargs.get(param_name), Fluid):
                            _broadcast_into_fluid(final_kwargs[param_name], param_name, context)
                        else:
                            final_kwargs[param_name] = found_val

                # Scoped settings (lower priority than explicit kwargs)
                global_settings = context.get(cls_name) or {}
                if isinstance(global_settings, dict):
                    resolved_globals = resolver.resolve(global_settings)
                    clean_settings = {k: v for k, v in resolved_globals.items() if not k.startswith("_confluid_")}
                    for k, v in clean_settings.items():
                        if k not in data.kwargs:
                            final_kwargs[k] = v

            if valid_params:
                final_kwargs = {k: v for k, v in final_kwargs.items() if k in valid_params}

            for k, v in final_kwargs.items():
                if isinstance(v, Fluid):
                    _broadcast_into_fluid(v, k, context)

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
        _broadcast_into_fluid(data, path_prefix, context)
        return data

    # 3. Handle Reference Objects
    if isinstance(data, Reference):
        if context:
            data.context = context
        # Resolve immediately if automatic (from YAML tag)
        if data.automatic:
            resolver = Resolver(context=context)
            resolved = resolver.resolve(f"!ref:{data.target}")
            if resolved != f"!ref:{data.target}":
                return _flow_recursive(resolved, context=context, path_prefix=path_prefix)
        return data

    # 4. Handle generic Fluid
    if isinstance(data, Fluid):
        if context:
            data.context = context
        _broadcast_into_fluid(data, path_prefix, context)
        return data

    # 5. Handle Lists
    if isinstance(data, list):
        return [_flow_recursive(item, context=context, path_prefix=path_prefix) for item in data]

    # 6. Handle Standard Dictionaries
    if isinstance(data, dict):
        return {k: _flow_recursive(v, context=context, path_prefix=path_prefix) for k, v in data.items()}

    return data


def _broadcast_into_fluid(fluid: Any, path_prefix: str, context: Optional[Dict[str, Any]]) -> None:
    """Helper to perform broadcasting into a Fluid object's kwargs."""
    from confluid.fluid import Class, Fluid

    if not isinstance(fluid, Fluid) or not context:
        return

    target_cls = None
    if isinstance(fluid, Class):
        target_cls = fluid.target
    if isinstance(target_cls, str):
        target_cls = get_registry().get_class(target_cls)

    if target_cls:
        try:
            sig = inspect.signature(target_cls.__init__)
            cls_name = getattr(target_cls, "__confluid_name__", target_cls.__name__)
            for param_name in sig.parameters:
                if param_name in ("self", "cls"):
                    continue

                # Check root, then check ClassName.param
                candidates = [param_name, f"{cls_name}.{param_name}"]
                if path_prefix:
                    candidates.append(f"{path_prefix}.{param_name}")

                for candidate in candidates:
                    if candidate in context and param_name not in fluid.kwargs:
                        val = context[candidate]
                        if not isinstance(val, (dict, list)) or _is_marker(val):
                            fluid.kwargs[param_name] = val
                            break
        except (ValueError, TypeError):
            pass


def _is_marker(val: Any) -> bool:
    """Helper to identify confluid markers."""
    from confluid.fluid import Class, Fluid, Reference

    if isinstance(val, (Fluid, Class, Reference)):
        return True
    if isinstance(val, dict):
        return "_confluid_class_" in val or "_confluid_ref_" in val
    return False
