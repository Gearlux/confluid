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

from confluid.exceptions import ScopeError
from confluid.fluid import Fluid, ScopeBlock, format_yaml_loc

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

    Raises:
        ScopeError: An active keyed scope names a dimension the document
            declares, with a value no block carries — see
            :func:`_check_active_values_are_declared`.
    """
    logger.debug(f"Resolving scopes: {active}")
    _check_active_values_are_declared(config, active)
    resolved = _resolve_value(config, active)
    if isinstance(resolved, dict):
        resolved = {k: v for k, v in resolved.items() if k not in ("scope_aliases", "scopes")}
    return resolved


def _walk_dimensions(config: Any) -> Tuple[Dict[str, Set[str]], Set[str]]:
    """The ONE dimension walker: ``(positive values per key, keys carrying a negation)``.

    Walks dicts, lists, ``ScopeBlock.contents`` and :class:`confluid.fluid.Fluid`
    kwargs — the same node kinds :func:`_resolve_value` walks, which is the
    invariant they must keep (a dimension advertised but not resolvable is the
    bug class this module's docstring already records). Both public discovery
    functions and the activation check read this; do not add a second traversal.

    Positive and negated blocks are separated because they say OPPOSITE things
    about which values are meaningful — see :func:`_check_active_values_are_declared`.
    """
    positive: Dict[str, Set[str]] = {}
    negated: Set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, ScopeBlock):
            if node.value is not None:
                values = positive.setdefault(node.key, set())
                if node.negate:
                    negated.add(node.key)
                else:
                    values.add(node.value)
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
    return positive, negated


def discover_dimension_values(config: Any) -> Dict[str, Set[str]]:
    """Return every *keyed* scope dimension in ``config``, mapped to its selectable values.

    ``{"framework": {"torch", "keras"}, "model": {"convnet"}}`` for a document
    carrying ``!scope:framework=torch`` / ``!scope:framework=keras`` /
    ``!scope:model=convnet`` blocks. Boolean scopes (``value is None``) declare no
    value and are absent entirely.

    A dimension declared ONLY by negated blocks maps to an EMPTY set, not to the
    values those blocks name: ``!notscope:task=segmentation`` is activated by every
    value EXCEPT ``segmentation``, so its value is the one thing that does not
    select it. The key is still present — it is a real dimension a CLI must bind.

    Answers "what may I ask for?", which is why the values are the positive ones.
    """
    return _walk_dimensions(config)[0]


def discover_dimensions(config: Any) -> Set[str]:
    """Return the set of *keyed* scope dimension names appearing anywhere in ``config``.

    Boolean scopes (``value is None``) are not dimensions and are not returned.
    Liquifai uses this to learn which ``--KEY VAL`` flags should bind to scope
    activation rather than confluid overrides.

    Derived from :func:`_walk_dimensions` rather than traversing again — two
    walkers over the same node kinds is exactly how the pre-2026-08 asymmetry
    with :func:`_resolve_value` arose.
    """
    return set(_walk_dimensions(config)[0])


def _check_active_values_are_declared(config: Any, active: Dict[str, Optional[str]]) -> None:
    """Raise when an active keyed scope asks for a variant the document does not have.

    Without this, asking for a variant that does not exist silently produced the
    DEFAULT: a typo'd ``--framework kears`` ran the default backend and said
    nothing, which is indistinguishable from success until the artifacts are read.

    The rule is deliberately narrow. It fires only when the dimension is declared
    by POSITIVE blocks alone and the requested value matches none of them. Three
    neighbouring cases stay silent, each by design:

    * an *undeclared* dimension (``--scope framework=keras`` against a config with
      no ``!scope:framework=…`` block at all) is an inert no-op, which is what lets
      a CLI pass a dimension unconditionally while a config grows into it;
    * an *unset* dimension resolves to whatever the document's unscoped keys say;
    * a dimension carrying ANY negated block accepts EVERY value, because that is
      what a negation means — ``!notscope:task=segmentation`` fires for
      ``task=classification`` precisely because the value differs, and it is
      deactivated by ``task=segmentation``. Both outcomes are meaningful, so there
      is no value left to reject.
    """
    if not active:
        return
    positive, negated = _walk_dimensions(config)
    for key, value in active.items():
        if value is None or key in negated or not positive.get(key):
            continue
        if value not in positive[key]:
            known = ", ".join(sorted(positive[key]))
            raise ScopeError(
                f"No scope block matches {key}={value!r}. "
                f"This document declares {key} with: {known}. "
                f"Either use one of those values, or add a `!scope:{key}={value}` block."
            )


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

    Dicts, lists and :class:`Fluid` kwargs are walked. A ``ScopeBlock``
    encountered as a list element or top-level value is resolved by replacing it
    with its contents (when active) or dropping it (when inactive). Inside dicts
    (and inside a marker's kwargs), the splice happens in place at the wrapper's
    slot.

    The ``Fluid`` arm matters because a marker's kwargs are a mapping like any
    other — ``model: !class:Foo`` with a sibling ``alt: !scope:model=bar`` INSIDE
    the parent ``!class:`` block is the natural way to write a per-slot
    alternative. Without it the nested wrapper survived resolution untouched and
    the scope silently did nothing, while ``discover_dimensions`` (which has
    always walked ``Fluid.kwargs``) still advertised the dimension — so the CLI
    accepted ``--model bar`` and dropped it on the floor. The two walkers must
    agree on which nodes carry scope blocks.
    """
    if isinstance(value, dict):
        return _resolve_dict(value, active)
    if isinstance(value, Fluid):
        # Mutated in place, matching the sibling walker `loader._process_includes_recursive`
        # — a marker is a node, not a container the caller can rebuild around.
        value.kwargs = _resolve_dict(value.kwargs, active)
        return value
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
                if v.contents and not isinstance(v.contents, dict):
                    # A sequence/scalar body has no KEYS to splice, and the wrapper key
                    # is inert scaffolding that cannot stand in for them — so there is no
                    # coherent result here (in a LIST the same body is meaningful; see
                    # `_resolve_list`). Raised only when ACTIVE, matching the rule that an
                    # inactive block is dropped without its contents being examined.
                    loc = format_yaml_loc(v)
                    where = f" at {loc}" if loc else ""
                    raise ScopeError(
                        f"Scope block {v.key!r} under key {k!r}{where} has a "
                        f"{type(v.contents).__name__} body, but a block spliced into a mapping "
                        f"must carry a mapping body (its keys are what get spliced). "
                        f"Write the block's contents as `key: value` pairs, or move the block "
                        f"into a list if you meant to add list entries."
                    )
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
            raise ScopeError(f"Circular scope alias detected: {' -> '.join(path + [name])}")
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
