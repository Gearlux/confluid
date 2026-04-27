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
    from confluid.fluid import Class, Clone, Instance, Reference

    def _parse_inline_kwargs(args_str: str) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if args_str and args_str.strip():
            for pair in args_str.split(","):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    kwargs[k.strip()] = v.strip()
        return kwargs

    def _stamp(fl: Any, loader: yaml.SafeLoader, node: yaml.nodes.Node) -> Any:
        """Attach the YAML source location of `node` to `fl` for diagnostics.

        Stored as ``(filename_or_None, line, column)`` on ``fl._yaml_loc``;
        line/column are 1-based. Surfaces in :func:`format_yaml_loc` so error
        messages can point at the offending YAML mapping.
        """
        mark = node.start_mark
        filename = getattr(loader, "name", None)
        fl._yaml_loc = (filename, mark.line + 1, mark.column + 1)
        return fl

    def ref_constructor(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.nodes.Node) -> Any:
        return _stamp(Reference(tag_suffix), loader, node)

    def class_constructor(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.nodes.Node) -> Any:
        instant = re.match(r"^([\w_.]+)\((.*)\)$", tag_suffix)
        factory = Instance if instant else Class
        name = instant.group(1) if instant else tag_suffix

        if isinstance(node, yaml.nodes.MappingNode):
            mapping: dict[str, Any] = {str(k): v for k, v in loader.construct_mapping(node, deep=True).items()}
            return _stamp(factory(name, **mapping), loader, node)

        if isinstance(node, yaml.nodes.ScalarNode) and instant:
            return _stamp(factory(name, **_parse_inline_kwargs(instant.group(2))), loader, node)

        return _stamp(Class(tag_suffix), loader, node)

    def clone_constructor(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.nodes.Node) -> Any:
        if isinstance(node, yaml.nodes.MappingNode):
            mapping: dict[str, Any] = {str(k): v for k, v in loader.construct_mapping(node, deep=True).items()}
            return _stamp(Clone(tag_suffix, **mapping), loader, node)
        return _stamp(Clone(tag_suffix), loader, node)

    yaml.SafeLoader.add_multi_constructor("!ref:", ref_constructor)
    yaml.SafeLoader.add_multi_constructor("!class:", class_constructor)
    yaml.SafeLoader.add_multi_constructor("!clone:", clone_constructor)

    def ref_compat(loader: yaml.SafeLoader, node: Any) -> Any:
        return _stamp(Reference(loader.construct_scalar(node)), loader, node)

    def class_compat(loader: yaml.SafeLoader, node: Any) -> Any:
        val = loader.construct_scalar(node)
        instant = re.match(r"^([\w_.]+)\((.*)\)$", val)
        if instant:
            return _stamp(Instance(instant.group(1), **_parse_inline_kwargs(instant.group(2))), loader, node)
        return _stamp(Class(val), loader, node)

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
            # Route through materialize() so inner !ref: targets (dotted imports
            # like `posixpath.join`, cross-kwarg references) get resolved
            # against the Fluid's own kwargs. A raw _deep_flow skips that pass.
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
    """Resolve config data and instantiate all Class objects recursively.

    Within a single materialize pass, identical raw markers (reached directly
    or via ``!ref:``) produce a single flowed ``Instance`` object, which is
    materialized into a single live instance. ``!clone:`` opts out of this
    sharing with an explicit deepcopy.
    """
    _acceptable_keys_cache.clear()
    _post_init_attrs_cache.clear()
    if context:
        context = expand_dotted_keys(context)
    old_ctx = getattr(_state, "context", None)
    old_flow_memo = getattr(_state, "flow_memo", None)
    old_instance_memo = getattr(_state, "instance_memo", None)
    _state.context = context
    _state.flow_memo = {}
    _state.instance_memo = {}
    try:
        result = _flow_recursive(data, parent_context=context)
        return _deep_flow(result)
    finally:
        _state.context = old_ctx
        _state.flow_memo = old_flow_memo
        _state.instance_memo = old_instance_memo


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


_acceptable_keys_cache: Dict[str, Optional[frozenset[str]]] = {}
_post_init_attrs_cache: Dict[str, frozenset[str]] = {}


