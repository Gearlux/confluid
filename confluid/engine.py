"""The materialization engine — flow / materialize / resolve + broadcasting.

Extracted from ``fluid.py`` + ``loader.py`` (2026-07) to break their module
cycle. The layering is now one-directional:

    ``fluid`` (marker data classes, LEAF)
        ↑
    ``state`` (the ``_ENGINE_STATE`` ContextVar + the public
                ``active_context()`` / ``collect_report()``)
        ↑
    ``broadcast`` (the ONE precedence rule and its machinery: ``_KeyScope`` /
                ``_View`` / ``_prepare_kwargs`` / ``_splice_kwargs_at_slot``,
                the accept-lists, the settability predicates. Materializes
                nothing — that is what keeps this edge one-directional.)
        ↑
    ``engine`` (this module: flow/cast, materialize/resolve, _flow_recursive)
        ↑
    ``loader`` (YAML parsing: ConfluidLoader, load, includes,
                imports, scopes glue)

``state`` and ``broadcast`` came out of this module (2026-08-03): the ordered-merge
rule was implemented twice — here and, over live objects, in ``configurator`` — and
the two copies diverged four separate ways in a single day, three of them silently.
Both callers now import the one module. Names with users stay importable from here
(the caches, ``_prepare_kwargs``, …); zero-user compat re-exports are pruned as
found — sixteen so far, ledger in the AGENTS broadcast mandate and the CHANGELOG.
NEW code imports from the real home (``confluid.broadcast`` / ``confluid.state``).

No lazy seam remains. The last — ``resolve()`` body-importing ``loader.load``
for a str/Path convenience — went 2026-08-17 when the stop point moved onto
``load(until="settled")``: this module now works on PREPARED data only
(``settle`` = pass 7, ``materialize`` = passes 7–9). The one before it —
``resolver`` body-importing this module to flow a Reference's cursor — went
with the attribute references (record 19, phase 2).

(The loader's own compat re-export block was pruned 2026-08-08 — it had zero
users; only its real ``materialize`` / ``settle`` dependency remains. New code
imports engine names from ``confluid.engine`` or, better, the real home
modules ``confluid.broadcast`` / ``confluid.state``.)
"""

from copy import copy
from dataclasses import replace
from typing import Any, Dict, FrozenSet, Optional, Set, Tuple, Type

from loggair import get_logger

# Broadcasting moved to `confluid.broadcast` — the ordered-merge rule was
# implemented twice (here and over live objects in `configurator`) and the copies
# diverged; one module both import is what stops that. Names below are engine
# dependencies; the caches double as compat re-exports for test suites that
# reach them via `engine.<cache>` (zero-user re-exports are pruned as found —
# the ledger lives in the AGENTS broadcast mandate and the CHANGELOG).
from confluid.broadcast import (  # noqa: F401
    _acceptable_keys_cache,
    _broadcast_pool,
    _cache_key,
    _cascade_scalar_positions,
    _get_acceptable_keys,
    _is_glob_key,
    _KeyScope,
    _late_bare_keys_per_slot,
    _param_kind_cache,
    _pop_glob_routing,
    _prepare_kwargs,
    _receiver_cache,
    _settability_target,
    _splice_kwargs_at_slot,
    _View,
    clear_pass_caches,
    dict_at_slot_kind,
    holds_marker,
    merge_bare_pool_into_kwargs,
    refuse_if_undeclared,
    refuse_if_variadic_name,
    register_pass_cache,
    tune_marker,
)
from confluid.exceptions import (
    AmbiguousClassError,
    ConfigurationError,
    ConstructionError,
    ReferenceResolutionError,
    UnknownClassError,
)
from confluid.fluid import (
    Fluid,
    Partial,
    Reference,
    T,
    Target,
    _at_yaml_loc,
    addressed_keys_of,
    format_yaml_loc,
    is_order_resolved,
    late_bare_keys_of,
)
from confluid.introspect import init_callable, init_setattr_names, slot_names, slots
from confluid.merger import expand_dotted_keys
from confluid.partial import partial_param_names
from confluid.registry import _resolve_selector_values, get_registry, parse_target_spec, resolve_class
from confluid.report import ConfigurationReport
from confluid.resolver import Resolver, refuse_attribute_reference, resolve_reference_path

# The engine state moved to `confluid.state` so `confluid.broadcast` can read
# the ambient report without importing the engine (see that module's docstring
# for the layering). These are the engine's own dependencies — the public
# `active_context` / `collect_report` seams live in `confluid.state` and
# `confluid` top-level.
from confluid.state import _ENGINE_STATE, _active_report, _EngineState, get_active_context

logger = get_logger("confluid.engine")

# Engine-owned introspection cache: non-@configurable ancestor attributes per
# class (see _get_parent_attr_blacklist). Its OWN dict — it used to squat in a
# broadcast-owned cache under suffixed '#parent_blacklist' keys,
# violating that module's stated cache ownership. Registered with the ONE
# per-pass clear (broadcast.clear_pass_caches — fired by materialize / resolve
# / configure), so it never needs a second clear site.
_parent_blacklist_cache: Dict[Any, FrozenSet[str]] = register_pass_cache({})


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
        elif not _is_definition_shaped(v):
            report.add_config_keys((k,))


def _is_definition_shaped(value: Any) -> bool:
    """True when ``value`` is / transitively holds a Fluid marker, or is a list.

    Named for its one JOB — excluding node DEFINITIONS from unused-key tracking —
    because ``validation._contains_fluid`` shares neither its semantics (it treats
    ANY list as fluid-shaped and never recurses tuples) nor its purpose, and the
    shared name was a de-duplication trap.
    """
    if isinstance(value, Fluid) or isinstance(value, list):
        return True
    if isinstance(value, dict):
        return any(_is_definition_shaped(v) for v in value.values())
    return False


def materialize(data: Any, context: Optional[Dict[str, Any]] = None, solidify: bool = True) -> Any:
    """Passes 7–9 on PREPARED data: settle, build every ``Target``, solidify.

    The engine's entry — NOT a public name: the public spelling is ``load(x)``
    (``load`` runs the passes an input still needs and ends here). Kept as the
    engine's own function because a bare registry-configurable TYPE flowed
    directly (:func:`_flow_bare_type`) also enters here.

    Within a single materialize pass, identical raw markers (reached directly
    or via ``${ref:...}``) flow to a single marker object, which is
    materialized into a single live instance. A marker written TWICE is two
    instances — that is the one spelling for independence.

    ``solidify=False`` suppresses the post-flow ``solidify()`` hook for every
    object built in this pass (see :func:`flow`) — for static
    introspection that needs live objects but must NOT pay for the expensive
    finalize (e.g. building a model backbone). The objects are still fully
    constructed (``__init__`` only stores values per the zero-arg / lazy-init
    convention), just not solidified.
    """
    clear_pass_caches()
    # ``${...}`` interpolation — the same Resolver pass ``load()`` runs
    # (docs/interpolation.md promises it "at materialization"; measured, this
    # entry point skipped it and the literal ``${...}`` rode into values
    # silently — configure() resolves, so materialize() was the one odd path).
    # Idempotent after load(): substituted strings carry no placeholders left,
    # and a miss keeps the literal either way. Marker KWARGS interpolate too
    # (in place, text-only — see Resolver._interpolate_fluid_kwargs).
    interp_context = context if context is not None else (data if isinstance(data, dict) else None)
    resolver = Resolver(context=interp_context or {})
    resolved_data = resolver.resolve(data)
    if context is data:
        context = resolved_data  # keep the "context IS the document" identity
    elif context is not None:
        context = resolver.resolve(context)
    data = resolved_data
    if context:
        context = expand_dotted_keys(context)
    report = _active_report()  # carry the ambient report through the fresh state
    if report is not None and isinstance(context, dict):
        _register_document_keys(report, context)
    token = _ENGINE_STATE.set(
        _EngineState(
            context=context,
            flow_memo={},
            instance_memo={},
            memo_keepalive=[],
            suppress_solidify=not solidify,
            report=report,
        )
    )
    try:
        result = _flow_recursive(data, parent_context=context)
        return instantiate(result)
    finally:
        _ENGINE_STATE.reset(token)


