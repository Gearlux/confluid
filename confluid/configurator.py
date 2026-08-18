"""Post-construction configuration (``configure`` / ``configure_from_file``) — via the document.

Applies a config to ALREADY-CONSTRUCTED object graphs, in place — the Post-Construction Paradigm.
Since architecture record 19 (phase 4) it does so through the ONE precedence rule rather than a
second implementation of it over live objects:

1. the objects become a marker document — :func:`confluid.dumper.to_markers`, the same
   reconstruction rule ``dump()`` uses, so an object's document is one thing;
2. the config is merged AFTER it (``deep_merge`` + dotted-key expansion) — document order, last
   spec wins, exactly as an experiment overlay lands on a base file;
3. pass 7 settles the merged document (``load(until="settled")`` — the pass ``hydraide``
   emits): bare keys, class-name blocks, ``Cls.inst.attr`` forms, ``'*'`` / ``'**'`` riders,
   dict-at-slot tunes, references — one scanner, the report recorded as it goes;
4. the settled values are applied back onto the live objects — a plain value is set (validated
   under the init policy), a marker at a slot holding a marker is tuned IN PLACE, a marker
   standing for a live child recurses into it, ``solidify()`` fires post-order.

The caller's positional objects are addressable only by class name and bare keys (as before);
NAMED objects (``configure(config=…, trainer=t)``) are additionally addressable by that name —
``trainer.model.lr: 0.7`` reaches the nested model, which the live walker used to leave unused.
An object the dumper cannot represent (not ``@configurable``, not built by confluid) is warned
about and skipped: it has no document, so nothing can be settled for it.

Both entry points return a :class:`confluid.ConfigurationReport` — applied / failed / unused
override keys for the whole call (see ``confluid.report``); inside a
:func:`confluid.collect_report` block the ambient report is adopted, so a load-then-configure
pass aggregates into one report.
"""

from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, Optional, Set, Union

import yaml
from loggair import get_logger

from confluid.broadcast import clear_pass_caches, dict_at_slot_kind
from confluid.dumper import to_markers
from confluid.engine import _ctor_params, _maybe_solidify, flow
from confluid.exceptions import ConfigurationError
from confluid.fluid import Fluid, Target
from confluid.loader import ConfluidLoader, load
from confluid.merger import deep_merge, expand_dotted_keys
from confluid.report import ConfigurationReport
from confluid.resolver import Resolver, parse_value
from confluid.state import _ENGINE_STATE, _active_report
from confluid.validation import get_policy, validate_setattr

logger = get_logger("confluid.configurator")

_MISSING = object()


