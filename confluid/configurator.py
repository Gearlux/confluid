"""Post-construction configuration (``configure`` / ``configure_from_file``).

Applies a config document to ALREADY-CONSTRUCTED object graphs, in place —
the Post-Construction Paradigm. Matching follows confluid's ONE rule:
**flat-view, document-order, last-write-wins** (the same rule the YAML
materialization path applies via ``engine._prepare_kwargs``), scanned over
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

from pathlib import Path
from typing import Any, Dict, Optional, Set, Union

import yaml
from loggair import get_logger

from confluid.merger import expand_dotted_keys
from confluid.report import ConfigurationReport
from confluid.resolver import Resolver

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
    from confluid.engine import _active_report

    ambient = _active_report()
    report = ambient if ambient is not None else ConfigurationReport()

    if config is None:
        return report

    if isinstance(config, str) and (":" in config or "\n" in config):
        # Parse with ConfluidLoader so tag-carrying strings (e.g. "!class:Model")
        # construct Fluid markers. Plain yaml.safe_load would raise on the tags —
        # the global SafeLoader deliberately knows nothing about them.
        from confluid.loader import ConfluidLoader

        config = yaml.load(config, Loader=ConfluidLoader)

    if not isinstance(config, dict):
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

    visited: Set[int] = set()
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
    / ``import:`` directives and ``!class:`` / ``!ref:`` markers are honoured;
    the loaded config is then walked and applied to each instance exactly as
    :func:`configure` does (same matching, resolution, and per-field
    validation). This is a wrapper only — it adds no behaviour beyond loading.

    Args:
        *instances: The already-constructed objects to configure in place.
        path: Path to the YAML config file (``str`` or ``Path``).
        context: Optional explicit resolution context for ``!ref:`` / ``${...}``
            (defaults to the loaded config itself, mirroring :func:`configure`).

    Raises:
        confluid.ConfigFileNotFoundError: If ``path`` does not exist.
    """
    from confluid.loader import load_config

    return configure(*instances, config=load_config(path), context=context)