def settle(data: Any, *, context: Optional[Dict[str, Any]] = None) -> Any:
    """Pass 7 alone — broadcast-resolve a PREPARED document to its settled marker graph.

    Like :func:`materialize`, but stops before ``instantiate``: it applies
    broadcasting and reference resolution (sharing referenced markers by
    identity via ``flow_memo`` — so a fan-out ``${ref:...}`` is one object
    reached twice), and returns the resulting ``Target`` / ``Partial`` markers
    with their broadcast siblings merged into ``.kwargs`` — WITHOUT constructing
    any live object. ``data`` is the pass-6 document (``load(x,
    until="document")``); the public spelling is ``load(x, until="settled")``,
    which is what a graph editor's YAML→graph import and ``hydraide`` read.
    ``materialize(data, solidify=False)`` is the instantiate-but-cheap
    counterpart; prefer it unless you specifically need un-built markers.

    A *dotted* reference (``${ref:a.b}`` — attribute/method access) stays a
    ``Reference`` here, exactly as a plain whole-object ``${ref:name}`` does:
    reading ``split.train`` would mean BUILDING ``split``, and this function
    constructs nothing. Use ``load()`` when you want the attribute's value —
    it still resolves it off ONE shared instance.
    """
    ctx = context if context is not None else (data if isinstance(data, dict) else None)
    if ctx:
        ctx = expand_dotted_keys(ctx)
    clear_pass_caches()
    # replace() (not a fresh _EngineState) deliberately leaves suppress_solidify
    # untouched — settle() never managed that flag (it builds no objects).
    token = _ENGINE_STATE.set(
        replace(
            _ENGINE_STATE.get(),
            context=ctx,
            flow_memo={},
            instance_memo={},
            memo_keepalive=[],
        )
    )
    try:
        return _flow_recursive(data, parent_context=ctx)
    finally:
        _ENGINE_STATE.reset(token)


