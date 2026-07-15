"""The materialization engine — flow / materialize / resolve + broadcasting.

Extracted from ``fluid.py`` + ``loader.py`` (2026-07) to break their module
cycle. The layering is now one-directional:

    ``fluid`` (marker data classes, LEAF)
        ↑
    ``engine`` (this module: flow/cast, materialize/resolve, _flow_recursive,
                broadcasting/_prepare_kwargs, accept-lists, the ``_ENGINE_STATE``
                ContextVar + the public ``active_context()``)
        ↑
    ``loader`` (YAML parsing: ConfluidLoader, load/load_config, includes,
                imports, scopes glue)

Two deliberate lazy seams remain (both documented at the site):
``resolve()`` body-imports ``loader.load`` (str/Path convenience), and
``resolver._materialize_cursor`` body-imports this module (``_ENGINE_STATE``/``flow``).

``confluid.loader`` re-exports the moved names for backward compatibility —
new code should import from here.
"""

import inspect
from contextlib import contextmanager
from contextvars import ContextVar
from copy import copy
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Type

from loggair import get_logger

from confluid.exceptions import ConstructionError, ReferenceResolutionError, UnknownClassError
from confluid.fluid import Class, Clone, Fluid, Instance, Lazy, Reference, T, format_yaml_loc
from confluid.introspect import baked_init_attrs, init_setattr_names, init_source_available
from confluid.merger import expand_dotted_keys
from confluid.registry import get_registry, resolve_class
from confluid.report import ConfigurationReport
from confluid.resolver import Resolver, resolve_reference_path

logger = get_logger("confluid.engine")


@dataclass(frozen=True)
class _EngineState:
    """Immutable per-context engine state (one ContextVar, set/reset by token).

    A ``contextvars.ContextVar`` — not ``threading.local()`` — so an active
    materialization context is inherited by asyncio tasks and by
    ``asyncio.to_thread`` workers (a ``threading.local`` silently dropped it,
    making ``!ref:`` resolution fail inside an event-loop task). A raw
    ``Thread`` / ``run_in_executor`` still does NOT inherit contextvars — see
    :func:`active_context` for the boundary contract.
    """

    context: Optional[Dict[str, Any]] = None
    flow_memo: Optional[Dict[int, Any]] = None
    instance_memo: Optional[Dict[int, Any]] = None
    suppress_solidify: bool = False
    # Ambient ConfigurationReport installed by collect_report(). Mutable by
    # design (like the memo dicts riding this frozen dataclass); every
    # instrumentation site is ``if report is not None``-guarded so the
    # default path stays zero-cost.
    report: Optional[ConfigurationReport] = None


_ENGINE_STATE: ContextVar[_EngineState] = ContextVar("confluid_engine_state", default=_EngineState())


def get_active_context() -> Optional[Dict[str, Any]]:
    return _ENGINE_STATE.get().context


@contextmanager
def active_context(context: Optional[Dict[str, Any]]) -> Iterator[None]:
    """Activate ``context`` for bare ``flow()`` calls inside the block.

    The public way to make ``!ref:``/broadcast resolution work for ``flow()``
    calls made OUTSIDE a ``materialize()`` pass (e.g. domain code flowing a
    deferred ``Lazy`` slot later, on another thread). Fresh flow/instance memos
    are installed so dotted refs share one instance within the block; the
    previous state is restored on exit (nesting-safe).

    The mapping is activated VERBATIM when it has no dotted keys — live
    instances in the context keep their identity (``flow(Reference("x")) is
    ctx["x"]``). A context WITH dotted keys is expanded like ``materialize``
    does (``expand_dotted_keys`` deep-copies non-Fluid leaves, so prefer
    pre-nested dicts when identity of live values matters).

    Thread/async boundary contract: the state rides a ``contextvars.ContextVar``,
    so it IS inherited by asyncio tasks and ``asyncio.to_thread`` workers. It is
    NOT inherited by a raw ``threading.Thread`` or ``loop.run_in_executor`` —
    either wrap the target with ``contextvars.copy_context().run(...)`` or enter
    ``active_context(...)`` inside the worker itself.
    """
    ctx = expand_dotted_keys(context) if context and any("." in k for k in context) else context
    # Fresh memos, but the ambient report (collect_report) carries forward —
    # a fresh state would silently stop the pass's tracking.
    token = _ENGINE_STATE.set(_EngineState(context=ctx, flow_memo={}, instance_memo={}, report=_active_report()))
    try:
        yield
    finally:
        _ENGINE_STATE.reset(token)


def _active_report() -> Optional[ConfigurationReport]:
    """The ambient ConfigurationReport, if a ``collect_report()`` block is active."""
    return _ENGINE_STATE.get().report


@contextmanager
def collect_report() -> Iterator[ConfigurationReport]:
    """Collect a :class:`ConfigurationReport` for everything inside the block.

    The engine-side counterpart of the report :func:`confluid.configure`
    returns: ``load()`` / ``materialize()`` / ``flow()`` calls inside the
    block record their applied broadcasts and document keys into the yielded
    report, and a nested ``configure()`` adopts (and returns) the same
    ambient report — so one report spans a load-then-configure pass::

        with collect_report() as report:
            model = load("config.yaml")
            configure(model, config=overrides)
        print(report.summary())

    Nesting-safe: an already-active report is reused (the inner block
    aggregates into it). On exit the aggregate unused-keys DEBUG summary is
    logged once, by the outermost block only.
    """
    state = _ENGINE_STATE.get()
    owns = state.report is None
    report = state.report or ConfigurationReport()
    token = _ENGINE_STATE.set(replace(state, report=report))
    try:
        yield report
    finally:
        _ENGINE_STATE.reset(token)
        if owns:
            report.log_unused()


