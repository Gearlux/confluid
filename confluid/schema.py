import inspect
import re
from typing import Any, Dict, List, Tuple, get_type_hints


def get_hierarchy(target: Any) -> Dict[str, Any]:
    """
    Introspect a class or instance to build a map of configurable paths.
    Returns: Dict[path, (type_str, default_value, docstring)]
    """
    hierarchy: Dict[str, Any] = {}
    _build_hierarchy_recursive(target, "", hierarchy, set())
    return hierarchy


def _build_hierarchy_recursive(obj: Any, prefix: str, hierarchy: Dict[str, Any], visited: set) -> None:
    """Recursive helper for hierarchy building."""
    if obj is None:
        return

    # 1. Handle Functions/Callables specifically
    if not isinstance(obj, type) and callable(obj) and not hasattr(obj, "__confluid_configurable__"):
        try:
            sig = inspect.signature(obj)
            type_hints = get_type_hints(obj)
            docstring = getattr(obj, "__doc__", "") or ""
            param_docs = _parse_docstring(docstring)

            for param_name, param in sig.parameters.items():
                if param_name in ("self", "cls", "args", "kwargs", "name"):
                    continue

                path = f"{prefix}.{param_name}" if prefix else param_name
                param_type = type_hints.get(param_name, Any)
                type_str = getattr(param_type, "__name__", str(param_type))
                default = param.default if param.default is not inspect.Parameter.empty else None
                doc = param_docs.get(param_name, "")

                # 3. Recurse if the parameter type is configurable
                if hasattr(param_type, "__confluid_configurable__"):
                    _build_hierarchy_recursive(param_type, path, hierarchy, visited)
                else:
                    # Only add to hierarchy if it's a "leaf" (not a configurable container)
                    hierarchy[path] = (type_str, default, doc)
            return
        except (ValueError, TypeError):
            return

    # Handle both classes and instances
    cls = obj if isinstance(obj, type) else obj.__class__

    # Avoid infinite recursion - check id(obj) in current branch
    obj_id = id(obj)
    if obj_id in visited:
        return
    # Use a new set for children to allow same type in parallel branches but detect cycles
    new_visited = visited | {obj_id}

    # 1. Determine prefix
    if not prefix:
        cls_name = getattr(cls, "__confluid_name__", cls.__name__)
        instance_name = getattr(obj, "name", None) if not isinstance(obj, type) else None
        node_name = instance_name or cls_name
        current_prefix = node_name
    else:
        # If prefix is provided, it already contains the parameter/instance name
        current_prefix = prefix

    # 2. Extract parameter documentation from docstring
    init_method = getattr(cls, "__init__", None)
    docstring = getattr(init_method, "__doc__", "") or ""
    param_docs = _parse_docstring(docstring)

    # 3. Get type hints and defaults from __init__
    try:
        if init_method is None:
            return
        sig = inspect.signature(init_method)
        type_hints = get_type_hints(init_method)

        for param_name, param in sig.parameters.items():
            if param_name in ("self", "cls", "args", "kwargs", "name"):
                continue

            # Check visibility
            member = getattr(cls, param_name, None)
            if member and getattr(member, "__confluid_ignore__", False):
                continue

            path = f"{current_prefix}.{param_name}"

            # Extract type string
            param_type = type_hints.get(param_name, Any)
            type_str = getattr(param_type, "__name__", str(param_type))

            # Extract default
            default = param.default if param.default is not inspect.Parameter.empty else None

            # Extract docstring for this parameter
            doc = param_docs.get(param_name, "")

            # 3. Recurse if the parameter type is configurable
            if hasattr(param_type, "__confluid_configurable__"):
                _build_hierarchy_recursive(param_type, path, hierarchy, new_visited)
            else:
                hierarchy[path] = (type_str, default, doc)

    except (ValueError, TypeError):
        pass