def instantiate(data: Any) -> Any:
    """Build every ``Target`` in a SETTLED tree, at any depth — pass 8 (record 19, phase 3).

    The tree is what pass 7 (``_flow_recursive`` — the document ``hydraide`` emits) produced:
    every marker carries its final kwargs, every reference is resolved. So this walk is
    plain: a ``Target`` is built (its nested markers are built by ``flow`` from their own
    settled kwargs, a marker reached twice — a YAML alias, a shared reference — builds once
    through the instance memo); a ``Partial`` stays deferred (a runtime-injection point,
    built later by ``flow(partial, **runtime)`` with its nested markers); dicts and lists
    are walked recursively, so a marker inside a plain mapping or list is built too — it
    used to descend ONE level and hand back an unbuilt marker (F4).
    """
    if isinstance(data, Target):
        return data if data.partial else flow(data)
    if isinstance(data, Fluid):
        return flow(data)
    if isinstance(data, dict):
        return {k: instantiate(v) for k, v in data.items()}
    if isinstance(data, list):
        return [instantiate(item) for item in data]
    return data


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
    cache_key = _cache_key(cls)
    if cache_key in _parent_blacklist_cache:
        return _parent_blacklist_cache[cache_key]

    blacklist: Set[str] = set()
    try:
        mro = cls.__mro__
    except AttributeError:
        _parent_blacklist_cache[cache_key] = frozenset()
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
    _parent_blacklist_cache[cache_key] = result
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

    See :func:`_get_parent_attr_blacklist` (this module) for the
    blacklist construction.
    """
    cls = obj.__class__
    blacklist = _get_parent_attr_blacklist(cls)
    return frozenset(name for name in vars(obj).keys() if not name.startswith("_") and name not in blacklist)


# ---------------------------------------------------------------------------
# Marker materialization
# ---------------------------------------------------------------------------
# (The public settability predicates this section once introduced live in
# ``confluid.broadcast`` — ``accepts_key`` / ``accepts_broadcast`` /
# ``accepts_any_key`` — importable from there or from ``confluid`` top-level.)


def _flow_recursive(data: Any, parent_context: Optional[Dict[str, Any]] = None, slot_key: Optional[str] = None) -> Any:
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
        return {k: _flow_recursive(v, parent_context=local_ctx, slot_key=k) for k, v in data.items()}

    # 2. Class/Instance from YAML tags — apply broadcasting to kwargs
    if isinstance(data, Target):
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
        # Pass ANY already-resolved callable through (class OR builder function) —
        # nulling a function target here made the receiver fall back to resolving
        # the bare __name__, which for an unregistered code-built marker
        # (``PartialClass(builder_fn)``) yielded accept-EVERYTHING and no NoBroadcast
        # gates. The receiver normalizes via the one ``_settability_target``.
        actual_target = data.target if not isinstance(data.target, str) else None
        # Always prepared (even with no parent context) so own kwargs get
        # scope tags, glob routing, and in-marker dotted-key expansion.
        # The slot this marker sits in: the key the caller descended through (a kwarg name,
        # shared by every element of a list kwarg), else the entry that IS / holds the marker.
        self_key = None
        if parent_context:
            if slot_key is not None and slot_key in parent_context:
                self_key = slot_key
            else:
                self_key = next((k for k, v in parent_context.items() if v is data or holds_marker(v, data)), None)
        merged_kwargs = _prepare_kwargs(
            target_name, data.kwargs, parent_context or {}, target=actual_target, self_obj=data, self_key=self_key
        )

        # Splice this Fluid's prepared kwargs into its slot in parent_context to
        # preserve document order for downstream broadcasts.
        child_ctx = _splice_kwargs_at_slot(parent_context or {}, self_key, merged_kwargs, receiver_cls=data.target)
        # Routing entries ('**'/'*' glob blocks, STRICT sub-blocks) are
        # addressing metadata: they ride in child_ctx only, never into the
        # marker's kwargs (→ ctor / post-init / dump / settled output).
        # A slot a BLOCK delivered was arbitrated at the block's position (C2): the bare
        # keys the block out-positioned must not reach the marker nested at that slot
        # through the child view — where the slot sits at THIS marker's earlier position
        # and would lose to them again. Pop exactly those keys for that slot's descent, so
        # pass 7 settles the contest itself (record 19, phase 4 — the settled document is
        # what configure() applies, so it must already be right).
        beaten = getattr(merged_kwargs, "beaten_per_slot", None) or {}

        def _view_for(slot: str) -> Any:
            lost = beaten.get(slot)
            if not lost:
                return child_ctx
            narrowed = _View(child_ctx)
            for bk in lost:
                narrowed.pop(bk, None)
            rider = narrowed.get("**")
            if isinstance(rider, dict) and any(bk in rider for bk in lost):
                # a `'**'` rider's scalar sits at the RIDER's index in the cascade — it lost too
                narrowed.set("**", {rk: rv for rk, rv in rider.items() if rk not in lost}, narrowed.scope_of("**"))
            return narrowed

        resolved_kwargs = {
            k: _flow_recursive(v, parent_context=_view_for(k), slot_key=k)
            for k, v in merged_kwargs.items()
            if not (_is_glob_key(k) or merged_kwargs.scope_of(k) is _KeyScope.STRICT)
        }
        res_obj = copy(data)
        res_obj.kwargs = resolved_kwargs
        # Keep the ADDRESSED/BARE split the pass above computed. `resolved_kwargs`
        # is a plain dict on purpose (a `_View` would leak a dict SUBCLASS into
        # settled output, `dump()` and anything that pickles a marker), so the
        # provenance rides as a frozenset of names instead. Its one reader routes a
        # `**kwargs` constructor's arguments — see `_flow_target`.
        res_obj._addressed_keys = frozenset(
            k for k in resolved_kwargs if merged_kwargs.scope_of(k) is not _KeyScope.BARE
        )
        # `_prepare_kwargs` above walked `parent_context` in DOCUMENT ORDER and
        # unrolled this marker's own kwargs at its own slot's position, so an own
        # kwarg competing with a bare key of the same name has already been settled
        # by position — last spec wins. Record that, so the later broadcast pass
        # leaves the outcome alone instead of re-applying the bare key blind.
        res_obj._order_resolved = True
        # The verdict per dict-valued slot: which cascade keys BEAT it. Computed
        # from the slot's position for the marker's OWN dict kwargs — which is
        # correct, they sit at the marker — then OVERRIDDEN for any slot a class
        # block delivered, where the authoritative position is the BLOCK's and only
        # the scanner still has it (C2). The scanner records the complement (the
        # keys the block out-positioned), so the winners are the rest of the pool.
        late = _late_bare_keys_per_slot(child_ctx, resolved_kwargs)
        beaten = getattr(merged_kwargs, "beaten_per_slot", None)
        if beaten:
            pool = _cascade_scalar_positions(child_ctx)
            for slot, out_positioned in beaten.items():
                if slot in resolved_kwargs:
                    late[slot] = frozenset(k for k in pool if k not in out_positioned)
        res_obj._late_bare_keys = late
        if flow_memo is not None:
            flow_memo[raw_id] = res_obj
        return res_obj

    # 3. Reference — resolved against the DOCUMENT ROOT, once, here (record 19, phase 3).
    #    A `!ref:` means the root key it names: the enclosing plain mapping never shadows
    #    it (a local probe is what used to recurse forever on `r: {x: !ref:x}` — F6), and
    #    a reference to a plain VALUE is inlined now rather than left as a late-bound
    #    marker for a consumer to materialize later (F5). The result: hydraide's
    #    document is CLOSED — no `_ref_` survives — and construction never resolves a
    #    reference. Unresolvable → a located error, at `until="settled"` and `"objects"` alike.
    if isinstance(data, Reference):
        return _settle_reference(data, parent_context)

    # 4. Generic Fluid — pass through
    if isinstance(data, Fluid):
        return data

    # 5. Lists
    if isinstance(data, list):
        return [_flow_recursive(item, parent_context=parent_context, slot_key=slot_key) for item in data]

    return data


def _settle_reference(ref: Reference, parent_context: Optional[Dict[str, Any]]) -> Any:
    """Resolve a ``Reference`` in pass 7 — nearest enclosing scope first, then the document root.

    Scope: ``parent_context`` is the enclosing mapping's view (its own keys over its parents'),
    so an included FRAGMENT's internal reference finds the fragment's key, and a top-level key is
    the fallback. What a scope may NOT do is answer with the reference itself: ``r: {x: !ref:x}``
    with a root ``x`` used to find its own marker in the enclosing scope and recurse forever (F6);
    that hit is skipped and the root answers. The FIRST segment decides the rest (phase 2): an
    exact key shares the marker (identity via ``flow_memo``), a dotted/bracketed path walks dict
    keys and list indices and yields the VALUE (inlined — nothing is late-bound any more, F5),
    a walk that leaves structure is refused as an attribute reference, and anything else is an
    import path. A miss raises a located ``ReferenceResolutionError`` — so ``hydraide`` reports
    it instead of emitting `_ref_`.
    """
    root: Any = get_active_context() or {}
    scope: Any = parent_context if parent_context else root
    target = ref.target

    for ctx in (scope, root):
        if target in ctx and ctx[target] is not ref:
            return _flow_recursive(ctx[target], parent_context=parent_context)
    if target in scope or target in root:
        raise ReferenceResolutionError(
            f"Self-referential !ref:{target}{_at_yaml_loc(ref)}: the only {target!r} in scope is this "
            f"reference itself. Define a top-level {target!r} key, or remove the reference."
        )
    refuse_attribute_reference(target, root if _first_key(target) in root else scope, _at_yaml_loc(ref))
    for ctx in (scope, root):
        found = Resolver(context=ctx)._lookup_path(target, ctx)
        if found is not None and found is not ref:
            return (
                _flow_recursive(found, parent_context=parent_context)
                if isinstance(found, (Fluid, dict, list))
                else found
            )
    imported = resolve_reference_path(target, root)  # an import path (`posixpath.join`), or None
    if imported is not None:
        return imported
    raise ReferenceResolutionError(
        f"Cannot resolve !ref:{target}{_at_yaml_loc(ref)}: no key in scope, no structural path and no "
        "importable name matches it."
    )


def _first_key(target: str) -> str:
    """The first path segment of a reference target (`split` for `split.train` / `packs[0].x`)."""
    from confluid.resolver import _first_segment

    return _first_segment(target)


def flow(obj: Any, *runtime_args: Any, solidify: bool = True, **runtime_kwargs: Any) -> Any:
    """Instantiate a deferred object (a ``Target`` / ``Reference`` marker) into a live instance.

    Idempotent: already-live objects are returned unchanged.
    Accepts runtime kwargs that merge with stored kwargs (runtime wins).

    **Positional runtime args.** A marker carries KWARGS only (that is all a
    config mapping can express), but a target may take its inputs POSITIONALLY — the case
    that forced this is a variadic signature, ``DataLoaders(*loaders, path=…,
    device=…)``, where the loaders have no keyword to arrive under. So
    ``flow(node, a, b, key=value)`` calls ``target(a, b, **merged_kwargs)``: the
    positional half is runtime-only injection (never stored on a marker, never
    round-tripped by ``dump()``), exactly like the ``params=`` / ``dataset=``
    kwargs a deferred slot is flowed with. Passing them to a target that takes
    none is the target's own ``TypeError``, raised through the located
    construction wrapper.

    Within a ``materialize()`` pass, the same ``Target`` marker (reached
    directly or via ``${ref:...}``) produces a single live object — subsequent
    ``flow()`` calls on the same marker return the cached instance.

    **Auto-solidification:** if the object has a ``solidify()`` method, it is
    called — after instantiation for a marker, and on the pass-through for an
    ALREADY-LIVE object, because "``flow(model)`` handles it transparently" has
    to hold whichever way the model reached the slot. A model wired with a
    ``_target_:`` marker (or handed in live from Python) otherwise arrives unbuilt, and
    the failure lands far away: an optimizer flowed with ``params=`` gets an
    empty parameter list. ``solidify()`` is therefore expected to be IDEMPOTENT
    (build-once-and-cache), since a live object may be flowed more than once.

    Pass ``solidify=False`` to SUPPRESS that ``solidify()`` for this whole
    subtree — for static introspection that must build the object cheaply
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
            return flow(obj, *runtime_args, **runtime_kwargs)
        finally:
            _ENGINE_STATE.reset(token)

    # Idempotency — already-live objects pass through, still solidified (see the
    # docstring). Runtime args/kwargs are DROPPED here rather than raising: the
    # object is already built, and a slot flowed with `flow(slot, train, valid)`
    # must stay safe when a config wired a live object into it.
    if not isinstance(obj, (Fluid, str, type, dict)):
        _maybe_solidify(obj)
        return obj

    # An EXPLICIT ``flow(lazy)`` call builds the Partial — even with no runtime
    # kwargs. A ``Partial`` defers construction past the AUTO-flow walkers
    # (``instantiate`` and ``materialize``'s recursive descent, which both skip it
    # without calling ``flow()``); a deliberate ``flow()`` by domain code is a
    # "build this now" request. The runtime-injection case still works because
    # the missing args are passed as ``runtime_kwargs`` (e.g.
    # ``flow(self.optimizer, params=model.parameters())``); a slot needing no
    # runtime args (e.g. a deferred ``lightning`` Trainer) is built by a bare
    # ``flow(self.lightning)``. So there is NO Partial early-return — a ``Partial`` (a
    # ``Class`` subclass) falls through to the Class instantiation path.

    context = get_active_context()

    # Instance memoization — only within an active materialize() pass and only
    # when no runtime kwargs override the stored ones (overrides must yield a
    # fresh object).
    instance_memo = _ENGINE_STATE.get().instance_memo
    if (
        isinstance(obj, Target)
        and not obj.partial
        and instance_memo is not None
        and not runtime_kwargs
        and not runtime_args
    ):
        cached = instance_memo.get(id(obj))
        if cached is not None:
            return cached

    if isinstance(obj, Target):
        return _flow_target(obj, context, instance_memo, runtime_args, runtime_kwargs)
    if isinstance(obj, type):
        return _flow_bare_type(obj, context, runtime_args, runtime_kwargs)
    if isinstance(obj, Reference):
        return _flow_reference(obj, context, runtime_args, runtime_kwargs)
    if isinstance(obj, Fluid):
        return _flow_generic_fluid(obj, runtime_args, runtime_kwargs)
    if isinstance(obj, str) and (obj.startswith("!class:") or obj.startswith("!ref:")):
        return _flow_string_tag(obj, context, runtime_args, runtime_kwargs)
    return obj


