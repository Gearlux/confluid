"""Broadcasting and ordered matching — how a document's keys reach a node.

Confluid has ONE precedence rule: values apply in DOCUMENT ORDER and the last
spec wins. There are no specificity tiers — a key written at a node and a key
cascading past it are ordered by position alone. This module owns that rule and
the machinery that carries it: the scope tags (:class:`_KeyScope`), the tagged
view (:class:`_View`), the ordered merge (:func:`_prepare_kwargs`), the child-view
splice (:func:`_splice_kwargs_at_slot`), the accept-lists, and the three public
settability predicates.

Split out of ``engine`` because the rule had been implemented TWICE — here and,
over live objects, in ``configurator`` — and the two copies diverged four
separate ways in a single day (which spellings deliver to a deferred slot,
whether a ``Lazy`` is eagerly flowed, whether a bare key reaches a deferred slot
at all, and whether any of it was ordered). Three of those four failed silently.
One module both callers import is what stops the fifth.

Layer position: ``fluid -> state -> broadcast -> engine``. Nothing here
materializes anything — no ``flow``, no ``_flow_recursive`` — which is what keeps
the dependency one-directional. Keep it that way: code that needs to BUILD an
object belongs in ``engine``.
"""

import collections.abc as cabc
import inspect
import typing
from enum import Enum
from typing import Any, Callable, Dict, FrozenSet, Optional, Set

from loggair import get_logger

from confluid.fluid import Fluid, Reference
from confluid.introspect import baked_init_attrs, init_callable, init_setattr_names, init_source_available
from confluid.registry import resolve_class
from confluid.state import _ENGINE_STATE

logger = get_logger("confluid.broadcast")

# Introspection caches, keyed by class name. Every reader is in this module;
# ``engine`` clears them once per ``materialize`` / ``resolve`` pass through
# the re-export (its own ``_parent_blacklist_cache`` lives in ``engine`` and
# is cleared alongside — cache ownership follows module ownership).
_acceptable_keys_cache: Dict[str, Optional[FrozenSet[str]]] = {}
_post_init_attrs_cache: Dict[str, FrozenSet[str]] = {}
# Per-class: ``{param_name: "dict" | "list" | None}`` — None means "not annotated
# as a dict/list-shaped type" (default scalar/Fluid-only broadcast rules apply).
_param_kind_cache: Dict[str, Dict[str, Optional[str]]] = {}
# Classes already warned about an unscannable ``__init__`` (compiled/frozen —
# see :func:`_warn_if_init_unscannable`). Deliberately NOT cleared by
# materialize/resolve: those clear the attr caches once per pass, which would
# re-fire the warning on every config load. One warning per class per process.
_warned_unscannable_inits: Set[str] = set()


def _same_target(fluid_target: Any, cls: Callable[..., Any]) -> bool:
    """True if ``fluid_target`` resolves to the same class object as ``cls``.

    Identity-only comparison: two classes that share a short name across
    different modules are NOT considered "same". This prevents the
    self-broadcast guard from over-skipping fluids whose target happens to
    share a name with the receiving class.

    Handles three cases:
      * ``fluid_target`` IS ``cls`` — fast path.
      * ``fluid_target`` is a string and ``resolve_class`` resolves it to
        ``cls`` — registry-confirmed match.
      * ``fluid_target`` is a string equal to the fully-qualified
        ``cls.__module__.__qualname__`` — last-resort match for classes
        that aren't registered yet but whose dotted path is unambiguous.

    Bare-name strings (``"Trainer"``) that can't be registry-resolved are
    treated as "not same" — better to broadcast and let the receiver's
    accept-list filter than to silently skip across module boundaries.
    """
    if fluid_target is cls:
        return True
    if isinstance(fluid_target, str):
        resolved = resolve_class(fluid_target)
        if resolved is cls:
            return True
        qualified = f"{cls.__module__}.{cls.__qualname__}"
        if fluid_target == qualified:
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
    cache_key = f"{target.__module__}.{target.__qualname__}"
    if cache_key in _post_init_attrs_cache:
        return _post_init_attrs_cache[cache_key]

    # Declared escape hatch: ``@configurable(broadcast_attrs=[...])``. UNIONED
    # with the scanned names, never a replacement — declaring can't lose scanned
    # attrs (redundant in dev checkouts, load-bearing in compiled/frozen
    # deployments where ``inspect.getsource`` fails and the scan is empty).
    declared = getattr(target, "__confluid_broadcast_attrs__", None)
    names: Set[str] = set(declared or ())
    try:
        mro = target.__mro__
    except AttributeError:
        result = frozenset(names)
        _post_init_attrs_cache[cache_key] = result
        return result

    for klass in mro:
        if klass is object:
            continue
        init = klass.__dict__.get("__init__")
        if init is None:
            continue
        scanned = init_setattr_names(init)
        names.update(scanned)
        if not scanned:
            # Source unavailable (compiled/frozen) or a genuinely empty body:
            # fall back to the build-time bake table (``python -m confluid.bake``,
            # emitted while source still existed). An empty-body class bakes an
            # empty entry, so the union is a no-op for it. Applies per MRO
            # class, so baked in-package base classes contribute too.
            names.update(baked_init_attrs(klass) or ())

    if declared is None:
        _warn_if_init_unscannable(target, cache_key)

    result = frozenset(names)
    _post_init_attrs_cache[cache_key] = result
    return result


