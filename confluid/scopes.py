"""Tag-driven scope resolution for confluid configs.

Scopes are activated via the ``scopes=`` kwarg on :func:`confluid.load`
(typically forwarded by liquifai from ``--scope`` and dimension-bound CLI
flags). Each scope name is either:

* a bare ``"name"`` — boolean scope, matches ``!scope:name`` / ``!notscope:name``
* a ``"key=value"`` pair — keyed scope, matches ``!scope:key=value`` /
  ``!scope:key(value)`` and their negative twins

After alias and hierarchy expansion the active set becomes a
``{key: value_or_None}`` map. ``resolve_scopes`` walks the loaded dict (and
nested dicts / lists) replacing every :class:`confluid.fluid.ScopeBlock` in
place: its ``contents`` are spliced at its slot when the block is active,
otherwise the block is dropped.

Negation uses the *unset ⇒ active* convention: ``!notscope:debug`` is active
when ``"debug"`` is not in the set; ``!notscope:task=segmentation`` is active
when no ``task=…`` scope is supplied at all, OR when one is supplied but its
value differs from ``segmentation``.

``scope_aliases`` (top-level) and ``scopes`` (top-level metadata) are
stripped at the end of resolution.
"""

from typing import Any, Dict, List, Optional, Set, Tuple

from loggair import get_logger

from confluid.fluid import ScopeBlock

logger = get_logger("confluid.scopes")


def parse_scope_arg(arg: str) -> Tuple[str, Optional[str]]:
    """Parse one CLI / kwarg scope string into a ``(key, value)`` pair.

    ``"debug"`` → ``("debug", None)``; ``"task=classification"`` → ``("task", "classification")``.
    Whitespace around the ``=`` is stripped.
    """
    if "=" in arg:
        key, value = arg.split("=", 1)
        return key.strip(), value.strip()
    return arg.strip(), None


def normalize_active(scopes: List[str], aliases: Optional[Dict[str, Any]] = None) -> Dict[str, Optional[str]]:
    """Build the post-alias post-hierarchy ``{key: value_or_None}`` activation map.

    Aliases only apply to *boolean* scope names; a keyed entry like
    ``task=classification`` is passed through verbatim. Hierarchical boolean
    names (``"prod.gpu"``) are expanded so each ancestor (``"prod"``) is also
    active. Last write wins per key, mirroring CLI re-specification.
    """
    aliases = aliases or {}
    active: Dict[str, Optional[str]] = {}

    for raw in scopes:
        key, value = parse_scope_arg(raw)
        if value is None and key in aliases:
            for expanded in _resolve_aliases([key], aliases):
                for h in _expand_hierarchy(expanded):
                    active[h] = None
            continue
        if value is None:
            for h in _expand_hierarchy(key):
                active[h] = None
        else:
            # Keyed scopes never alias-expand and never hierarchy-split their value.
            active[key] = value
    return active


def resolve_scopes(config: Any, active: Dict[str, Optional[str]]) -> Any:
    """Walk ``config`` recursively, splicing or dropping :class:`ScopeBlock` nodes.

    Args:
        config: The raw loaded structure (typically a dict). Nested dicts and
            lists are traversed; non-container nodes are returned verbatim.
        active: The activation map produced by :func:`normalize_active`. Pass
            ``{}`` to drop every positive scope block and keep every negative
            one (the "no --scope flags" case).

    Returns:
        A new structure with every ``ScopeBlock`` resolved. Top-level
        ``scope_aliases`` and ``scopes`` metadata keys are stripped if present.
    """
    logger.debug(f"Resolving scopes: {active}")
    resolved = _resolve_value(config, active)
    if isinstance(resolved, dict):
        resolved = {k: v for k, v in resolved.items() if k not in ("scope_aliases", "scopes")}
    return resolved