def _flow_target(
    obj: Any,
    context: Optional[Dict[str, Any]],
    instance_memo: Optional[Dict[int, Any]],
    runtime_args: Tuple[Any, ...],
    runtime_kwargs: Dict[str, Any],
) -> Any:
    """Materialize a ``Class`` / ``Instance`` / ``Partial`` marker into a live object.

    The phase sequence: resolve the target callable → merge + resolve kwargs →
    split constructor kwargs from post-init attrs → construct (under the YAML
    validation mode, with any positional runtime args ahead of the kwargs) →
    memoize + stamp origin → apply post-init attrs → broadcast onto remaining
    Fluid-valued instance attrs → auto-solidify.
    """
    target = _resolve_target_callable(obj)

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
    # The marker's OWN kwargs were settled in pass 7 (record 19, phase 3): every bare
    # key that reaches this marker or the markers nested in its kwargs is already IN
    # ``obj.kwargs``, so no cascade pool feeds them here — only the marker's own glob
    # blocks (`'**'` riders written on it) still route. The ACTIVE context's bare keys
    # are needed ONLY below, for what pass 7 cannot see: markers born inside the
    # constructor (a body slot `self.optimizer = PartialClass(...)`, a ctor default).
    broadcast_ctx = glob_pool
    root_pool = _broadcast_pool(context) if context else glob_pool
    # A slot the RECEIVING class declared deferred (`Partial[T]`, or a body slot
    # holding a `PartialClass(...)`) keeps its value unbuilt, whatever the value's
    # own `partial` says. This is the receiver's declared contract — "this slot
    # needs a runtime argument I will supply" — not the parent-context guessing
    # that `Target` replaced: it is static, local to the class, and readable.
    # Without it a config writing a plain `_target_:` into an optimizer slot would
    # construct it here with no `params`, far from the config that caused it.
    partial_slots = partial_param_names(target)
    merged = {
        k: _resolve_kwarg_value(v, context=context, broadcast_ctx=broadcast_ctx, slot_is_partial=k in partial_slots)
        for k, v in merged.items()
    }

    params = _ctor_params(target)
    if params is None:
        return obj  # class without a resolvable __init__ — leave the marker as-is
    # Filter to the declared parameters. The ONLY case that passes everything is an
    # unreadable signature — an EMPTY set is a real answer ("takes nothing") and must
    # filter to nothing, or a zero-parameter constructor receives every config key.
    ctor = dict(merged) if params is _UNKNOWN_PARAMS else {k: v for k, v in merged.items() if k in params}
    if _takes_var_keyword(target):
        ctor.update(_var_keyword_extras(obj, merged, runtime_kwargs))

    instance = _construct(target, runtime_args, ctor, obj)

    # Memoize so a second flow() of the same Instance marker returns this
    # exact object (see module docstring). Positional runtime args override the
    # stored spec exactly as kwargs do, so they suppress memoization too.
    if (
        isinstance(obj, Target)
        and not obj.partial
        and instance_memo is not None
        and not runtime_kwargs
        and not runtime_args
    ):
        instance_memo[id(obj)] = instance
        # The memo keys on id(); a marker handed to PUBLIC flow() (a ctor-local
        # recipe, per the documented idiom) dies right after this call, and its
        # recycled address then reads as a memo HIT for an unrelated node —
        # measured: one maker's widget served to the next (BUGS-2026-08-13 X3).
        keepalive = _ENGINE_STATE.get().memo_keepalive
        if keepalive is not None:
            keepalive.append(obj)

    # Preserve Confluid origin for serialization round-trip. The dumper reads
    # __confluid_kwargs__ two ways: as the whole-object representation for
    # NON-configurable targets (its __confluid_class__ branch), and as the
    # per-param FALLBACK for @configurable instances whose constructor
    # transformed a param instead of storing it verbatim (eager classes).
    # This overwrites any capture the @configurable validation wrap stamped
    # during __init__ — deliberately: the resolved ctor dict is the richer
    # value (live children, Partial markers). A capture=False class
    # (__confluid_no_capture__) skips BOTH attrs together — they exist only
    # for the dump round-trip, and __confluid_class__ without
    # __confluid_kwargs__ would break the dumper's non-configurable branch.
    if not getattr(target, "__confluid_no_capture__", False):
        try:
            instance.__confluid_class__ = target
            instance.__confluid_kwargs__ = ctor
        except (TypeError, AttributeError):
            pass  # Built-in types / __slots__-only classes may reject arbitrary attrs

    _apply_post_init_attrs(instance, target, merged, ctor, obj, context, root_pool)
    _broadcast_onto_instance(instance, params, ctor, context, root_pool)
    _maybe_solidify(instance)
    return instance


def _resolve_target_callable(node: Any) -> Any:
    """Resolve a MARKER's string target to its class/callable; pass callables through.

    Takes the marker, **never the bare target string**, because the marker is what carries
    ``_yaml_loc`` — handed only the string, this reported ``Cannot resolve class:
    pkg.sources.HDF5WindwoSource`` with no file and no line, leaving the reader a
    30-frame traceback and a config tree to grep (measured 2026-08-11, on the exact defect a
    rename produces). ``ConstructionError`` a few frames later named its line the whole time.

    This is a CONSTRUCTION funnel, so it resolves ``strict=True``: an ambiguous name must
    stop the run naming its candidates, never bind whichever module happened to import
    last. It also passes the active document, which is what lets a ``@axis=$key`` selector
    read the choice from the config (``!class:FourierOp@framework=$framework``) — the
    context is available here even for a node nested inside another marker's kwargs.
    """
    target = getattr(node, "target", node)
    if not isinstance(target, str):
        return target
    try:
        resolved = resolve_class(target, strict=True, context=get_active_context())
    except AmbiguousClassError as exc:
        # Raised inside the registry, which has never seen the document — re-raise with the
        # line that wrote the ambiguous name, keeping the candidate list the registry built.
        raise AmbiguousClassError(f"{exc}{_at_yaml_loc(node)}") from exc
    if resolved is None:
        raise UnknownClassError(f"Cannot resolve class: {target}{_at_yaml_loc(node)}{_selector_detail(target)}")
    return resolved


def _selector_detail(target: str) -> str:
    """Explain a selector miss in terms of the RESOLVED filter, not the raw spelling.

    ``!class:Loss@framework=$framework`` failing is otherwise reported verbatim, which
    hides the thing the reader needs: which value ``$framework`` actually carried.
    """
    try:
        name, selectors = parse_target_spec(target)
    except ConfigurationError:
        return ""
    if not selectors:
        return ""
    resolved = _resolve_selector_values(selectors, get_active_context())
    shown = ", ".join(f"{axis}={value!r}" for axis, value in resolved.items())
    return f" — no class named {name!r} with {shown} is registered"