def get_hierarchy_from_instance(root: Any) -> Dict[str, Tuple[str, Any, str]]:
    """Walk a live (flowed) object graph to enumerate every configurable kwarg.

    Mirror of :func:`get_hierarchy` but driven by the *concrete* objects DI
    handed back from ``flow()`` / ``LiquifyApp.liquify()``. That means this
    function sees post-construction setattr keys (e.g. ``Enable.visualize``)
    and defaults the user never wrote into YAML — both are invisible to the
    static-type walker.

    Rules applied at each ``@configurable`` instance reached through
    ``root`` (a dict, list, or object):

    * For every ``__init__`` param (skipping ``self``/``cls``), record
      ``(type_str, live_attribute_value, docstring)`` at path
      ``"<prefix>.<ClassName>.<param>"``.
    * Every ``vars(instance)`` key that is NOT in ``__init__``, NOT
      leading-underscore and NOT a ``__confluid_*__`` marker is surfaced as
      a leaf with current value + runtime type (docstring blank). This
      exposes the post-construction toggle pattern.
    * If a ctor-param value is itself ``@configurable`` → recurse. If it is
      a non-``@configurable`` instance → enumerate *its* ``__init__`` params
      as leaves (one level) but do not recurse further into its own
      attribute graph.
    * ``list`` / ``tuple`` / ``dict`` of configurables recurse with
      ``[N]`` / ``[key]`` suffixes.
    * Cycle-safe: tracks ``id(obj)`` per branch (same convention as
      :func:`get_hierarchy`).

    ``root`` is typically the ``dict`` returned by
    :meth:`liquifai.core.LiquifyApp.liquify` (top-level command kwargs).
    Any dict/list/object shape is accepted — the walker routes.
    """
    hierarchy: Dict[str, Tuple[str, Any, str]] = {}
    _walk_instance(root, "", hierarchy, set())
    return hierarchy


