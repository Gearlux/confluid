import inspect
import re
from typing import Any, Dict, get_type_hints


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

    # Handle both classes and instances
    cls = obj if isinstance(obj, type) else obj.__class__

    # Avoid infinite recursion
    obj_id = id(obj)
    if obj_id in visited:
        return
    visited.add(obj_id)

    # 1. Extract name for this node
    cls_name = getattr(cls, "__confluid_name__", cls.__name__)
    instance_name = getattr(obj, "name", None) if not isinstance(obj, type) else None

    node_name = instance_name or cls_name
    current_prefix = f"{prefix}.{node_name}" if prefix else node_name

    # 2. Extract parameter documentation from docstring
    docstring = getattr(cls.__init__, "__doc__", "") or ""  # type: ignore[misc]
    param_docs = _parse_docstring(docstring)

    # 3. Get type hints and defaults from __init__
    try:
        init_method = cls.__init__  # type: ignore[misc]
        sig = inspect.signature(init_method)
        type_hints = get_type_hints(init_method)

        for param_name, param in sig.parameters.items():
            if param_name in ("self", "cls", "args", "kwargs"):
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

            hierarchy[path] = (type_str, default, doc)

            # 3. Recurse if the parameter type is configurable
            if hasattr(param_type, "__confluid_configurable__"):
                _build_hierarchy_recursive(param_type, current_prefix, hierarchy, visited)

    except (ValueError, TypeError):
        pass


def _parse_docstring(docstring: str) -> Dict[str, str]:
    """
    Parse Google/NumPy style docstring to extract parameter help.
    """
    param_docs: Dict[str, str] = {}
    if not docstring:
        return param_docs

    # Find the Args/Parameters section
    section_match = re.search(r"(?:Args|Parameters|Arguments):\s*(.*)", docstring, re.DOTALL | re.IGNORECASE)
    content = section_match.group(1) if section_match else docstring

    # Match "parameter (type): description" or "parameter: description"
    pattern = re.compile(
        r"^\s*([\w_]+)\s*(?:\([^\)]+\))?:\s*(.*?)(?=\n\s*[\w_]+\s*(?:\([^\)]+\))?:|\n\s*\n|$)",
        re.MULTILINE | re.DOTALL,
    )

    for match in pattern.finditer(content):
        name, description = match.groups()
        clean_desc = " ".join(description.split())
        param_docs[name] = clean_desc

    return param_docs