def _register_document_keys(report: ConfigurationReport, config: Dict[str, Any]) -> None:
    """Register a document's top-level keys as unused-tracking candidates.

    Engine-path filter: a key whose value is (or transitively contains) a
    ``Fluid`` marker, or is a list, is a DEFINITION — the node tree being
    built — not an override candidate, and is excluded. Glob dict blocks
    register per non-dict leaf (``**.lr`` notation) so a partially consumed
    glob reports precisely.
    """
    for k, v in config.items():
        if k in ("*", "**") and isinstance(v, dict):
            report.add_config_keys(f"{k}.{leaf}" for leaf, lv in v.items() if not isinstance(lv, dict))
        elif not _contains_fluid(v):
            report.add_config_keys((k,))


def _contains_fluid(value: Any) -> bool:
    """True when ``value`` is / transitively holds a Fluid marker, or is a list."""
    if isinstance(value, Fluid) or isinstance(value, list):
        return True
    if isinstance(value, dict):
        return any(_contains_fluid(v) for v in value.values())
    return False


def materialize(data: Any, context: Optional[Dict[str, Any]] = None, solidify: bool = True) -> Any:
    """Resolve config data and instantiate all Class objects recursively.

    Within a single materialize pass, identical raw markers (reached directly
    or via ``!ref:``) produce a single flowed ``Instance`` object, which is
    materialized into a single live instance. ``!clone:`` opts out of this
    sharing with an explicit deepcopy.

    ``solidify=False`` suppresses the post-flow ``solidify()`` hook for every
    object built in this pass (see :func:`confluid.fluid.flow`) — for static
    introspection that needs live objects but must NOT pay for the expensive
    finalize (e.g. building a model backbone). The objects are still fully
    constructed (``__init__`` only stores values per the zero-arg / lazy-init
    convention), just not solidified.
    """
    _acceptable_keys_cache.clear()
    _post_init_attrs_cache.clear()
    _param_kind_cache.clear()
    if context:
        context = expand_dotted_keys(context)
    report = _active_report()  # carry the ambient report through the fresh state
    if report is not None and isinstance(context, dict):
        _register_document_keys(report, context)
    token = _ENGINE_STATE.set(
        _EngineState(context=context, flow_memo={}, instance_memo={}, suppress_solidify=not solidify, report=report)
    )
    try:
        result = _flow_recursive(data, parent_context=context)
        return _deep_flow(result)
    finally:
        _ENGINE_STATE.reset(token)


def resolve(
    data: Any,
    *,
    context: Optional[Dict[str, Any]] = None,
    scopes: Optional[List[str]] = None,
) -> Any:
    """Broadcast-resolve a config to a Fluid marker graph WITHOUT instantiating.

    Like :func:`materialize`, but stops before ``_deep_flow``: it parses,
    resolves scopes/includes, applies broadcasting and ``!ref:`` resolution
    (sharing referenced markers by identity via ``flow_memo`` — so a fan-out
    ``!ref:`` is one object reached twice), and returns the resulting
    ``Instance`` / ``Lazy`` / ``Class`` markers with their broadcast siblings
    merged into ``.kwargs`` — WITHOUT constructing any live object.

    Use for static structural introspection of a config (e.g. FluxStudio's
    YAML→graph import) when even side-effect-free construction is undesirable.
    ``materialize(data, solidify=False)`` is the instantiate-but-cheap
    counterpart; prefer it unless you specifically need un-built markers.

    Caveat: a *dotted* ``!ref:a.b`` (attribute/method access) still instantiates
    its target subtree to read the attribute — plain whole-object ``!ref:name``
    stays a marker.
    """
    # The ONE deliberate engine->YAML seam: resolve() accepts a str/Path for
    # convenience, which needs the YAML loader. Lazy import keeps the module
    # graph one-directional (loader imports engine at top level, not vice versa).
    from confluid.loader import load

    prepared = load(data, flow=False, context=context, scopes=scopes)
    ctx = context if context is not None else (prepared if isinstance(prepared, dict) else None)
    if ctx:
        ctx = expand_dotted_keys(ctx)
    _acceptable_keys_cache.clear()
    _post_init_attrs_cache.clear()
    _param_kind_cache.clear()
    # replace() (not a fresh _EngineState) deliberately leaves suppress_solidify
    # untouched — resolve() never managed that flag (it builds no objects).
    token = _ENGINE_STATE.set(replace(_ENGINE_STATE.get(), context=ctx, flow_memo={}, instance_memo={}))
    try:
        return _flow_recursive(prepared, parent_context=ctx)
    finally:
        _ENGINE_STATE.reset(token)


