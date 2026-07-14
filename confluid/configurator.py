"""Post-construction configuration (``configure`` / ``configure_from_file``).

Applies a config document to ALREADY-CONSTRUCTED object graphs, in place —
the Post-Construction Paradigm. Matching follows confluid's ONE rule:
**flat-view, document-order, last-write-wins** (the same rule the YAML
materialization path applies via ``loader._prepare_kwargs``), scanned over
live objects instead of Fluid markers:

* a ``ClassName:`` / ``<instance-name>:`` dict block is unrolled inline at
  its document position (a sub-block keyed by the instance name inside a
  class block — the ``Cls.inst.attr`` form — unrolls inline too);
* a bare non-dict key broadcasts into any object whose accept-list carries it;
* whichever assignment comes LAST in document order wins — no priority tiers;
* a dict-valued block entry addressing a configurable child recurses into it,
  with the sub-block spliced into the child's visible view at its position;
* block contents become ambient context for the object's subtree, mirroring
  ``_splice_kwargs_at_slot`` in the loader.

The object graph is walked via ``vars(obj)`` — property getters are NEVER
executed. Unknown non-dict keys inside a block addressed to an object emit a
warning (typo protection); a present key with value ``None`` SETS ``None``
(presence is explicit in the scan, so ``dropout: null`` works).
"""

from pathlib import Path
from typing import Any, Dict, Optional, Set, Union

import yaml
from loggair import get_logger

from confluid.merger import expand_dotted_keys
from confluid.resolver import Resolver

logger = get_logger("confluid.configurator")


def configure(*instances: Any, config: Any, context: Optional[Dict[str, Any]] = None) -> None:
    """Apply configuration to one or more existing object instances.

    Recursively walks the object graph and sets attributes by matching class
    names, instance names, and broadcast keys — document order,
    last-write-wins (see the module docstring for the full matching rule).
    """
    if config is None:
        return

    if isinstance(config, str) and (":" in config or "\n" in config):
        # Parse with ConfluidLoader so tag-carrying strings (e.g. "!class:Model")
        # construct Fluid markers. Plain yaml.safe_load would raise on the tags —
        # the global SafeLoader deliberately knows nothing about them.
        from confluid.loader import ConfluidLoader

        config = yaml.load(config, Loader=ConfluidLoader)

    if not isinstance(config, dict):
        return

    resolved_context = context if context is not None else config
    resolver = Resolver(context=resolved_context)
    config = expand_dotted_keys(resolver.resolve(config))

    visited: Set[int] = set()
    for instance in instances:
        _walk(instance, config, resolved_context, visited)


def configure_from_file(*instances: Any, path: Union[str, Path], context: Optional[Dict[str, Any]] = None) -> None:
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

    configure(*instances, config=load_config(path), context=context)


