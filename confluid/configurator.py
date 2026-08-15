"""Post-construction configuration (``configure`` / ``configure_from_file``).

Applies a config document to ALREADY-CONSTRUCTED object graphs, in place —
the Post-Construction Paradigm. Matching follows confluid's ONE rule:
**flat-view, document-order, last-write-wins** (the same rule the YAML
materialization path applies via ``broadcast._prepare_kwargs``), scanned over
live objects instead of Fluid markers:

* a ``ClassName:`` / ``<instance-name>:`` dict block is unrolled inline at
  its document position (a sub-block keyed by the instance name inside a
  class block — the ``Cls.inst.attr`` form — unrolls inline too);
* a bare non-dict key broadcasts into any object whose accept-list carries it;
* whichever assignment comes LAST in document order wins — no priority tiers;
* a dict-valued block entry addressing a configurable child recurses into it,
  with the sub-block spliced into the child's visible view at its position;
* **addressed keys are exact** (2026-07): a matched block's values configure
  that object only — they stay visible in the subtree view for ordering but
  never re-apply below. Cascade is opt-in via glob blocks: ``'**'`` applies
  its contents like bare keys to the matched object AND every descendant
  (``mid.**.lr``), ``'*'`` to the direct children only; both are gated by
  the NoBroadcast opt-outs like bare keys. Deeper named segments
  (``root.mid.lr``) are strict one-level hops, mirroring
  ``_splice_kwargs_at_slot`` / ``_prepare_kwargs`` in the engine.

The object graph is walked via ``vars(obj)`` — property getters are NEVER
executed. Unknown non-dict keys inside a block addressed to an object emit a
warning (typo protection); a present key with value ``None`` SETS ``None``
(presence is explicit in the scan, so ``dropout: null`` works).

Both entry points return a :class:`confluid.ConfigurationReport` — applied /
failed / unused override keys for the whole call (see ``confluid.report``);
inside a :func:`confluid.collect_report` block the ambient report is adopted,
so a load-then-configure pass aggregates into one report.
"""

import inspect
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple, Union

import yaml
from loggair import get_logger

from confluid.broadcast import (
    _broadcast_pool,
    _mark_used_key,
    _receiver_for_instance,
    _scan_view,
    _settability_target,
    _spliced_at_slot,
    _spliced_subtree_view,
    clear_pass_caches,
    dict_at_slot_kind,
    merge_bare_pool_into_kwargs,
    refuse_if_undeclared,
    trace_enabled,
    tune_marker,
)
from confluid.engine import _ctor_params, _maybe_solidify, flow
from confluid.exceptions import ConfigurationError
from confluid.fluid import Target
from confluid.loader import ConfluidLoader, load_config
from confluid.merger import expand_dotted_keys
from confluid.report import ConfigurationReport
from confluid.resolver import Resolver, parse_value
from confluid.state import _active_report
from confluid.validation import get_policy, validate_setattr

logger = get_logger("confluid.configurator")