def _walk_instance(
    obj: Any,
    prefix: str,
    hierarchy: Dict[str, Tuple[str, Any, str]],
    visited: set,
    *,
    shallow: bool = False,
) -> None:
    """Recursive walker. ``shallow`` = True: enumerate ctor params as leaves,
    don't recurse into their values (used when the host class isn't
    ``@configurable`` — per the plan's one-level rule)."""
    if obj is None:
        return
    if isinstance(obj, (str, bytes, int, float, bool)):
        return  # primitives at the top level aren't hosts for kwargs

    if isinstance(obj, dict):
        for k, v in obj.items():
            sub = f"{prefix}.{k}" if prefix else str(k)
            _walk_instance(v, sub, hierarchy, visited)
        return

    if isinstance(obj, (list, tuple)):
        for i, item in enumerate(obj):
            sub = f"{prefix}[{i}]"
            _walk_instance(item, sub, hierarchy, visited)
        return

    # Class objects fall through to type-based walk — defer to get_hierarchy's
    # existing logic by recursing via the canonical helper.
    if isinstance(obj, type):
        _build_hierarchy_recursive(obj, prefix, hierarchy, visited)
        return

    # Instance path
    obj_id = id(obj)
    if obj_id in visited:
        return
    new_visited = visited | {obj_id}

    cls = obj.__class__
    is_configurable = bool(getattr(cls, "__confluid_configurable__", False))

    # Prefer an instance-level `name` over the class name when it's set — this
    # matches `_build_hierarchy_recursive`'s root-level behaviour and lets a
    # YAML disambiguate sibling instances of the same class (e.g. two Enable
    # wrappers named "overlay" and "labelstudio") so shortest-unique-path
    # display and dotted CLI overrides (`--overlay.visualize`) both work.
    instance_name = getattr(obj, "name", None) if not isinstance(obj, type) else None
    segment = str(instance_name) if instance_name else _configurable_class_name(cls)
    node_prefix = f"{prefix}.{segment}" if prefix else segment

    init_method = getattr(cls, "__init__", None)
    if init_method is None:
        return

    try:
        sig = inspect.signature(init_method)
        type_hints = get_type_hints(init_method)
    except (ValueError, TypeError):
        return

    # Prefer __init__'s own docstring; fall back to the class docstring
    # because user code commonly puts the Args: block at class level.
    docstring = init_method.__doc__ or cls.__doc__ or ""
    param_docs = _parse_docstring(docstring)

    ctor_param_names: set = set()
    for param_name, param in sig.parameters.items():
        if param_name in ("self", "cls", "args", "kwargs"):
            continue
        ctor_param_names.add(param_name)

        member = getattr(cls, param_name, None)
        if member is not None and getattr(member, "__confluid_ignore__", False):
            continue

        path = f"{node_prefix}.{param_name}"
        param_type = type_hints.get(param_name, Any)
        type_str = getattr(param_type, "__name__", str(param_type))
        live_value = getattr(
            obj,
            param_name,
            param.default if param.default is not inspect.Parameter.empty else None,
        )
        doc = param_docs.get(param_name, "")

        # Shallow mode (host is non-@configurable): record and move on.
        if shallow:
            hierarchy[path] = (type_str, live_value, doc)
            continue

        # Full walk: recurse into configurable-bearing values; otherwise leaf.
        if _is_configurable_instance(live_value):
            _walk_instance(live_value, path, hierarchy, new_visited)
            continue
        if _is_non_configurable_instance(live_value):
            # One level deep — walker will enumerate its ctor args as leaves
            # and stop (shallow=True) because the host isn't @configurable.
            _walk_instance(live_value, path, hierarchy, new_visited, shallow=True)
            continue
        if isinstance(live_value, (list, tuple)) and any(_is_any_instance(x) for x in live_value):
            for i, item in enumerate(live_value):
                child_shallow = not _is_configurable_instance(item)
                _walk_instance(item, f"{path}[{i}]", hierarchy, new_visited, shallow=child_shallow)
            continue
        if isinstance(live_value, dict) and any(_is_any_instance(x) for x in live_value.values()):
            for k, v in live_value.items():
                child_shallow = not _is_configurable_instance(v)
                _walk_instance(v, f"{path}[{k}]", hierarchy, new_visited, shallow=child_shallow)
            continue

        hierarchy[path] = (type_str, live_value, doc)

    if not is_configurable:
        # Non-@configurable: ctor args are enumerated above but we don't
        # enumerate post-construction setattr keys — per the one-level rule.
        return

    # Post-construction keys: ``vars(obj)`` filtered to drop attributes that
    # non-``@configurable`` ancestors contributed (class annotations, class
    # constants, ``__init__``-body setattrs). Keeps user-declared post-init
    # setattrs (e.g. ``self.loss_fn = nn.CrossEntropyLoss()`` in a Trainer's
    # body) AND post-construction setattrs done by Confluid's machinery or
    # the user externally (the Enable wrapper's ``obj.visualize = True``
    # pattern). See [confluid/confluid/loader.py:get_configurable_attrs].
    from confluid.loader import get_configurable_attrs

    declared_names = get_configurable_attrs(obj)
    for attr_name in declared_names:
        if attr_name in ctor_param_names:
            continue
        if attr_name.startswith("__confluid_"):
            continue
        attr_value = getattr(obj, attr_name)
        path = f"{node_prefix}.{attr_name}"
        type_str = type(attr_value).__name__
        if _is_configurable_instance(attr_value):
            _walk_instance(attr_value, path, hierarchy, new_visited)
            continue
        if isinstance(attr_value, (list, tuple)) and any(_is_configurable_instance(x) for x in attr_value):
            for i, item in enumerate(attr_value):
                _walk_instance(item, f"{path}[{i}]", hierarchy, new_visited)
            continue
        if isinstance(attr_value, dict) and any(_is_configurable_instance(x) for x in attr_value.values()):
            for k, v in attr_value.items():
                _walk_instance(v, f"{path}[{k}]", hierarchy, new_visited)
            continue
        hierarchy[path] = (type_str, attr_value, "")


def _is_configurable_instance(obj: Any) -> bool:
    """True when ``obj`` is a live (non-type) instance of a ``@configurable`` class."""
    if obj is None or isinstance(obj, type):
        return False
    cls = getattr(obj, "__class__", None)
    if cls is None:
        return False
    return bool(getattr(cls, "__confluid_configurable__", False))


def _is_any_instance(obj: Any) -> bool:
    """True when ``obj`` is any user-class instance (not a primitive/None/type)."""
    if obj is None or isinstance(obj, type):
        return False
    if isinstance(obj, (str, bytes, int, float, bool, list, tuple, dict, set)):
        return False
    return hasattr(obj, "__class__")