def _deep_flow(data: Any) -> Any:
    """Flow the top-level Fluid + any Instance objects in the tree.

    ``Lazy`` Fluids are left deferred at every level — they are
    runtime-injection points whose construction happens later (e.g.
    inside ``configure_optimizers`` once ``model.parameters()`` is
    available). Flowing them here would either fail (missing runtime
    args) or produce a partially-initialized object.
    """
    _flow = flow  # same-module; alias keeps the moved body verbatim

    def _maybe_flow(v: Any) -> Any:
        if isinstance(v, Lazy):
            return v
        if isinstance(v, Instance):
            return _flow(v)
        return v

    if isinstance(data, Lazy):
        return data
    if isinstance(data, Fluid):
        return _flow(data)
    if isinstance(data, dict):
        return {k: _maybe_flow(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_maybe_flow(item) for item in data]
    return data


_acceptable_keys_cache: Dict[str, Optional[frozenset[str]]] = {}
_post_init_attrs_cache: Dict[str, frozenset[str]] = {}
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


def _get_parent_attr_blacklist(cls: type) -> frozenset[str]:
    """Non-underscore attribute names contributed by NON-``@configurable`` ancestors.

    Returns the union, across every non-``@configurable`` class in ``cls.__mro__``
    (excluding ``cls`` and ``object``), of:

    * Names from ``__annotations__`` (e.g. ``training: bool`` annotated on
      ``torch.nn.Module``'s class body — gets set instance-side via
      ``super().__setattr__('training', True)`` which the AST scan can't see).
    * Names from ``__dict__`` whose value is a non-callable, non-property
      attribute (e.g. class-level constants like
      ``CHECKPOINT_HYPER_PARAMS_KEY = "hyper_parameters"`` on
      ``pytorch_lightning.LightningModule``).
    * Names assigned via ``self.<name> = …``, ``self.<name>: T = …``, or
      ``setattr(self, "<name>", …)`` in the class's ``__init__`` body
      (e.g. ``self.prepare_data_per_node: bool = True`` in
      ``pytorch_lightning.core.hooks.DataHooks.__init__``).

    Used by parameter-discovery walkers to subtract parent-class contributions
    from ``vars(obj)`` so the configurable surface reflects only what the
    user (and Confluid's own broadcast machinery) put there.
    """
    cache_key = f"{cls.__module__}.{cls.__qualname__}#parent_blacklist"
    if cache_key in _post_init_attrs_cache:
        return _post_init_attrs_cache[cache_key]

    blacklist: Set[str] = set()
    try:
        mro = cls.__mro__
    except AttributeError:
        _post_init_attrs_cache[cache_key] = frozenset()
        return frozenset()

    for klass in mro:
        if klass is object or klass is cls:
            continue
        if getattr(klass, "__confluid_configurable__", False):
            continue
        for name in getattr(klass, "__annotations__", {}).keys():
            if not name.startswith("_"):
                blacklist.add(name)
        for name, val in klass.__dict__.items():
            if name.startswith("_"):
                continue
            if callable(val) or isinstance(val, property):
                continue
            blacklist.add(name)
        init = klass.__dict__.get("__init__")
        if init is not None:
            blacklist.update(init_setattr_names(init))

    result = frozenset(blacklist)
    _post_init_attrs_cache[cache_key] = result
    return result


def get_configurable_attrs(obj: Any) -> frozenset[str]:
    """Return non-underscore instance attributes of ``obj`` that belong to its ``@configurable`` surface.

    Filters ``vars(obj)`` to exclude attributes contributed by non-``@configurable``
    parent classes — their class annotations (``training: bool`` on
    ``torch.nn.Module``), class-level constants
    (``CHECKPOINT_HYPER_PARAMS_KEY`` on ``pytorch_lightning.LightningModule``),
    and ``__init__``-body setattrs (``self.prepare_data_per_node: bool = True``
    on ``pytorch_lightning.core.hooks.DataHooks``). Anything still present
    after that subtraction is either a constructor parameter the user
    declared, a post-construction setattr the user did themselves, or one
    Confluid's broadcast/Enable machinery wrote on the instance.

    See [confluid/confluid/loader.py:_get_parent_attr_blacklist] for the
    blacklist construction.
    """
    cls = obj.__class__
    blacklist = _get_parent_attr_blacklist(cls)
    return frozenset(name for name in vars(obj).keys() if not name.startswith("_") and name not in blacklist)


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
    if isinstance(cls_or_name, type):
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
        init_method = getattr(target, "__init__", None)
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
    import typing

    target: Any
    if isinstance(cls_or_name, type):
        target = cls_or_name
    else:
        from confluid.registry import resolve_class

        target = resolve_class(cls_or_name)
        if target is None:
            return {}

    cache_key = f"{target.__module__}.{target.__qualname__}"
    if cache_key in _param_kind_cache:
        return _param_kind_cache[cache_key]

    kinds: Dict[str, Optional[str]] = {}
    try:
        init_method = getattr(target, "__init__", None)
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
    import typing

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
        try:
            import collections.abc as cabc
        except ImportError:  # pragma: no cover
            cabc = None  # type: ignore[assignment]
        if cabc is not None:
            if origin in (cabc.Mapping, cabc.MutableMapping):
                return "dict"
            if origin in (
                cabc.Sequence,
                cabc.MutableSequence,
                cabc.Iterable,
                cabc.Collection,
            ):
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
        """Assign ``key`` (keeping its position if present) with a scope tag."""
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
        if k in (cls_name, instance_name) and isinstance(v, dict):
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


def _flow_recursive(data: Any, parent_context: Optional[Dict[str, Any]] = None) -> Any:
    # Shared-identity memo: ensures the same raw marker (reached directly or via
    # !ref:) always flows to the same Instance/Class marker object, so a single
    # live object is instantiated downstream.
    flow_memo: Optional[Dict[int, Any]] = _ENGINE_STATE.get().flow_memo

    # 1. Plain dictionaries — pass merged context down (a grouping dict is
    #    transparent: it consumes no nesting level, and its own keys are
    #    fresh BARE entries within the subtree).
    if isinstance(data, dict):
        if parent_context:
            local_ctx = _View(parent_context)  # tags copied when parent is a _View
            for k, v in data.items():
                local_ctx.set(k, v, _KeyScope.BARE)
        else:
            local_ctx = _View(data)
        return {k: _flow_recursive(v, parent_context=local_ctx) for k, v in data.items()}

    # 2. Class/Instance from YAML tags — apply broadcasting to kwargs
    if isinstance(data, (Class, Instance)):
        if flow_memo is not None and id(data) in flow_memo:
            return flow_memo[id(data)]
        raw_id = id(data)
        target_name = (
            data.target
            if isinstance(data.target, str)
            else getattr(
                data.target,
                "__confluid_name__",
                getattr(data.target, "__name__", ""),
            )
        )
        actual_target = data.target if isinstance(data.target, type) else None
        # Always prepared (even with no parent context) so own kwargs get
        # scope tags, glob routing, and in-marker dotted-key expansion.
        merged_kwargs = _prepare_kwargs(
            target_name, data.kwargs, parent_context or {}, target=actual_target, self_obj=data
        )

        # Splice this Fluid's prepared kwargs into its slot in parent_context to
        # preserve document order for downstream broadcasts.
        self_key = None
        if parent_context:
            self_key = next((k for k, v in parent_context.items() if v is data), None)
        child_ctx = _splice_kwargs_at_slot(parent_context or {}, self_key, merged_kwargs, receiver_cls=data.target)
        # Routing entries ('**'/'*' glob blocks, STRICT sub-blocks) are
        # addressing metadata: they ride in child_ctx only, never into the
        # marker's kwargs (→ ctor / post-init / dump / resolve() output).
        resolved_kwargs = {
            k: _flow_recursive(v, parent_context=child_ctx)
            for k, v in merged_kwargs.items()
            if not (_is_glob_key(k) or merged_kwargs.scope_of(k) is _KeyScope.STRICT)
        }
        res_obj = copy(data)
        res_obj.kwargs = resolved_kwargs
        if flow_memo is not None:
            flow_memo[raw_id] = res_obj
        return res_obj

    # 3. Reference — resolve against parent context
    if isinstance(data, Reference):
        if parent_context and data.target in parent_context:
            resolved = parent_context[data.target]
            # Self-reference guard: a kwarg like ``foo: !ref:foo`` with no
            # outer ``foo`` in scope splices itself into ``parent_context``,
            # so the only ``foo`` it can resolve against is itself —
            # recursing here would stack-overflow. Fail loudly instead.
            if resolved is data:
                loc = format_yaml_loc(data)
                loc_str = f" at {loc}" if loc else ""
                raise ReferenceResolutionError(
                    f"Self-referential !ref:{data.target}{loc_str}: the only "
                    f"{data.target!r} in scope is this reference itself. "
                    f"Define a top-level {data.target!r} key (e.g. "
                    f"`{data.target}: null`), or remove the kwarg."
                )
            return _flow_recursive(resolved, parent_context=parent_context)
        # Support dotted paths and method calls (e.g., "obj.method()") via
        # the unified rich resolver (attribute access, brackets, module import).
        if parent_context:
            resolved = resolve_reference_path(data.target, parent_context)
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


def flow(obj: Any, *, solidify: bool = True, **runtime_kwargs: Any) -> Any:
    """Instantiate a deferred object (Class, Reference, string tag) into a live instance.

    Idempotent: already-live objects are returned unchanged.
    Accepts runtime kwargs that merge with stored kwargs (runtime wins).

    Within a ``materialize()`` pass, the same ``Instance`` marker (reached
    directly or via ``!ref:``) produces a single live object — subsequent
    ``flow()`` calls on the same marker return the cached instance.

    **Auto-solidification:** After instantiation, if the returned object has a
    ``solidify()`` method, it is called automatically. This enables lazy
    initialization patterns where an object defers materialization of internal
    state until after construction is complete. Domain code does not need to
    manually trigger solidification — ``flow(model)`` handles it transparently.

    Pass ``solidify=False`` to SUPPRESS that post-flow ``solidify()`` for this
    whole subtree — for static introspection that must build the object cheaply
    without paying for the expensive finalize (e.g. a model backbone). The
    suppression rides a thread-local flag, so every nested ``flow()`` inherits
    it; ``materialize(..., solidify=False)`` uses the same channel.

    This function is the DISPATCHER; each marker type's materialization lives
    in a ``_flow_*`` phase helper below.
    """
    # Solidify suppression: re-enter with the ambient flag set so the whole
    # subtree (every nested flow()) skips the expensive solidify() hook. Restored
    # afterwards so a later non-suppressed flow() in the same context is unaffected.
    if not solidify:
        token = _ENGINE_STATE.set(replace(_ENGINE_STATE.get(), suppress_solidify=True))
        try:
            return flow(obj, **runtime_kwargs)
        finally:
            _ENGINE_STATE.reset(token)

    # Idempotency — already-live objects pass through.
    if not isinstance(obj, (Fluid, str, type, dict)):
        return obj

    # An EXPLICIT ``flow(lazy)`` call builds the Lazy — even with no runtime
    # kwargs. A ``Lazy`` defers construction past the AUTO-flow walkers
    # (``_deep_flow`` and ``materialize``'s recursive descent, which both skip it
    # without calling ``flow()``); a deliberate ``flow()`` by domain code is a
    # "build this now" request. The runtime-injection case still works because
    # the missing args are passed as ``runtime_kwargs`` (e.g.
    # ``flow(self.optimizer, params=model.parameters())``); a slot needing no
    # runtime args (e.g. a deferred ``lightning`` Trainer) is built by a bare
    # ``flow(self.lightning)``. So there is NO Lazy early-return — a ``Lazy`` (a
    # ``Class`` subclass) falls through to the Class instantiation path.

    context = get_active_context()

    # Instance memoization — only within an active materialize() pass and only
    # when no runtime kwargs override the stored ones (overrides must yield a
    # fresh object).
    instance_memo = _ENGINE_STATE.get().instance_memo
    if isinstance(obj, Instance) and instance_memo is not None and not runtime_kwargs:
        cached = instance_memo.get(id(obj))
        if cached is not None:
            return cached

    if isinstance(obj, (Class, Instance)):
        return _flow_target(obj, context, instance_memo, runtime_kwargs)
    if isinstance(obj, type):
        return _flow_bare_type(obj, context, runtime_kwargs)
    if isinstance(obj, Reference):
        return _flow_reference(obj, context, runtime_kwargs)
    if isinstance(obj, Clone):
        return _flow_clone(obj, runtime_kwargs)
    if isinstance(obj, Fluid):
        return _flow_generic_fluid(obj, runtime_kwargs)
    if isinstance(obj, str) and (obj.startswith("!class:") or obj.startswith("!ref:")):
        return _flow_string_tag(obj, context, runtime_kwargs)
    return obj


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


def _flow_target(
    obj: Any,
    context: Optional[Dict[str, Any]],
    instance_memo: Optional[Dict[int, Any]],
    runtime_kwargs: Dict[str, Any],
) -> Any:
    """Materialize a ``Class`` / ``Instance`` / ``Lazy`` marker into a live object.

    The phase sequence: resolve the target callable → merge + resolve kwargs →
    split constructor kwargs from post-init attrs → construct (under the YAML
    validation mode) → memoize + stamp origin → apply post-init attrs →
    broadcast onto remaining Fluid-valued instance attrs → auto-solidify.
    """
    target = _resolve_target_callable(obj.target)

    # kwargs already contain broadcasting (merged by _flow_recursive)
    merged: dict[str, Any] = dict(obj.kwargs)
    merged.update(runtime_kwargs)

    # Glob routing keys ('*'/'**') are addressing metadata, never values.
    # The materialize path strips them before markers reach flow(); a
    # hand-built marker flowed directly still honours them here: '**'
    # contents apply to the receiver (gated like bare keys) and both feed
    # the nested-Class broadcast pool below.
    glob_pool = _pop_glob_routing(merged, target)

    # Flow Instance values (instant), keep Class/Reference deferred for
    # configurable targets (which manually flow their kwargs with runtime
    # injection — e.g. ``configure_optimizers`` flows the optimizer Class
    # with ``params=self.parameters()``). For NON-configurable targets
    # (e.g. ``pytorch_lightning.Trainer``) the constructor receives the
    # kwargs verbatim and never flow()s them, so deferred Class fluids
    # would reach attribute hooks unconverted ("'Class' object has no
    # attribute 'setup'"). For those targets, eagerly materialize nested
    # Class fluids inside list/dict kwargs.
    #
    # The nested-Class broadcast pool is the ACTIVE context (bare root keys
    # + '**' contents) — never the receiver's own kwargs (addressed keys do
    # not cascade); without a context only the marker's own glob blocks feed it.
    is_configurable_target = bool(getattr(target, "__confluid_configurable__", False))
    broadcast_ctx = _broadcast_pool(context) if context else glob_pool
    merged = {
        k: _resolve_kwarg_value(
            v, context=context, broadcast_ctx=broadcast_ctx, eager_classes=not is_configurable_target
        )
        for k, v in merged.items()
    }

    params = _ctor_params(target)
    if params is None:
        return obj  # class without a resolvable __init__ — leave the marker as-is
    ctor = {k: v for k, v in merged.items() if k in params} if params else merged

    instance = _construct(target, ctor, obj)

    # Memoize so a second flow() of the same Instance marker returns this
    # exact object (see module docstring).
    if isinstance(obj, Instance) and instance_memo is not None and not runtime_kwargs:
        instance_memo[id(obj)] = instance

    # Preserve Confluid origin for serialization round-trip. The dumper reads
    # __confluid_kwargs__ two ways: as the whole-object representation for
    # NON-configurable targets (its __confluid_class__ branch), and as the
    # per-param FALLBACK for @configurable instances whose constructor
    # transformed a param instead of storing it verbatim (eager classes).
    # This overwrites any capture the @configurable validation wrap stamped
    # during __init__ — deliberately: the resolved ctor dict is the richer
    # value (live children, Lazy markers). A capture=False class
    # (__confluid_no_capture__) skips BOTH attrs together — they exist only
    # for the dump round-trip, and __confluid_class__ without
    # __confluid_kwargs__ would break the dumper's non-configurable branch.
    if not getattr(target, "__confluid_no_capture__", False):
        try:
            instance.__confluid_class__ = target
            instance.__confluid_kwargs__ = ctor
        except (TypeError, AttributeError):
            pass  # Built-in types / __slots__-only classes may reject arbitrary attrs

    _apply_post_init_attrs(instance, target, merged, params)
    _broadcast_onto_instance(instance, params, ctor, context, broadcast_ctx)
    _maybe_solidify(instance)
    return instance


def _resolve_target_callable(target: Any) -> Any:
    """Resolve a string target to its class/callable via the registry; pass callables through."""
    if isinstance(target, str):
        resolved = resolve_class(target)
        if resolved is None:
            raise UnknownClassError(f"Cannot resolve class: {target}")
        return resolved
    return target


def _resolve_kwarg_value(
    v: Any,
    *,
    context: Optional[Dict[str, Any]],
    broadcast_ctx: Dict[str, Any],
    eager_classes: bool = False,
) -> Any:
    """Resolve ONE kwarg value for a target under materialization.

    A ``Lazy`` (a ``Class`` subclass) is a runtime-injection point: keep it
    deferred through materialization regardless of ``eager_classes`` — an
    explicit ``flow()`` by domain code builds it later; the auto-flow walkers
    here must never instantiate it. ``Instance`` flows now; a ``Class`` stub
    receives broadcasting from ``broadcast_ctx`` (with the self-broadcast and
    Fluid-through-**kwargs guards) and stays deferred unless ``eager_classes``;
    ``Reference`` flows when a context is active (unresolvable → kept deferred);
    containers recurse.
    """
    if isinstance(v, Lazy):
        return v
    if isinstance(v, Instance):
        return flow(v)
    if isinstance(v, Class):
        # Apply broadcasting: pull matching keys from full context
        report = _ENGINE_STATE.get().report
        broadcasted = dict(v.kwargs)
        acceptable = _get_acceptable_keys(v.target)
        inner_target_cls = (
            v.target if isinstance(v.target, type) else resolve_class(v.target) if isinstance(v.target, str) else None
        )
        inner_blocked = _broadcast_blocked_keys(inner_target_cls)
        for bk, bv in broadcast_ctx.items():
            if bk in broadcasted or isinstance(bv, (dict, list)):
                continue
            if inner_blocked is None or bk in inner_blocked:
                continue  # NoBroadcast param / broadcast=False class — bare keys never land
            if isinstance(bv, Fluid):
                # Fluids only broadcast through an explicit accepted
                # key — never via the **kwargs catchall (which would
                # pull the outer Class into nested targets and loop).
                if acceptable is None or bk not in acceptable:
                    continue
                # Self-broadcast guard: skip a Fluid whose target is
                # the same class we're filling. Avoids infinite
                # recursion when an inherited attribute (e.g.
                # pl.LightningModule.trainer) makes the class's own
                # name an acceptable broadcast target.
                if inner_target_cls is not None:
                    if _same_target(bv.target, inner_target_cls):
                        continue
            elif acceptable is not None and bk not in acceptable:
                continue
            logger.trace(f"broadcast: {bk!r} -> {getattr(inner_target_cls, '__name__', v.target)} (nested-class)")
            broadcasted[bk] = bv
            if report is not None:
                report.record_applied(bk, str(getattr(inner_target_cls, "__name__", v.target)), "nested-class")
                report.mark_used(bk)
        v_copy = copy(v)
        v_copy.kwargs = broadcasted
        v_copy._yaml_loc = getattr(v, "_yaml_loc", None)
        if eager_classes:
            return flow(v_copy)
        return v_copy
    if isinstance(v, Reference) and context:
        try:
            return flow(v)
        except ValueError:
            return v  # Unresolvable reference — keep deferred
    if isinstance(v, Fluid):
        return v  # Other Fluid types stay as-is
    if isinstance(v, list):
        return [
            _resolve_kwarg_value(i, context=context, broadcast_ctx=broadcast_ctx, eager_classes=eager_classes)
            for i in v
        ]
    if isinstance(v, dict):
        return {
            dk: _resolve_kwarg_value(dv, context=context, broadcast_ctx=broadcast_ctx, eager_classes=eager_classes)
            for dk, dv in v.items()
        }
    return v


def _ctor_params(target: Any) -> Optional[Set[str]]:
    """Constructor-parameter names of the target's OWN signature.

    For a class, its ``__init__`` (minus self/cls); for a plain callable (a
    builder FUNCTION like torchvision's ``fasterrcnn_resnet50_fpn``), the
    callable itself. Using ``target.__init__`` for a function resolves
    ``object.__init__`` → ``(*args, **kwargs)``, so the ctor kwarg filter would
    keep only keys named ``args``/``kwargs`` — dropping EVERY real kwarg and
    silently building the function's defaults. Returns ``None`` when a class
    has no ``__init__`` at all (caller leaves the marker unbuilt); an
    un-introspectable signature returns the empty set (caller passes every
    kwarg to the call).
    """
    try:
        if inspect.isclass(target):
            init_method = getattr(target, "__init__", None)
            if init_method is None:
                return None
            sig = inspect.signature(init_method)
        else:
            sig = inspect.signature(target)
        return {p for p in sig.parameters if p not in ("self", "cls")}
    except (ValueError, TypeError):
        return set()


def _construct(target: Any, ctor: Dict[str, Any], obj: Any) -> Any:
    """Call the target with the ctor kwargs under the YAML validation mode.

    YAML-driven materialization honours ``policy.yaml`` instead of
    ``policy.init`` so direct-Python instantiation and YAML loads can be tuned
    independently (the wrapped ``__init__`` reads ``policy.init``; we swap it
    for this single call). A constructor failure re-raises as the ORIGINAL
    exception class with a located message where ``Class(msg)`` rebuilds
    (TypeError / ValueError / …); classes that can't be rebuilt from a plain
    string (pydantic's ``ValidationError``) fall back to ``ConstructionError``,
    chaining the original via ``__cause__``.
    """
    from confluid.validation import get_policy, override_init_mode

    try:
        with override_init_mode(get_policy().yaml):
            return target(**ctor)
    except Exception as exc:
        target_name = getattr(target, "__name__", str(target))
        loc = format_yaml_loc(obj)
        location = f" at {loc}" if loc else ""
        msg = f"Failed to construct {target_name}{location}: {exc}"
        try:
            raise type(exc)(msg) from exc
        except TypeError:
            raise ConstructionError(msg) from exc


def _apply_post_init_attrs(instance: Any, target: Any, merged: Dict[str, Any], params: Set[str]) -> None:
    """Assign non-constructor kwargs as attributes on a configurable instance.

    Post-init attrs land on a live instance — if the value is still a Fluid
    marker (e.g. a nested ``!class:X`` that broadcasting carried in), it is
    materialized now: unlike constructor args, post-init attrs have no
    runtime-kwarg injection channel, so a deferred marker would just pollute a
    slot typed as the real dependency (``nn.Module.__setattr__`` would even
    reject it). EXCEPTION — a ``Lazy`` (``!lazy:``) stays deferred: it is a
    deliberate runtime-injection point the owning class flows when ready.

    Misconfiguration guard: if the slot's OWN default is a ``Lazy`` (a deferred
    runtime-injection body slot, e.g. ``self.optimizer = LazyClass(...)``), a
    supplied deferred ``Class`` (``!class:`` no-parens) would be eagerly built
    here and break the slot (an optimizer built with no ``params``). The slot's
    laziness is inherited — the supplied value is auto-deferred with a warning
    to wire it ``!lazy:``. (An ``Instance``, ``!class:Foo()``, is a deliberate
    eager request and is NOT auto-deferred.) The slot's current default is read
    from ``__dict__`` — never ``getattr``, which would execute a property
    getter (e.g. ``LightningModule.trainer`` raises when unattached).

    Assigned names are recorded on ``__confluid_extra__`` for the dumper's
    round-trip.
    """
    if not getattr(target, "__confluid_configurable__", False):
        return
    extra_keys: list[str] = []
    for k, v in merged.items():
        if _is_glob_key(k):
            continue  # glob routing metadata — never an attribute
        if params and k not in params:
            member = getattr(target, k, None)
            if isinstance(member, property) and member.fset is None:
                continue
            if getattr(member, "__confluid_ignore__", False):
                continue
            if isinstance(v, Fluid) and not isinstance(v, Lazy):
                existing = instance.__dict__.get(k)
                if type(v) is Class and isinstance(existing, Lazy):
                    logger.warning(
                        f"Config slot {k!r} on {getattr(target, '__name__', target)} received a "
                        "'!class:' value but the slot is a deferred (lazy) runtime-injection slot; "
                        "treating it as '!lazy:'. Wire it '!lazy:' in YAML to make the intent "
                        "explicit and silence this."
                    )
                    v = Lazy(v.target, **v.kwargs)
                else:
                    v = flow(v)
            setattr(instance, k, v)
            extra_keys.append(k)
    try:
        instance.__confluid_extra__ = extra_keys
    except (TypeError, AttributeError):
        pass


def _broadcast_onto_instance(
    instance: Any,
    params: Set[str],
    ctor: Dict[str, Any],
    context: Optional[Dict[str, Any]],
    broadcast_ctx: Dict[str, Any],
) -> None:
    """Apply broadcasting to any Fluid-valued instance attribute.

    Covers attrs from constructor defaults AND ``__init__``-body assignments
    (e.g. ``self.lightning = Class(L.Trainer)`` without a ``lightning`` ctor
    parameter) — this is what lets users keep ``@configurable`` signatures
    clean without sacrificing broadcast reach. A callable target may return a
    ``__dict__``-less object (a plain dict / primitive / ``__slots__``-only
    instance); such results have no attribute namespace to broadcast into and
    are skipped. A second sweep covers ctor-default params that don't appear
    on ``__dict__`` (e.g. slot descriptors that getattr resolves but vars()
    misses).
    """
    seen: set[str] = set()
    instance_vars = getattr(instance, "__dict__", None)
    for attr_name, attr_val in list(instance_vars.items()) if instance_vars else []:
        if attr_name.startswith("__confluid_"):
            continue
        if not isinstance(attr_val, Fluid):
            continue
        resolved = _resolve_kwarg_value(attr_val, context=context, broadcast_ctx=broadcast_ctx)
        if resolved is not attr_val:
            try:
                setattr(instance, attr_name, resolved)
            except (AttributeError, TypeError):
                pass  # Read-only property or __slots__
        seen.add(attr_name)

    for param_name in params - seen:
        if param_name not in ctor:
            attr_val = getattr(instance, param_name, None)
            if isinstance(attr_val, Fluid):
                resolved = _resolve_kwarg_value(attr_val, context=context, broadcast_ctx=broadcast_ctx)
                if resolved is not attr_val:
                    try:
                        setattr(instance, param_name, resolved)
                    except (AttributeError, TypeError):
                        pass  # Read-only property or __slots__


def _maybe_solidify(instance: Any) -> None:
    """Auto-solidify post-flow unless suppression is active on this thread.

    If the instance has a ``solidify()`` method, call it to finalize lazy
    internal state (e.g. a model backbone built on demand so
    ``self.parameters()`` is populated for optimizers). Skipped under
    ``flow(solidify=False)`` / ``materialize(solidify=False)``.
    """
    if not _ENGINE_STATE.get().suppress_solidify:
        solidify_method = getattr(instance, "solidify", None)
        if callable(solidify_method):
            solidify_method()


def _flow_bare_type(obj: type, context: Optional[Dict[str, Any]], runtime_kwargs: Dict[str, Any]) -> Any:
    """A bare type passed directly (e.g. ``flow(MyClass, x=1)``).

    A registry-configurable type is wrapped in an ``Instance`` marker (kwargs
    assigned post-construction so a runtime kwarg literally named ``target``
    can't collide) and materialized so broadcasting from ``context`` applies;
    a plain type is just called.
    """
    if get_registry().is_configurable(obj):
        marker = Instance(obj)
        marker.kwargs.update(runtime_kwargs)
        return materialize(marker, context=context)
    return obj(**runtime_kwargs)


def _flow_reference(obj: Any, context: Optional[Dict[str, Any]], runtime_kwargs: Dict[str, Any]) -> Any:
    """Resolve a ``Reference``: exact context key → rich path resolver → structural fallback.

    The exact whole-object key flows the referenced value (sharing identity);
    ``resolve_reference_path`` handles dotted attribute access, brackets, and
    module imports; the structural ``_resolve_ref`` is the last resort for
    nested paths. Unresolvable → typed ``ReferenceResolutionError``.
    """
    if context and obj.target in context:
        return flow(context[obj.target], **runtime_kwargs)
    if context:
        dotted = resolve_reference_path(obj.target, context)
        if dotted is not None:
            return dotted
    resolver = Resolver(context=context or {})
    resolved = resolver._resolve_ref(obj.target)
    if resolved is not None and resolved != f"!ref:{obj.target}":
        return flow(resolved, **runtime_kwargs)
    raise ReferenceResolutionError(f"Cannot resolve Reference: {obj.target}")


def _flow_clone(obj: Any, runtime_kwargs: Dict[str, Any]) -> Any:
    """Resolve a ``Clone``: flow the referenced value, deepcopy it, apply overrides."""
    from copy import deepcopy

    resolved = flow(Reference(obj.target), **runtime_kwargs)
    cloned = deepcopy(resolved)
    for k, v in obj.kwargs.items():
        setattr(cloned, k, v)
    return cloned


def _flow_generic_fluid(obj: Any, runtime_kwargs: Dict[str, Any]) -> Any:
    """Generic ``Fluid`` fallback — treat as a Class when the target resolves."""
    target = obj.target
    if isinstance(target, str):
        resolved = resolve_class(target)
        if resolved is not None:
            base_kwargs = {**obj.kwargs, **runtime_kwargs}
            return resolved(**base_kwargs)
        raise UnknownClassError(f"Class '{target}' not found in registry.")
    return flow(target, **{**obj.kwargs, **runtime_kwargs})


def _flow_string_tag(obj: str, context: Optional[Dict[str, Any]], runtime_kwargs: Dict[str, Any]) -> Any:
    """String tags (``"!class:Name"`` / ``"!ref:path"``) — resolve then flow.

    An unresolvable tag string is returned verbatim (deferred for a later
    pass), mirroring the resolver's leave-the-literal convention.
    """
    resolver = Resolver(context=context)
    resolved = resolver.resolve(obj)
    if isinstance(resolved, str) and (resolved.startswith("!class:") or resolved.startswith("!ref:")):
        return obj
    return flow(resolved, **runtime_kwargs)


def cast(obj: Any, cls: Type[T], **runtime_kwargs: Any) -> T:
    """Ensure an object is 'Solid' by flowing it if it is a Fluid.

    Acts as both a runtime materializer (flow) and a static type cast.

    Args:
        obj: The object to cast (can be a Fluid or a live instance).
        cls: The target class for type hinting.
        **runtime_kwargs: Optional kwargs to pass to flow() if obj is a Fluid.
    """
    from typing import cast as typing_cast

    return typing_cast(Any, flow(obj, **runtime_kwargs))  # type: ignore[no-any-return]