def _walk(
    obj: Any,
    view: Dict[str, Any],
    context: Dict[str, Any],
    visited: Set[int],
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

    from confluid.engine import flow

    obj = flow(obj)

    obj_id = id(obj)
    if obj_id in visited:
        return
    visited.add(obj_id)

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
    obj_dict = getattr(obj, "__dict__", None)
    if obj_dict:
        for attr_val in list(obj_dict.values()):
            if not callable(attr_val):
                _walk(attr_val, child_view, context, visited, report)


def _apply(
    obj: Any, view: Dict[str, Any], context: Dict[str, Any], visited: Set[int], report: ConfigurationReport
) -> Dict[str, Any]:
    """Configure one object from its view; return the spliced view for its subtree.

    Scans ``view`` in document order collecting assignments (last write wins),
    dict-valued child recursions, and the subtree view. Assignment values are
    resolved, string-coerced via ``parse_value``, ``Class``/``Instance``
    markers flowed, then validated + setattr'd. Scope tags (see
    ``engine._KeyScope``) gate what applies: EXACT entries are an ancestor's
    addressed values (inert here), STRICT entries are one-level routing
    blocks (matched by name or skipped), glob blocks apply gated like bare
    keys.
    """
    cls = obj.__class__
    cls_name = getattr(cls, "__confluid_name__", cls.__name__)
    instance_name = getattr(obj, "name", None)
    if not isinstance(instance_name, str):
        instance_name = None

    from confluid.engine import _broadcast_blocked_keys, _expand_block_keys, _get_acceptable_keys, _KeyScope, _scope_of

    acceptable = _get_acceptable_keys(cls)
    own_attrs = {k for k in vars(obj) if not k.startswith("_")}
    broadcast_blocked = _broadcast_blocked_keys(cls)

    def _settable(key: str) -> bool:
        member = getattr(cls, key, None)
        if member is not None and getattr(member, "__confluid_ignore__", False):
            return False
        if isinstance(member, property) and member.fset is None:
            return False
        return acceptable is None or key in acceptable or key in own_attrs

    def _is_configurable(value: Any) -> bool:
        return getattr(getattr(value, "__class__", None), "__confluid_configurable__", False)

    target_label = f"{cls_name} {instance_name!r}" if instance_name else cls_name

    # Document-order scan: assignments overwrite (last write wins); dict-valued
    # block entries addressing a configurable child become recursions; other
    # dict-valued block entries become one-level routing in the child view.
    # ``origins`` mirrors ``assignments`` with the origin of each key's LAST
    # write, so the report gets ONE applied record per attribute — the final
    # effective assignment.
    assignments: Dict[str, Any] = {}
    origins: Dict[str, str] = {}

    def _mark_used(key: str, origin: str) -> None:
        report.mark_used(f"**.{key}" if origin == "glob '**'" else f"*.{key}" if origin == "glob '*'" else key)

    recursions: Dict[str, Dict[str, Any]] = {}

    def _consume_block(
        block: Dict[str, Any], *, origin: str = "block", gated: bool = False, floating: bool = False
    ) -> None:
        """Unroll a block addressed to this object.

        ``gated=True`` for glob-delivered contents (the NoBroadcast opt-outs
        apply, like bare keys — and unmatched keys stay silent, like bare
        keys); named-block contents bypass the gate and warn on typos.
        ``floating=True`` for ``'**'`` contents: nested named dicts are
        matched-or-ignored (the riding ``'**'`` entry keeps them floating).
        """
        for bk, bv in _expand_block_keys(block).items():
            if bk == "**" and isinstance(bv, dict):
                _consume_block(bv, origin="glob '**'", gated=True, floating=True)
                continue  # the '**' entry itself is re-emitted by _spliced
            if bk == "*" and isinstance(bv, dict):
                continue  # addresses my direct children — routed by _spliced
            if isinstance(bv, dict) and bk in (cls_name, instance_name) and (floating or not gated):
                # Addressed to me again (``Cls.inst.attr`` form, or a named
                # match while floating under '**') — unroll inline, ungated.
                _consume_block(bv, origin=f"block {bk!r}")
                continue
            if isinstance(bv, dict):
                if _settable(bk):
                    if _is_configurable(getattr(obj, bk, None)):
                        recursions[bk] = bv
                    else:
                        assignments[bk] = bv  # a plain dict-typed attribute value
                        origins[bk] = origin
                    _mark_used(bk, origin)
                # else: a name-scoped block for a direct child — routed as
                # STRICT by _spliced; never a typo warning (dicts are blocks).
                continue
            if gated:
                if broadcast_blocked is not None and bk not in broadcast_blocked and _settable(bk):
                    logger.trace(f"configure: {bk!r} -> {cls_name} ({origin})")
                    assignments[bk] = bv
                    origins[bk] = origin
                    _mark_used(bk, origin)
                continue
            if _settable(bk):
                logger.trace(f"configure: {bk!r} -> {cls_name} ({origin})")
                assignments[bk] = bv
                origins[bk] = origin
                _mark_used(bk, origin)
            else:
                logger.warning(f"configure(): {cls_name} block has no attribute {bk!r} — ignored")
                report.record_failed(bk, target_label, "unknown-attribute")

    for k, v in view.items():
        scope = _scope_of(view, k)
        if scope is _KeyScope.EXACT:
            continue  # an ancestor's addressed value — ordering visibility only
        if scope is _KeyScope.ADDRESSED:
            # An attr-recursion delivered this entry to exactly this object —
            # consume it like matched-block content (assign / recurse / route).
            _consume_block({k: v}, origin="addressed")
        elif k == "**" and isinstance(v, dict):
            _consume_block(v, origin="glob '**'", gated=True, floating=True)
        elif k == "*" and isinstance(v, dict):
            # Introduced one level up — this object is the "any child" it addresses.
            _consume_block(v, origin="glob '*'", gated=True)
        elif k in (cls_name, instance_name) and isinstance(v, dict):
            report.mark_used(k)  # a named block is "used" once it matches an object
            _consume_block(v, origin=f"block {k!r}")
        elif scope is _KeyScope.STRICT:
            continue  # routing block for a sibling name — not mine
        elif (
            not isinstance(v, dict)
            and broadcast_blocked is not None  # None = class-level broadcast opt-out
            and k not in broadcast_blocked  # NoBroadcast[...] params never take bare keys
            and _settable(k)
        ):
            logger.trace(f"configure: {k!r} -> {cls_name} (bare)")
            assignments[k] = v  # broadcast — dicts at the top level are blocks for others
            origins[k] = "bare"
            report.mark_used(k)

    _assign(obj, assignments, context, report, origins, target_label)

    # Splice this object's addressed blocks into the subtree view at their
    # positions (the live-object analogue of ``_splice_kwargs_at_slot``):
    # scalars become EXACT (visible for ordering, never re-applied), nested
    # dicts STRICT (one level), glob blocks keep their reach; inherited
    # one-level routing is dropped — its level is spent at this boundary.
    child_view = _spliced(view, cls_name, instance_name)

    for attr_name, sub_block in recursions.items():
        child = getattr(obj, attr_name, None)
        if child is not None:
            _walk(child, _spliced_at(child_view, attr_name, sub_block), context, visited, report)

    return child_view


def _assign(
    obj: Any,
    assignments: Dict[str, Any],
    context: Dict[str, Any],
    report: ConfigurationReport,
    origins: Dict[str, str],
    target_label: str,
) -> None:
    """Resolve, coerce, materialize, validate, and setattr the merged assignments.

    Reports into ``report``: a validation failure records a ``"validation"``
    failed key (strict mode records then re-raises; warn mode records with
    the value still applied), and every successful setattr records ONE
    applied key with its last-write origin from ``origins`` (plus the
    eager-class staleness note when it fires).
    """
    from confluid.engine import _ctor_params
    from confluid.engine import flow as _flow
    from confluid.fluid import Class, Instance
    from confluid.resolver import parse_value
    from confluid.validation import get_policy, validate_setattr

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
        if isinstance(resolved_val, (Class, Instance)):
            resolved_val = _flow(resolved_val)
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
        report.record_applied(attr_name, target_label, origins.get(attr_name, "block"), note)


def _spliced(view: Dict[str, Any], cls_name: str, instance_name: Optional[str]) -> Dict[str, Any]:
    """Return the subtree view: routing hoisted from matched blocks, spent levels dropped.

    The live-object analogue of ``engine._splice_kwargs_at_slot``:

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
    from confluid.engine import _KeyScope, _scope_of, _View

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
                _hoist_routing_from(out, {k: v}, instance_name)
            continue
        if isinstance(v, dict) and k in block_keys and scope is not _KeyScope.EXACT:
            if scope is not _KeyScope.STRICT:
                out.set(k, v, scope)  # floating block — deeper same-name nodes rematch
            _hoist_routing_from(out, v, instance_name)
            continue
        if k == "*" and isinstance(v, dict):
            continue  # one-level routing — spent at this boundary
        if k == "**" and isinstance(v, dict):
            out.set(k, v, _KeyScope.BARE)
            if isinstance(v.get("*"), dict):
                _hoist_strict(out, "*", v["*"])  # '*' inside a floating '**' routes my children
            continue
        if scope is _KeyScope.STRICT:
            continue  # routing for a sibling name — spent
        out.set(k, v, scope)
    return out


def _hoist_routing_from(out: Any, block: Dict[str, Any], instance_name: Optional[str]) -> None:
    """Hoist a matched block's routing contents ('**'/'*'/named sub-blocks) into ``out``."""
    from confluid.engine import _expand_block_keys, _KeyScope

    for bk, bv in _expand_block_keys(block).items():
        if bk == instance_name and isinstance(bv, dict):
            _hoist_routing_from(out, bv, instance_name)  # Cls.inst.attr form unrolls inline
            continue
        if not isinstance(bv, dict):
            continue  # scalars were applied by _apply; the floating block keeps them visible
        if bk == "**":
            prev = out.get("**")
            if isinstance(prev, dict):
                bv = {**prev, **bv}
            out.set("**", bv, _KeyScope.BARE)  # keeps floating below
            if isinstance(bv.get("*"), dict):
                _hoist_strict(out, "*", bv["*"])  # '*' inside the rider routes my children
            continue
        _hoist_strict(out, bk, bv)  # '*' or a deeper path segment — one level


def _hoist_strict(out: Any, key: str, block: Dict[str, Any]) -> None:
    from confluid.engine import _KeyScope, _scope_of

    prev = out.get(key)
    if isinstance(prev, dict) and _scope_of(out, key) is _KeyScope.STRICT:
        block = {**prev, **block}
    out.set(key, block, _KeyScope.STRICT)


def _spliced_at(view: Dict[str, Any], key: str, sub_block: Dict[str, Any]) -> Dict[str, Any]:
    """Return ``view`` with ``sub_block``'s entries spliced at ``key``'s position.

    Used for child recursion: the block addressed to the child replaces the
    attr-keyed entry, so its values sit at the block's document position
    (later than earlier broadcasts → they win for the child, as authored).
    The entries are ADDRESSED — consumed by that one child, spent below it.
    """
    from confluid.engine import _KeyScope, _scope_of, _View

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
