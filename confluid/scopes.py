"""Scope resolution for confluid configs (``_scope_:`` / ``_notscope_:`` blocks).

Scopes are activated via the ``scopes=`` kwarg on :func:`confluid.load`
(typically forwarded by a CLI framework from ``--scope`` and dimension-bound CLI
flags). Each scope name is either:

* a bare ``"name"`` — a boolean scope, matching ``_scope_: {name: }``
* a ``"key=value"`` pair — a keyed scope, matching ``_scope_: {key: value}``
  (and the ``_notscope_:`` negative twins)

After alias and hierarchy expansion the active set becomes a
``{key: value_or_None}`` map. ``resolve_scopes`` walks the loaded dict (and
nested dicts / lists) replacing every :class:`confluid.fluid.ScopeBlock` in
place: its ``contents`` are spliced at its slot when the block is active,
otherwise the block is dropped.

Negation uses the *unset ⇒ active* convention: ``_notscope_: {debug: }`` is active
when ``"debug"`` is not in the set; ``_notscope_: {task: segmentation}`` is active
when no ``task=…`` scope is supplied at all, OR when one is supplied but its
value differs from ``segmentation``.

A document may also declare ``default_scopes: [dim=value, ...]`` — the value a
KEYED dimension takes when the caller names none. A default is filled in per
dimension (a caller value for that dimension wins), goes through the same
declared-value check as an activation, and is keyed ONLY: a boolean scope
cannot be switched off by the caller, so as a default it would be always-on
(``!notscope:`` is the spelling for "active while unset").

``scope_aliases``, ``default_scopes`` (top-level load inputs) and ``scopes``
(top-level metadata) are stripped at the end of resolution.
"""

from typing import Any, Dict, List, Optional, Set, Tuple

from loggair import get_logger

from confluid.exceptions import ScopeError
from confluid.fluid import Fluid, ScopeBlock, _at_yaml_loc, format_yaml_loc
from confluid.merger import deep_merge

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


#: The top-level keys that configure the LOAD rather than the program. Read by the
#: loader before pass 4 and stripped from the resolved document by :func:`resolve_scopes`.
METADATA_KEYS = ("scope_aliases", "default_scopes", "scopes")


def parse_default_scopes(value: Any, where: str = "") -> Dict[str, str]:
    """Validate a document's ``default_scopes:`` value into a ``{dimension: value}`` map.

    The value must be a list of ``"dim=value"`` strings. Every entry MUST be keyed:
    a bare name (a boolean scope, or an alias — aliases expand boolean names only)
    is refused, because nothing the caller passes could switch such a default off.
    ``None`` (key absent) is an empty map. Raises :class:`ScopeError` on any other
    shape, naming the offending entry and the spelling that works.
    """
    if value is None:
        return {}
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ScopeError(
            f"{where}`default_scopes` must be a list of 'dimension=value' strings, got {value!r}. "
            f"Write it as `default_scopes: [framework=lightning]`."
        )
    defaults: Dict[str, str] = {}
    for raw in value:
        key, val = parse_scope_arg(raw)
        if val is None:
            raise ScopeError(
                f"{where}`default_scopes` entry {raw!r} names no value: a default names the value a "
                f"dimension takes when the caller passes none, and a boolean scope (or an alias) "
                f"has no value to default — nothing the caller passes could switch it off. Write "
                f"`{key}=<value>`, or use `!notscope:{key}` for a block that is active while `{key}` is unset."
            )
        defaults[key] = val  # last write wins, mirroring the activation list
    return defaults


def default_scopes(config: Any) -> Dict[str, str]:
    """The ``{dimension: value}`` map a RAW document's ``default_scopes:`` declares.

    The public reader — what a CLI shows beside a dimension's offered values
    (:func:`discover_dimension_values`), and what the loader applies. Takes the RAW
    document (``load(path, until="raw")``): by the time pass 4 has run the key is
    stripped. A non-dict root, or a document without the key, answers ``{}``; a
    malformed key raises the same :class:`ScopeError` ``load()`` raises.
    """
    if not isinstance(config, dict):
        return {}
    return parse_default_scopes(config.get("default_scopes"))