def _resolve_kwarg_value(
    v: Any,
    *,
    context: Optional[Dict[str, Any]],
    broadcast_ctx: Dict[str, Any],
    slot_is_partial: bool = False,
) -> Any:
    """Resolve ONE kwarg value for a target under materialization.

    A ``Partial`` (a ``Class`` subclass) is a runtime-injection point: keep it
    deferred through materialization regardless of ``eager_classes`` — an
    explicit ``flow()`` by domain code builds it later; the auto-flow walkers
    here must never instantiate it. **It still receives broadcasting**, though,
    because "do not BUILD it" and "do not CONFIGURE it" are different
    statements and only the first is what deferral means: merging broadcast
    keys into a marker's ``kwargs`` constructs nothing, which is exactly why the
    ``Class`` branch below can do it and still hand back a deferred stub. A
    ``Partial`` therefore takes that same branch (it IS a ``Class``) and only the
    terminal ``eager_classes`` flow is withheld from it.

    This was a real gap until 2026-08-03: an early ``return v`` here meant a
    ``!lazy:`` marker written in the DOCUMENT received bare keys while an
    identical one created in an ``__init__`` BODY did not — same marker type,
    two answers. A consumer declaring ``self.optimizer = PartialClass(AdamW,
    lr=1e-4)`` could not be retuned by ``lr:`` (or ``--lr``) at all: the run
    trained at the hard-coded default and reported nothing, which is the silent
    class of failure. ``Instance`` flows now; ``Reference`` flows when a context
    is active (unresolvable → kept deferred); containers recurse.
    """
    if isinstance(v, Target):
        # ONE instance per marker per pass — the invariant `!ref:` rests on. By the
        # time a kwarg reaches here its `!ref:` has already been resolved to the
        # TARGET MARKER, so two slots referencing one node arrive holding the same
        # object; if the document also flowed it at top level, that instance already
        # exists. Consult the memo before building, and record under the ORIGINAL
        # marker after — the broadcast copy below would otherwise sit between the
        # marker and its memo entry, and each slot would build its own.
        instance_memo = _ENGINE_STATE.get().instance_memo
        if instance_memo is not None and not v.partial:
            cached = instance_memo.get(id(v))
            if cached is not None:
                return cached

        # Apply broadcasting: pull matching keys from full context
        report = _ENGINE_STATE.get().report
        broadcasted = dict(v.kwargs)
        inner_target_cls = _settability_target(v.target)
        # Confluid has ONE precedence rule — document order, last spec wins — and this
        # pass is NOT where it is decided. `_flow_recursive` already merged this marker's
        # own kwargs against the surrounding bare keys BY POSITION and stamped
        # `_order_resolved`; re-applying a bare key here would overwrite that outcome
        # unconditionally, which is a specificity tier by another name.
        #
        # So the question is only "has the ordered merge run for this marker yet?".
        # It has not for a marker built in CODE (a ctor default `engine: Any =
        # Class(Engine, power=7)`, a body slot `self.optimizer = PartialClass(AdamW,
        # lr=1e-4)`) — those are defaults that never appeared in the document and so
        # never took a position; broadcasting exists to override exactly those, which is
        # why a plain `def __init__(self, power=7)` loses to a bare `power:` too.
        #
        # This used to test `_yaml_loc is not None` — a PROXY for the same question that
        # answered wrong for one shape: a marker the document only TUNED (`engine: {power:
        # 50}`) inherits the code marker's empty location, so its author-written value was
        # read as a default and any bare key beat it regardless of where either sat.
        already_ordered = is_order_resolved(v)
        label = str(getattr(inner_target_cls, "__name__", v.target))

        def _record_nested(bk: str) -> None:
            if report is not None:
                report.record_applied(bk, label, "nested-class")
                report.mark_used(bk)

        # The gates themselves (containers, NoBroadcast, the Fluid declared-key +
        # self-target guard) live in the ONE cascade function both paths call —
        # only the ordering verdict is computed here, because the ordering MODEL
        # is per-caller (see broadcast.merge_bare_pool_into_kwargs).
        merge_bare_pool_into_kwargs(
            broadcasted,
            broadcast_ctx,
            v.target,
            protected=frozenset(broadcasted) if already_ordered else frozenset(),
            on_applied=_record_nested if report is not None else None,
        )
        v_copy = copy(v)
        # The memos key on id(); a freed copy's address is reused and reads as a
        # HIT for an unrelated node. Pin it for the pass (see _EngineState).
        keepalive = _ENGINE_STATE.get().memo_keepalive
        if keepalive is not None:
            keepalive.append(v_copy)
        v_copy.kwargs = broadcasted
        v_copy._yaml_loc = getattr(v, "_yaml_loc", None)
        # `partial` is the ONLY thing that withholds construction. A Partial is
        # configured like any other marker but never built here — the point is
        # that domain code supplies the missing runtime argument later
        # (`flow(self.optimizer, params=...)`).
        if v_copy.partial or slot_is_partial:
            # A slot the receiver declared deferred keeps its marker unbuilt. The
            # value is NOT rewritten to a Partial here — the ONE promotion site is
            # the post-init guard in `_apply_post_init_attrs`, which also warns.
            return v_copy
        built = flow(v_copy)
        if instance_memo is not None:
            instance_memo[id(v)] = built
            # ``v`` may be a SHORT-LIVED marker (the slot-tune path's tune_marker
            # copy) — pin what the memo keys on, or a recycled address hands one
            # slot another slot's instance (BUGS-2026-08-13 E1: 5 distinct
            # objects for 12 tuned slots, measured).
            if keepalive is not None:
                keepalive.append(v)
        return built
    if isinstance(v, Reference) and context:
        try:
            return flow(v)
        except ValueError:
            return v  # Unresolvable reference — keep deferred
    if isinstance(v, Fluid):
        return v  # Other Fluid types stay as-is
    if isinstance(v, list):
        return [
            _resolve_kwarg_value(i, context=context, broadcast_ctx=broadcast_ctx, slot_is_partial=slot_is_partial)
            for i in v
        ]
    if isinstance(v, dict):
        return {
            dk: _resolve_kwarg_value(dv, context=context, broadcast_ctx=broadcast_ctx, slot_is_partial=slot_is_partial)
            for dk, dv in v.items()
        }
    return v


class _UnknownParams(Set[str]):
    """A set that means "the signature could not be read", not "it takes nothing".

    A plain ``set()`` cannot carry that distinction, and the two need OPPOSITE
    handling: unreadable means pass every kwarg through (best effort), while a
    genuinely empty parameter list means pass none. Subclasses ``set`` so it stays a
    valid ``Set[str]`` for every reader; callers tell it apart by IDENTITY against
    :data:`_UNKNOWN_PARAMS`, never by truthiness.
    """


#: The single instance of :class:`_UnknownParams` — compare with ``is``.
_UNKNOWN_PARAMS = _UnknownParams()


