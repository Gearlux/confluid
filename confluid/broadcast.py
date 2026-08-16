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
whether a ``Partial`` is eagerly flowed, whether a bare key reaches a deferred slot
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
from copy import copy
from enum import Enum
from typing import Annotated, Any, Callable, Dict, FrozenSet, List, Literal, Optional, Protocol, Set, Tuple, TypeVar

from loggair import get_active_config, get_logger

from confluid.exceptions import ConfigurationError
from confluid.fluid import Fluid, Reference, Target, _at_yaml_loc
from confluid.introspect import _slots_cache, baked_init_attrs, init_callable, init_source_available, slot_names, slots
from confluid.merger import expand_dotted_mapping
from confluid.registry import resolve_class
from confluid.state import _ENGINE_STATE

logger = get_logger("confluid.broadcast")

# Introspection caches, keyed by TARGET IDENTITY (see :func:`_cache_key`). Every
# reader is in this module; all are registered in ``_PASS_CACHES`` below and
# cleared together by ``clear_pass_caches()`` at every entry point (materialize /
# resolve / configure). ``engine._parent_blacklist_cache`` registers itself
# alongside — cache ownership follows module ownership, the clear has ONE site.
_acceptable_keys_cache: Dict[Any, Optional[FrozenSet[str]]] = {}
# Per-class: ``{param_name: "dict" | "list" | None}`` — None means "not annotated
# as a dict/list-shaped type" (default scalar/Fluid-only broadcast rules apply).
_param_kind_cache: Dict[Any, Dict[str, Optional[str]]] = {}
# Per-pass receiver cache: a _Receiver is a pure function of the (spelling,
# target, instance-name) triple — its closures capture only per-pass-cached
# accept-list data — so 2,500 same-class markers build ~one receiver instead
# of 2,500. Cleared by engine.materialize/resolve alongside the attr caches.
_receiver_cache: Dict[Any, "_Receiver"] = {}

# Classes already warned about an unscannable ``__init__`` (compiled/frozen —
# see :func:`_warn_if_init_unscannable`). Deliberately NOT cleared by
# materialize/resolve: those clear the attr caches once per pass, which would
# re-fire the warning on every config load. One warning per class per process.
_warned_unscannable_inits: Set[str] = set()


class _Clearable(Protocol):
    """Anything with a ``clear()`` — the one thing a per-pass cache must offer."""

    def clear(self) -> None: ...  # noqa: E704 — Protocol stub


_ClearableT = TypeVar("_ClearableT", bound=_Clearable)

#: Every per-pass introspection cache, cleared together at each entry point
#: (``materialize()`` / ``resolve()`` / ``configure()``) via
#: :func:`clear_pass_caches`. A module OWNING such a cache registers it at
#: import time (``engine._parent_blacklist_cache`` does) — ownership stays with
#: the owning module, the clear happens in ONE place. The five-line clear block
#: used to exist twice (materialize + resolve, edited in tandem by convention)
#: and ``configure()`` cleared nothing at all — a same-qualname class redefined
#: between calls (a notebook cell re-run) served its previous definition's
#: accept-list with no diagnostic.
_PASS_CACHES: List[_Clearable] = [
    _acceptable_keys_cache,
    _param_kind_cache,
    _receiver_cache,
    # ``introspect`` owns the slot enumeration but cannot import this module
    # (it is the stdlib-only leaf), so the clear is registered from here.
    _slots_cache,
]


def register_pass_cache(cache: _ClearableT) -> _ClearableT:
    """Register a per-pass cache for :func:`clear_pass_caches`; returns it unchanged.

    For caches owned by OTHER modules (cache ownership follows module
    ownership) — declare-and-register in one line::

        _my_cache: Dict[str, int] = register_pass_cache({})
    """
    _PASS_CACHES.append(cache)
    return cache


def clear_pass_caches() -> None:
    """Clear every registered per-pass introspection cache — the ONE clear site."""
    for cache in _PASS_CACHES:
        cache.clear()
    refresh_log_gates()


# --------------------------------------------------------------------------- #
# Log gates — do not BUILD a record no sink can accept
#
# The scanner's diagnostics are per-KEY, so a single materialize pass emits one
# TRACE record per applied broadcast: 10,000 of them for a 2,500-marker tree,
# measured. Python evaluates the f-string before the logger can decide anything,
# and loggair configures its handlers at TRACE with a filter — so loguru's own
# ``level_no < core.min_level`` fast path never fires and every discarded record
# is still formatted, stamped with a fresh timestamp, and dispatched to both
# handlers. Measured on ``examples/performance.py``: 30.1 % of the pass.
#
# So the gate has to be at the CALL SITE, and it is refreshed once per pass by
# ``clear_pass_caches()`` above — the hook every entry point already fires.
# --------------------------------------------------------------------------- #

#: Severity numbers loguru assigns its built-in levels. Only the floor matters
#: here; an unrecognised name is treated as "could accept TRACE" (conservative).
_LEVEL_NUMBERS: Dict[str, int] = {
    "TRACE": 5,
    "DEBUG": 10,
    "INFO": 20,
    "SUCCESS": 25,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50,
}
_TRACE_LEVEL = 5

#: The logger this module was handed at import (and its type). A test (or a
#: consumer) that swaps a module's ``logger`` out gets an UNGATED logger back —
#: the gate answers "can a loggair sink accept this?", which is not a question we
#: can ask of a foreign object, and silently swallowing a collector's records
#: would make every log-asserting test a false green.
_NATIVE_LOGGER = logger
_NATIVE_LOGGER_TYPE = type(logger)

#: Whether a TRACE record can reach any sink. Defaults to True so a ``flow()``
#: outside a pass (which never clears caches) keeps its diagnostics.
_trace_on: bool = True


def _level_floor(config: Dict[str, Any]) -> int:
    """The lowest severity any configured sink could accept, per loggair's config."""
    names = [config.get("file_level"), config.get("console_level")]
    names.extend((config.get("module_levels") or {}).values())
    floors = [_LEVEL_NUMBERS.get(str(name).upper(), _TRACE_LEVEL) for name in names if name]
    return min(floors) if floors else _TRACE_LEVEL


def _compute_trace_gate(config: Dict[str, Any]) -> bool:
    """Whether TRACE output is worth building, given loggair's resolved config.

    Conservative in one direction only: an unconfigured logger, an unknown level
    name, or a per-module override anywhere at TRACE all answer True. Logging
    slightly more than necessary costs time; logging less than asked costs a
    diagnostic, and this module's diagnostics are what a "my knob did not take"
    investigation greps.
    """
    if not config.get("configured"):
        return True
    return _level_floor(config) <= _TRACE_LEVEL


def refresh_log_gates() -> None:
    """Recompute the per-pass log gates from loggair's active configuration."""
    global _trace_on
    if logger is not _NATIVE_LOGGER:
        _trace_on = True  # a swapped-in collector is never gated — see _NATIVE_LOGGER
        return
    try:
        _trace_on = _compute_trace_gate(get_active_config())
    except Exception:  # a logging probe must never break a configuration pass
        _trace_on = True


def trace_enabled(local_logger: Any = None) -> bool:
    """Whether a per-KEY TRACE diagnostic is worth building — the shared gate.

    Sibling modules with their own per-key TRACE sites (``configurator``) pass
    their own ``logger`` so a monkeypatched collector is recognised the same way
    :func:`refresh_log_gates` recognises one here. This module's own hot sites
    read ``_trace_on`` directly — one global load per key rather than a call.
    """
    if local_logger is not None and not isinstance(local_logger, _NATIVE_LOGGER_TYPE):
        return True
    return _trace_on


def _cache_key(target: Any) -> Any:
    """The per-pass cache key for a target — its IDENTITY, never its dotted name.

    ``f"{module}.{qualname}"`` looks unique and is not: two distinct classes
    share it whenever they are defined in the same scope (a class factory, a
    plugin loader building classes in a loop, a decorator that rebuilds a class,
    a parametrised test fixture). The registry already knows this — see
    ``registry._claim_key``, which suffixes ``~2`` for exactly this case — but
    the introspection caches keyed on the raw name until 2026-08-11 and so
    served one class's accept-list to its same-named sibling: the second class
    was built on its defaults and had the FIRST one's key ``setattr``-ed onto it,
    silently. Pinned by ``tests/test_duplicate_names.py::
    test_same_qualname_classes_do_not_share_an_accept_list``.

    Classes, functions and strings are all hashable, so the target itself is the
    key; the identity-free fallback covers the rare callable whose class defines
    ``__eq__`` without ``__hash__``. Holding the target as a key keeps it alive
    only until the next :func:`clear_pass_caches`, which every entry point fires.
    """
    try:
        hash(target)
    except TypeError:
        return id(target)
    return target