def normalize_active(
    scopes: List[str],
    aliases: Optional[Dict[str, Any]] = None,
    defaults: Optional[Dict[str, str]] = None,
) -> Dict[str, Optional[str]]:
    """Build the post-alias post-hierarchy ``{key: value_or_None}`` activation map.

    Aliases only apply to *boolean* scope names; a keyed entry like
    ``task=classification`` is passed through verbatim. Hierarchical boolean
    names (``"prod.gpu"``) are expanded so each ancestor (``"prod"``) is also
    active. Last write wins per key, mirroring CLI re-specification.

    ``defaults`` (the document's parsed ``default_scopes:``) fills in every KEYED
    dimension the caller's ``scopes`` did not name — a caller value for that
    dimension always wins, and a default never touches a boolean scope.
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
    for key, value in (defaults or {}).items():
        if key not in active:
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
        A new structure with every ``ScopeBlock`` resolved. The top-level
        :data:`METADATA_KEYS` (``scope_aliases`` / ``default_scopes`` / ``scopes``)
        are stripped if present.

    Raises:
        ScopeError: An active keyed scope names a dimension the document
            declares, with a value no block carries — see
            :func:`_check_active_values_are_declared`.
    """
    logger.debug(f"Resolving scopes: {active}")
    _check_active_values_are_declared(config, active)
    resolved = _resolve_value(config, active)
    if isinstance(resolved, dict):
        resolved = {k: v for k, v in resolved.items() if k not in METADATA_KEYS}
    return resolved