def configure(*instances: Any, config: Any, context: Optional[Dict[str, Any]] = None) -> ConfigurationReport:
    """Apply configuration to one or more existing object instances.

    Recursively walks the object graph and sets attributes by matching class
    names, instance names, and broadcast keys — document order,
    last-write-wins (see the module docstring for the full matching rule).

    Returns:
        A :class:`confluid.ConfigurationReport` spanning ALL instances of the
        call: every applied override (with receiver + origin), failed keys
        (unknown block attributes, per-field validation failures), and the
        document keys that matched nothing. Inside a
        :func:`confluid.collect_report` block the ambient report is adopted
        (and returned), so a load-then-configure pass aggregates into one
        report; otherwise a fresh report is returned and its unused-keys
        DEBUG summary logged here.
    """
    ambient = _active_report()
    report = ambient if ambient is not None else ConfigurationReport()

    if config is None:
        return report

    if isinstance(config, str) and (":" in config or "\n" in config):
        # Parse with ConfluidLoader so tag-carrying strings (e.g. "!class:Model")
        # construct Fluid markers. Plain yaml.safe_load would raise on the tags —
        # the global SafeLoader deliberately knows nothing about them.
        config = yaml.load(config, Loader=ConfluidLoader)

    if not isinstance(config, dict):
        # A silent empty report here read as "configured fine" — the canonical
        # miss being configure(model, config="overrides.yaml"): a plain
        # filename fails the YAML heuristic above, stays a str, and NOTHING
        # was applied with no diagnostic anywhere.
        hint = " — for a config file path, use configure_from_file(path=...)" if isinstance(config, str) else ""
        logger.warning(f"configure(): config is a {type(config).__name__}, not a mapping; nothing applied{hint}")
        return report

    resolved_context = context if context is not None else config
    resolver = Resolver(context=resolved_context)
    config = expand_dotted_keys(resolver.resolve(config))

    # Register unused-tracking candidates: every top-level document key is an
    # override candidate here (unlike the engine path, a marker-valued key IS
    # an override — _assign flows it); glob blocks register per non-dict leaf.
    for k, v in config.items():
        if k in ("*", "**") and isinstance(v, dict):
            report.add_config_keys(f"{k}.{leaf}" for leaf, lv in v.items() if not isinstance(lv, dict))
        else:
            report.add_config_keys((k,))

    # configure() is an entry point exactly like materialize()/resolve(): a
    # class redefined since the last pass (a notebook cell re-run) must not be
    # served its previous definition's accept-list (the caches key on
    # module.qualname, which a redefinition reuses).
    clear_pass_caches()

    # id -> the OBJECT itself: recording an id PINS the object for the call.
    # A plain Set[int] recorded ids of flow() temporaries the walk then dropped;
    # gc recycled their addresses and later objects read as "already visited" —
    # whole subtrees silently unconfigured (BUGS-2026-08-13 X1: 450 of 512
    # objects missed under DEFAULT gc, measured).
    visited: Dict[int, Any] = {}
    for instance in instances:
        _walk(instance, config, resolved_context, visited, report)

    if ambient is None:
        report.log_unused()
    return report


def configure_from_file(
    *instances: Any, path: Union[str, Path], context: Optional[Dict[str, Any]] = None
) -> ConfigurationReport:
    """Load a YAML config file and apply it to existing instances in one call.

    A convenience for the ``load_config`` + :func:`configure` two-step, so

    >>> configure_from_file(trainer, path="experiment.yaml")   # doctest: +SKIP

    is equivalent to ``configure(trainer, config=load_config("experiment.yaml"))``.
    The file is read via :func:`confluid.load_config`, so recursive ``include:``
    / ``import:`` directives and ``_target_`` / reference markers are honoured;
    the loaded config is then walked and applied to each instance exactly as
    :func:`configure` does (same matching, resolution, and per-field
    validation). This is a wrapper only — it adds no behaviour beyond loading.

    Args:
        *instances: The already-constructed objects to configure in place.
        path: Path to the YAML config file (``str`` or ``Path``).
        context: Optional explicit resolution context for references / ``${...}``
            (defaults to the loaded config itself, mirroring :func:`configure`).

    Raises:
        confluid.ConfigFileNotFoundError: If ``path`` does not exist.
    """
    return configure(*instances, config=load_config(path), context=context)