def discover_dimensions(config: Any) -> Set[str]:
    """Return the set of *keyed* scope dimension names appearing anywhere in ``config``.

    Walks dicts, lists, ``ScopeBlock.contents``, and :class:`confluid.fluid.Fluid`
    kwargs. Boolean scopes (``value is None``) are not dimensions and are not
    returned. Liquifai uses this to learn which ``--KEY VAL`` flags should bind
    to scope activation rather than confluid overrides.
    """
    from confluid.fluid import Fluid

    found: Set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, ScopeBlock):
            if node.value is not None:
                found.add(node.key)
            walk(node.contents)
            return
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
            return
        if isinstance(node, list):
            for v in node:
                walk(v)
            return
        if isinstance(node, Fluid):
            for v in node.kwargs.values():
                walk(v)
            return

    walk(config)
    return found


def _is_active(block: ScopeBlock, active: Dict[str, Optional[str]]) -> bool:
    if block.value is None:
        # Boolean scope — active iff the key is in the activation map (any value).
        present = block.key in active
        return (not present) if block.negate else present
    # Keyed scope — active iff active[key] equals block.value.
    matches = active.get(block.key) == block.value
    if block.negate:
        # Unset ⇒ active (per plan): the negation block fires when the user
        # didn't specify this dimension at all, OR specified a different value.
        return not matches
    return matches


def _resolve_value(value: Any, active: Dict[str, Optional[str]]) -> Any:
    """Recursively resolve scope blocks inside ``value``.

    Dicts and lists are walked. A ``ScopeBlock`` encountered as a list element
    or top-level value is resolved by replacing it with its contents (when
    active) or dropping it (when inactive). Inside dicts, the splice happens
    in place at the wrapper's slot.
    """
    if isinstance(value, dict):
        return _resolve_dict(value, active)
    if isinstance(value, list):
        return _resolve_list(value, active)
    if isinstance(value, ScopeBlock):
        # Bare ScopeBlock at the top level (rare) — return contents if active.
        if _is_active(value, active):
            return _resolve_value(value.contents, active)
        return None
    return value


def _resolve_dict(d: Dict[str, Any], active: Dict[str, Optional[str]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, ScopeBlock):
            if _is_active(v, active):
                resolved_contents = _resolve_dict(v.contents, active) if v.contents else {}
                for bk, bv in resolved_contents.items():
                    out[bk] = bv
            # else: drop the wrapper entirely
            continue
        out[k] = _resolve_value(v, active)
    return out


def _resolve_list(items: List[Any], active: Dict[str, Optional[str]]) -> List[Any]:
    out: List[Any] = []
    for item in items:
        if isinstance(item, ScopeBlock):
            if _is_active(item, active):
                resolved = _resolve_value(item.contents, active)
                if isinstance(resolved, dict):
                    out.append(resolved)
                elif isinstance(resolved, list):
                    out.extend(resolved)
                else:
                    out.append(resolved)
            continue
        out.append(_resolve_value(item, active))
    return out


def _resolve_aliases(requested: List[str], aliases: Dict[str, Any]) -> List[str]:
    """Recursively expand alias chains. Detects circular references."""
    resolved: List[str] = []
    seen: Set[str] = set()

    def expand(name: str, path: List[str]) -> None:
        if name in path:
            raise ValueError(f"Circular scope alias detected: {' -> '.join(path + [name])}")
        if name in seen:
            return
        seen.add(name)
        if name in aliases:
            target = aliases[name]
            new_path = path + [name]
            if isinstance(target, str):
                expand(target, new_path)
            elif isinstance(target, list):
                for t in target:
                    expand(t, new_path)
        else:
            resolved.append(name)

    for r in requested:
        seen.clear()
        expand(r, [])
    return resolved


def _expand_hierarchy(scope_name: str) -> List[str]:
    """``"prod.gpu"`` → ``["prod", "prod.gpu"]``. Each ancestor activates with it."""
    parts = scope_name.split(".")
    return [".".join(parts[: i + 1]) for i in range(len(parts))]