def _ctor_params(target: Any) -> Optional[Set[str]]:
    """Constructor-parameter names of the target's OWN signature.

    For a class, its ``__init__`` (minus self/cls); for a plain callable (a
    builder FUNCTION like torchvision's ``fasterrcnn_resnet50_fpn``), the
    callable itself. Using ``target.__init__`` for a function resolves
    ``object.__init__`` → ``(*args, **kwargs)``, so the ctor kwarg filter would
    keep only keys named ``args``/``kwargs`` — dropping EVERY real kwarg and
    silently building the function's defaults.

    Three outcomes, and they must stay distinguishable — conflating the last two is
    what made a zero-parameter constructor unconfigurable:

    * ``None`` — the class has no callable ``__init__``; the caller leaves the marker
      unbuilt. This looks unreachable (every class inherits ``object.__init__``) and
      is not: ``__init__ = None`` is a legal class attribute, and ``getattr`` then
      returns ``None``. Such a class cannot be constructed by anyone — ``Nulled()``
      raises ``TypeError: 'NoneType' object is not callable`` — so handing the marker
      back unbuilt is a deliberate graceful degradation. Deleting the branch as dead
      code would fall through to ``inspect.signature(None)``, which raises, yielding
      :data:`_UNKNOWN_PARAMS` and a call to ``None(**kwargs)``: the same failure with
      a worse message. (A located error might be better still than returning a
      marker; that is a behaviour change, not a cleanup, and is deliberately not made
      here.)
    * :data:`_UNKNOWN_PARAMS` — the signature could not be read, so no filtering is
      possible and the caller passes every kwarg through as a best effort.
    * a set (possibly EMPTY) — the signature WAS read. An empty one means the target
      genuinely takes no parameters, so the caller must pass none. Returning a plain
      ``set()`` for the unreadable case made those two indistinguishable, and the
      caller's ``if params else pass-everything`` fallback then fired for
      ``def __init__(self)`` — handing every config key to a constructor that accepts
      none (``TypeError: ZeroArg.__init__() got an unexpected keyword argument``).
      That shape is one the class-design convention actively encourages: a minimal
      constructor with the dependencies as ``__init__``-body slots, taken to its
      limit of no parameters at all.

    ``*args`` (VAR_POSITIONAL) and POSITIONAL_ONLY parameters are both EXCLUDED,
    for one reason: their names can never be passed by keyword, so keeping them
    would let a config key that happens to match one (``loaders`` for
    ``DataLoaders(*loaders, …)``; ``k`` for ``__init__(self, k, /)``) through the
    filter and into a ``TypeError`` from the call itself. A ``*args`` input arrives
    as one of ``flow()``'s positional runtime args instead; a positional-only
    parameter falls through to a post-init ``setattr``, which is what
    ``configure()`` has always done for it — so both paths now agree.

    A ``**kwargs`` (VAR_KEYWORD) parameter is deliberately KEPT, and the
    asymmetry with ``*args`` is load-bearing rather than an oversight: its
    presence is what makes the returned set non-empty, and a non-empty set is
    what routes every unmatched key to a post-init ``setattr`` — which IS the
    documented behaviour of a ``**kwargs`` ``@configurable`` class (every bare
    broadcast key lands as an attribute; ``docs/broadcasting.md`` → "Classes with
    ``**kwargs`` constructors", pinned by ``tests/test_broadcast_scoping.py``
    and ``examples/broadcasting.py``). Dropping it would silently redirect those
    keys into the constructor. What such a target ALSO needs — its runtime
    kwargs, which are call arguments rather than config keys — is handled by
    :func:`_takes_var_keyword` at the one call site in :func:`_flow_target`.
    """
    if init_callable(target) is None:
        return None
    try:
        # ONE enumeration, one projection. ``var_positional`` and ``positional_only``
        # are dropped for the reason above; ``var_keyword`` is KEPT, and that
        # asymmetry is load-bearing rather than an oversight.
        return slot_names(target, frozenset({"keyword", "var_keyword"}))
    except (ValueError, TypeError):
        return _UNKNOWN_PARAMS


def _takes_var_keyword(target: Any) -> bool:
    """Whether the target's own signature accepts arbitrary keywords (``**kwargs``).

    Such a signature names no parameter for any of its inputs, so the
    constructor-kwarg filter in :func:`_flow_target` drops every one of them and
    builds the target with NOTHING. Measured against a ``transformers.Trainer``
    subclass declaring ``def __init__(self, **kwargs)``: it died as *"`Trainer`
    requires either a `model` or `model_init` argument"* — a message pointing
    nowhere near confluid. :func:`_var_keyword_extras` says what to pass instead.
    """
    return any(slot.kind == "var_keyword" for slot in slots(target))


def _var_keyword_extras(
    obj: Any,
    merged: Dict[str, Any],
    runtime_kwargs: Dict[str, Any],
) -> Dict[str, Any]:
    """What a ``**kwargs`` constructor receives beyond its named parameters.

    The rule is ADDRESSING, and it is the same rule the accept-list uses (see
    ``docs/broadcasting.md`` → "Asking whether a key may land"): a key aimed AT
    this node is an argument; a key that merely cascaded past it is not.

    * **Runtime kwargs** — ``flow(node, model=…)`` — are call arguments by
      construction and always pass.
    * **Addressed config keys** — written on the marker (``!lazy:Trainer(a=1)``)
      or delivered by a block naming it — are what the author asked this node to
      be built with, so they pass too. Without this a forwarding subclass never
      sees a config key: it would land as an ATTRIBUTE on the built object and
      quietly do nothing.
    * **Bare broadcast keys** — a top-level ``name:`` cascading into every
      accepting node — do NOT. A ``**kwargs`` class has an unknowable accept-list,
      so confluid errs permissive and every bare key reaches it; feeding those to
      the constructor would turn "permissive broadcasting" into "the constructor
      is called with whatever the document happens to contain". They keep landing
      as post-init attributes, which is the documented behaviour (pinned by
      ``tests/test_broadcast_scoping.py`` and ``examples/broadcasting.py``).

    ``obj._addressed_keys`` is ``None`` for a marker that was never merged against
    a document — a hand-built one, or a direct ``flow(marker)`` — where every
    kwarg is by definition the marker's own.
    """
    addressed = addressed_keys_of(obj)
    extras = {k: v for k, v in merged.items() if not _is_glob_key(k) and (addressed is None or k in addressed)}
    extras.update({k: v for k, v in runtime_kwargs.items() if not _is_glob_key(k)})
    return extras


def _construct(target: Any, args: Tuple[Any, ...], ctor: Dict[str, Any], obj: Any) -> Any:
    """Call the target with the positional runtime args + ctor kwargs, in YAML validation mode.

    YAML-driven materialization honours ``policy.yaml`` instead of
    ``policy.init`` so direct-Python instantiation and YAML loads can be tuned
    independently (the wrapped ``__init__`` reads ``policy.init``; we swap it
    for this single call). A constructor failure re-raises as the ORIGINAL
    exception class with a located message where ``Class(msg)`` rebuilds
    (TypeError / ValueError / …); classes that can't be rebuilt from a plain
    string (pydantic's ``ValidationError``) fall back to ``ConstructionError``,
    chaining the original via ``__cause__``.

    ``args`` comes from ``flow(node, a, b)`` and is empty for every marker built
    from YAML — a tag carries kwargs only, so positional injection is a
    runtime-only channel (see :func:`flow`).
    """
    from confluid.validation import get_policy, override_init_mode

    try:
        with override_init_mode(get_policy().yaml):
            return target(*args, **ctor)
    except Exception as exc:
        target_name = getattr(target, "__name__", str(target))
        msg = f"Failed to construct {target_name}{_at_yaml_loc(obj)}: {exc}"
        try:
            raise type(exc)(msg) from exc
        except TypeError:
            raise ConstructionError(msg) from exc