def _warn_if_init_unscannable(target: type, cache_key: str) -> None:
    """Warn ONCE per class when the TARGET's own ``__init__`` can't be AST-scanned.

    In compiled / frozen / zip deployments ``inspect.getsource`` raises, the
    body scan silently returns empty, and post-init broadcast attrs vanish —
    a dev-vs-packaged behavioral divergence with no other diagnostic. Fires
    only for the target's OWN ``__init__`` (from ``target.__dict__``) on a
    ``@configurable`` class with no ``broadcast_attrs`` declaration AND no
    build-time bake-table entry (``confluid.bake``); MRO parents with
    unreadable source stay silent (builtins are normal).
    """
    if cache_key in _warned_unscannable_inits:
        return
    if not getattr(target, "__confluid_configurable__", False):
        return
    own_init = target.__dict__.get("__init__")
    if own_init is None or init_source_available(own_init):
        return
    if baked_init_attrs(target) is not None:
        return  # covered by a build-time bake table — packaged mode is healthy
    _warned_unscannable_inits.add(cache_key)
    logger.warning(
        f"cannot scan __init__ body of {cache_key} (source unavailable — compiled/frozen?): "
        f"post-init broadcast attrs are invisible; run 'confluid-bake <package>' at build time "
        f"or declare @configurable(broadcast_attrs=[...])"
    )


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

    Resolution order: a string name is resolved to its class FIRST, then
    cached under the resolved class's fully-qualified name. This prevents
    two classes that share a short name across different modules from
    silently inheriting one another's accept-list.
    """
    target: Any
    if isinstance(cls_or_name, type) or callable(cls_or_name):
        # A class OR an already-resolved callable target (a registered builder
        # FUNCTION) — introspect it directly. Round-tripping a callable through
        # resolve_class returns None (its passthrough branch is type-only), which
        # cached "accepts everything" for every function target.
        target = cls_or_name
    else:
        # Always resolve the string first so the cache key is module-qualified.

        resolved = resolve_class(cls_or_name)
        if resolved is None:
            # Truly unresolvable — cache the negative result under the raw
            # name so repeated lookups stay O(1). Two modules with the same
            # unresolvable name collide, but the value is None in both cases
            # so the collision is benign.
            if cls_or_name in _acceptable_keys_cache:
                return _acceptable_keys_cache[cls_or_name]
            _acceptable_keys_cache[cls_or_name] = None
            return None
        target = resolved

    cache_key = f"{target.__module__}.{target.__qualname__}"
    if cache_key in _acceptable_keys_cache:
        return _acceptable_keys_cache[cache_key]

    keys: Set[str] = set()
    try:
        # Class → its __init__; plain callable (a registered builder FUNCTION) →
        # the callable itself. Reading ``target.__init__`` unconditionally gave a
        # function ``object.__init__`` = ``(*args, **kwargs)``, so every builder
        # function answered "accepts everything" (see introspect.init_callable).
        init_method = init_callable(target)
        if init_method is None:
            _acceptable_keys_cache[cache_key] = None
            return None
        sig = inspect.signature(init_method)
        if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
            # A **kwargs constructor makes the accept-list unknowable, and the
            # gates treat ``None`` as accept-EVERYTHING: every bare top-level /
            # glob-delivered key broadcasts into instances of this class. See
            # docs/broadcasting.md → "Classes with **kwargs constructors".
            logger.trace(
                f"accept-list unknown for {cache_key} (**kwargs constructor) — "
                f"bare broadcasts are unfiltered for this class"
            )
            _acceptable_keys_cache[cache_key] = None
            return None
        keys.update(p for p in sig.parameters if p not in ("self", "cls"))
    except (ValueError, TypeError):
        _acceptable_keys_cache[cache_key] = None
        return None

    # Class-only extras: settable class attributes and __init__-body slots.
    # A plain callable has neither (its function attributes are not config
    # slots, and there is no body to setattr into post-construction).
    if isinstance(target, type) and getattr(target, "__confluid_configurable__", False):
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
        # post-init injection loop in confluid.engine.flow already assigns
        # any matching kwarg via setattr — broadcasting just needs to know
        # the names so a top-level YAML key can flow into them.
        keys.update(_get_post_init_attrs(target))

    result = frozenset(keys)
    _acceptable_keys_cache[cache_key] = result
    return result


def _get_param_kinds(cls_or_name: Any) -> Dict[str, Optional[str]]:
    """Return ``{param_name: "dict" | "list" | None}`` for a target's ctor.

    Used by :func:`_accepts` to decide whether a dict/list value at a
    matching key in the parent context should be broadcast IN (when the
    target's annotation says it expects a dict/list) or left to recurse as
    a config sub-block (the default for un-annotated/scalar-shaped params).

    Resolution is annotation-only (no runtime values); if a class doesn't
    annotate, the value stays None and the legacy "skip dict/list" rule
    applies. ``typing.get_type_hints`` is wrapped in a try/except because
    forward references that can't be resolved would otherwise raise.
    """
    target: Any
    if isinstance(cls_or_name, type) or callable(cls_or_name):
        target = cls_or_name
    else:
        target = resolve_class(cls_or_name)
        if target is None:
            return {}

    cache_key = f"{target.__module__}.{target.__qualname__}"
    if cache_key in _param_kind_cache:
        return _param_kind_cache[cache_key]

    kinds: Dict[str, Optional[str]] = {}
    try:
        # Same class-vs-callable dispatch as _get_acceptable_keys — a builder
        # function's own signature, never object.__init__ (see init_callable).
        init_method = init_callable(target)
        if init_method is None:
            _param_kind_cache[cache_key] = kinds
            return kinds
        sig = inspect.signature(init_method)
    except (ValueError, TypeError):
        _param_kind_cache[cache_key] = kinds
        return kinds

    # Try to resolve forward refs via typing.get_type_hints; fall back to
    # the raw .annotation when that fails (common for self-referential or
    # third-party-imported annotations).
    try:
        hints = typing.get_type_hints(init_method)
    except Exception:
        hints = {}

    for name, param in sig.parameters.items():
        if name in ("self", "cls"):
            continue
        ann = hints.get(name, param.annotation)
        kinds[name] = _classify_annotation(ann)

    _param_kind_cache[cache_key] = kinds
    return kinds


def _classify_annotation(ann: Any) -> Optional[str]:
    """Map a type annotation to ``"dict"`` / ``"list"`` / None.

    Recognizes the obvious built-ins (``dict``, ``list``, ``tuple``,
    ``set``) and their ``typing`` analogues (``Dict``, ``List``, ``Tuple``,
    ``Set``, ``Mapping``, ``Sequence``, ``MutableMapping``, etc.). Unions
    that include any of these on either side count as the corresponding
    kind — e.g. ``Optional[Dict[str, int]]`` classifies as ``"dict"``.

    Returns None for anything else (including bare ``Any`` and unannotated).
    """
    if ann is inspect.Parameter.empty:
        return None

    # Direct built-ins.
    if ann in (dict, list, tuple, set, frozenset):
        return "dict" if ann is dict else "list"

    # typing.* origins.
    origin = typing.get_origin(ann)
    if origin is not None:
        if origin in (dict,) or origin is typing.Dict:  # type: ignore[attr-defined]
            return "dict"
        if origin in (list, tuple, set, frozenset):
            return "list"
        # Abstract collections from typing/collections.abc.
        if origin in (cabc.Mapping, cabc.MutableMapping):
            return "dict"
        if origin in (cabc.Sequence, cabc.MutableSequence, cabc.Iterable, cabc.Collection):
            return "list"
        if origin is typing.Union:
            for arg in typing.get_args(ann):
                kind = _classify_annotation(arg)
                if kind is not None:
                    return kind
    return None


class _KeyScope(Enum):
    """Broadcast scope of one key in a config view (see :class:`_View`).

    * ``BARE`` — an un-addressed key (implicit ``**.key``): broadcasts to
      every accepting node in the subtree. The default for untagged keys,
      so a plain root document is all-BARE by construction.
    * ``EXACT`` — an addressed value already delivered to its target node
      (a Fluid's own kwarg, or a matched named block's scalar). Stays in
      the view for document ordering and ``!ref:`` resolution but is never
      re-applied below.
    * ``STRICT`` — a routing sub-block (a deeper path segment such as the
      ``opt`` in ``Trainer: {opt: {lr: …}}``, or a ``'*'`` glob block)
      valid for exactly one more nesting level; dropped at the next Fluid
      boundary by :func:`_splice_kwargs_at_slot`.
    * ``ADDRESSED`` — used by the configurator's attr-recursion path: an
      entry of a block addressed to exactly the object now consuming the
      view (applied like matched-block contents, spent below it). Avoids
      wrapping the sub-block under the child's class name, which would
      collide with a floating block of the same name in same-class trees.
    """

    BARE = "bare"
    EXACT = "exact"
    STRICT = "strict"
    ADDRESSED = "addressed"


class _View(dict):
    """An ordered config view whose keys carry broadcast-scope tags.

    A plain ``dict`` subclass so every existing ``isinstance`` / iteration /
    ``in`` / value-identity site keeps working; the ``scopes`` side-table
    (missing key ⇒ ``BARE``) is what the scoping rules read. The dict-API
    surface preserves the tags — ``_View(view)``, ``view.copy()``, and
    ``view.update(other_view)`` all carry the side-table — so engine code can
    copy views without silently flattening addressing. The ONE remaining
    degradation is a copy through plain-dict syntax (``dict(view)`` /
    ``{**view}``), which yields an untagged dict (all-BARE): correct for the
    root document, lossy anywhere else — construct a ``_View`` instead.
    """

    __slots__ = ("scopes",)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.scopes: Dict[str, _KeyScope] = {}
        if args and isinstance(args[0], _View):
            self.scopes.update(args[0].scopes)

    def set(self, key: str, value: Any, scope: _KeyScope) -> None:
        """Assign ``key`` (keeping its position if present) with a scope tag.

        The ONE write path into a merged view, and therefore the one place that
        can see a value being replaced. Every silent misconfiguration in this area
        has looked identical from outside — the run uses a value the author did not
        write at the node they wrote it on, and nothing says so — so an overwrite
        that CHANGES the value is logged with both sides and the scope that won.

        DEBUG, not warning: overwriting is normal operation and the point of bare
        keys (a sweep's ``lr:`` overriding per-node defaults is the feature). This
        exists so "my knob did not take" is one grep instead of a bisect.
        """
        if key in self and self[key] != value:
            # The article is chosen from the WINNING scope's name because two of
            # the four (``exact``, ``addressed``) start with a vowel: a hardcoded
            # "a" reads as a typo in the very line an operator greps to explain a
            # value they did not expect.
            article = "an" if scope.value[0] in "aeiou" else "a"
            logger.debug(
                f"override: {key!r} {self[key]!r} -> {value!r} "
                f"({self.scope_of(key).value} value replaced by {article} {scope.value} one; "
                f"document order decides — the later spec wins)"
            )
        self[key] = value
        if scope is _KeyScope.BARE:
            self.scopes.pop(key, None)
        else:
            self.scopes[key] = scope

    def scope_of(self, key: str) -> _KeyScope:
        return self.scopes.get(key, _KeyScope.BARE)

    def pop(self, key: str, *default: Any) -> Any:  # type: ignore[override]
        self.scopes.pop(key, None)
        return super().pop(key, *default)

    def copy(self) -> "_View":
        """A ``_View`` copy carrying the scope tags — ``dict.copy()`` on a
        subclass returns a plain ``dict``, which would silently drop them."""
        return _View(self)

    def update(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        """``dict.update`` with last-write-wins on the scope tags.

        Each updated key takes the SOURCE's scope: a ``_View`` source carries
        its tag over; a plain-dict / iterable-of-pairs / keyword source is
        untagged, so it CLEARS any existing tag (the key is now BARE) —
        keeping the side-table consistent with the values under it.
        """
        if args and not isinstance(args[0], dict):
            args = (list(args[0]), *args[1:])  # materialize a one-shot iterator
        super().update(*args, **kwargs)
        src = args[0] if args else None
        if isinstance(src, _View):
            for k in src:
                if k in src.scopes:
                    self.scopes[k] = src.scopes[k]
                else:
                    self.scopes.pop(k, None)
        elif isinstance(src, dict):
            for k in src:
                self.scopes.pop(k, None)
        elif src is not None:
            for k, _ in src:
                self.scopes.pop(k, None)
        for k in kwargs:
            self.scopes.pop(k, None)


def _scope_of(view: Any, key: str) -> _KeyScope:
    """Scope of ``key`` in ``view`` — plain (untagged) dicts are all-BARE."""
    if isinstance(view, _View):
        return view.scope_of(key)
    return _KeyScope.BARE


_GLOB_KEYS = ("*", "**")


def _is_glob_key(key: Any) -> bool:
    """True for the glob routing block names ``'*'`` / ``'**'``.

    Glob keys are addressing metadata, never values: they must not reach
    constructor kwargs, post-init setattrs, or ``__confluid_kwargs__``
    capture.
    """
    return key in _GLOB_KEYS


def _expand_block_keys(block: Dict[str, Any]) -> Dict[str, Any]:
    """Expand dotted keys INSIDE a block / marker-kwargs mapping.

    The in-block analogue of :func:`confluid.merger.expand_dotted_keys`
    (which only processes the document's top-level keys): ``'**.lr'`` inside
    a matched ``Trainer:`` block nests to ``{'**': {'lr': …}}``, so
    ``Trainer: {'**.lr': 1}`` ≡ ``Trainer.**.lr: 1``. Unlike the merger
    variant this shares every value by REFERENCE (never deep-copies), so
    resolved ``!ref:`` identity survives; dicts descended into are
    shallow-copied copy-on-write so the caller's input is never mutated
    (a ``Fluid`` target's kwargs ARE descended into and extended in place,
    mirroring the merger's traversal). No-op (same object) when no key
    contains a dot.
    """
    if not any("." in k for k in block):
        return block
    out: Dict[str, Any] = {k: v for k, v in block.items() if "." not in k}
    dotted = sorted((k for k in block if "." in k), key=lambda k: (k.count("."), k))
    for key in dotted:
        value = block[key]
        parts = key.split(".")
        cur: Dict[str, Any] = out
        for part in parts[:-1]:
            nxt = cur.get(part)
            if isinstance(nxt, Fluid):
                cur = nxt.kwargs
                continue
            if isinstance(nxt, dict):
                copied = dict(nxt)
                cur[part] = copied
                cur = copied
                continue
            fresh: Dict[str, Any] = {}
            cur[part] = fresh
            cur = fresh
        last = parts[-1]
        prev = cur.get(last)
        if isinstance(prev, dict) and isinstance(value, dict):
            cur[last] = {**prev, **value}
        else:
            cur[last] = value
    return out


def _late_bare_keys_per_slot(child_ctx: Dict[str, Any], kwargs: Dict[str, Any]) -> Dict[str, FrozenSet[str]]:
    """For each dict-valued kwarg, the BARE keys positioned AFTER it in the document.

    A mapping addressed at a slot (``optimizer: {lr: 0.5}``) is the one addressing
    form that cannot be ordered where every other form is. A marker gets its own
    :func:`_prepare_kwargs` pass against ``child_ctx`` and so competes with bare
    keys by position; a plain dict is not a marker, is applied only after
    construction (the first moment the slot's deferred default is knowable), and
    carries no position of its own — dicts take no attributes.

    So the contest is settled HERE, while the ordering is still in hand, and only
    its OUTCOME is carried forward: the bare keys that sit later than the slot and
    therefore beat it. A slot with an empty set wins outright. ``child_ctx`` is the
    spliced view, whose key order IS document order, which is what makes an index
    comparison meaningful across the two levels.

    Returns an empty mapping when nothing needs it — the overwhelmingly common
    case, and the one that must stay allocation-free.
    """
    slots = [k for k, v in kwargs.items() if isinstance(v, dict) and not _is_glob_key(k) and k in child_ctx]
    if not slots:
        return {}
    order = {k: i for i, k in enumerate(child_ctx)}
    bare = [(k, i) for k, i in order.items() if _scope_of(child_ctx, k) is _KeyScope.BARE]
    return {slot: frozenset(k for k, i in bare if i > order[slot]) for slot in slots}


def _splice_kwargs_at_slot(
    parent_context: Dict[str, Any],
    self_key: Optional[str],
    kwargs: Dict[str, Any],
    receiver_cls: Any = None,
) -> Dict[str, Any]:
    """Build the receiver's child view: replace ``parent_context[self_key]``
    with ``kwargs``'s items at the same position, preserving document
    order. When ``self_key`` is not in ``parent_context`` (top-level call,
    or identity match failed), kwargs are appended at the end.

    This is where the scoping semantics flip (2026-07): the returned view is
    a :class:`_View` whose tags decide what descendants may consume —

    * inherited ``STRICT`` entries (and ``'*'`` glob blocks) from
      ``parent_context`` are DROPPED: their one nesting level is spent at
      this Fluid boundary (if they matched this receiver, their contents are
      already in ``kwargs``);
    * ``kwargs`` entries keep the scope :func:`_prepare_kwargs` assigned —
      own kwargs / matched-block values are ``EXACT`` (visible for ordering
      and ``!ref:`` resolution, never re-broadcast below: addressed keys no
      longer cascade), bare-derived values stay ``BARE`` (a bare key keeps
      cascading through the node it landed on), hoisted routing sub-blocks
      are ``STRICT``, and a ``'**'`` glob block stays ``BARE`` so it floats
      to every depth.

    Collisions on a key ``kk`` that appears in BOTH ``parent_context`` and
    ``kwargs`` are resolved by inspecting the receiver class's type:

    * ``kwargs[kk]`` is a :class:`Reference` → keep parent's value
      (avoids infinite recursion when ``foo: !ref:foo`` would resolve
      against itself).
    * ``kk`` is a typed param of the receiver (i.e. in its accept-list)
      → keep parent's value. The receiver's constructor consumes
      ``kwargs[kk]`` directly via ``resolved_kwargs``; the parent's
      entry at ``kk`` is broadcast metadata aimed at descendants and
      must remain visible in ``child_ctx``.
    * Otherwise (``kk`` is NOT a typed param of the receiver) →
      ``kwargs`` wins. The kwarg was placed on the receiver's YAML
      block specifically to shield/override for descendants; it sits at
      a later document position than the colliding parent broadcast, so
      last-write-wins gives it the slot.
    * ``receiver_cls`` is unknown or accepts ``**kwargs`` (accept-list
      is ``None``) → keep parent's value. Conservative fallback that
      preserves pre-existing dotted-broadcast behaviour.
    """
    acceptable = _get_acceptable_keys(receiver_cls) if receiver_cls is not None else None

    def _parent_wins(kk: str, kv: Any) -> bool:
        if kk == "**":
            return False  # glob riders always re-emit, merged with the parent's
        if kk not in parent_context:
            return False
        if isinstance(kv, Reference):
            return True
        if acceptable is None:
            return True
        return kk in acceptable

    out = _View()

    def _emit_parent(k: str, v: Any) -> None:
        scope = _scope_of(parent_context, k)
        if scope is _KeyScope.STRICT or (k == "*" and isinstance(v, dict)):
            return  # one-level routing — spent at this Fluid boundary
        if k == "**" and isinstance(v, dict):
            prev = out.get("**")
            if isinstance(prev, dict):
                v = {**prev, **v}  # later parent rider merges over the own one
        out.set(k, v, scope)

    def _emit_merged(kk: str, kv: Any) -> None:
        if kk == "**" and isinstance(kv, dict):
            prev = out.get("**")
            if isinstance(prev, dict):
                kv = {**prev, **kv}  # a node's own rider merges with the parent's
        out.pop(kk, None)
        out.set(kk, kv, _scope_of(kwargs, kk) if isinstance(kwargs, _View) else _KeyScope.EXACT)

    def _shield_glob_rider() -> None:
        """Rewrite a floating ``'**'`` rider with the receiver's shield values.

        An own/block value the receiver does NOT accept was placed on its
        block to override the subtree (the wrapper-shield idiom — the same
        not-a-typed-param signal ``_parent_wins`` reads). When a ``'**'``
        glob rider carries the same key, the shield value replaces the
        rider's entry for THIS subtree (copy-on-write — the parent's rider
        dict is shared by sibling subtrees and must not be mutated).
        """
        rider = out.get("**")
        if not isinstance(rider, dict) or not isinstance(kwargs, _View):
            return
        if acceptable is None:
            return
        replacements = {
            kk: kv
            for kk, kv in kwargs.items()
            if kk in rider and kk not in acceptable and kwargs.scope_of(kk) is _KeyScope.EXACT
        }
        if replacements:
            out.set("**", {**rider, **replacements}, out.scope_of("**"))

    if self_key is None or self_key not in parent_context:
        for k, v in parent_context.items():
            _emit_parent(k, v)
        for k, v in kwargs.items():
            if _parent_wins(k, v):
                continue
            _emit_merged(k, v)
        _shield_glob_rider()
        return out
    for k, v in parent_context.items():
        if k == self_key:
            for kk, kv in kwargs.items():
                if _parent_wins(kk, kv):
                    continue
                _emit_merged(kk, kv)
        elif k in kwargs and k != "**" and not _parent_wins(k, kwargs[k]):
            # Wrapper's value at this key will win at the self_key slot;
            # skip parent's value at its original position so the wrapper's
            # value ends up at the slot.
            continue
        else:
            _emit_parent(k, v)
    _shield_glob_rider()
    return out


def _broadcast_blocked_keys(target_cls: Any) -> Optional[frozenset[str]]:
    """Bare-broadcast exclusion set for a receiver, or ``None`` for block-everything.

    ``None`` ⇒ the class carries ``@configurable(broadcast=False)`` — NO bare
    key may land. Otherwise the (possibly empty) set of ``NoBroadcast[...]``
    parameter names. Addressed ``ClassName:``/instance blocks and
    ``configure()`` blocks are NEVER gated by this — the accept-list stays the
    single settability authority; this is a broadcast-only overlay.
    """
    if target_cls is None:
        return frozenset()
    if getattr(target_cls, "__confluid_no_broadcast__", False):
        return None
    from confluid.no_broadcast import no_broadcast_param_names

    return no_broadcast_param_names(target_cls)


def accepts_key(target: Any, key: str) -> bool:
    """True if ``key`` can set an attribute on ``target`` when ADDRESSED explicitly.

    "Addressed" means the key names its receiver — a ``ClassName:`` block, an
    exact dotted path, a marker's own kwargs, or a :func:`configure` block.
    Such keys are gated by the accept-list ALONE: constructor parameters,
    public settable class attributes, and ``__init__``-body slots (AST-scanned,
    plus any ``broadcast_attrs=`` declaration and baked table). A target whose
    constructor takes ``**kwargs`` accepts everything.

    ``target`` may be a class, a live instance, or the dotted string a
    ``!class:`` marker carries; an unresolvable target accepts nothing.

    Example::

        accepts_key(Trainer, "lr")        # True  — a ctor param
        accepts_key(Trainer, "typo")      # False — nothing to set
    """
    cls = _settability_target(target)
    if cls is None:
        return False
    acceptable = _get_acceptable_keys(cls)
    return True if acceptable is None else key in acceptable


def accepts_broadcast(target: Any, key: str) -> bool:
    """True if a BARE (unaddressed) ``key`` may cascade onto ``target``.

    Stricter than :func:`accepts_key`: on top of the accept-list this honours
    the two broadcast opt-outs — ``@configurable(broadcast=False)`` on the
    class (nothing bare ever lands) and ``NoBroadcast[T]`` on the parameter
    (that one slot is excluded). Use this for a key the user did not address to
    a specific receiver, so an opt-out declared in code is respected no matter
    which front-end delivered the key.

    Example::

        @configurable(broadcast=False)
        class Pinned:
            def __init__(self, lr: float = 0.1) -> None: ...

        accepts_key(Pinned, "lr")        # True  — `Pinned: {lr: …}` still works
        accepts_broadcast(Pinned, "lr")  # False — a bare `lr:` must not land
    """
    if not accepts_key(target, key):
        return False
    blocked = _broadcast_blocked_keys(_settability_target(target))
    if blocked is None:
        return False  # @configurable(broadcast=False) — nothing bare lands
    return key not in blocked


def accepts_any_key(target: Any) -> bool:
    """True if ``target`` has NO accept-list, so it cannot refuse any key.

    The two predicates above answer "may this key land here?"; this one answers the
    prior question "does this target discriminate between keys at all?". It is True
    for a ``**kwargs`` constructor (and for the rarer target whose signature cannot
    be read), where :func:`accepts_key` and :func:`accepts_broadcast` return True for
    EVERY key — including keys the target has never heard of.

    An external front-end needs that distinction, because "the class declares this
    key" and "the class cannot refuse this key" justify different deliveries: only
    the first says the key was aimed here, and therefore only the first may be
    written into a marker's own kwargs, which is the ADDRESSED channel — a
    constructor argument. A key that merely fits through a ``**kwargs`` signature
    must be left to cascade as a BARE key and land as a post-init attribute, which
    is the same split :func:`flow` applies to a document's own keys (see
    docs/broadcasting.md → "Classes with ``**kwargs`` constructors"). Skipping this
    check is how a CLI ``--run_name x`` reached a metric's constructor and raised
    ``Unexpected keyword arguments`` from inside a library that never asked for it.

    An unresolvable target is False — it accepts nothing, not everything, matching
    :func:`accepts_key`.

    Example::

        class Declares:
            def __init__(self, lr: float = 0.1) -> None: ...

        class Forwards:
            def __init__(self, **kwargs: Any) -> None: ...

        accepts_key(Declares, "run_name")        # False — nothing to set
        accepts_key(Forwards, "run_name")        # True  — cannot refuse it
        accepts_any_key(Declares)                # False — it has an accept-list
        accepts_any_key(Forwards)                # True  — it has none
    """
    cls = _settability_target(target)
    if cls is None:
        return False
    return _get_acceptable_keys(cls) is None


def _settability_target(target: Any) -> Any:
    """Normalize a class / callable / instance / dotted-name into the target to introspect.

    A plain routine (a registered builder FUNCTION) is returned AS-IS — taking
    ``type(func)`` (= ``function``, whose ``__init__`` takes ``**kwargs``) made
    all three predicates answer yes-to-everything for function targets, the
    exact failure ``accepts_any_key`` exists to prevent. A live instance still
    normalizes to its class.
    """
    if target is None:
        return None
    if isinstance(target, str):
        return resolve_class(target)
    if isinstance(target, type) or inspect.isroutine(target):
        return target
    return type(target)


def _prepare_kwargs(
    cls_name: str,
    own_kwargs: Dict[str, Any],
    parent_context: Dict[str, Any],
    target: Any = None,
    self_obj: Any = None,
) -> "_View":
    """Flat-view, document-order, last-write-wins kwarg assembly.

    Walks ``parent_context`` in document order. The receiving Fluid's own
    ``own_kwargs`` are unrolled at the position WHERE ``self_obj`` sits in
    ``parent_context`` (matched by Python identity); when ``self_obj`` is
    not found, they are applied at the end. Class-name and instance-name
    dict blocks (``Foo: {...}``) are unrolled inline at their position.
    Bare scalar/Fluid values broadcast when the key matches the receiving
    class's ``acceptable`` set.

    There is no "explicit kwargs > broadcast" priority — every source is
    ordered by its YAML position. Whichever assignment comes last wins.

    Scoping (2026-07 — addressed keys are exact, cascade is opt-in):

    * Only ``BARE``-tagged parent entries broadcast; ``EXACT`` entries
      (an ancestor's addressed values, kept visible for ordering/``!ref:``)
      are skipped, so a value delivered to one node no longer cascades to
      its descendants.
    * A ``'**'`` glob block (``trainer.**.lr``) applies its contents like
      bare keys — gated by the NoBroadcast opt-outs — to this node AND
      keeps floating below (zero-or-more levels); a named dict inside it
      matches like a floating block.
    * A ``'*'`` glob block applies one level below its introducer only:
      encountered in ``parent_context`` it addresses THIS node (any name);
      introduced by own kwargs / a matched block it is hoisted as STRICT
      routing for the direct children.
    * A dict inside own kwargs / a matched block that is not consumed as a
      dict-typed param value is hoisted as a STRICT routing sub-block — a
      deeper path segment valid for the direct children only.
    * Dotted keys inside blocks and marker kwargs are expanded here via
      :func:`_expand_block_keys` (``Trainer: {'**.lr': 1}`` ≡
      ``Trainer.**.lr: 1``).

    ``cls_name`` is the receiver's target name (used for class-name block
    matching and accept-list lookup). ``target`` is an optional class object
    for parameter inspection (avoids name collisions). ``self_obj`` is the
    Fluid being materialized — passed so we can locate its slot in
    ``parent_context``.
    """
    if cls_name.endswith("()"):
        cls_name = cls_name[:-2]
    instance_name = own_kwargs.get("name")

    acceptable = _get_acceptable_keys(target or cls_name)
    target_cls = target if isinstance(target, type) else resolve_class(cls_name) if cls_name else None
    param_kinds = _get_param_kinds(target_cls or cls_name) if (target_cls or cls_name) else {}
    broadcast_blocked = _broadcast_blocked_keys(target_cls)
    # A class-name block is matched by NAME, but `cls_name` is whatever the target was
    # SPELLED as — which may be a dotted path (`!class:pkg.mod.Widget`) or carry a tag
    # selector (`!class:Widget@framework=torch`). Both are spellings of the same class, so
    # a `Widget:` block must still reach it; before this, a dotted target silently ignored
    # its block (measured: the value stayed at the constructor default). The registered
    # name of the resolved class is therefore matched alongside the literal spelling —
    # which also aligns this path with `configure()`, which has always keyed off the
    # stamped `__confluid_name__` (configurator._apply).
    block_names = {cls_name}
    if target_cls is not None:
        registered = target_cls.__dict__.get("__confluid_name__") if hasattr(target_cls, "__dict__") else None
        block_names.add(str(registered or getattr(target_cls, "__name__", "")))
    block_names.discard("")

    def _accepts(k: str, v: Any) -> bool:
        if isinstance(v, Fluid):
            if acceptable is None or k not in acceptable:
                return False
            # Skip same-target Fluids that are not self — broadcasting them
            # in would loop on infinite re-materialization.
            if target_cls is not None and _same_target(v.target, target_cls):
                return False
            return True
        if isinstance(v, dict):
            # Plain dict — only broadcast IN when the target annotates the
            # param as a dict/mapping. Otherwise keep the legacy behavior
            # (recurse as a config sub-block, do NOT pull the dict in as
            # a value).
            if param_kinds.get(k) == "dict":
                return acceptable is None or k in acceptable
            return False
        if isinstance(v, list):
            if param_kinds.get(k) == "list":
                return acceptable is None or k in acceptable
            return False
        if acceptable is not None and k not in acceptable:
            return False
        return True

    merged = _View()
    self_unrolled = False

    # Ambient ConfigurationReport (collect_report). ``origins`` tracks the
    # origin of the LAST write per key so overwrites collapse to one applied
    # record (last-write-wins); own kwargs erase an entry (a marker's own
    # kwargs are definitions, not overrides). None-guarded — zero-cost off.
    report = _ENGINE_STATE.get().report
    origins: Dict[str, str] = {}

    def _mark_used(k: str, origin: str) -> None:
        if report is not None:
            report.mark_used(f"**.{k}" if origin == "glob '**'" else f"*.{k}" if origin == "glob '*'" else k)

    def _apply_gated(k: str, v: Any, origin: str, scope: _KeyScope) -> None:
        """Bare-style application: NoBroadcast opt-outs gate, accept-list filters."""
        if broadcast_blocked is not None and k not in broadcast_blocked and _accepts(k, v):
            logger.trace(f"broadcast: {k!r} -> {cls_name} ({origin})")
            merged.set(k, v, scope)
            if report is not None:
                origins[k] = origin
                _mark_used(k, origin)

    def _hoist_routing(k: str, v: Dict[str, Any]) -> None:
        """Keep a routing block ('**' floats, '*'/named sub-blocks are one-level)."""
        prev = merged.get(k)
        if isinstance(prev, dict):
            v = {**prev, **v}
        merged.set(k, v, _KeyScope.BARE if k == "**" else _KeyScope.STRICT)

    def _consume_block(block: Dict[str, Any], *, origin: str, gated: bool, floating: bool = False) -> None:
        """Unroll a block addressed to this node into ``merged``.

        ``gated=True`` for glob-delivered contents (the NoBroadcast opt-outs
        apply, like bare keys); named-block contents bypass them (addressed).
        ``floating=True`` for ``'**'`` contents: nested named dicts are
        matched-or-ignored (the riding ``'**'`` entry keeps them floating)
        instead of being hoisted as one-level STRICT routing.
        """
        for bk, bv in _expand_block_keys(block).items():
            if bk == "**" and isinstance(bv, dict):
                _consume_block(bv, origin="glob '**'", gated=True, floating=True)
                _hoist_routing("**", bv)
                continue
            if bk == "*" and isinstance(bv, dict):
                _hoist_routing("*", bv)
                continue
            if isinstance(bv, dict) and bk in (cls_name, instance_name) and (floating or not gated):
                # Addressed to me again (``Cls.inst.attr`` form, or a named
                # match while floating under '**') — unroll inline, ungated.
                _consume_block(bv, origin=f"block {bk!r}", gated=False)
                continue
            if isinstance(bv, dict) and not _accepts(bk, bv):
                # A dict at a key the receiver DECLARES is a value aimed at that slot,
                # not a path segment — the rule ``_apply_own`` already applies to the inline
                # spelling (``!class:Trainer()`` + a nested ``optimizer:``). The two
                # disagreed, and only this branch used the stricter ``_accepts`` test,
                # which admits a dict ONLY for a dict-TYPED param. A deferred body slot
                # is not dict-typed, so ``Trainer: {optimizer: {lr: 0.5}}`` was hoisted
                # as routing for the children and never reached the slot at all — the
                # run kept the code default with nothing competing and nothing logged.
                # Restricted to the ADDRESSED path: a glob-delivered dict (``gated``)
                # is genuinely routing, and a floating ``'**'`` rider keeps floating.
                if not gated and not floating and acceptable is not None and bk in acceptable:
                    logger.trace(f"broadcast: {bk!r} -> {cls_name} ({origin}, slot value)")
                    merged.set(bk, bv, _KeyScope.EXACT)
                    if report is not None:
                        origins[bk] = origin
                    continue
                if not floating:
                    _hoist_routing(bk, bv)  # deeper path segment → direct children
                continue
            if gated:
                _apply_gated(bk, bv, origin, _KeyScope.EXACT)
            elif _accepts(bk, bv):
                logger.trace(f"broadcast: {bk!r} -> {cls_name} ({origin})")
                merged.set(bk, bv, _KeyScope.EXACT)
                if report is not None:
                    origins[bk] = origin

    def _apply_own(kwargs: Dict[str, Any]) -> None:
        """Unroll the receiver's own kwargs — addressed to me, thus EXACT."""
        for k, v in _expand_block_keys(kwargs).items():
            if k == "**" and isinstance(v, dict):
                _consume_block(v, origin="glob '**'", gated=True, floating=True)
                _hoist_routing("**", v)
            elif k == "*" and isinstance(v, dict):
                _hoist_routing("*", v)
            elif isinstance(v, dict) and acceptable is not None and k not in acceptable:
                # Not a param/attr of mine — a sub-block addressing a direct
                # child by name (e.g. the expanded form of ``trainer.b.lr``).
                _hoist_routing(k, v)
            else:
                merged.set(k, v, _KeyScope.EXACT)
                origins.pop(k, None)  # own kwargs are definitions, not overrides

    for k, v in parent_context.items():
        # Receiving Fluid's own slot — unroll its kwargs at this position.
        if self_obj is not None and v is self_obj and not self_unrolled:
            _apply_own(own_kwargs)
            self_unrolled = True
            continue

        # Same-target Fluid that isn't self — skip (would otherwise loop).
        if isinstance(v, Fluid) and target_cls is not None and _same_target(v.target, target_cls):
            continue

        scope = _scope_of(parent_context, k)
        if scope is _KeyScope.EXACT:
            continue  # an ancestor's addressed value — ordering/!ref: visibility only

        # '**' glob block — floats at every level; contents act like bare keys.
        if k == "**" and isinstance(v, dict):
            _consume_block(v, origin="glob '**'", gated=True, floating=True)
            continue

        # '*' glob block — introduced one level up; I am the "any child" it addresses.
        if k == "*" and isinstance(v, dict):
            _consume_block(v, origin="glob '*'", gated=True)
            continue

        # Class-name / instance-name dict block — unroll inline (addressed → ungated).
        if (k in block_names or k == instance_name) and isinstance(v, dict):
            if report is not None:
                report.mark_used(k)  # a named block is "used" once it matches an object
            _consume_block(v, origin=f"block {k!r}", gated=False)
            continue

        if scope is _KeyScope.STRICT:
            continue  # routing block for a sibling name — not mine

        # Plain broadcast — the only path the NoBroadcast opt-out gates:
        # addressed blocks above always work. ``blocked is None`` means the
        # class opted out entirely (@configurable(broadcast=False)).
        _apply_gated(k, v, "bare", _KeyScope.BARE)

    if not self_unrolled:
        _apply_own(own_kwargs)

    if report is not None and origins:
        label = f"{cls_name} {instance_name!r}" if isinstance(instance_name, str) else cls_name
        for k, origin in origins.items():
            report.record_applied(k, label, origin)

    return merged


def _pop_glob_routing(merged: Dict[str, Any], target: Any) -> Dict[str, Any]:
    """Remove ``'*'``/``'**'`` glob blocks from ``merged``; return their scalar pool.

    Direct-flow counterpart of the materialize path's glob handling for
    hand-built markers: ``'**'`` contents additionally apply to the receiver
    itself (gated by the accept-list and the NoBroadcast opt-outs, like bare
    keys — zero-or-more levels includes the receiver); ``'*'`` contents only
    feed the nested-Class pool (one level below = the marker's direct
    children). Dict-valued contents are routing for deeper levels and stay
    out of the pool (the nested-Class loop skips dicts anyway).
    """
    pool: Dict[str, Any] = {}
    star = merged.pop("*", None)
    if isinstance(star, dict):
        pool.update({k: v for k, v in star.items() if not isinstance(v, dict)})
    star2 = merged.pop("**", None)
    if isinstance(star2, dict):
        target_cls = target if isinstance(target, type) else None
        acceptable = _get_acceptable_keys(target)
        blocked = _broadcast_blocked_keys(target_cls)
        report = _ENGINE_STATE.get().report
        for gk, gv in star2.items():
            if isinstance(gv, dict):
                continue
            pool[gk] = gv
            if gk in merged or blocked is None or gk in blocked:
                continue
            if acceptable is not None and gk not in acceptable:
                continue
            merged[gk] = gv
            if report is not None:
                target_label = str(getattr(target, "__name__", target))
                report.record_applied(gk, target_label, "glob '**'")
                report.mark_used(f"**.{gk}")
    return pool


def _broadcast_pool(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten a context's ``'**'`` entry into the nested-Class broadcast pool.

    The nested-Class broadcast loop in :func:`_resolve_kwarg_value` skips
    dict/list values, so a ``'**'`` glob block at the top level of the active
    context would be invisible to it; unroll its non-dict contents at the
    block's document position (``'*'`` blocks are depth-addressed and stay
    out — the recursive-descent path handles them).
    """
    if "**" not in ctx and "*" not in ctx:
        return ctx
    pool: Dict[str, Any] = {}
    for k, v in ctx.items():
        if k == "**" and isinstance(v, dict):
            for gk, gv in v.items():
                if not isinstance(gv, dict):
                    pool[gk] = gv
        elif k == "*" and isinstance(v, dict):
            continue
        else:
            pool[k] = v
    return pool