def _walk(
    obj: Any,
    view: Dict[str, Any],
    context: Dict[str, Any],
    visited: Dict[int, Any],
    report: ConfigurationReport,
) -> None:
    """Traverse the object graph, configuring each configurable object from its view.

    ``view`` is the object's *visible* config — the document with every
    ancestor's addressed blocks spliced in at their positions (the live-object
    mirror of the loader's flat-view context propagation). Recursion follows
    ``vars(obj)`` (instance attributes only): property getters are never
    executed, and derived/property-held state is by mandate recomputed, never
    configured.
    """
    if obj is None:
        return

    if isinstance(obj, Target):
        # A marker slot is NOT walked into, and is NOT tuned here either — its OWNER
        # tunes it (see ``_apply``), because only the owner's scan knows where each of
        # its blocks sat relative to the bare keys. Flowing it would build the target
        # early (an optimizer with no ``params``) and configure an object that is never
        # written back to the attribute, discarding every key applied to it.
        #
        # ``Target``, not ``Partial``: the hazard the paragraph above describes is not
        # about deferral, it is about the marker being the thing the attribute HOLDS.
        # For a plain ``self.opt = Target(Opt)`` the next line used to commit exactly
        # that — flow a temporary, configure it, discard it, and record the key as
        # applied, so a later ``flow(obj.opt)`` built with the defaults (C4).
        # ``Partial`` IS a ``Target``, so deferred slots are unaffected; ``Reference``
        # and ``Clone`` are NOT, so they stay on the flow path and keep raising rather
        # than degrading to a silent no-op.
        obj_id = id(obj)
        if obj_id in visited:
            return
        visited[obj_id] = obj  # the value IS the pin — see the note at the call site
        # A marker's kwargs can hold LIVE objects (``Target(Stage, dep=widget)``), and
        # those are part of the graph: a bare key must still reach them. Flowing the
        # marker used to reach them as a side effect, so NOT walking here would trade
        # one silent skip for another — measured against
        # ``test_memo_pinning.py::test_configure_reaches_every_object_…``, which went
        # from 0 to 64 missed widgets.
        for kwarg_value in list(obj.kwargs.values()):
            _walk(kwarg_value, view, context, visited, report)
        return

    # Materialize marker-valued attrs, but with solidify SUPPRESSED: since
    # flow() finalizes live objects too (architecture record 2), an unsuppressed
    # call here fired every solidify() BEFORE this pass applied its values —
    # derived state was built from the PRE-configure config and, solidify being
    # idempotent-by-contract, never rebuilt. The hook is re-fired post-order
    # below, after this object AND its subtree carry their new values — the
    # same point in an object's life the load path fires it (children final,
    # own config final, then finalize).
    obj = flow(obj, solidify=False)

    obj_id = id(obj)
    if obj_id in visited:
        return
    visited[obj_id] = obj  # the value IS the pin — see the note at the call site

    if isinstance(obj, (list, tuple)):
        for item in obj:
            _walk(item, view, context, visited, report)
        return

    if isinstance(obj, dict):
        for v in obj.values():
            _walk(v, view, context, visited, report)
        return

    child_view = view
    if getattr(obj.__class__, "__confluid_configurable__", False):
        child_view = _apply(obj, view, context, visited, report)

    # Recurse into instance attributes only (vars, not dir) — no getters fire.
    # Scalars / __slots__ objects carry no __dict__ and simply end the walk.
    #
    # ROUTINES and CLASSES are skipped, not everything CALLABLE: an op and a
    # framework module define ``__call__``, so the old ``callable()`` filter
    # skipped every one of them — the load path broadcasts into the same class
    # fine (C3). Such a child was usually still reached BY ACCIDENT, through the
    # ctor-kwargs capture dict sitting in ``__dict__``, which is why the defect
    # surfaced only for ``capture=False`` or for a constructor that stores
    # something other than what it captured (there the discarded object was
    # configured and the key reported applied).
    #
    # ``isclass`` is not decoration: a class object's ``__dict__`` is a TRUTHY
    # mappingproxy of its own attributes, so recursing into one would walk class
    # internals and could write a config key onto the CLASS.
    obj_dict = getattr(obj, "__dict__", None)
    if obj_dict:
        for attr_val in list(obj_dict.values()):
            if not (inspect.isroutine(attr_val) or inspect.isclass(attr_val)):
                _walk(attr_val, child_view, context, visited, report)

    # Post-order finalize: the suppressed solidify from the flow() above is
    # re-fired now that the configuration has been applied to this object and
    # its whole subtree. First visit only (the visited check above) — the hook
    # is idempotent by contract, but the ordering promise is what matters here.
    _maybe_solidify(obj)


