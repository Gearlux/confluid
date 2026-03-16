import importlib
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union, cast

import yaml
from logflow import get_logger

from confluid.merger import deep_merge, expand_dotted_keys
from confluid.registry import get_registry
from confluid.resolver import Resolver
from confluid.scopes import resolve_scopes

logger = get_logger("confluid.loader")


def _register_constructors() -> None:
    """Register custom YAML constructors for !ref and !class."""

    def ref_constructor(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.nodes.Node) -> Any:
        return {"_confluid_ref_": tag_suffix}

    def class_constructor(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.nodes.Node) -> Any:

        if isinstance(node, yaml.nodes.MappingNode):
            kwargs = loader.construct_mapping(node, deep=True)
            return {"_confluid_class_": tag_suffix, **kwargs}
        if isinstance(node, yaml.nodes.ScalarNode):
            match = re.match(r"^([\w_]+)(?:\((.*)\))?$", tag_suffix)
            if match:
                cls_name, args_str = match.groups()
                kwargs = {}
                if args_str and args_str.strip():
                    for pair in args_str.split(","):
                        if "=" in pair:
                            k, v = pair.split("=", 1)
                            kwargs[k.strip()] = v.strip()
                return {"_confluid_class_": cls_name, **kwargs}
        return {"_confluid_class_": tag_suffix}

    yaml.SafeLoader.add_multi_constructor("!ref:", ref_constructor)
    yaml.SafeLoader.add_multi_constructor("!class:", class_constructor)

    def ref_compat(loader: yaml.SafeLoader, node: Any) -> Any:
        return {"_confluid_ref_": loader.construct_scalar(node)}

    def class_compat(loader: yaml.SafeLoader, node: Any) -> Any:
        val = loader.construct_scalar(node)

        if "(" in val and val.endswith(")"):
            match = re.match(r"^([\w_]+)\((.*)\)$", val)
            if match:
                cls_name, args_str = match.groups()
                kwargs = {}
                if args_str and args_str.strip():
                    for pair in args_str.split(","):
                        if "=" in pair:
                            k, v = pair.split("=", 1)
                            kwargs[k.strip()] = v.strip()
                return {"_confluid_class_": cls_name, **kwargs}
        return {"_confluid_class_": val}

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
        # SMART FALLBACK: If path not found, try resolving relative to SOURCE_ROOT
        # This handles project-prefixed paths like 'waivefront/configs/...'
        source_root = Path("/Users/gertbehi/source")
        alt_path = source_root / str(path).split("/source/")[-1]  # Handle potential double-prefixing
        if not alt_path.exists():
            # Try literal join with source_root
            # Find the first project name in the path
            parts = Path(path).parts
            for i, p in enumerate(parts):
                if p in ["waivefront", "logflow", "confluid", "liquify", "dataflux", "torpedo", "navigaitor"]:
                    candidate = source_root / Path(*parts[i:])
                    if candidate.exists():
                        path = candidate
                        break
            else:
                raise FileNotFoundError(f"Not found: {path}")
        else:
            path = alt_path

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
    if isinstance(data, list):
        return [_process_includes_recursive(item, current_path, _included) for item in data]

    if not isinstance(data, dict):
        return data

    # From here, data is a dict
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
                # 1. Try relative to current file
                target_path = current_path.parent / inc_path
                if not target_path.exists():
                    # 2. Let load_config handle the smart source_root lookup
                    target_path = Path(inc_path)

                inc_data = load_config(target_path, _included=set(_included))
                merged_base = deep_merge(merged_base, inc_data)
            processed_dict = deep_merge(merged_base, processed_dict)

    return processed_dict


def load(
    data: Any, scopes: Optional[List[str]] = None, flow: bool = True, context: Optional[Dict[str, Any]] = None
) -> Any:
    _register_constructors()

    if isinstance(data, (str, Path)):
        str_data = str(data)
        if "\n" not in str_data and ":" not in str_data and len(str_data) < 255 and Path(str_data).exists():
            data = load_config(data)
        else:
            data = cast(Dict[str, Any], yaml.safe_load(str_data) or {})
            data = _process_includes_recursive(data, Path.cwd() / "string.yaml", set())

    if not isinstance(data, dict):
        return data

    data = cast(Dict[str, Any], _process_imports(data))
    active_scopes = scopes or data.get("scopes", [])
    if active_scopes:
        data = resolve_scopes(data, active_scopes)

    # Resolve markers (tags and refs) first!
    resolver = Resolver(context=context or data)
    data = resolver.resolve(data)

    # Expand dotted keys after markers are resolved
    data = expand_dotted_keys(data)

    if not flow:
        return data

    return materialize(data, context=context or data)


def materialize(data: Any, context: Optional[Dict[str, Any]] = None) -> Any:
    return _flow_recursive(data, context=context)


def _flow_recursive(data: Any, context: Optional[Dict[str, Any]] = None) -> Any:
    if isinstance(data, dict):
        if "_confluid_class_" in data:
            cls_name = data["_confluid_class_"]
            args = {k: v for k, v in data.items() if not k.startswith("_confluid_")}

            cls = get_registry().get_class(cls_name)
            if not cls:
                return data

            resolver = Resolver(context=context)
            final_kwargs = {}
            for k, v in args.items():
                res_v = resolver.resolve(v)
                final_kwargs[k] = _flow_recursive(res_v, context=context)

            if context:
                global_settings = context.get(cls_name) or {}
                if isinstance(global_settings, dict):
                    resolved_globals = resolver.resolve(global_settings)
                    clean_settings = {k: v for k, v in resolved_globals.items() if not k.startswith("_confluid_")}
                    final_kwargs = {**clean_settings, **final_kwargs}

            return cls(**final_kwargs)

        if "_confluid_ref_" in data:
            resolver = Resolver(context=context)
            return resolver.resolve(data)

        return {k: _flow_recursive(v, context=context) for k, v in data.items()}

    if isinstance(data, list):
        return [_flow_recursive(item, context=context) for item in data]

    return data