def _same_target(fluid_target: Any, cls: type) -> bool:
    """True if ``fluid_target`` refers to the same class as ``cls``.

    Handles both the post-resolution case (``fluid_target`` is a type) and
    the pre-resolution case (``fluid_target`` is a dotted-name string that
    hasn't been turned into a class yet — typical at YAML-load time).
    """
    if fluid_target is cls:
        return True
    if isinstance(fluid_target, str):
        from confluid.registry import resolve_class

        resolved = resolve_class(fluid_target)
        if resolved is cls:
            return True
        # Last-resort string compare in case resolve_class misses (e.g. the
        # class hasn't been imported into the registry yet but the names
        # match a fully-qualified module path).
        qualified = f"{cls.__module__}.{cls.__qualname__}"
        if fluid_target in (cls.__name__, qualified):
            return True
    return False


def _get_post_init_attrs(target: type) -> frozenset[str]:
    """Extract attribute names assigned in ``__init__`` bodies via AST.

    Walks the class MRO, parses each class's ``__init__`` source, and collects
    every ``self.<name> = ...`` target. Private names (underscore-prefixed) are
    skipped to avoid broadcasting into implementation details. Results cache
    per-class by dotted module name.

    This is what lets broadcasting see post-init attributes (e.g.
    ``self.loss_fn = nn.CrossEntropyLoss()`` in a Trainer's ``__init__``
    body) in addition to the constructor signature — so a top-level YAML
    ``loss_fn: !class:...`` flows into the Trainer without the user
    duplicating the key under the trainer block.
    """
    import ast
    import inspect
    import textwrap

    cache_key = f"{target.__module__}.{target.__qualname__}"
    if cache_key in _post_init_attrs_cache:
        return _post_init_attrs_cache[cache_key]

    names: Set[str] = set()
    try:
        mro = target.__mro__
    except AttributeError:
        _post_init_attrs_cache[cache_key] = frozenset()
        return frozenset()

    for klass in mro:
        if klass is object:
            continue
        init = klass.__dict__.get("__init__")
        if init is None:
            continue
        try:
            source = inspect.getsource(init)
        except (OSError, TypeError):
            continue
        try:
            # ``textwrap.dedent`` preserves relative indentation (strips the
            # common leading whitespace), so the method body still sits
            # under its ``def`` header. ``inspect.cleandoc`` would flatten
            # every line to column 0 and break the parse.
            tree = ast.parse(textwrap.dedent(source))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            targets = (
                [node.target]
                if isinstance(node, (ast.AnnAssign, ast.AugAssign))
                else node.targets if isinstance(node, ast.Assign) else []
            )
            for t in targets:
                if (
                    isinstance(t, ast.Attribute)
                    and isinstance(t.value, ast.Name)
                    and t.value.id == "self"
                    and not t.attr.startswith("_")
                ):
                    names.add(t.attr)

    result = frozenset(names)
    _post_init_attrs_cache[cache_key] = result
    return result


def _get_acceptable_keys(cls_or_name: Any) -> Optional[frozenset[str]]:
    """Return constructor params (+ configurable properties + post-init attrs) for a class.

    Accepts either a class object or a string name (resolved via registry).
    Returns None if the class cannot be resolved or accepts **kwargs (broadcast everything).

    For ``@configurable`` targets the result also includes attribute names
    assigned in the class's ``__init__`` body (via AST inspection). This
    makes broadcasting see post-init attributes such as
    ``self.loss_fn = nn.CrossEntropyLoss()`` even though they aren't listed
    in the constructor signature — so a top-level YAML key matching one of
    those names flows into the target without having to be duplicated under
    the target's block.
    """
    if isinstance(cls_or_name, type):
        cache_key = f"{cls_or_name.__module__}.{cls_or_name.__qualname__}"
        target = cls_or_name
    else:
        cache_key = cls_or_name
        target = None  # resolve below

    if cache_key in _acceptable_keys_cache:
        return _acceptable_keys_cache[cache_key]

    import inspect

    if target is None:
        from confluid.registry import resolve_class

        target = resolve_class(cache_key)
        if target is None:
            _acceptable_keys_cache[cache_key] = None
            return None

    keys: Set[str] = set()
    try:
        init_method = getattr(target, "__init__", None)
        if init_method is None:
            _acceptable_keys_cache[cache_key] = None
            return None
        sig = inspect.signature(init_method)
        if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
            _acceptable_keys_cache[cache_key] = None
            return None
        keys.update(p for p in sig.parameters if p not in ("self", "cls"))
    except (ValueError, TypeError):
        _acceptable_keys_cache[cache_key] = None
        return None

    if getattr(target, "__confluid_configurable__", False):
        for name in dir(target):
            if name.startswith("_") or name in keys:
                continue
            member = getattr(target, name, None)
            if member is None or callable(member):
                continue
            if getattr(member, "__confluid_ignore__", False):
                continue
            if isinstance(member, property) and member.fset is None:
                continue
            keys.add(name)

        # Fold in attribute names assigned in __init__'s body (AST scan).
        # These are instance attributes not visible via dir(cls), but the
        # post-init injection loop in confluid.fluid.flow already assigns
        # any matching kwarg via setattr — broadcasting just needs to know
        # the names so a top-level YAML key can flow into them.
        keys.update(_get_post_init_attrs(target))

    result = frozenset(keys)
    _acceptable_keys_cache[cache_key] = result
    return result