def _tune_deferred(
    marker: Any, view: Dict[str, Any], report: ConfigurationReport, beaten: FrozenSet[str] = frozenset()
) -> None:
    """Merge the bare keys a deferred slot did NOT already beat into its kwargs.

    The live-object analogue of the engine's nested-``Class`` broadcast. Two rules
    carry over unchanged and both are load-bearing:

    * the target's accept-list gates what may land (a bare key the class never
      declared is not silently attached), as do the NoBroadcast opt-outs;
    * ``beaten`` — the keys a block addressed at this slot out-positioned, computed
      by the caller's scan and passed in — are skipped, because document order
      already settled them. Everything else is a bare key written LATER than the
      block, so it wins. It is a PARAMETER rather than marker state on purpose: a
      verdict about one document must not survive into the next ``configure()``.

    A kwarg the marker already carries and that nothing beat is left alone only when
    the bare key lost; otherwise last-spec-wins applies and the bare key overwrites.
    """
    target_cls = _settability_target(marker.target)
    if target_cls is None:
        # KEPT difference with the engine cascade (which merges accept-everything
        # for an unresolvable target, serving resolve()-introspection of free
        # inputs): a configure-path slot whose target this process cannot even
        # resolve can never be flowed by it either — tuning would be noise.
        return
    # ``_broadcast_pool`` unrolls a ``'**'`` rider's scalars into the pool
    # (tagged BARE — rider contents cascade by definition), exactly as the
    # engine's nested-marker loop does. Passing the RAW view here was the
    # D5-mirror gap: rider contents sat under the ``'**'`` dict key, which
    # the cascade's container gate skips, so ``'**.lr': 0.01`` tuned a
    # deferred slot under load() and was silently ignored under configure().
    merge_bare_pool_into_kwargs(
        marker.kwargs,
        _broadcast_pool(view),
        target_cls,
        protected=beaten,
        on_applied=report.mark_used,
        origin="deferred slot",
    )