def _walk(
    obj: Any,
    view: Dict[str, Any],
    context: Dict[str, Any],
    visited: Set[int],
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
            _walk(item, view, context, visited)
        return

    if isinstance(obj, dict):
        for v in obj.values():
            _walk(v, view, context, visited)
        return

    child_view = view
    if getattr(obj.__class__, "__confluid_configurable__", False):
        child_view = _apply(obj, view, context, visited)

    # Recurse into instance attributes only (vars, not dir) — no getters fire.
    # Scalars / __slots__ objects carry no __dict__ and simply end the walk.
    obj_dict = getattr(obj, "__dict__", None)
    if obj_dict:
        for attr_val in list(obj_dict.values()):
            if not callable(attr_val):
                _walk(attr_val, child_view, context, visited)


def _apply(obj: Any, view: Dict[str, Any], context: Dict[str, Any], visited: Set[int]) -> Dict[str, Any]:
    """Configure one object from its view; return the spliced view for its subtree.

    Scans ``view`` in document order collecting assignments (last write wins),
    dict-valued child recursions, and the subtree view. Assignment values are
    resolved, string-coerced via ``parse_value``, ``Class``/``Instance``
    markers flowed, then validated + setattr'd.
    """
    cls = obj.__class__
    cls_name = getattr(cls, "__confluid_name__", cls.__name__)
    instance_name = getattr(obj, "name", None)
    if not isinstance(instance_name, str):
        instance_name = None

    from confluid.engine import _broadcast_blocked_keys, _get_acceptable_keys

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

    # Document-order scan: assignments overwrite (last write wins); dict-valued
    # block entries addressing a configurable child become recursions; anything
    # else dict-valued travels ambiently in the child view.
    assignments: Dict[str, Any] = {}
    recursions: Dict[str, Dict[str, Any]] = {}

    def _consume_block(block: Dict[str, Any]) -> None:
        for bk, bv in block.items():
            if bk == instance_name and isinstance(bv, dict):
                # ``Cls.inst.attr`` form — the instance-named sub-block nests
                # inside the class block; unroll it inline (later → wins).
                _consume_block(bv)
                continue
            if isinstance(bv, dict):
                if _settable(bk):
                    if _is_configurable(getattr(obj, bk, None)):
                        recursions[bk] = bv
                    else:
                        assignments[bk] = bv  # a plain dict-typed attribute value
                # else: a name-scoped block for a descendant — travels via the
                # spliced child view; never a typo warning (dicts are blocks).
                continue
            if _settable(bk):
                logger.trace(f"configure: {bk!r} -> {cls_name} (block)")
                assignments[bk] = bv
            else:
                logger.warning(f"configure(): {cls_name} block has no attribute {bk!r} — ignored")

    for k, v in view.items():
        if k in (cls_name, instance_name) and isinstance(v, dict):
            _consume_block(v)
        elif (
            not isinstance(v, dict)
            and broadcast_blocked is not None  # None = class-level broadcast opt-out
            and k not in broadcast_blocked  # NoBroadcast[...] params never take bare keys
            and _settable(k)
        ):
            logger.trace(f"configure: {k!r} -> {cls_name} (bare)")
            assignments[k] = v  # broadcast — dicts at the top level are blocks for others

    _assign(obj, assignments, context)

    # Splice this object's addressed blocks into the subtree view at their
    # positions (the live-object analogue of ``_splice_kwargs_at_slot``), so
    # descendants see block contents — incl. name-scoped sub-blocks — as
    # ambient keys, preserving document order for last-write-wins downstream.
    child_view = _spliced(view, cls_name, instance_name)

    for attr_name, sub_block in recursions.items():
        child = getattr(obj, attr_name, None)
        if child is not None:
            _walk(child, _spliced_at(child_view, attr_name, sub_block), context, visited)

    return child_view


def _assign(obj: Any, assignments: Dict[str, Any], context: Dict[str, Any]) -> None:
    """Resolve, coerce, materialize, validate, and setattr the merged assignments."""
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
        if attr_name in eager_params:
            cls_label = getattr(cls, "__confluid_name__", cls.__name__)
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
        validate_setattr(cls, attr_name, resolved_val, get_policy().init)
        setattr(obj, attr_name, resolved_val)


def _spliced(view: Dict[str, Any], cls_name: str, instance_name: Optional[str]) -> Dict[str, Any]:
    """Return ``view`` with the object's addressed blocks unrolled at their positions."""
    block_keys = {cls_name, instance_name} - {None}
    if not any(k in view and isinstance(view[k], dict) for k in block_keys):
        return view
    out: Dict[str, Any] = {}
    for k, v in view.items():
        if k in block_keys and isinstance(v, dict):
            _unroll_into(out, v, instance_name)
        else:
            out[k] = v
    return out


def _unroll_into(out: Dict[str, Any], block: Dict[str, Any], instance_name: Optional[str]) -> None:
    """Unroll a block's entries into ``out`` in order, nesting through the inst sub-block."""
    for bk, bv in block.items():
        if bk == instance_name and isinstance(bv, dict):
            _unroll_into(out, bv, instance_name)
        else:
            out[bk] = bv


def _spliced_at(view: Dict[str, Any], key: str, sub_block: Dict[str, Any]) -> Dict[str, Any]:
    """Return ``view`` with ``sub_block``'s entries unrolled at ``key``'s position.

    Used for child recursion: the sub-block addressed to the child replaces
    the attr-keyed entry, so its values sit at the block's document position
    (later than earlier broadcasts → they win for the child, as authored).
    """
    out: Dict[str, Any] = {}
    placed = False
    for k, v in view.items():
        if k == key and not placed:
            out.update(sub_block)
            placed = True
        else:
            out[k] = v
    if not placed:
        out.update(sub_block)
    return out