def _dotted_name(target: Any) -> str:
    """``module.QualName`` for DIAGNOSTICS only — never a cache key.

    Readable in a log line, and deliberately not unique: :func:`_cache_key` is
    the identity answer.
    """
    qualname = getattr(target, "__qualname__", None)
    if qualname is None:
        return str(target)
    return f"{getattr(target, '__module__', '?')}.{qualname}"


#: Per-pass memo for :func:`_same_target`, the ONE uncached introspection helper
#: until 2026-08-11. It is asked once per (view entry × marker) — 113,000 times
#: for a 2,500-marker tree, 50,002 of which reached ``resolve_class`` — and it is
#: a pure function of its two arguments within a pass. Measured: 9.5 % of
#: ``materialize()`` on ``examples/performance.py``.
_same_target_cache: Dict[Any, bool] = register_pass_cache({})


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

    Memoized per pass (``_same_target_cache``): the registry lookup below is the
    expensive half and the answer cannot change within one materialization.
    """
    if fluid_target is cls:
        return True
    memo_key = (_cache_key(fluid_target), _cache_key(cls))
    cached = _same_target_cache.get(memo_key)
    if cached is not None:
        return cached
    result = _resolves_to_same_class(fluid_target, cls)
    _same_target_cache[memo_key] = result
    return result


def _resolves_to_same_class(fluid_target: Any, cls: Callable[..., Any]) -> bool:
    """The uncached body of :func:`_same_target` — registry resolution included."""
    if isinstance(fluid_target, str):
        resolved = resolve_class(fluid_target)
        if resolved is cls:
            return True
        qualified = f"{cls.__module__}.{cls.__qualname__}"
        if fluid_target == qualified:
            return True
    return False


def _warn_if_init_unscannable(target: type) -> None:
    """Warn ONCE per class when the TARGET's own ``__init__`` can't be AST-scanned.

    In compiled / frozen / zip deployments ``inspect.getsource`` raises, the
    body scan silently returns empty, and post-init broadcast attrs vanish —
    a dev-vs-packaged behavioral divergence with no other diagnostic. Fires
    only for the target's OWN ``__init__`` (from ``target.__dict__``) on a
    ``@configurable`` class with no ``broadcast_attrs`` declaration AND no
    build-time bake-table entry (``confluid.bake``); MRO parents with
    unreadable source stay silent (builtins are normal).

    The warned-set is keyed by dotted NAME, not by the identity
    :func:`_cache_key` uses: it is deliberately never cleared (once per class per
    PROCESS), so holding class objects in it would pin them for the process
    lifetime. The cost of the coarser key is that a same-named sibling defined in
    the same scope inherits the warning — one missing diagnostic line, not a
    wrong value.
    """
    name = _dotted_name(target)
    if name in _warned_unscannable_inits:
        return
    if not getattr(target, "__confluid_configurable__", False):
        return
    own_init = target.__dict__.get("__init__")
    if own_init is None or init_source_available(own_init):
        return
    if baked_init_attrs(target) is not None:
        return  # covered by a build-time bake table — packaged mode is healthy
    _warned_unscannable_inits.add(name)
    logger.warning(
        f"cannot scan __init__ body of {name} (source unavailable — compiled/frozen?): "
        f"post-init broadcast attrs are invisible; run 'confluid-bake <package>' at build time "
        f"or declare @configurable(broadcast_attrs=[...])"
    )


#: Kinds a key may be ADDRESSED at. ``var_positional`` is excluded and that is the
#: point: a ``*args`` name can never be passed by keyword, so a config key of that
#: name addresses nothing — it used to pass the accept-list and land as a post-init
#: attribute nothing reads. ``var_keyword`` is handled separately (its presence
#: makes the whole list ``None``), and a setterless property never becomes a slot.
_SETTABLE_KINDS: FrozenSet[str] = frozenset({"positional_only", "keyword", "class_attr", "body_slot"})

#: Kinds a target NAMES. Identical to the settable set — the ``**kwargs`` catchall
#: names nothing, which is the whole distinction :func:`declares_key` draws.
_DECLARED_KINDS: FrozenSet[str] = _SETTABLE_KINDS


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

    Resolution order: a string name is resolved to its class FIRST, then cached
    under the resolved class ITSELF (:func:`_cache_key`). This prevents two
    classes that share a name — across modules, or across two definitions in one
    scope — from silently inheriting one another's accept-list.
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

    cache_key = _cache_key(target)
    if cache_key in _acceptable_keys_cache:
        return _acceptable_keys_cache[cache_key]

    target_slots = slots(target)
    if init_callable(target) is None:
        _acceptable_keys_cache[cache_key] = None
        return None
    if any(slot.kind == "var_keyword" for slot in target_slots):
        # A **kwargs constructor makes the accept-list unknowable, and the
        # gates treat ``None`` as accept-EVERYTHING: every bare top-level /
        # glob-delivered key broadcasts into instances of this class. See
        # docs/broadcasting.md → "Classes with **kwargs constructors".
        logger.trace(
            f"accept-list unknown for {_dotted_name(target)} (**kwargs constructor) — "
            f"bare broadcasts are unfiltered for this class"
        )
        _acceptable_keys_cache[cache_key] = None
        return None

    # The packaged-mode diagnostic. It used to ride inside the body-slot scan; with
    # the scan moved to ``introspect`` (stdlib-only, so it has no logger) the warning
    # lives here, at the accept-list — which is where its consequence lands anyway:
    # an unscannable ``__init__`` means post-init slots are absent from THIS set, so
    # broadcasting silently stops reaching them.
    if getattr(target, "__confluid_broadcast_attrs__", None) is None:
        _warn_if_init_unscannable(target)

    keys = {slot.name for slot in target_slots if slot.kind in _SETTABLE_KINDS}
    result = frozenset(keys)
    _acceptable_keys_cache[cache_key] = result
    return result


def _get_param_kinds(cls_or_name: Any) -> Dict[str, Optional[str]]:
    """Return ``{slot_name: "dict" | "list" | None}`` for a target's slots.

    Used by :func:`_accepts` to decide whether a dict/list value at a
    matching key in the parent context should be broadcast IN (when the
    slot's annotation says it expects a dict/list) or left to recurse as
    a config sub-block (the default for un-annotated/scalar-shaped slots).

    A projection over the ONE :func:`introspect.slots` enumeration — it was the
    surviving hand-rolled signature walk after the 2026-08-12 consolidation, and
    the walk was BLIND to body slots: a class declaring ``self.transforms:
    list[Any] = [...]`` answered ``{}``, so an addressed ``transforms: [...]``
    block was refused as a value on the load path while ``configure()`` applied
    it. An ANNOTATED body slot now classifies exactly like the equivalent ctor
    param (the two declaration halves of the class-design convention behave
    alike); an unannotated one stays ``None`` (``Slot.annotation`` is ``Any``),
    so the routing/block reading is unchanged for it.
    """
    target: Any
    if isinstance(cls_or_name, type) or callable(cls_or_name):
        target = cls_or_name
    else:
        target = resolve_class(cls_or_name)
        if target is None:
            return {}

    cache_key = _cache_key(target)
    if cache_key in _param_kind_cache:
        return _param_kind_cache[cache_key]

    kinds: Dict[str, Optional[str]] = {slot.name: _classify_annotation(slot.annotation) for slot in slots(target)}
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

    # Peel ``Annotated`` FIRST: ``slots()`` resolves hints WITH extras
    # (``include_extras=True``), so a range-marked container param — the
    # workspace's ``Annotated[Tuple[float, float], Interval(...)]`` convention —
    # arrives wrapped, and ``get_origin`` would report ``Annotated`` instead of
    # the container. Classify the payload; the metadata is not shape.
    if typing.get_origin(ann) is Annotated:
        return _classify_annotation(typing.get_args(ann)[0])

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

    __slots__ = ("scopes", "beaten_per_slot")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.scopes: Dict[str, _KeyScope] = {}
        #: Per slot, the cascade keys the delivering BLOCK out-positioned — set by
        #: ``_prepare_kwargs`` from the scanner's verdict, read by the engine (C2).
        #: Deliberately NOT inherited on copy: it belongs to one scan of one node.
        self.beaten_per_slot: Dict[str, FrozenSet[str]] = {}
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
        if key in self and self._changed(self[key], value):
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

    @staticmethod
    def _changed(prev: Any, new: Any) -> bool:
        """Whether an overwrite replaces the value — safe for any operand.

        Config values are arbitrary objects; a numpy/torch array's ``__eq__``
        returns an ARRAY, whose truth value raises. Identity decides those:
        this is a diagnostic, and a false "changed" on an equal-but-distinct
        array costs one DEBUG line, while raising here crashes the merge.
        """
        try:
            return bool(prev != new)
        except Exception:
            return prev is not new

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


def _merge_rider(prev: Any, new: Dict[str, Any]) -> Dict[str, Any]:
    """Merge a later ``'**'`` rider over an existing one (inner last-write-wins).

    The one spelling of the rider-merge idiom — it was inlined four times
    across the two splice implementations before Phase B.
    """
    return {**prev, **new} if isinstance(prev, dict) else new


def _spent_at_boundary(key: str, value: Any, scope: "_KeyScope") -> bool:
    """True when an inherited entry's routing level is SPENT at a node boundary.

    STRICT entries and ``'*'`` glob blocks are one-level routing: valid for the
    direct children of their introducer only, dropped when a child view is
    built for the next level down. (A ``'**'`` rider floats and never spends.)
    """
    return scope is _KeyScope.STRICT or (key == "*" and isinstance(value, dict))


#: Slot values a mapping may simply REPLACE: plain data and "empty". Everything
#: else in a slot is an OBJECT the author almost certainly meant to reach INTO.
_ASSIGNABLE_SLOT_TYPES = (dict, list, tuple, set, frozenset, str, bytes, int, float, bool, complex)


def dict_at_slot_kind(existing: Any) -> Literal["marker", "configurable", "assign", "opaque"]:
    """What a mapping addressed at a slot MEANS, decided by what the slot HOLDS — the ONE classifier.

    Both paths dispatch on this (BUGS-2026-08-13, C1/C1b — the load path used to have
    only two of the four arms and assigned the raw dict over live children):

    * ``"marker"``       — a ``Target`` (``Partial`` included): TUNE it (``tune_marker``);
    * ``"configurable"`` — a live ``@configurable`` instance: walk INTO it and set fields;
    * ``"assign"``       — plain data (dict/list/scalar) or nothing: the mapping IS the value;
    * ``"opaque"``       — any other live object (a non-configurable instance, an
      unresolved ``Reference``/``Clone``): a located ``ConfigurationError`` — the user
      decision of 2026-08-13 is to REFUSE, never to silently replace an object with a dict.

    Reads the CLASS mark, never a property — and callers hand it ``vars(obj).get(key)``,
    so no getter ever runs.
    """
    if isinstance(existing, Target):
        return "marker"
    if isinstance(existing, Fluid):
        return "opaque"  # a Reference/Clone cannot be tuned by a mapping — refuse loudly
    if existing is None or isinstance(existing, _ASSIGNABLE_SLOT_TYPES):
        return "assign"
    if getattr(type(existing), "__confluid_configurable__", False):
        return "configurable"
    return "opaque"


def tune_marker(existing: Fluid, mapping: Dict[str, Any]) -> Fluid:
    """A mapping addressed at a slot holding a deferred marker TUNES it — the ONE spelling.

    Shallow-copies the marker (identity fields — ``_yaml_loc``, the engine's
    ordering bookkeeping — ride along with the instance ``__dict__``) and
    merges ``mapping`` over its kwargs, last-write-wins per key. Assigning the
    raw dict instead was the historical bug this rule replaced: the slot's
    target vanished and every kwarg set in code went with it. The engine's
    post-init tune and the live sink's ``dict_at_slot`` carried twin inline
    copies (already drifted cosmetically — one restated ``_yaml_loc``, the
    other did not); this is the single implementation both call.
    """
    tuned = copy(existing)
    tuned.kwargs = {**existing.kwargs, **mapping}
    return tuned


def _merge_routing(out: "_View", key: str, block: Dict[str, Any]) -> None:
    """Hoist a routing block — ``'**'`` floats (BARE), anything else one-level (STRICT).

    D1-adjudicated (2026-08-08): an existing entry at ``key`` is merged ONLY
    when it is itself routing — the ``'**'`` rider, or a STRICT block; any
    other previous value (an EXACT slot value, an own-kwarg dict) is REPLACED,
    last-write-wins. The old marker-path variant merged unconditionally,
    folding an addressed slot value into child routing — contents leaked to
    descendants they were never aimed at. The configure-path variant already
    merged STRICT-only; this is now the one policy. Pinned in
    ``tests/test_scanner.py``.
    """
    prev = out.get(key)
    if isinstance(prev, dict) and (key == "**" or out.scope_of(key) is _KeyScope.STRICT):
        block = {**prev, **block}
    out.set(key, block, _KeyScope.BARE if key == "**" else _KeyScope.STRICT)


def _is_glob_key(key: Any) -> bool:
    """True for the glob routing block names ``'*'`` / ``'**'``.

    Glob keys are addressing metadata, never values: they must not reach
    constructor kwargs, post-init setattrs, or ``__confluid_kwargs__``
    capture.
    """
    return key in _GLOB_KEYS


def _expand_block_keys(block: Dict[str, Any]) -> Dict[str, Any]:
    """Expand dotted keys INSIDE a block / marker-kwargs mapping.

    The in-block analogue of :func:`confluid.merger.expand_dotted_keys` (which
    only processes the document's top-level keys): ``'**.lr'`` inside a matched
    ``Trainer:`` block nests to ``{'**': {'lr': …}}``, so
    ``Trainer: {'**.lr': 1}`` ≡ ``Trainer.**.lr: 1``. ONE grammar, two
    policies: both are :func:`confluid.merger.expand_dotted_mapping`; this
    in-block policy shares every value by REFERENCE (never deep-copies), so
    resolved ``!ref:`` identity survives, dicts descended into are
    shallow-copied copy-on-write so the caller's input is never mutated (a
    ``Fluid`` target's kwargs ARE descended into and extended in place, in both
    policies), and a dict landing on an existing dict merges shallow,
    last-write. No-op (same object) when no key contains a dot.
    """
    if not any("." in k for k in block):
        return block

    def _cow(cur: Dict[str, Any], part: str, nxt: Dict[str, Any]) -> Dict[str, Any]:
        copied = dict(nxt)
        cur[part] = copied
        return copied

    return expand_dotted_mapping(
        block,
        copy_value=lambda v: v,
        merge_leaf=lambda prev, value: {**prev, **value},
        descend=_cow,
    )


def _cascade_scalar_positions(view: Dict[str, Any]) -> Dict[str, int]:
    """Document position of every scalar a CASCADE could deliver — the ONE candidate set.

    Both ordering verdicts read this: the engine's late-keys stamp
    (:func:`_late_bare_keys_per_slot`) and the configure() scan's beaten set
    (``_scan_view``'s ``_bare_before``). A BARE top-level key sits at its own
    index; a ``'**'`` rider's scalar contents sit at the RIDER's index — the
    rider is the delivery vehicle, so its position is where its contents
    compete. A name delivered both ways keeps the LATER index (last write
    wins, and the later delivery is the one whose value survives the pool).
    Dict-valued rider contents are addressed forms (the D5 rider-mapping
    cell), not cascade scalars, and ``'*'`` blocks are depth-addressed —
    neither belongs here.

    This set existed twice with DIFFERENT filters (the load side kept
    BARE-scoped keys and could not see rider contents at all; the configure
    side kept non-dict keys of any scope and skipped every dict entry
    including the rider), so the rider × slot-mapping contest was
    position-INSENSITIVE on both paths with OPPOSITE winners — the D7
    adjudication (2026-08-10, ``docs/architecture.md`` record 8).
    """
    out: Dict[str, int] = {}
    for i, (k, v) in enumerate(view.items()):
        if _scope_of(view, k) is not _KeyScope.BARE:
            continue
        if k == "**" and isinstance(v, dict):
            for gk, gv in v.items():
                if not isinstance(gv, dict):
                    out[gk] = i
        elif not _is_glob_key(k) and not isinstance(v, dict):
            out[k] = i
    return out


def _late_bare_keys_per_slot(child_ctx: Dict[str, Any], kwargs: Dict[str, Any]) -> Dict[str, FrozenSet[str]]:
    """For each dict-valued kwarg, the cascade keys positioned AFTER it in the document.

    A mapping addressed at a slot (``optimizer: {lr: 0.5}``) is the one addressing
    form that cannot be ordered where every other form is. A marker gets its own
    :func:`_prepare_kwargs` pass against ``child_ctx`` and so competes with bare
    keys by position; a plain dict is not a marker, is applied only after
    construction (the first moment the slot's deferred default is knowable), and
    carries no position of its own — dicts take no attributes.

    So the contest is settled HERE, while the ordering is still in hand, and only
    its OUTCOME is carried forward: the cascade-deliverable keys
    (:func:`_cascade_scalar_positions` — bare keys AND a ``'**'`` rider's scalar
    contents, at the rider's position) that sit later than the slot and
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
    positions = _cascade_scalar_positions(child_ctx)
    return {slot: frozenset(k for k, i in positions.items() if i > order[slot]) for slot in slots}


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
        if _spent_at_boundary(k, v, scope):
            return  # one-level routing — spent at this Fluid boundary
        if k == "**" and isinstance(v, dict):
            v = _merge_rider(out.get("**"), v)  # later parent rider merges over the own one
        out.set(k, v, scope)

    def _emit_merged(kk: str, kv: Any) -> None:
        if kk == "**" and isinstance(kv, dict):
            kv = _merge_rider(out.get("**"), kv)  # a node's own rider merges with the parent's
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


def _spliced_subtree_view(view: Dict[str, Any], cls_name: str, instance_name: Optional[str]) -> Dict[str, Any]:
    """Return the subtree view: routing hoisted from matched blocks, spent levels dropped.

    The live-object analogue of ``broadcast._splice_kwargs_at_slot``:

    * a matched (floating) block STAYS in the view — a deeper node with the
      same class/instance name matches it again (``**.name`` anchoring); its
      scalars were already applied to this object and are simply carried
      inside the block, never as ambient bare keys (the cascade removal);
    * a matched block's ROUTING contents are hoisted as additional entries
      at the block's position: ``'**'`` keeps floating (BARE, merged with an
      existing rider), ``'*'`` and named sub-blocks become STRICT (valid for
      the direct children only);
    * inherited STRICT entries and ``'*'`` glob blocks are dropped — their
      one level is spent at this object.
    """
    block_keys = {cls_name, instance_name} - {None}
    has_block = any(
        k in view and isinstance(view[k], dict) and _scope_of(view, k) is not _KeyScope.EXACT for k in block_keys
    )
    has_routing = ("*" in view and isinstance(view["*"], dict)) or (
        isinstance(view, _View) and any(s in (_KeyScope.STRICT, _KeyScope.ADDRESSED) for s in view.scopes.values())
    )
    star2 = view.get("**")
    has_glob_router = isinstance(star2, dict) and isinstance(star2.get("*"), dict)
    if not (has_block or has_routing or has_glob_router):
        return view

    out = _View()
    for k, v in view.items():
        scope = _scope_of(view, k)
        if scope is _KeyScope.ADDRESSED:
            # Delivered to the object that just consumed this view; its dict
            # contents route one level further, scalars are spent.
            if isinstance(v, dict):
                _hoist_block_routing(out, {k: v}, instance_name)
            continue
        if isinstance(v, dict) and k in block_keys and scope is not _KeyScope.EXACT:
            if scope is not _KeyScope.STRICT:
                out.set(k, v, scope)  # floating block — deeper same-name nodes rematch
            _hoist_block_routing(out, v, instance_name)
            continue
        if k == "*" and isinstance(v, dict):
            continue  # one-level routing — spent at this boundary
        if k == "**" and isinstance(v, dict):
            out.set(k, v, _KeyScope.BARE)
            if isinstance(v.get("*"), dict):
                _merge_routing(out, "*", v["*"])  # '*' inside a floating '**' routes my children
            continue
        if scope is _KeyScope.STRICT:
            continue  # routing for a sibling name — spent
        out.set(k, v, scope)
    return out


def _hoist_block_routing(out: Any, block: Dict[str, Any], instance_name: Optional[str]) -> None:
    """Hoist a matched block's routing contents ('**'/'*'/named sub-blocks) into ``out``."""
    for bk, bv in _expand_block_keys(block).items():
        if bk == instance_name and isinstance(bv, dict):
            _hoist_block_routing(out, bv, instance_name)  # Cls.inst.attr form unrolls inline
            continue
        if not isinstance(bv, dict):
            continue  # scalars were applied by _apply; the floating block keeps them visible
        if bk == "**":
            _merge_routing(out, "**", bv)  # rider hoist — the ONE merge spelling; keeps floating below
            merged = out.get("**")
            if isinstance(merged, dict) and isinstance(merged.get("*"), dict):
                _merge_routing(out, "*", merged["*"])  # '*' inside the rider routes my children
            continue
        _merge_routing(out, bk, bv)  # '*' or a deeper path segment — one level


def _spliced_at_slot(view: Dict[str, Any], key: str, sub_block: Dict[str, Any]) -> Dict[str, Any]:
    """Return ``view`` with ``sub_block``'s entries spliced at ``key``'s position.

    Used for child recursion: the block addressed to the child replaces the
    attr-keyed entry, so its values sit at the block's document position
    (later than earlier broadcasts → they win for the child, as authored).
    The entries are ADDRESSED — consumed by that one child, spent below it.
    """
    out = _View()
    placed = False
    for k, v in view.items():
        if k == key and not placed:
            for bk, bv in sub_block.items():
                out.set(bk, bv, _KeyScope.ADDRESSED)
            placed = True
        else:
            out.set(k, v, _scope_of(view, k))
    if not placed:
        for bk, bv in sub_block.items():
            out.set(bk, bv, _KeyScope.ADDRESSED)
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


def refuse_if_undeclared(target: Any, key: str, node: Any = None) -> None:
    """Raise when ``target`` is ``strict_attrs`` and ``key`` names nothing it declares.

    The opt-in complement of the permissive default. Confluid absorbs an unknown
    ADDRESSED key as a post-init attribute — ``engine._apply_post_init_attrs`` IS
    the post-construction toggle mechanism — and since 2026-08-12 it warns while
    doing so. A class marked ``@configurable(strict_attrs=True)`` closes that
    surface: the same key raises instead.

    "Declared" is the accept-list: constructor parameters (or the callable's own
    signature for a builder function), public settable class attributes, and
    ``__init__``-body slots. A ``**kwargs`` target has NO accept-list — nothing is
    undeclared for it — so marking one is meaningless rather than an error, and it
    is never refused. BARE keys never reach here: the accept-list drops them
    first, which is what keeps a strict class usable in a document that also
    configures something else.
    """
    cls = _settability_target(target)
    if cls is None or not getattr(cls, "__confluid_strict_attrs__", False):
        return
    acceptable = _get_acceptable_keys(cls)
    if acceptable is None or key in acceptable:
        return
    name = getattr(cls, "__name__", cls)
    known = ", ".join(sorted(acceptable)) or "(nothing)"
    raise ConfigurationError(
        f"{name} has no attribute {key!r}{_at_yaml_loc(node)} and is declared "
        f"strict_attrs=True, so it will not be set as a post-init attribute. "
        f"{name} declares: {known}."
    )


def refuse_if_variadic_name(target: Any, key: str, node: Any = None) -> None:
    """Raise when an ADDRESSED ``key`` names a ``*args`` parameter of ``target``.

    Such a key can never reach the parameter — a variadic is positional-only by
    construction, and a confluid marker carries keyword arguments alone. Until
    2026-08-12 it was silently accepted as a post-init ATTRIBUTE instead: the
    constructor never saw it, and ``obj.loaders`` held a value the object ignored.

    Hydra refuses the identical spelling (measured, 1.3.5: ``loaders: [...]`` on a
    ``*loaders`` target raises ``InstantiationException``) and offers ``_args_`` as
    the separate channel. Confluid's channel is ``flow(node, a, b)`` — runtime-only
    by mandate — so the config-side answer is the same: refuse the name.

    ADDRESSED only. A BARE key is an implicit ``**.key`` that cascades tree-wide
    and legitimately matches nothing, so a document whose top-level key happens to
    collide with some class's variadic parameter must keep loading. Hydra never
    faces that case — it has no bare-key broadcast. The bare path structurally
    cannot reach here: the accept-list drops the key before either call site.
    """
    cls = _settability_target(target)
    if cls is None:
        return
    if key not in {slot.name for slot in slots(cls) if slot.kind == "var_positional"}:
        return
    name = getattr(cls, "__name__", cls)
    raise ConfigurationError(
        f"{name} cannot accept {key!r}{_at_yaml_loc(node)}: it is a *args parameter, which can never be "
        f"passed by keyword, so no config key can reach it. Pass the values positionally — "
        f"flow(node, a, b) — or give the target a keyword parameter."
    )


def accepts_key(target: Any, key: str) -> bool:
    """True if ``key`` can set an attribute on ``target`` when ADDRESSED explicitly.

    "Addressed" means the key names its receiver — a ``ClassName:`` block, an
    exact dotted path, a marker's own kwargs, or a :func:`configure` block.
    Such keys are gated by the accept-list ALONE: constructor parameters,
    public settable class attributes, and ``__init__``-body slots (AST-scanned,
    plus any ``broadcast_attrs=`` declaration and baked table). A target whose
    constructor takes ``**kwargs`` accepts everything.

    ``target`` may be a class, a live instance, or the dotted string a
    ``_target_:`` marker carries; an unresolvable target accepts nothing.

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


def declares_key(target: Any, key: str) -> bool:
    """True if ``target`` NAMES ``key`` — the ``**kwargs`` catchall never counts.

    The question BETWEEN :func:`accepts_key` and :func:`accepts_any_key`: a
    ``**kwargs`` constructor cannot REFUSE any key, so ``accepts_key`` answers
    yes to everything — but the keys such a target DECLARES (named constructor
    parameters, settable class attributes, ``__init__``-body slots) are
    knowable, and a library that forwards its catchall somewhere strict rejects
    every other name at a call site nowhere near the config (torchmetrics:
    every metric takes ``**kwargs`` and raises "Unexpected keyword arguments").
    A consumer sizing such targets re-derived exactly this answer locally; this
    is that answer, beside the predicates it complements. For a target with no
    catchall it agrees with ``accepts_key`` by construction. An unresolvable
    target declares nothing (``False``), like its siblings.
    """
    cls = _settability_target(target)
    if cls is None:
        return False
    # ONE projection for both branches. It used to short-circuit to the
    # accept-list here and run a separate kind-filtered walk only for a
    # ``**kwargs`` target — so the identical ``*loaders`` parameter was
    # "declared" on a plain class and "not declared" on one that also took
    # ``**kwargs``. Pinned by tests/test_introspection_agreement.py.
    return key in slot_names(cls, _DECLARED_KINDS)


def _settability_target(target: Any) -> Any:
    """Normalize a class / callable / instance / dotted-name into the target to introspect.

    A plain routine (a registered builder FUNCTION) is returned AS-IS — taking
    ``type(func)`` (= ``function``, whose ``__init__`` takes ``**kwargs``) made
    all three predicates answer yes-to-everything for function targets, the
    exact failure ``accepts_any_key`` exists to prevent. A live instance still
    normalizes to its class.

    This is the ONE marker-target normalizer — every "turn ``marker.target``
    into the thing to introspect" site goes through it (the public predicates,
    :func:`merge_bare_pool_into_kwargs`, :func:`_receiver_for_target`, the
    engine's nested-marker cascade, ``configurator._tune_deferred``). The idiom
    used to be inlined six ways, and five of the copies degraded a function
    OBJECT target to ``None`` (``resolve_class`` is string/type-only), so a
    ``PartialClass(builder_fn, …)`` slot bypassed the NoBroadcast opt-outs on the
    engine cascade and could not be tuned by ``configure()`` at all — while the
    identical class-target slot behaved, and while these predicates answered
    correctly. Do not re-inline it.
    """
    if target is None:
        return None
    if isinstance(target, str):
        return resolve_class(target)
    if isinstance(target, type) or inspect.isroutine(target):
        return target
    return type(target)


#: How a block's contents reached the node consuming them — the scanner's
#: delivery vocabulary (previously an implicit ``gated``/``floating`` boolean
#: pair whose four call-shapes encoded three real states):
#:
#: * ``"addressed"`` — a named block / own kwargs / an attr-recursion: aimed at
#:   exactly this node, bypasses the broadcast opt-outs;
#: * ``"glob_one"`` — a ``'*'`` block's contents: a cascade form, gated by the
#:   NoBroadcast opt-outs like bare keys, spent at this level;
#: * ``"rider"`` — a ``'**'`` block's contents: gated like ``glob_one``, but
#:   floating — nested named dicts stay matched-or-ignored for deeper nodes
#:   instead of being hoisted as one-level routing.
_Delivery = Literal["addressed", "glob_one", "rider"]


class _Receiver:
    """What the scanner may ask about the node consuming a view.

    Built ONLY by the factory functions below (kept side by side — a future
    per-path divergence must be two adjacent functions in one diff, never a
    branch inside the walk). The predicate fields are where the paths
    legitimately differ; everything else is shared derivation from the same
    accept-list machinery. See ``tests/test_cross_path_pins.py`` for the pinned
    cross-path differences the predicates encode.
    """

    __slots__ = (
        "cls_name",
        "block_names",
        "inner_names",
        "instance_name",
        "target_cls",
        "acceptable",
        "blocked",
        "accepts_value",
        "dict_slot",
        "own_dict_routes",
        "skip_bare_value",
    )

    def __init__(
        self,
        *,
        cls_name: str,
        block_names: FrozenSet[str],
        inner_names: FrozenSet[str],
        instance_name: Optional[str],
        target_cls: Any,
        acceptable: Optional[FrozenSet[str]],
        blocked: Optional[FrozenSet[str]],
        accepts_value: Callable[[str, Any], bool],
        dict_slot: Callable[[str, _Delivery], bool],
        own_dict_routes: Callable[[str], bool],
        skip_bare_value: Callable[[Any], bool],
    ) -> None:
        self.cls_name = cls_name
        self.block_names = block_names
        self.inner_names = inner_names
        self.instance_name = instance_name
        self.target_cls = target_cls
        self.acceptable = acceptable
        self.blocked = blocked
        self.accepts_value = accepts_value
        self.dict_slot = dict_slot
        self.own_dict_routes = own_dict_routes
        self.skip_bare_value = skip_bare_value


def _receiver_for_target(cls_name: str, own_kwargs: Dict[str, Any], target: Any = None) -> _Receiver:
    """The MARKER-path receiver: a class/callable target being materialized.

    Absorbs the name normalization, block-name derivation and the value-aware
    ``_accepts`` predicate that used to live inline in ``_prepare_kwargs``.
    A class-name block is matched by NAME, but ``cls_name`` is whatever the
    target was SPELLED as — a dotted path or a tag-selector spelling must
    still be reached by its ``Widget:`` block, so the resolved class's
    registered name is matched alongside the literal spelling (which also
    aligns this path with ``configure()``, keyed off ``__confluid_name__``).
    """
    if cls_name.endswith("()"):
        cls_name = cls_name[:-2]
    instance_name = own_kwargs.get("name")
    instance_str = instance_name if isinstance(instance_name, str) else None

    cache_key = (cls_name, _cache_key(target), instance_str)
    cached = _receiver_cache.get(cache_key)
    if cached is not None:
        return cached

    acceptable = _get_acceptable_keys(target or cls_name)
    target_cls = _settability_target(target or cls_name or None)
    param_kinds = _get_param_kinds(target_cls or cls_name) if (target_cls or cls_name) else {}
    blocked = _broadcast_blocked_keys(target_cls)
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
            # Plain dict — a VALUE only when the target annotates the param as
            # a dict/mapping; otherwise it is a block (routing / slot content).
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

    def _dict_slot(k: str, delivery: _Delivery) -> bool:
        # A dict at a key the receiver DECLARES is a slot value — on the
        # ADDRESSED delivery unconditionally, and on the glob deliveries
        # since the D5 adjudication (2026-08-09: all four cells of the
        # rider×shape matrix apply; `'**.optimizer.lr': 0.01` used to tune the
        # slot under configure() and silently no-op under load()). A glob
        # delivery is a cascade form, so it respects the NoBroadcast opt-outs
        # exactly like a bare key; an undeclared key stays routing.
        if acceptable is None or k not in acceptable:
            return False
        if delivery != "addressed":
            return blocked is not None and k not in blocked
        return True

    def _own_dict_routes(k: str) -> bool:
        # In own kwargs, a dict at a key that is NOT mine is a sub-block
        # addressing a direct child by name (the expanded ``trainer.b.lr``).
        return acceptable is not None and k not in acceptable

    def _skip_bare(v: Any) -> bool:
        # Same-target Fluid that isn't self — skip (would otherwise loop).
        return isinstance(v, Fluid) and target_cls is not None and _same_target(v.target, target_cls)

    receiver = _Receiver(
        cls_name=cls_name,
        block_names=frozenset(block_names),
        inner_names=frozenset(n for n in (cls_name, instance_str) if n),
        instance_name=instance_str,
        target_cls=target_cls,
        acceptable=acceptable,
        blocked=blocked,
        accepts_value=_accepts,
        dict_slot=_dict_slot,
        own_dict_routes=_own_dict_routes,
        skip_bare_value=_skip_bare,
    )
    _receiver_cache[cache_key] = receiver
    return receiver


def _receiver_for_instance(obj: Any) -> _Receiver:
    """The LIVE-OBJECT-path receiver: an already-built instance under configure().

    Kept DIRECTLY beside :func:`_receiver_for_target` on purpose — the fields
    where the two differ are the documented cross-path behaviors, each with a
    named pin in ``tests/test_cross_path_pins.py``:

    * ``accepts_value`` is NAME-ONLY (``_settable``: the accept-list union the
      live ``vars(obj)`` names, minus ignore-marked members and setterless
      properties) and refuses every dict — a top-level dict is always a BLOCK
      on this path (D4).
    * ``dict_slot`` matches the marker path since the D5 adjudication
      (2026-08-09): a rider-delivered dict reaches a declared slot on BOTH
      paths, gated deliveries respecting the NoBroadcast opt-outs.
    * There is no own-kwargs consumption and no same-target parent skip — a
      live object has no marker kwargs, and its view values were already
      ordered by the ancestors' splices.

    NOT cached in ``_receiver_cache``: the predicates close over THIS
    instance's ``vars`` — per-object state, not per-class.
    """
    cls = obj.__class__
    cls_name = str(getattr(cls, "__confluid_name__", cls.__name__))
    instance_name = getattr(obj, "name", None)
    instance_str = instance_name if isinstance(instance_name, str) else None

    acceptable = _get_acceptable_keys(cls)
    blocked = _broadcast_blocked_keys(cls)
    own_attrs = {k for k in vars(obj) if not k.startswith("_")}

    def _settable(key: str) -> bool:
        member = getattr(cls, key, None)
        if isinstance(member, property) and member.fset is None:
            return False
        return acceptable is None or key in acceptable or key in own_attrs

    def _accepts(k: str, v: Any) -> bool:
        return not isinstance(v, dict) and _settable(k)

    def _dict_slot(k: str, delivery: _Delivery) -> bool:
        # Mirrors the marker-path predicate since the D5 adjudication: a
        # glob (rider / '*') delivery is a cascade form and respects the
        # NoBroadcast opt-outs; this path always reached the slot but never
        # consulted ``blocked``, which broke the NoBroadcast promise for
        # glob-delivered mappings.
        if not _settable(k):
            return False
        if delivery != "addressed":
            return blocked is not None and k not in blocked
        return True

    def _own_dict_routes(k: str) -> bool:  # no own kwargs on this path
        return False

    def _skip_bare(v: Any) -> bool:
        return False

    names = frozenset(n for n in (cls_name, instance_str) if n)
    return _Receiver(
        cls_name=cls_name,
        block_names=names,
        inner_names=names,
        instance_name=instance_str,
        target_cls=cls,
        acceptable=acceptable,
        blocked=blocked,
        accepts_value=_accepts,
        dict_slot=_dict_slot,
        own_dict_routes=_own_dict_routes,
        skip_bare_value=_skip_bare,
    )


# Origin labels the scanner attaches to its emissions. They reach TRACE lines
# and the report VERBATIM (docs/report.md documents them); the ONE place that
# ever PARSES one is :func:`_mark_used_key` directly below — keep the labels
# and their parser adjacent, and never match on the raw strings elsewhere
# (both sinks used to carry a byte-identical parsing expression, so renaming a
# label in the scanner would have silently broken unused-tracking in TWO
# modules).
_ORIGIN_RIDER = "glob '**'"
_ORIGIN_GLOB_ONE = "glob '*'"


def _mark_used_key(key: str, origin: str) -> str:
    """The report's used-key spelling for a scanner emission — the ONE parser of origin labels.

    Glob-delivered keys register per leaf under their glob prefix (``**.lr`` /
    ``*.lr``) so a partially consumed glob block reports precisely; everything
    else registers under the plain key.
    """
    return f"**.{key}" if origin == _ORIGIN_RIDER else f"*.{key}" if origin == _ORIGIN_GLOB_ONE else key


class _ScanSink(Protocol):
    """Effect writer for one :func:`_scan_view` pass — sinks apply, never gate.

    The scanner owns ALL gating (accept-lists, NoBroadcast, scopes, ordering
    positions) through the receiver; a sink only records outcomes. A sink
    method MUST NOT grow branches on keys or scopes — a new rule belongs in
    the scanner, behind a receiver predicate, or nowhere.
    """

    def apply(self, key: str, value: Any, origin: str, scope: _KeyScope, own: bool, gated: bool, pos: int) -> None: ...

    def dict_at_slot(self, key: str, block: Dict[str, Any], origin: str, bare_before: FrozenSet[str]) -> None: ...

    def route(self, key: str, block: Dict[str, Any]) -> None: ...

    def unknown(self, key: str, value: Any, *, origin: str) -> None: ...

    def matched(self, name: str) -> None: ...


def _scan_view(
    view: Dict[str, Any],
    receiver: _Receiver,
    sink: _ScanSink,
    *,
    own_kwargs: Optional[Dict[str, Any]] = None,
    self_obj: Any = None,
) -> None:
    """The ONE walk of a document view for a receiving node — both paths.

    Emits the outcome of every gate to ``sink`` in document order (last
    emission per key wins downstream, which IS the precedence rule). The
    five-branch block ladder, the glob semantics, the named-block matching and
    the position bookkeeping exist exactly once, here; what differs between
    the marker path and the live-object path is declared in the receiver's
    predicates and in what each sink does with an emission — never in this
    walk. (Predecessors: ``_prepare_kwargs``'s and ``configurator._apply``'s
    separate ``_consume_block`` closures, which diverged four ways in one day
    when the rule lived twice — docs/architecture.md records 3 and 8.)
    """
    accepts_value = receiver.accepts_value
    blocked = receiver.blocked
    apply = sink.apply
    route = sink.route
    unknown = sink.unknown
    dict_at_slot = sink.dict_at_slot
    current_pos = 0
    _positions: Optional[Dict[str, int]] = None

    def _bare_before() -> FrozenSet[str]:
        # Partial: only a dict-at-slot emission needs positions (the rare case),
        # so the common pass never pays the extra view walk. The candidate set
        # is the ONE cascade definition (bare keys + '**'-rider scalars at the
        # rider's index) — a private non-dict filter here skipped the rider,
        # so its contents could never be "beaten" and always won (D7).
        nonlocal _positions
        if _positions is None:
            _positions = _cascade_scalar_positions(view)
        pos = current_pos
        return frozenset(k for k, i in _positions.items() if i < pos)

    def _consume(block: Dict[str, Any], *, origin: str, delivery: _Delivery) -> None:
        """Unroll a block addressed to this node — the ONE branch ladder.

        ``delivery`` names how the block reached this node (see
        :data:`_Delivery`): ``"addressed"`` contents bypass the broadcast
        opt-outs, glob-delivered contents (``"glob_one"`` / ``"rider"``) are
        gated by them like bare keys, and only under a ``"rider"`` do nested
        named dicts stay floating (matched-or-ignored — the riding ``'**'``
        entry keeps them in reach) instead of being hoisted as one-level
        routing.
        """
        gated = delivery != "addressed"
        floating = delivery == "rider"
        for bk, bv in _expand_block_keys(block).items():
            if bk == "**" and isinstance(bv, dict):
                _consume(bv, origin=_ORIGIN_RIDER, delivery="rider")
                route("**", bv)
                continue
            if bk == "*" and isinstance(bv, dict):
                route("*", bv)
                continue
            if isinstance(bv, dict) and bk in receiver.inner_names and delivery in ("rider", "addressed"):
                # Addressed to me again (``Cls.inst.attr`` form, or a named
                # match while floating under '**') — unroll inline, ungated.
                _consume(bv, origin=f"block {bk!r}", delivery="addressed")
                continue
            if isinstance(bv, dict) and not accepts_value(bk, bv):
                # Not a dict-typed VALUE. Either a slot content aimed at a
                # declared key (the receiver's dict_slot predicate — the paths
                # deliberately differ on when, see the D5 pin), or routing for
                # the direct children (spent while floating).
                if receiver.dict_slot(bk, delivery):
                    dict_at_slot(bk, bv, origin, _bare_before())
                    continue
                if not floating:
                    route(bk, bv)
                continue
            if gated:
                if blocked is not None and bk not in blocked and accepts_value(bk, bv):
                    apply(bk, bv, origin, _KeyScope.EXACT, False, True, current_pos)
                continue
            if accepts_value(bk, bv):
                apply(bk, bv, origin, _KeyScope.EXACT, False, False, current_pos)
            else:
                unknown(bk, bv, origin=origin)

    def _consume_own(kwargs: Dict[str, Any]) -> None:
        """The receiver's own kwargs — addressed to me by definition, thus EXACT."""
        for k, v in _expand_block_keys(kwargs).items():
            if k == "**" and isinstance(v, dict):
                _consume(v, origin=_ORIGIN_RIDER, delivery="rider")
                route("**", v)
            elif k == "*" and isinstance(v, dict):
                route("*", v)
            elif isinstance(v, dict) and receiver.own_dict_routes(k):
                route(k, v)
            else:
                apply(k, v, "own", _KeyScope.EXACT, True, False, current_pos)

    self_unrolled = False
    skip_bare_value = receiver.skip_bare_value
    block_names = receiver.block_names
    instance_name = receiver.instance_name
    for pos, (k, v) in enumerate(view.items()):
        current_pos = pos
        # Receiving Fluid's own slot — unroll its kwargs at this position.
        if self_obj is not None and v is self_obj and not self_unrolled:
            if own_kwargs is not None:
                _consume_own(own_kwargs)
            self_unrolled = True
            continue
        if skip_bare_value(v):
            continue
        scope = _scope_of(view, k)
        if scope is _KeyScope.EXACT:
            continue  # an ancestor's addressed value — ordering/!ref: visibility only
        if scope is _KeyScope.ADDRESSED:
            # An attr-recursion delivered this entry to exactly this object
            # (live path) — consume it like matched-block content.
            _consume({k: v}, origin="addressed", delivery="addressed")
            continue
        if k == "**" and isinstance(v, dict):
            _consume(v, origin=_ORIGIN_RIDER, delivery="rider")
            continue
        if k == "*" and isinstance(v, dict):
            _consume(v, origin=_ORIGIN_GLOB_ONE, delivery="glob_one")
            continue
        if (k in block_names or k == instance_name) and isinstance(v, dict):
            sink.matched(k)
            _consume(v, origin=f"block {k!r}", delivery="addressed")
            continue
        if scope is _KeyScope.STRICT:
            continue  # routing block for a sibling name — not mine
        # Plain broadcast — the only path the NoBroadcast opt-out gates.
        if blocked is not None and k not in blocked and accepts_value(k, v):
            apply(k, v, "bare", _KeyScope.BARE, False, True, pos)

    if not self_unrolled and own_kwargs is not None:
        _consume_own(own_kwargs)


class _MergeSink:
    """The marker-path sink: decisions become the merged ``_View`` + report records.

    Reproduces ``_prepare_kwargs``'s output contract exactly — the ``_View``
    with its scope tags, the TRACE lines, and the report's origins/mark-used
    conventions (own kwargs are definitions and erase an origin; ungated block
    values record an origin but are never marked used; gated and bare applies
    do both).
    """

    __slots__ = ("cls_name", "merged", "report", "origins", "contest", "target", "self_obj", "beaten_per_slot")

    def __init__(self, cls_name: str, target: Any = None, self_obj: Any = None) -> None:
        self.cls_name = cls_name
        # Carried for ``unknown``'s variadic refusal only: the TARGET to introspect
        # and the NODE whose ``_yaml_loc`` locates the error.
        self.target = target if target is not None else cls_name
        self.self_obj = self_obj
        self.merged = _View()
        self.report = _ENGINE_STATE.get().report
        self.origins: Dict[str, str] = {}
        # Every candidate the scanner emitted per key, as raw ``(origin, value,
        # pos)`` in the order it emitted them — which IS document order, so the
        # last one is the winner. Raw and un-rendered on purpose: the ``repr``
        # happens in ``record_applied``, and only for keys something contested.
        # Built only when a report is active; the default path stays zero-cost.
        self.contest: Dict[str, List[Tuple[str, Any, int]]] = {}

        #: Per slot, the cascade keys the DELIVERING BLOCK out-positioned —
        #: the scanner's verdict, at the block's own position (C2). Mirrors
        #: ``configurator._LiveSink.beaten_per_slot``.
        self.beaten_per_slot: Dict[str, FrozenSet[str]] = {}

    def _mark_used(self, k: str, origin: str) -> None:
        if self.report is not None:
            self.report.mark_used(_mark_used_key(k, origin))

    def apply(self, key: str, value: Any, origin: str, scope: _KeyScope, own: bool, gated: bool, pos: int) -> None:
        self.merged.set(key, value, scope)
        if self.report is not None:
            # Recorded BEFORE the own-kwarg early return: a marker's own value is
            # a legitimate competitor (it is unrolled at the marker's own slot and
            # ordered like everything else), and "my kwarg lost to a bare key" is
            # the contest readers most often need explained.
            self.contest.setdefault(key, []).append((origin, value, pos))
        if own:
            self.origins.pop(key, None)  # own kwargs are definitions, not overrides
            return
        if _trace_on:  # per-KEY site — see the log-gate block
            logger.trace(f"broadcast: {key!r} -> {self.cls_name} ({origin})")
        if self.report is None:
            return
        self.origins[key] = origin
        if gated:
            self._mark_used(key, origin)

    def dict_at_slot(self, key: str, block: Dict[str, Any], origin: str, bare_before: FrozenSet[str]) -> None:
        if _trace_on:  # per-KEY site — see the log-gate block
            logger.trace(f"broadcast: {key!r} -> {self.cls_name} ({origin}, slot value)")
        # C1 (BUGS-2026-08-13): a mapping delivered at a slot whose merged value is
        # already a MARKER (the receiver's own kwarg — a nested ``_target_:``) TUNES
        # that marker instead of replacing it; the nested child then BUILDS with the
        # merged kwargs. Value-state dispatch, same as the live sink's — not a new
        # key/scope gate (those stay in the scanner).
        prev = self.merged.get(key)
        if isinstance(prev, Target):
            self.merged.set(key, tune_marker(prev, block), _KeyScope.EXACT)
        else:
            self.merged.set(key, block, _KeyScope.EXACT)
        # KEEP the scanner's verdict. It is computed at THIS BLOCK's position, which
        # is the only place that position still exists — by the time the engine sees
        # the merged kwargs they have been spliced at the MARKER's slot, and a second
        # verdict computed from there answers a different question (C2). The live
        # sink has always recorded it; this one accepted the argument and dropped it,
        # so the two paths disagreed for the ordinary "node first, overrides below"
        # layout while both edge orderings agreed.
        self.beaten_per_slot[key] = bare_before
        if self.report is not None:
            self.origins[key] = origin

    def route(self, key: str, block: Dict[str, Any]) -> None:
        _merge_routing(self.merged, key, block)

    def unknown(self, key: str, value: Any, *, origin: str) -> None:
        # A key naming a ``*args`` parameter can never land anywhere, whatever the
        # class does — that is refused outright.
        refuse_if_variadic_name(self.target, key, self.self_obj)
        # Anything else undeclared is REPORTED, not refused. This used to be a
        # no-op justified as "constructor validation is this path's typo
        # enforcement" — measured false: the key is not a ctor param, so the ctor
        # filter drops it and it never reaches validation at all. ``configure()``
        # has always warned here; the load path said nothing for the same mistake.
        from confluid.fluid import format_yaml_loc

        refuse_if_undeclared(self.target, key, self.self_obj)
        loc = format_yaml_loc(self.self_obj)
        logger.warning(
            f"{self.cls_name} block has no attribute {key!r}" f"{f' (receiver at {loc})' if loc else ''} — ignored"
        )
        if self.report is not None:
            self.report.record_failed(key, self.cls_name, "unknown-attribute")

    def matched(self, name: str) -> None:
        if self.report is not None:
            self.report.mark_used(name)  # a named block is "used" once it matches


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

    Since 2026-08-08 this is a thin composition: the receiver factory answers
    "who is consuming", :func:`_scan_view` performs the ONE walk, and
    :class:`_MergeSink` writes the outcomes into the returned view.
    """
    receiver = _receiver_for_target(cls_name, own_kwargs, target)
    sink = _MergeSink(receiver.cls_name, target=target or cls_name, self_obj=self_obj)
    _scan_view(parent_context, receiver, sink, own_kwargs=own_kwargs, self_obj=self_obj)

    if sink.report is not None and sink.origins:
        name = receiver.instance_name
        label = f"{receiver.cls_name} {name!r}" if name is not None else receiver.cls_name
        for k, origin in sink.origins.items():
            sink.report.record_applied(k, label, origin, candidates=sink.contest.get(k, ()))

    # Ride out on the view: the engine stamps this onto the marker, and the
    # view is the only thing this function returns.
    sink.merged.beaten_per_slot = sink.beaten_per_slot
    return sink.merged


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
        pool.update({k: v for k, v in star2.items() if not isinstance(v, dict)})
        # Receiver application goes through the ONE cascade gate — this branch
        # carried its own inline copy with strictly weaker gates (no list skip,
        # no Fluid declared-key/same-target guards), the exact drift class the
        # shared function exists to end. Keys the marker already carries keep
        # winning (``protected`` — they are the marker's own spec, and this
        # path has no document ordering to consult).
        report = _ENGINE_STATE.get().report
        target_label = str(getattr(target, "__name__", target))

        def _record(k: str) -> None:
            if report is not None:
                report.record_applied(k, target_label, _ORIGIN_RIDER)
                report.mark_used(_mark_used_key(k, _ORIGIN_RIDER))

        merge_bare_pool_into_kwargs(
            merged, star2, target, protected=frozenset(merged), on_applied=_record, origin=_ORIGIN_RIDER
        )
    return pool


def _broadcast_pool(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten a context's ``'**'`` entry into the deferred-marker broadcast pool.

    The deferred-tuning cascade (:func:`merge_bare_pool_into_kwargs`, fed by
    the engine's nested-marker loop AND — since the D5-mirror fix, 2026-08-09
    — ``configure()``'s ``_tune_deferred``) skips dict/list values, so a
    ``'**'`` glob block at the top level of the view would be invisible to it;
    unroll its non-dict contents at the block's document position, tagged
    BARE — rider contents cascade by definition. (``'*'`` blocks are
    depth-addressed and stay out — the recursive-descent path handles them.)

    The output is a scope-preserving :class:`_View`: flattening to a plain
    dict erased the tags exactly when a rider was present, which made an
    ancestor's EXACT (addressed) values look BARE to the cascade gate — a
    value delivered to one node could leak into its descendants' deferred
    slots whenever a ``'**'`` block happened to exist in the same view.
    """
    if "**" not in ctx and "*" not in ctx:
        return ctx
    pool = _View()
    for k, v in ctx.items():
        if k == "**" and isinstance(v, dict):
            for gk, gv in v.items():
                if not isinstance(gv, dict):
                    pool.set(gk, gv, _KeyScope.BARE)
        elif k == "*" and isinstance(v, dict):
            continue
        else:
            pool.set(k, v, _scope_of(ctx, k))
    return pool


def merge_bare_pool_into_kwargs(
    marker_kwargs: Dict[str, Any],
    pool: Dict[str, Any],
    target: Any,
    *,
    protected: FrozenSet[str] = frozenset(),
    on_applied: Optional[Callable[[str], None]] = None,
    origin: str = "nested-class",
) -> None:
    """Merge a bare-key pool into a deferred marker's kwargs, IN PLACE — the ONE cascade gate.

    Both paths fill un-ordered marker kwargs from a pool of bare keys: the
    engine's nested-marker broadcast (``_resolve_kwarg_value``) and
    ``configure()``'s deferred-slot tuning (``configurator._tune_deferred``).
    The two used to carry separate copies of the gates and drifted twice —
    a bare LIST tuned a deferred slot on the configure path only, and the
    Fluid self-broadcast guard existed on the engine path only (adjudicated
    2026-08-08 to the engine's stricter gates; pins in
    ``tests/test_cross_path_pins.py``). This function is the single copy.

    The gates, in order: container values (dict/list) never cascade; only
    BARE-scoped pool keys apply (a plain-dict pool is all-BARE); ``protected``
    keys are skipped — it carries the CALLER's ordering verdict, because the
    ordering MODELS deliberately differ (the engine passes the marker's
    already-settled keys when ``is_order_resolved``, per-materialize-pass;
    ``configure()`` passes its call-scoped ``beaten`` set — see
    docs/architecture.md record 6); the NoBroadcast opt-outs gate like bare
    keys (a ``broadcast=False`` class takes nothing); a Fluid value requires a
    DECLARED key (never the ``**kwargs`` catchall) and a different target
    (``_same_target`` — self-broadcast would loop); a non-Fluid value passes
    the accept-list.

    ``on_applied`` receives each merged key — reporting stays caller-owned
    (the report asymmetry between the paths is documented-deliberate).
    ``origin`` labels the TRACE line only.
    """
    target_cls = _settability_target(target)
    blocked = _broadcast_blocked_keys(target_cls)
    if blocked is None:
        return  # @configurable(broadcast=False) — nothing bare ever lands
    acceptable = _get_acceptable_keys(target if target is not None else target_cls)
    label = getattr(target_cls, "__name__", target)
    for bk, bv in pool.items():
        if isinstance(bv, (dict, list)):
            continue  # containers are blocks/definitions, never cascade values
        if _scope_of(pool, bk) is not _KeyScope.BARE:
            continue  # an ancestor's addressed value — ordering visibility only
        if bk in protected or bk in blocked:
            continue
        if isinstance(bv, Fluid):
            if acceptable is None or bk not in acceptable:
                continue  # Fluids never ride the **kwargs catchall
            if target_cls is not None and _same_target(bv.target, target_cls):
                continue  # self-broadcast guard — would loop on re-materialization
        elif acceptable is not None and bk not in acceptable:
            continue
        if _trace_on:  # per-KEY site — see the log-gate block
            logger.trace(f"broadcast: {bk!r} -> {label} ({origin})")
        marker_kwargs[bk] = bv
        if on_applied is not None:
            on_applied(bk)