#: Side channel of `_walk_dimensions`: per dimension, where its positive values are
#: declared — read ONLY by `_check_active_values_are_declared` for its error message
#: (the walker's public return stays a pair; discovery has no use for locations).
#: Reset per walk; single-threaded within one load like the rest of pass 4.
_DECLARATION_LOCS: Dict[str, List[str]] = {}


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
            for key, value in node.dims.items():
                if value is None:
                    continue  # boolean dimension — no selectable value to report
                values = positive.setdefault(key, set())
                if node.negate:
                    negated.add(key)
                else:
                    values.add(value)
                    loc = format_yaml_loc(node)
                    if loc:
                        _DECLARATION_LOCS.setdefault(key, []).append(loc)
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
    carrying ``_scope_: {framework: torch}`` / ``{framework: keras}`` /
    ``{model: convnet}`` blocks. Boolean scopes (``value is None``) declare no
    value and are absent entirely.

    A dimension declared ONLY by negated blocks maps to an EMPTY set, not to the
    values those blocks name: ``_notscope_: {task: segmentation}`` is activated by every
    value EXCEPT ``segmentation``, so its value is the one thing that does not
    select it. The key is still present — it is a real dimension a CLI must bind.

    Answers "what may I ask for?", which is why the values are the positive ones.
    """
    return _walk_dimensions(config)[0]


def discover_dimensions(config: Any) -> Set[str]:
    """Return the set of *keyed* scope dimension names appearing anywhere in ``config``.

    Boolean scopes (``value is None``) are not dimensions and are not returned.
    A CLI framework uses this to learn which ``--KEY VAL`` flags should bind to scope
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
      no ``framework``-keyed scope block at all) is an inert no-op, which is what lets
      a CLI pass a dimension unconditionally while a config grows into it;
    * an *unset* dimension resolves to whatever the document's unscoped keys say;
    * a dimension carrying ANY negated block accepts EVERY value, because that is
      what a negation means — ``_notscope_: {task: segmentation}`` fires for
      ``task=classification`` precisely because the value differs, and it is
      deactivated by ``task=segmentation``. Both outcomes are meaningful, so there
      is no value left to reject.
    """
    if not active:
        return
    _DECLARATION_LOCS.clear()
    positive, negated = _walk_dimensions(config)
    for key, value in active.items():
        if key in negated or not positive.get(key):
            continue
        if value is None:
            # A BARE activation of a KEYED dimension (`--scope framework` against
            # `framework=keras|lightning` blocks) can never select a variant — and it
            # SUPPRESSED the document's `default_scopes` value on top, so the run
            # silently used neither (BUGS-2026-08-19 SR13). Name the values.
            known = ", ".join(sorted(positive[key]))
            locs = ", ".join(_DECLARATION_LOCS.get(key, []))
            declared_at = f" (declared at {locs})" if locs else ""
            raise ScopeError(
                f"{key} is a KEYED dimension — a bare activation selects nothing. "
                f"This document declares {key} with: {known}{declared_at}; write {key}=<value>."
            )
        if value not in positive[key]:
            known = ", ".join(sorted(positive[key]))
            locs = ", ".join(_DECLARATION_LOCS.get(key, []))
            declared_at = f" (declared at {locs})" if locs else ""
            raise ScopeError(
                f"No scope block matches {key}={value!r}. "
                f"This document declares {key} with: {known}{declared_at}. "
                f"Either use one of those values, or add a `_scope_: {{{key}: {value}}}` block."
            )


def _is_active(block: ScopeBlock, active: Dict[str, Optional[str]]) -> bool:
    """Whether ``block`` fires under the ``active`` map. ALL its dimensions must match.

    A block with several dimensions is an AND — ``{framework: keras, model: convnet}``
    fires only when both are selected, which is what lets one block hold the
    combination that previously needed a block nested inside another.
    """

    def matches(key: str, value: Optional[str]) -> bool:
        if value is None:
            return key in active  # boolean dimension — present under any value
        return active.get(key) == value

    matched = all(matches(k, v) for k, v in block.dims.items())
    # Unset ⇒ active: a negation fires when the dimension was not specified at
    # all, OR was specified with a different value. Both are "not matched".
    return (not matched) if block.negate else matched


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
                    raise ScopeError(
                        f"Scope block {v!r} under key {k!r}{_at_yaml_loc(v)} has a "
                        f"{type(v.contents).__name__} body, but a block spliced into a mapping "
                        f"must carry a mapping body (its keys are what get spliced). "
                        f"Write the block's contents as `key: value` pairs, or move the block "
                        f"into a list if you meant to add list entries."
                    )
                resolved_contents = _resolve_dict(v.contents, active) if v.contents else {}
                for bk, bv in resolved_contents.items():
                    _splice_key(out, bk, bv)
            # else: drop the wrapper entirely
            continue
        _splice_key(out, k, _resolve_value(v, active))
    return out


def _splice_key(out: Dict[str, Any], key: str, value: Any) -> None:
    """Land ``key`` in ``out`` by the include-paste rule — the ONE splice semantics.

    A scope block's contents are "spliced at the wrapper's slot", which
    ``docs/lifecycle.md`` and ``loader._splice_includes`` describe as the rule an
    included file follows — and an included file goes through ``merger.deep_merge``:
    a mapping over a marker TUNES it (a copy; anchors keep the original), a nested
    block deep-merges, anything else replaces, and a restated key is RE-ANCHORED at
    the later position. Plain assignment (``out[key] = value``) did none of that
    (BUGS-2026-08-19 SR1/SR2/PA2/PA3): an active block DELETED the marker it
    re-stated with a mapping, a nested block lost its other keys, and a key the
    block re-stated kept the EARLIER writer's position — so whether a later bare
    key won flipped with the activation. The plain-key arm of ``_resolve_dict`` goes
    through here too, because a plain key written AFTER a block is the overlay of
    what the block spliced (a literal duplicate is refused at parse, so a collision
    on that arm can only come from a block).

    The merge is per KEY (``deep_merge`` of the one colliding entry), not of the
    whole accumulated mapping — a fresh key is assigned as before, so the common
    no-collision case copies nothing.

    ``include:`` is the one key with a special rule: two active blocks each carrying
    one must BOTH be read, and ``include:`` already accepts a list, so the values
    combine in document order instead of the second dropping the first (P11's loss,
    through scopes). The combined directive sits at the LATER block's position; the
    settle loop (``loader._settle_scopes_and_includes``) splices it there.
    """
    if key not in out:
        out[key] = value
        return
    existing = out[key]
    merged: Any
    if key == "include":
        as_list = [*(existing if isinstance(existing, list) else [existing])]
        as_list.extend(value if isinstance(value, list) else [value])
        merged = as_list
    else:
        merged = deep_merge({key: existing}, {key: value})[key]
    # Re-anchor: the later writer's position is the one the precedence rule reads.
    out.pop(key)
    out[key] = merged


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