def _apply_post_init_attrs(
    instance: Any,
    target: Any,
    merged: Dict[str, Any],
    ctor: Dict[str, Any],
    obj: Any = None,
    context: Optional[Dict[str, Any]] = None,
    broadcast_ctx: Optional[Dict[str, Any]] = None,
) -> None:
    """Assign kwargs the CONSTRUCTOR did not take as attributes on a configurable instance.

    The gate is ``ctor`` — what was actually passed — rather than the declared
    parameter names, so a key can never be applied twice. The two agree wherever
    the ctor dict is the name-filtered one; they diverge exactly where a
    ``**kwargs`` target took a runtime kwarg no parameter is named for (see
    :func:`_takes_var_keyword`), which must NOT then also be set as an attribute.

    Post-init attrs land on a live instance — if the value is still a Fluid
    marker (e.g. a nested ``!class:X`` that broadcasting carried in), it is
    materialized now: unlike constructor args, post-init attrs have no
    runtime-kwarg injection channel, so a deferred marker would just pollute a
    slot typed as the real dependency (``nn.Module.__setattr__`` would even
    reject it). EXCEPTION — a ``Partial`` (``!lazy:``) stays deferred: it is a
    deliberate runtime-injection point the owning class flows when ready.

    Misconfiguration guard: if the slot's OWN default is a ``Partial`` (a deferred
    runtime-injection body slot, e.g. ``self.optimizer = PartialClass(...)``), a
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
        if k not in ctor:
            # A key naming a ``*args`` parameter reaches here because the ctor filter
            # dropped it — and it must not become an attribute instead. See
            # broadcast.refuse_if_variadic_name for why (and why BARE keys are exempt).
            refuse_if_variadic_name(target, k, obj)
            # B1: an undeclared key is AUDIBLE but still applied. This branch IS the
            # post-init attribute mechanism (the docstring above), so refusing here
            # would remove documented behaviour from the commonest spelling — that
            # is the opt-in ``strict_attrs`` mark instead (TASKS.md). Silence was the
            # real defect: the same typo is reported by ``configure()`` and, until
            # 2026-08-12, set without a word here.
            _warn_undeclared(instance, target, k, obj)
            member = getattr(target, k, None)
            if isinstance(member, property) and member.fset is None:
                continue
            # A ``__slots__`` / immutable target has no ``__dict__`` and may refuse the
            # attribute outright. "Last path that fits wins": the constructor was the
            # earlier path and did not take this key, so a setattr is the only one left —
            # and if it does not fit either, say so HERE, naming the key, the target and
            # the YAML position. Letting the raw ``AttributeError`` out reports
            # ``'S' object has no attribute '__dict__'`` from a line the author never
            # wrote, which points at the engine instead of at their config.
            existing = getattr(instance, "__dict__", {}).get(k)
            if isinstance(v, Fluid) and not getattr(v, "partial", False):
                if isinstance(v, Target) and isinstance(existing, Partial):
                    logger.warning(
                        f"Config slot {k!r} on {getattr(target, '__name__', target)} received an "
                        "eager '_target_:' value but the slot is a deferred runtime-injection "
                        "slot; deferring it. Add '_partial_: true' to the marker to make the "
                        "intent explicit and silence this."
                    )
                    v = Partial(v.target, **v.kwargs)
                else:
                    v = flow(v)
            elif isinstance(v, dict) and dict_at_slot_kind(existing) == "configurable":
                # C1b (BUGS-2026-08-13): the slot holds a LIVE @configurable child —
                # walk INTO it and set its fields, exactly as configure() always has.
                # The child OBJECT survives; nothing is reassigned on the host.
                _apply_mapping_onto_live(existing, v, obj)
                logger.trace(f"slot-apply: {k!r} -> live {type(existing).__name__} updated in place")
                continue
            elif isinstance(v, dict) and dict_at_slot_kind(existing) == "opaque":
                # User decision 2026-08-13: a mapping must never silently replace a
                # live object it cannot reach into. Refuse, located.
                raise ConfigurationError(
                    f"{getattr(target, '__name__', target)} slot {k!r}{_at_yaml_loc(obj)} holds a live "
                    f"{type(existing).__name__}, which is not @configurable — a mapping cannot be "
                    f"applied into it. Register the class (or mark it @configurable), wire the slot "
                    f"from config with a _target_: marker, or replace the whole value in code."
                )
            elif isinstance(v, dict) and isinstance(existing, Target):
                # A mapping addressed at a slot that already holds a deferred marker
                # TUNES that marker — it does not replace it. Assigning the raw dict
                # was the old behaviour and it destroyed the slot silently: the
                # canonical `optimizer: {lr: 0.5}` left a plain dict where an
                # optimizer belonged, so the value the user set was the only thing
                # that survived and the target class was simply gone. Merging keeps
                # the kwargs they did NOT mention (a `weight_decay` set in code
                # stays set), which is the whole reason to spell it as a block
                # rather than restating the marker.
                tuned = tune_marker(existing, v)
                # The mapping is an ADDRESSED value like any other, so document order
                # decides it against a competing bare key — but it reaches here with no
                # position of its own (see :func:`_late_bare_keys_per_slot`). The winner
                # was worked out during the ordered merge and handed over as the set of
                # bare keys that sit LATER than this slot; apply exactly those, through
                # the normal resolver so the accept-list and NoBroadcast gates still run.
                late = late_bare_keys_of(obj).get(k, frozenset())
                pool = {bk: bv for bk, bv in (broadcast_ctx or {}).items() if bk in late}
                if pool:
                    tuned = _resolve_kwarg_value(tuned, context=context, broadcast_ctx=pool)
                # Settled either way now — a later bare key has been applied, an earlier
                # one has lost. Mark it so the broadcast pass below does not re-run the
                # contest and hand the win to whichever key it happens to visit.
                # MARKERS only: _resolve_kwarg_value BUILDS a non-partial marker, so
                # ``tuned`` may be the LIVE result — stamping that crashed __slots__
                # targets and grew a stray attribute on everything else (E2).
                if isinstance(tuned, Fluid):
                    tuned._order_resolved = True
                logger.trace(f"slot-tune: {k!r} -> {existing.target} merged {sorted(v)} into the deferred marker")
                v = tuned
            try:
                setattr(instance, k, v)
            except AttributeError as exc:
                # No path fits: the constructor did not take this key and the object
                # refuses the attribute (``__slots__`` without a matching slot, a frozen
                # dataclass, a C type). Raise WHERE the config can be seen — the raw
                # AttributeError names ``__dict__`` or a read-only field and reads as an
                # engine fault rather than a misconfigured key.
                loc = format_yaml_loc(obj)
                raise ConstructionError(
                    f"{getattr(target, '__name__', target)} cannot accept {k!r}"
                    f"{f' (set at {loc})' if loc else ''}: it is not a constructor "
                    f"parameter and the object does not allow the attribute to be set "
                    f"({exc}). Add it to the constructor, or remove it from the config."
                ) from exc
            extra_keys.append(k)
    try:
        instance.__confluid_extra__ = extra_keys
    except (TypeError, AttributeError):
        pass


def _apply_mapping_onto_live(child: Any, mapping: Dict[str, Any], node: Any) -> None:
    """Walk a config mapping INTO a live ``@configurable`` object — the load path's recurse arm.

    The load-path twin of ``configure()``'s addressed-block recursion (C1b,
    BUGS-2026-08-13): each entry dispatches on what the CHILD's slot holds, via the
    ONE ``dict_at_slot_kind`` classifier — a marker is tuned, a nested live
    configurable child recurses, plain data is assigned, and an opaque live object
    refuses with a located error. Values are treated as the sibling paths treat
    them: a non-partial ``Target`` value is built, a ``Partial`` stays deferred,
    and unknown names warn (or are refused under ``strict_attrs``) exactly as the
    host's own-kwarg path warns.

    Reads the child's slots from ``vars()`` only — no property getter ever runs.
    """
    cls = type(child)
    for mk, mv in mapping.items():
        if _is_glob_key(mk):
            continue
        refuse_if_variadic_name(cls, mk, node)
        _warn_undeclared(child, cls, mk, node)
        member = getattr(cls, mk, None)
        if isinstance(member, property) and member.fset is None:
            continue  # derived state — never a config knob
        sub_existing = getattr(child, "__dict__", {}).get(mk)
        if isinstance(mv, dict):
            kind = dict_at_slot_kind(sub_existing)
            if kind == "marker":
                assert isinstance(sub_existing, Target)  # the classifier's "marker" arm guarantees it
                setattr(child, mk, tune_marker(sub_existing, mv))
                continue
            if kind == "configurable":
                _apply_mapping_onto_live(sub_existing, mv, node)
                continue
            if kind == "opaque":
                raise ConfigurationError(
                    f"{cls.__name__} slot {mk!r}{_at_yaml_loc(node)} holds a live "
                    f"{type(sub_existing).__name__}, which is not @configurable — a mapping cannot "
                    f"be applied into it. Register the class (or mark it @configurable), wire the "
                    f"slot from config with a _target_: marker, or replace the whole value in code."
                )
            # kind == "assign" — the mapping IS the value
        val = mv
        if isinstance(val, Target) and not val.partial:
            val = flow(val)
        try:
            setattr(child, mk, val)
        except AttributeError as exc:
            raise ConstructionError(
                f"{cls.__name__} cannot accept {mk!r}{_at_yaml_loc(node)}: the object does not "
                f"allow the attribute to be set ({exc})."
            ) from exc


def _warn_undeclared(instance: Any, target: Any, key: str, node: Any) -> None:
    """Warn + record when ``key`` names nothing ``target`` declares. Applies anyway (B1).

    "Declared" is the accept-list — constructor parameters, public settable class
    attributes, and ``__init__``-body slots. A ``**kwargs`` target has no
    accept-list (``None`` = accept-everything) and is therefore never reported: it
    accepts every key by design.

    Located, so the message is actionable: the marker carries ``_yaml_loc``.
    """
    refuse_if_undeclared(target, key, node)  # a strict class closes here instead
    acceptable = _get_acceptable_keys(target)
    if acceptable is None or key in acceptable:
        return
    label = getattr(target, "__name__", str(target))
    logger.warning(
        f"{label} has no attribute {key!r}{_at_yaml_loc(node)} — set as a post-init attribute "
        f"anyway. Declare it as a constructor parameter or an __init__-body slot, or remove it."
    )
    report = _ENGINE_STATE.get().report
    if report is not None:
        report.record_failed(key, label, "unknown-attribute")


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

    A slot the class declared DEFERRED (``Partial[T]``, or a body slot holding a
    ``PartialClass(...)``) is broadcast into but never BUILT here — the same rule
    ``_flow_target`` applies to the constructor kwargs. Without it this sweep undid
    that decision moments after the constructor honoured it: the instance was built
    with the marker intact, then this loop re-resolved the attribute with no
    knowledge of the slot and constructed it anyway, so an optimizer declared
    ``Partial[Optimizer]`` reached its constructor without the ``params`` it was
    waiting for (caught downstream by a CLI's flow-mode test, not here).
    """
    seen: set[str] = set()
    partial_slots = partial_param_names(type(instance))
    instance_vars = getattr(instance, "__dict__", None)
    for attr_name, attr_val in list(instance_vars.items()) if instance_vars else []:
        if attr_name.startswith("__confluid_"):
            continue
        if not isinstance(attr_val, Fluid):
            continue
        resolved = _resolve_kwarg_value(
            attr_val, context=context, broadcast_ctx=broadcast_ctx, slot_is_partial=attr_name in partial_slots
        )
        if resolved is not attr_val:
            try:
                setattr(instance, attr_name, resolved)
            except (AttributeError, TypeError) as exc:
                # Read-only property or __slots__ — but a property setter that
                # RAISES one of these from its own validation lands here too,
                # and the broadcast result is dropped either way. Say so: the
                # sibling post-init path raises a located ConstructionError
                # for the same event, so this asymmetry must at least be
                # visible in the log.
                logger.debug(
                    f"broadcast: {attr_name!r} on {type(instance).__name__} not settable "
                    f"({exc}) — nested-broadcast result dropped"
                )
        seen.add(attr_name)

    for param_name in params - seen:
        if param_name not in ctor:
            attr_val = getattr(instance, param_name, None)
            if isinstance(attr_val, Fluid):
                resolved = _resolve_kwarg_value(
                    attr_val,
                    context=context,
                    broadcast_ctx=broadcast_ctx,
                    slot_is_partial=param_name in partial_slots,
                )
                if resolved is not attr_val:
                    try:
                        setattr(instance, param_name, resolved)
                    except (AttributeError, TypeError) as exc:
                        logger.debug(
                            f"broadcast: {param_name!r} on {type(instance).__name__} not settable "
                            f"({exc}) — nested-broadcast result dropped"
                        )


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