class _LiveSink:
    """The configure-path sink: scanner decisions become assignments/recursions/tunes.

    An effect writer only — every gate already ran in ``_scan_view`` against
    the receiver ``_receiver_for_instance`` built for this object. What this
    sink owns is the LIVE three-way dispatch a marker path cannot have: a dict
    at a settable key tunes a deferred ``Class`` slot (recording which bare
    keys its block out-positioned), recurses into a live configurable child,
    or lands as a plain dict attribute. Warnings keep originating from THIS
    module's logger (the monkeypatch target tests rely on).
    """

    __slots__ = (
        "obj",
        "cls_name",
        "target_label",
        "report",
        "assignments",
        "origins",
        "contest",
        "recursions",
        "beaten_per_slot",
    )

    def __init__(self, obj: Any, cls_name: str, target_label: str, report: ConfigurationReport) -> None:
        self.obj = obj
        self.cls_name = cls_name
        self.target_label = target_label
        self.report = report
        # ``origins`` mirrors ``assignments`` with each key's LAST write, so the
        # report gets ONE applied record per attribute — the final assignment.
        self.assignments: Dict[str, Any] = {}
        self.origins: Dict[str, str] = {}
        # The marker path's ``_MergeSink.contest`` twin — raw ``(origin, value,
        # pos)`` per key, rendered by ``record_applied`` only where contested.
        self.contest: Dict[str, List[Tuple[str, Any, int]]] = {}
        self.recursions: Dict[str, Dict[str, Any]] = {}
        # attr name -> the bare keys a block addressed at that DEFERRED slot
        # out-positioned. Call-scoped by construction: lives and dies with this
        # scan (a verdict about one document must not survive into the next).
        self.beaten_per_slot: Dict[str, FrozenSet[str]] = {}

    def _mark_used(self, key: str, origin: str) -> None:
        self.report.mark_used(_mark_used_key(key, origin))

    def apply(self, key: str, value: Any, origin: str, scope: Any, own: bool, gated: bool, pos: int) -> None:
        if trace_enabled(logger):  # per-KEY site — see broadcast's log-gate block
            logger.trace(f"configure: {key!r} -> {self.cls_name} ({origin})")
        self.assignments[key] = value
        self.origins[key] = origin
        self.contest.setdefault(key, []).append((origin, value, pos))
        self._mark_used(key, origin)

    def dict_at_slot(self, key: str, block: Dict[str, Any], origin: str, bare_before: FrozenSet[str]) -> None:
        # The ONE dict-at-slot dispatch (broadcast.dict_at_slot_kind) — value-state,
        # decided by what the slot HOLDS, identical on both paths (C1/C1b,
        # BUGS-2026-08-13). Reads ``__dict__`` only: no property getter ever runs.
        existing = self.obj.__dict__.get(key)
        kind = dict_at_slot_kind(existing)
        if kind == "marker":
            # A deferred marker slot (``self.optimizer = PartialClass(...)``) —
            # TUNE the marker (the engine's rule for the identical spelling),
            # never recurse into it or replace it with the raw dict. The bare
            # keys this block out-positioned ride along for _tune_deferred.
            tuned = tune_marker(existing, block)
            self.beaten_per_slot[key] = bare_before
            self.assignments[key] = tuned
            self.origins[key] = origin
        elif kind == "configurable":
            self.recursions[key] = block  # a live configurable child — recurse after the scan
        elif kind == "opaque":
            # User decision 2026-08-13: never silently replace a live object with a dict.
            raise ConfigurationError(
                f"configure(): {self.cls_name} slot {key!r} holds a live "
                f"{type(existing).__name__}, which is not @configurable — a mapping cannot be "
                f"applied into it. Register the class (or mark it @configurable), or replace "
                f"the whole value in code."
            )
        else:
            self.assignments[key] = block  # a plain dict-typed attribute value
            self.origins[key] = origin
        self._mark_used(key, origin)

    def route(self, key: str, block: Dict[str, Any]) -> None:
        # Deliberately a no-op — the live path derives its subtree routing in
        # `_spliced_subtree_view`, which needs the WHOLE view's scope tags (an
        # ADDRESSED entry's spent scalars, floating-block rematch), not just
        # this scan's route emissions. Phase B weighed consuming these
        # emissions and kept the re-derivation: the splice pair is the one
        # sanctioned duality (docs/architecture.md record 8).
        pass

    def unknown(self, key: str, value: Any, *, origin: str) -> None:
        # The mark binds BOTH paths, or it means two different things.
        refuse_if_undeclared(self.obj, key)
        logger.warning(f"configure(): {self.cls_name} block has no attribute {key!r} — ignored")
        self.report.record_failed(key, self.target_label, "unknown-attribute")

    def matched(self, name: str) -> None:
        self.report.mark_used(name)  # a named block is "used" once it matches an object


def _apply(
    obj: Any, view: Dict[str, Any], context: Dict[str, Any], visited: Dict[int, Any], report: ConfigurationReport
) -> Dict[str, Any]:
    """Configure one object from its view; return the spliced view for its subtree.

    Scans ``view`` in document order collecting assignments (last write wins),
    dict-valued child recursions, and the subtree view. Assignment values are
    resolved, string-coerced via ``parse_value``, ``Class``/``Instance``
    markers flowed, then validated + setattr'd. Scope tags (see
    ``broadcast._KeyScope``) gate what applies: EXACT entries are an ancestor's
    addressed values (inert here), STRICT entries are one-level routing
    blocks (matched by name or skipped), glob blocks apply gated like bare
    keys.
    """
    receiver = _receiver_for_instance(obj)
    name = receiver.instance_name
    target_label = f"{receiver.cls_name} {name!r}" if name else receiver.cls_name

    sink = _LiveSink(obj, receiver.cls_name, target_label, report)
    _scan_view(view, receiver, sink)

    _assign(obj, sink.assignments, context, report, sink.origins, target_label, sink.contest)

    # Deferred slots are tuned by their OWNER, here, because only this scan knows where
    # each block sat relative to the bare keys. ``_walk`` deliberately does not touch a
    # Partial. Every deferred slot is visited — not only those a block addressed — since a
    # bare key with nothing competing must still reach one.
    for attr_name, slot in list(vars(obj).items()):
        if isinstance(slot, Target):
            _tune_deferred(slot, view, report, beaten=sink.beaten_per_slot.get(attr_name, frozenset()))

    # Splice this object's addressed blocks into the subtree view at their
    # positions (the live-object analogue of ``_splice_kwargs_at_slot``):
    # scalars become EXACT (visible for ordering, never re-applied), nested
    # dicts STRICT (one level), glob blocks keep their reach; inherited
    # one-level routing is dropped — its level is spent at this boundary.
    child_view = _spliced_subtree_view(view, receiver.cls_name, receiver.instance_name)

    for attr_name, sub_block in sink.recursions.items():
        child = getattr(obj, attr_name, None)
        if child is not None:
            _walk(child, _spliced_at_slot(child_view, attr_name, sub_block), context, visited, report)

    return child_view