def _prepare_kwargs(marker_dict: Dict[str, Any], parent_context: Dict[str, Any], target: Any = None) -> Dict[str, Any]:
    """Merge broadcast values and scoped blocks into a class marker dict.

    Priority: explicit kwargs > class-scoped block > instance-scoped block > broadcast scalars.
    ``target`` is an optional actual class object for parameter inspection (avoids name collisions).
    """
    cls_name = marker_dict.get("_confluid_class_", "")
    if cls_name.endswith("()"):
        cls_name = cls_name[:-2]
    instance_name = marker_dict.get("name")

    merged: Dict[str, Any] = {}

    # 1. Broadcast parent values into this class when the key matches an
    # acceptable target (ctor param, class-level attr, or post-init body
    # attr). Dicts and lists are filtered out — those top-level shapes are
    # typically class-scope blocks (``ClassName: {...}``) or unrelated
    # collections. Fluids broadcast only when ``k`` is EXPLICITLY listed in
    # ``acceptable`` — never through the ``**kwargs`` catchall (which would
    # pull the outer class back into nested classes and loop infinitely).
    # Self-broadcast is also guarded: a Fluid whose target matches the
    # receiving class itself is skipped. Inherited attrs from base classes
    # (e.g. ``pytorch_lightning.LightningModule.trainer`` is a settable
    # property, so ``trainer`` would otherwise land in every Trainer's
    # ``acceptable`` set and infinitely broadcast a top-level ``trainer:``
    # into its own materialization).
    from confluid.fluid import Fluid
    from confluid.registry import resolve_class

    acceptable = _get_acceptable_keys(target or cls_name)
    target_cls = target if isinstance(target, type) else resolve_class(cls_name) if cls_name else None
    for k, v in parent_context.items():
        if isinstance(v, (dict, list)):
            continue
        if isinstance(v, Fluid):
            if acceptable is None or k not in acceptable:
                continue
            if target_cls is not None and _same_target(v.target, target_cls):
                continue  # self-broadcast — skip to avoid infinite recursion
        elif acceptable is not None and k not in acceptable:
            continue
        merged[k] = v

    # 2. Class-scoped and instance-scoped blocks
    for key in [cls_name, instance_name]:
        block = parent_context.get(key) if key else None
        if isinstance(block, dict):
            merged.update(block)

    # 3. Explicit kwargs win
    merged.update(marker_dict)

    # 4. Recursion: ensure nested Fluid objects in merged also get the context
    for k, v in merged.items():
        if k == "_confluid_class_":
            continue
        if isinstance(v, dict) and "_confluid_class_" in v:
            merged[k] = _prepare_kwargs(v, merged)

    return merged


def _resolve_dotted_ref(target: str, context: Dict[str, Any]) -> Any:
    """Resolve a dotted reference path, supporting attribute access and method calls.

    Handles patterns like:
      - ``obj.attr`` — attribute access on a flowed object
      - ``obj.method()`` — method call on a flowed object
      - ``module.sub.func()`` — module-level function call (if not in context)

    Returns None if the reference cannot be resolved.
    """
    import importlib
    import re

    from confluid.fluid import Fluid
    from confluid.fluid import flow as _flow

    # Detect method call suffix: "path.method()"
    match = re.match(r"^(.+)\.([\w_]+)\(\)$", target)
    if match:
        obj_path, method_name = match.group(1), match.group(2)
    else:
        # Try plain dotted path: "path.attr"
        parts = target.rsplit(".", 1)
        if len(parts) == 2:
            obj_path, method_name = parts[0], parts[1]
        else:
            return None

    # Resolve the base object
    obj = None
    if obj_path in context:
        raw = context[obj_path]
        obj = _flow(raw) if isinstance(raw, Fluid) else raw
    else:
        # Fallback: try to import obj_path as a module or resolve as a class
        try:
            obj = importlib.import_module(obj_path)
        except ImportError:
            # Maybe obj_path is "module.Class", try resolving it
            from confluid.registry import resolve_class

            obj = resolve_class(obj_path)

    if obj is None:
        return None

    if match:
        # Method/Function call
        method = getattr(obj, method_name, None)
        if method is not None and callable(method):
            return method()
    else:
        # Plain dotted path — return attribute
        return getattr(obj, method_name, None)

    return None