def _flow_bare_type(
    obj: type,
    context: Optional[Dict[str, Any]],
    runtime_args: Tuple[Any, ...],
    runtime_kwargs: Dict[str, Any],
) -> Any:
    """A bare type passed directly (e.g. ``flow(MyClass, x=1)``).

    A registry-configurable type is wrapped in an ``Instance`` marker (kwargs
    assigned post-construction so a runtime kwarg literally named ``target``
    can't collide) and materialized so broadcasting from ``context`` applies;
    a plain type is just called.
    """
    if get_registry().is_configurable(obj):
        if runtime_args:
            # A marker carries kwargs only, and the broadcast pass reads it — so
            # there is nowhere for positional args to ride. Wrap the class in a
            # `Class`/`PartialClass` marker and flow THAT if you need both.
            raise ConstructionError(
                f"flow({obj.__name__}, <positional args>) is not supported for a registry-configurable "
                "class: it materializes through a marker, which carries keyword arguments only. "
                "Pass the arguments by keyword, or flow a Class/PartialClass marker instead."
            )
        marker = Target(obj)
        marker.kwargs.update(runtime_kwargs)
        return materialize(marker, context=context)
    return obj(*runtime_args, **runtime_kwargs)


def _flow_reference(
    obj: Any,
    context: Optional[Dict[str, Any]],
    runtime_args: Tuple[Any, ...],
    runtime_kwargs: Dict[str, Any],
) -> Any:
    """Resolve a ``Reference``: exact context key → import path → structural fallback.

    The exact whole-object key flows the referenced value (sharing identity);
    ``resolve_reference_path`` REFUSES an attribute / method-call reference (record 19,
    phase 2) and resolves an import path; the structural ``_resolve_ref`` is the last
    resort for nested dict/list paths. Unresolvable → typed ``ReferenceResolutionError``.
    """
    if context and obj.target in context:
        return flow(context[obj.target], *runtime_args, **runtime_kwargs)
    if context:
        refuse_attribute_reference(obj.target, context, _at_yaml_loc(obj))
        dotted = resolve_reference_path(obj.target, context)
        if dotted is not None:
            return dotted
    resolver = Resolver(context=context or {})
    resolved = resolver._resolve_ref(obj.target)
    if resolved is not None and resolved != f"!ref:{obj.target}":
        return flow(resolved, *runtime_args, **runtime_kwargs)
    raise ReferenceResolutionError(f"Cannot resolve Reference: {obj.target}")


def _flow_generic_fluid(obj: Any, runtime_args: Tuple[Any, ...], runtime_kwargs: Dict[str, Any]) -> Any:
    """Generic ``Fluid`` fallback — treat as a Class when the target resolves."""
    target = obj.target
    if isinstance(target, str):
        # A construction funnel like _resolve_target_callable — same strictness, same
        # context (so a `@axis=$key` selector resolves against the active document), and
        # the same rule that both misses name the YAML line that wrote the name.
        try:
            resolved = resolve_class(target, strict=True, context=get_active_context())
        except AmbiguousClassError as exc:
            raise AmbiguousClassError(f"{exc}{_at_yaml_loc(obj)}") from exc
        if resolved is not None:
            base_kwargs = {**obj.kwargs, **runtime_kwargs}
            return resolved(*runtime_args, **base_kwargs)
        raise UnknownClassError(f"Class '{target}' not found in registry{_at_yaml_loc(obj)}.")
    return flow(target, *runtime_args, **{**obj.kwargs, **runtime_kwargs})


def _flow_string_tag(
    obj: str,
    context: Optional[Dict[str, Any]],
    runtime_args: Tuple[Any, ...],
    runtime_kwargs: Dict[str, Any],
) -> Any:
    """String tags (``"!class:Name"`` / ``"!ref:path"``) — resolve then flow.

    An unresolvable tag string is returned verbatim (deferred for a later
    pass), mirroring the resolver's leave-the-literal convention.
    """
    resolver = Resolver(context=context)
    resolved = resolver.resolve(obj)
    if isinstance(resolved, str) and (resolved.startswith("!class:") or resolved.startswith("!ref:")):
        return obj
    return flow(resolved, *runtime_args, **runtime_kwargs)


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