def _is_non_configurable_instance(obj: Any) -> bool:
    return _is_any_instance(obj) and not _is_configurable_instance(obj)


def _configurable_class_name(cls: type) -> str:
    """Honour ``__confluid_name__`` when present, falling back to ``__name__``."""
    return getattr(cls, "__confluid_name__", cls.__name__)


def shortest_unique_paths(all_paths: List[str]) -> Dict[str, str]:
    """Map each dotted path to the shortest trailing suffix that uniquely identifies it.

    For example, given ``["LightningTrainer.experiment_name",
    "LightningTrainer.optimizer.AdamW.lr"]`` the result is
    ``{"LightningTrainer.experiment_name": "experiment_name",
    "LightningTrainer.optimizer.AdamW.lr": "lr"}`` because each leaf is unique.
    When two paths share a leaf, the algorithm walks more of the path toward
    the root until disambiguation is reached.

    Used by display/logging layers (``liquifai.report.show_configuration``,
    the marainer hyperparameter logger) that want to surface paths without
    the noisy root-class prefix unless it is needed to tell two values apart.
    """
    display_map: Dict[str, str] = {}
    for full_path in all_paths:
        parts = full_path.split(".")
        for i in range(1, len(parts) + 1):
            suffix = ".".join(parts[-i:])
            matches = [p for p in all_paths if p.endswith(f".{suffix}") or p == suffix]
            if len(matches) == 1:
                display_map[full_path] = suffix
                break
        else:
            display_map[full_path] = full_path
    return display_map


def _parse_docstring(docstring: str) -> Dict[str, str]:
    """
    Parse Google/NumPy style docstring to extract parameter help.

    A parameter's description spans its continuation lines: it runs until the
    next ``name:`` / ``name (type):`` entry, a blank line, or the end of the
    string. The terminator deliberately uses ``\\Z`` (end of string), NOT ``$`` —
    under ``re.MULTILINE`` ``$`` matches at the end of *every* physical line, which
    would truncate every multi-line description to its first line.
    """
    param_docs: Dict[str, str] = {}
    if not docstring:
        return param_docs

    # Find the Args/Parameters section
    section_match = re.search(r"(?:Args|Parameters|Arguments):\s*(.*)", docstring, re.DOTALL | re.IGNORECASE)
    content = section_match.group(1) if section_match else docstring

    # Match "parameter (type): description" or "parameter: description"
    pattern = re.compile(
        r"^\s*([\w_]+)\s*(?:\([^\)]+\))?:\s*(.*?)(?=\n\s*[\w_]+\s*(?:\([^\)]+\))?:|\n\s*\n|\Z)",
        re.MULTILINE | re.DOTALL,
    )

    for match in pattern.finditer(content):
        name, description = match.groups()
        clean_desc = " ".join(description.split())
        param_docs[name] = clean_desc

    return param_docs


def parse_param_docs(obj: Any) -> Dict[str, str]:
    """Return ``{param_name: help_text}`` from ``obj``'s docstring ``Args:`` section.

    Resolves the docstring the same way :func:`confluid.to_pydantic` does — for a
    class, its ``__init__`` docstring (falling back to the class docstring); for a
    function or other callable, its own ``__doc__``. This is the single source of
    per-parameter help reused across the workspace: navigaitor turns it into
    pydantic ``Field(description=...)`` (via ``to_pydantic``) for the form-spec /
    HTTP editor, and FluxStudio turns it into ComfyUI widget tooltips. Document a
    constructor parameter once in the class's ``Args:`` block and it surfaces in
    both GUIs.

    Args:
        obj: A class, function, or any object with a ``__doc__`` / ``__init__``.

    Returns:
        Mapping of parameter name to its parsed (whitespace-collapsed) help text.
        Empty when there is no docstring or no recognizable ``Args:`` entries.
    """
    if isinstance(obj, type):
        init = obj.__dict__.get("__init__") or getattr(obj, "__init__", None)
        init_doc = getattr(init, "__doc__", None) if init is not object.__init__ else None
        docstring = init_doc or obj.__doc__ or ""
    else:
        docstring = getattr(obj, "__doc__", "") or ""
    return _parse_docstring(docstring)