def configure(
    *instances: Any, config: Any, context: Optional[Dict[str, Any]] = None, **named: Any
) -> ConfigurationReport:
    """Apply ``config`` to existing objects, in place, through the document (see the module doc).

    Args:
        *instances: Objects to configure — reachable by class-name blocks and bare keys.
        config: A mapping, or YAML text; ``ClassName:`` / bare keys / dotted keys / ``'**'``
            riders, the same grammar a config file uses.
        context: Optional explicit interpolation context for ``${...}`` in ``config`` (defaults
            to ``config`` itself).
        **named: Objects addressable by NAME as well — ``configure(config={"trainer.epochs": 5},
            trainer=t)`` — the name is the document key the object sits under.

    Returns:
        A :class:`confluid.ConfigurationReport` spanning every object of the call: applied
        overrides (with receiver + origin), failed keys (unknown block attributes, per-field
        validation failures), and the config keys that matched nothing. Inside a
        :func:`confluid.collect_report` block the ambient report is adopted (and returned).
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
    config = expand_dotted_keys(Resolver(context=context if context is not None else config).resolve(config))

    # Register unused-tracking candidates: every top-level config key (dotted keys already
    # expanded to their block, which is what pass 7 marks used) is an override candidate —
    # a marker-valued key IS an override, it lands on a slot; glob blocks register per
    # non-dict leaf.
    for k, v in config.items():
        if k in ("*", "**") and isinstance(v, dict):
            report.add_config_keys(f"{k}.{leaf}" for leaf, lv in v.items() if not isinstance(lv, dict))
        else:
            report.add_config_keys((k,))

    # 1. The objects as a marker document. Positional objects sit under names no config
    #    key can address or collide with (`_0`, `_1`, …); named ones under their name.
    objects: Dict[str, Any] = {f"_{i}": obj for i, obj in enumerate(instances)}
    objects.update(named)
    memo: Dict[int, Any] = {}  # id(live) -> marker: a shared object is ONE marker
    document: Dict[str, Any] = {}
    for name, obj in objects.items():
        node = to_markers(obj, memo)  # a configurable → a marker; a dict/list of them → the container
        if not _contains_marker(node):
            logger.warning(
                f"configure(): {type(obj).__name__} is not @configurable and was not built by confluid — it "
                "has no document to settle, so nothing can be applied to it; skipped"
            )
            continue
        document[name] = node
    if not document:
        if ambient is None:
            report.log_unused()
        return report

    # 2. The config lands AFTER the objects' own keys — document order makes it win. A config
    #    key that NAMES an object (`trainer: {...}`, or `trainer.model.lr` expanded to it) tunes
    #    that object's marker where it stands — it must not re-anchor the object after the
    #    other config keys, or a bare key written next to it would lose to the object's own
    #    current values.
    addressed = {k: v for k, v in config.items() if k in document}
    rest = {k: v for k, v in config.items() if k not in document}
    for k, v in addressed.items():
        document[k] = deep_merge({k: document[k]}, {k: v})[k]  # P1: an overlay mapping TUNES a marker
    merged = expand_dotted_keys({**document, **rest})

    # 3. Pass 7 settles the whole thing, recording into THIS report. configure() is an
    #    entry point exactly like load(): a class redefined since the
    #    last pass must not be served its previous accept-list.
    clear_pass_caches()
    token = _ENGINE_STATE.set(replace(_ENGINE_STATE.get(), report=report))
    try:
        settled = load(merged, until="settled")
    finally:
        _ENGINE_STATE.reset(token)

    # 4. Apply the settled values back onto the live objects. ``visited`` maps id -> the
    #    OBJECT (recording an id pins it for the call — see the id-pinning rule).
    visited: Dict[int, Any] = {}
    for name in document:
        node = settled.get(name)
        if isinstance(node, Target):
            _apply(objects[name], node, visited, report)
        else:
            _materialize_value(node, visited, report)  # a container: every marker inside applies to its object
    if ambient is None:
        report.log_unused()
    return report


def configure_from_file(
    *instances: Any, path: Union[str, Path], context: Optional[Dict[str, Any]] = None, **named: Any
) -> ConfigurationReport:
    """Load a YAML config file and apply it to existing instances in one call.

    A convenience for the ``load(path, until="raw")`` + :func:`configure` two-step, so

    >>> configure_from_file(trainer, path="experiment.yaml")   # doctest: +SKIP

    is equivalent to ``configure(trainer, config=load("experiment.yaml", until="raw"))``.
    The file is read via :func:`confluid.load`, so recursive ``include:``
    / ``import:`` directives and ``_target_`` / reference markers are honoured. This is
    a wrapper only — it adds no behaviour beyond loading.

    Args:
        *instances: The already-constructed objects to configure in place.
        path: Path to the YAML config file (``str`` or ``Path``).
        context: Optional explicit interpolation context (defaults to the loaded config).
        **named: Objects addressable by name, as in :func:`configure`.

    Raises:
        confluid.ConfigFileNotFoundError: If ``path`` does not exist.
    """
    return configure(*instances, config=load(path, until="raw"), context=context, **named)


# --------------------------------------------------------------------------- #
# Applying a settled marker back onto the live object it stands for
# --------------------------------------------------------------------------- #


def _contains_marker(node: Any) -> bool:
    """A marker, or a dict/list holding one (any depth) — something the document can settle."""
    if isinstance(node, Fluid):
        return True
    if isinstance(node, dict):
        return any(_contains_marker(v) for v in node.values())
    if isinstance(node, list):
        return any(_contains_marker(v) for v in node)
    return False


def _live_of(node: Any) -> Any:
    """The live object a settled marker stands for (``to_markers`` stamped it; the pass-7 copy
    keeps the stamp), or ``None`` for a marker the config introduced."""
    return getattr(node, "__confluid_live__", None)


def _apply(obj: Any, node: Target, visited: Dict[int, Any], report: ConfigurationReport) -> None:
    """Set every settled kwarg of ``node`` on ``obj`` that differs from what ``obj`` holds."""
    if id(obj) in visited:
        return
    visited[id(obj)] = obj
    cls = obj.__class__
    label = getattr(cls, "__confluid_name__", cls.__name__)
    live_vars = getattr(obj, "__dict__", None) or {}
    eager_params: Set[str] = _ctor_params(cls) or set() if getattr(cls, "__confluid_eager__", False) else set()

    for attr, settled in node.kwargs.items():
        current = live_vars.get(attr, _MISSING)  # never getattr: a property getter must not run
        if isinstance(settled, Target):
            if isinstance(current, Fluid):
                # A marker at a slot holding a marker (a deferred body slot): tune IN PLACE —
                # the owner keeps its object, `flow(self.optimizer, ...)` later sees the values.
                # Live objects the marker's kwargs held come back as themselves, configured.
                current.kwargs.update(
                    {k: _materialize_value(v, visited, report, build=False) for k, v in settled.kwargs.items()}
                )
                continue
            live = _live_of(settled)
            if live is not None and live is current:
                _apply(live, settled, visited, report)  # a child object: recurse, no reassignment
                continue
            value: Any = settled if settled.partial else flow(settled)  # a marker the config introduced
        else:
            value = _materialize_value(settled, visited, report)
            if attr in live_vars and _same(value, current):
                continue
            if isinstance(value, dict) and dict_at_slot_kind(current) == "opaque":
                # The dict-at-slot rule's fourth arm (user ruling): a mapping addressed at a
                # slot holding a live object that is neither a marker nor configurable is
                # REFUSED, never silently swapped for a dict.
                raise ConfigurationError(
                    f"configure(): a mapping was addressed at {label}.{attr}, which holds a live "
                    f"{type(current).__name__} that is not @configurable — refusing to replace an object "
                    "with a dict. Register the class (or mark it @configurable), or replace the whole value in code."
                )
            if isinstance(value, str):
                value = parse_value(value)
        _set(obj, attr, value, label, eager_params, report)
    _maybe_solidify(obj)


def _materialize_value(value: Any, visited: Dict[int, Any], report: ConfigurationReport, *, build: bool = True) -> Any:
    """A settled value with the markers INSIDE it resolved back to the live world.

    A marker standing for a live object is applied to that object and becomes it; a marker
    standing for a live MARKER (a deferred slot found inside a container) is tuned in place;
    a marker the config introduced is built — unless ``build`` is False, which is the case
    INSIDE a deferred marker's kwargs, where a marker stays a marker until the owner flows it
    (exactly what the load path does with a bare marker delivered into a `Partial`).
    """
    if isinstance(value, Target):
        live = _live_of(value)
        if isinstance(live, Fluid):  # a marker that was inside a container / another marker's kwargs
            live.kwargs.update(
                {k: _materialize_value(v, visited, report, build=False) for k, v in value.kwargs.items()}
            )
            return live
        if live is not None:
            _apply(live, value, visited, report)
            return live
        if value.partial or not build:
            return value
        return flow(value)
    if isinstance(value, dict):
        return {k: _materialize_value(v, visited, report, build=build) for k, v in value.items()}
    if isinstance(value, list):
        return [_materialize_value(v, visited, report, build=build) for v in value]
    return value


def _same(new: Any, current: Any) -> bool:
    """``new`` is the value ``current`` already is — identity first, equality when it is a bool."""
    if new is current:
        return True
    try:
        eq = new == current
    except Exception:  # noqa: BLE001 — an array-like whose __eq__ is elementwise: treat as changed
        return False
    return eq is True


def _set(obj: Any, attr: str, value: Any, label: str, eager_params: Set[str], report: ConfigurationReport) -> None:
    """Validate under the init policy and set — recording a validation failure on the report."""
    note: Optional[str] = None
    if attr in eager_params:
        note = "eager-class constructor param — __init__ work not re-run"
        logger.warning(
            f"configure(): setting constructor param {attr!r} on eager class {label} — "
            f"__init__ work will NOT re-run; derived state may be stale"
        )
    try:
        detail = validate_setattr(obj.__class__, attr, value, get_policy().init)
    except Exception as exc:  # strict mode — record, then let it propagate
        report.record_failed(attr, label, "validation", str(exc))
        raise
    if detail is not None:  # warn mode — recorded, value still applied below
        report.record_failed(attr, label, "validation", detail)
    setattr(obj, attr, value)
    if note is not None:
        # Pass 7 already recorded the override; the staleness note rides on THAT record
        # (AppliedKey is frozen — replace the entry in place).
        for i in range(len(report.applied) - 1, -1, -1):
            entry = report.applied[i]
            if entry.key == attr and entry.target == label:
                report.applied[i] = replace(entry, note=note)
                break
