import importlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

import yaml

from confluid.merger import deep_merge


def _register_constructors() -> None:
    """Register custom YAML constructors for !ref and !class."""
    from confluid.resolver import ClassReference, Reference

    def ref_constructor(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.nodes.Node) -> Any:
        """Handle !ref:name."""
        return Reference(tag_suffix)

    def class_constructor(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.nodes.Node) -> Any:
        """Handle !class:Name(args) or !class:Name {args}."""
        import re

        if isinstance(node, yaml.nodes.MappingNode):
            # !class:Name {kw: val}
            from typing import cast

            kwargs = cast(Dict[str, Any], loader.construct_mapping(node, deep=True))
            return ClassReference(tag_suffix, kwargs)

        if isinstance(node, yaml.nodes.ScalarNode):
            # !class:Name(args)
            match = re.match(r"^([\w_]+)(?:\((.*)\))?$", tag_suffix)
            if match:
                cls_name, args = match.groups()
                return ClassReference(cls_name, args or "")

        return ClassReference(tag_suffix)

    # Register multi-constructors for the prefixes
    yaml.SafeLoader.add_multi_constructor("!ref:", ref_constructor)
    yaml.SafeLoader.add_multi_constructor("!class:", class_constructor)

    # Keep standard single-tag constructors for compatibility
    def ref_compat(loader: yaml.SafeLoader, node: Any) -> Any:
        return Reference(loader.construct_scalar(node))

    def class_compat(loader: yaml.SafeLoader, node: Any) -> Any:
        val = loader.construct_scalar(node)
        if "(" in val and val.endswith(")"):
            name, args = val[:-1].split("(", 1)
            return ClassReference(name, args)
        return ClassReference(val)

    yaml.SafeLoader.add_constructor("!ref", ref_compat)
    yaml.SafeLoader.add_constructor("!class", class_compat)


def load_config(path: Union[str, Path], _included: Optional[Set[Path]] = None) -> Dict[str, Any]:
    """
    Load configuration from a YAML file, recursively processing 'include:' directives.
    """
    path = Path(path).resolve()

    if _included is None:
        _included = set()

    if path in _included:
        raise ValueError(f"Circular include detected: {path}")

    _included.add(path)

    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")

    _register_constructors()

    with open(path, "r") as f:
        data = yaml.safe_load(f) or {}

    # Process imports and inclusions
    data = _process_imports(data)
    data = _process_includes(data, path, _included)

    return data


def _process_imports(data: Dict[str, Any]) -> Dict[str, Any]:
    """Handle 'import:' directive to populate registry."""
    if "import" in data:
        imports = data.pop("import")
        if isinstance(imports, str):
            imports = [imports]
        for module_name in imports:
            try:
                importlib.import_module(module_name)
            except ImportError as e:
                print(f"Warning: Failed to import module '{module_name}': {e}")
    return data


def _process_includes(data: Dict[str, Any], current_path: Path, _included: Set[Path]) -> Dict[str, Any]:
    """Handle 'include:' directive recursively."""
    if "include" in data:
        includes = data.pop("include")
        if isinstance(includes, str):
            includes = [includes]

        merged_base: Dict[str, Any] = {}
        for inc_path in includes:
            full_inc_path = current_path.parent / inc_path
            inc_data = load_config(full_inc_path, _included=_included)
            deep_merge(merged_base, inc_data)

        data = deep_merge(merged_base, data)
    return data


def load(data: Any, scopes: Optional[List[str]] = None) -> Any:
    """
    Reconstruct an object hierarchy from configuration data.

    Args:
        data: Dict, YAML string, or path to a config file.
        scopes: Optional list of scopes to activate.
    """
    from confluid.resolver import Resolver
    from confluid.scopes import resolve_scopes

    # Ensure custom constructors are registered
    _register_constructors()

    # 1. Resolve raw data if it's a file path
    if isinstance(data, (str, Path)):
        str_data = str(data)
        # If it doesn't look like YAML (no newlines/colons) AND it's a reasonable path length, check exists
        if "\n" not in str_data and ":" not in str_data and len(str_data) < 255:
            if Path(str_data).exists():
                data = load_config(data)
            else:
                # Might be a single-word YAML or reference, let safe_load try it
                data = yaml.safe_load(str_data)
        elif "\n" in str_data or ":" in str_data:
            # Looks like YAML
            data = yaml.safe_load(str_data)

    # 2. Resolve scopes if requested or declared in data
    active_scopes = scopes or data.get("scopes", []) if isinstance(data, dict) else []
    if active_scopes and isinstance(data, dict):
        data = resolve_scopes(data, active_scopes)

    # 3. Use resolver to turn strings into objects/Fluid
    context = data if isinstance(data, dict) else None
    resolver = Resolver(context=context)
    resolved = resolver.resolve(data)

    # 4. Recursively flow the resolved data
    return _flow_recursive(resolved, context=context)


def _flow_recursive(data: Any, context: Optional[Dict[str, Any]] = None) -> Any:
    """Recursively flow objects in dicts and lists."""
    from confluid.registry import get_registry
    from confluid.resolver import Resolver

    if isinstance(data, dict):
        if len(data) == 1:
            cls_name = list(data.keys())[0]
            cls = get_registry().get_class(cls_name)
            if cls:
                # Use Resolver to handle any @references in the arguments
                resolver = Resolver(context=context)
                resolved_kwargs = resolver.resolve(data[cls_name])

                # Recurse into resolved arguments
                kwargs = _flow_recursive(resolved_kwargs, context=context)

                # Instantiate
                return cls(**kwargs)

        return {k: _flow_recursive(v, context=context) for k, v in data.items()}

    if isinstance(data, list):
        return [_flow_recursive(item, context=context) for item in data]

    return data