def _assign(
    obj: Any,
    assignments: Dict[str, Any],
    context: Dict[str, Any],
    report: ConfigurationReport,
    origins: Dict[str, str],
    target_label: str,
    contest: Optional[Dict[str, List[Tuple[str, Any, int]]]] = None,
) -> None:
    """Resolve, coerce, materialize, validate, and setattr the merged assignments.

    Reports into ``report``: a validation failure records a ``"validation"``
    failed key (strict mode records then re-raises; warn mode records with
    the value still applied), and every successful setattr records ONE
    applied key with its last-write origin from ``origins`` (plus the
    eager-class staleness note when it fires).
    """
    cls = obj.__class__
    resolver = Resolver(context=context)

    # Staleness guard for @configurable(eager=True) classes: their __init__
    # does real work FROM its params, and a post-construction setattr of a
    # ctor-param attribute cannot re-run it. Body attributes stay silent —
    # they are freely reconfigurable by design.
    eager_params: Set[str] = set()
    if getattr(cls, "__confluid_eager__", False):
        eager_params = _ctor_params(cls) or set()

    for attr_name, val in assignments.items():
        note: Optional[str] = None
        if attr_name in eager_params:
            cls_label = getattr(cls, "__confluid_name__", cls.__name__)
            note = "eager-class constructor param — __init__ work not re-run"
            logger.warning(
                f"configure(): setting constructor param {attr_name!r} on eager class {cls_label} — "
                f"__init__ work will NOT re-run; derived state may be stale"
            )
        resolved_val = resolver.resolve(val)
        if isinstance(resolved_val, str):
            resolved_val = parse_value(resolved_val)
        # Materialize class markers (e.g. a "!class:Model(...)" string value
        # resolved to an Instance/Class Fluid) into live instances before setattr.
        # A ``Partial`` is EXCLUDED, exactly as it is in ``engine._apply_post_init_attrs``:
        # it is a deliberate runtime-injection point the owning class flows when it has
        # the missing argument, so building it here produces the wrong object (an
        # optimizer with no ``params``) and destroys the slot. `Partial` subclasses `Class`,
        # so the isinstance test above caught it and configure() ALONE built it eagerly —
        # a straight divergence from the load path for the identical config.
        if isinstance(resolved_val, Target) and not resolved_val.partial:
            resolved_val = flow(resolved_val)
        # Post-construction overrides honour the same per-field schema as the
        # constructor — re-uses ``policy.init`` because configure() is the
        # moral equivalent of "instantiate this attribute with this value",
        # just performed after the parent object exists.
        try:
            detail = validate_setattr(cls, attr_name, resolved_val, get_policy().init)
        except Exception as exc:  # strict mode — record, then let it propagate
            report.record_failed(attr_name, target_label, "validation", str(exc))
            raise
        if detail is not None:  # warn mode — recorded, value still applied below
            report.record_failed(attr_name, target_label, "validation", detail)
        setattr(obj, attr_name, resolved_val)
        report.record_applied(
            attr_name,
            target_label,
            origins.get(attr_name, "block"),
            note,
            candidates=(contest or {}).get(attr_name, ()),
        )