def _flow_recursive(data: Any, parent_context: Optional[Dict[str, Any]] = None) -> Any:
    from confluid.fluid import Class, Clone, Fluid, Instance, Reference

    # Shared-identity memo: ensures the same raw marker (reached directly or via
    # !ref:) always flows to the same Instance/Class marker object, so a single
    # live object is instantiated downstream.
    flow_memo: Optional[Dict[int, Any]] = getattr(_state, "flow_memo", None)

    # 1. Marker dictionaries → Fluid citizens with broadcasting applied
    if isinstance(data, dict):
        if "_confluid_class_" in data:
            if flow_memo is not None and id(data) in flow_memo:
                return flow_memo[id(data)]
            raw_id = id(data)
            if parent_context:
                data = _prepare_kwargs(data, parent_context)
            else:
                data = dict(data)  # Don't mutate the original

            cls_name = data.pop("_confluid_class_")
            if cls_name.endswith("()"):
                cls_name = cls_name[:-2]

            # Child context: parent context merges into data (context values fill gaps)
            child_ctx = deep_merge(data, parent_context) if parent_context else dict(data)
            resolved_kwargs = {k: _flow_recursive(v, parent_context=child_ctx) for k, v in data.items()}
            result = Instance(cls_name, **resolved_kwargs)
            if flow_memo is not None:
                flow_memo[raw_id] = result
            return result

        if "_confluid_ref_" in data:
            data = Reference(data["_confluid_ref_"])
        else:
            # Plain dict — pass merged context down
            local_ctx = {**parent_context, **data} if parent_context else dict(data)
            return {k: _flow_recursive(v, parent_context=local_ctx) for k, v in data.items()}

    # 2. Class/Instance from YAML tags — apply broadcasting to kwargs
    if isinstance(data, (Class, Instance)):
        if flow_memo is not None and id(data) in flow_memo:
            return flow_memo[id(data)]
        raw_id = id(data)
        if parent_context:
            target_name = (
                data.target
                if isinstance(data.target, str)
                else getattr(data.target, "__confluid_name__", getattr(data.target, "__name__", ""))
            )
            synthetic = {**data.kwargs, "_confluid_class_": target_name}
            actual_target = data.target if isinstance(data.target, type) else None
            merged_kwargs = _prepare_kwargs(synthetic, parent_context, target=actual_target)
            merged_kwargs.pop("_confluid_class_", None)
        else:
            merged_kwargs = dict(data.kwargs)

        child_ctx = deep_merge(merged_kwargs, parent_context) if parent_context else dict(merged_kwargs)
        resolved_kwargs = {k: _flow_recursive(v, parent_context=child_ctx) for k, v in merged_kwargs.items()}
        res_obj = copy(data)
        res_obj.kwargs = resolved_kwargs
        if flow_memo is not None:
            flow_memo[raw_id] = res_obj
        return res_obj

    # 3. Reference — resolve against parent context
    if isinstance(data, Reference):
        if parent_context and data.target in parent_context:
            return _flow_recursive(parent_context[data.target], parent_context=parent_context)
        # Support dotted paths and method calls (e.g., "obj.method()")
        if parent_context:
            resolved = _resolve_dotted_ref(data.target, parent_context)
            if resolved is not None:
                return resolved
        return data

    # 3b. Clone — resolve reference then deepcopy, merging extra kwargs
    if isinstance(data, Clone):
        if parent_context and data.target in parent_context:
            from copy import deepcopy

            resolved = _flow_recursive(parent_context[data.target], parent_context=parent_context)
            cloned = deepcopy(resolved)
            if data.kwargs and isinstance(cloned, (Class, Instance)):
                resolved_kwargs = {k: _flow_recursive(v, parent_context=parent_context) for k, v in data.kwargs.items()}
                cloned.kwargs.update(resolved_kwargs)
            return cloned
        return data

    # 4. Generic Fluid — pass through
    if isinstance(data, Fluid):
        return data

    # 5. Lists
    if isinstance(data, list):
        return [_flow_recursive(item, parent_context=parent_context) for item in data]

    return data
