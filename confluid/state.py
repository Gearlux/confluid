"""Engine state — the one ContextVar the materialization pass runs against.

Its own module for a layering reason, not for size. ``engine`` is where markers
become objects; ``broadcast`` is where a document's keys are merged onto them in
order. Broadcasting needs the ambient :class:`ConfigurationReport` (and nothing
else from the engine), so with the state living in ``engine`` the two modules
would import each other. Lifting it here gives the one-directional chain

    fluid -> state -> broadcast -> engine

and keeps ``broadcast`` free of any dependency on materialization.

Public surface: :func:`active_context` (the sanctioned way for external code to
activate a resolution context for bare ``flow()`` calls) and
:func:`collect_report`. ``_EngineState`` / ``_ENGINE_STATE`` are internal —
downstream reach-ins are prohibited; go through ``active_context``.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Any, Dict, Iterator, List, Optional

from confluid.merger import expand_dotted_keys
from confluid.report import ConfigurationReport


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
    # Strong references to every marker the memos key on, held for the pass.
    #
    # Both memos key on ``id(marker)``, which is only unique while the marker is
    # ALIVE — CPython reuses the address of a freed object, and a recycled id
    # reads as a memo HIT for a completely unrelated node. The engine builds
    # short-lived broadcast COPIES of markers (``_resolve_kwarg_value``), so
    # without this the second item of a list could be handed the first item's
    # instance: measured on a three-stage pipeline, every stage came back as
    # stage one. Document-owned markers are safe on their own; the copies are
    # not, and the memo cannot tell them apart.
    memo_keepalive: Optional[List[Any]] = None
    # True inside `resolve()`: a STRUCTURAL pass that must construct nothing.
    #
    # Only one site reads it — the dotted-`!ref:` branch in `engine._flow_recursive`,
    # which is the sole place a Reference triggers construction (reading
    # `split.train` means building `split`). With it set the Reference is handed
    # back late-bound, so `resolve()` keeps the promise its docstring makes.
    # `materialize()` / `load()` leave it False and behave exactly as before.
    structural: bool = False
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

    The public way to make reference resolution work for ``flow()`` calls made
    OUTSIDE a ``materialize()`` pass (e.g. domain code flowing a deferred
    ``Partial`` slot later, on another thread).

    **It does NOT enable broadcasting.** A ``flow()`` inside this block builds
    the target in isolation — the context's top-level keys are NOT injected into
    same-named constructor parameters, and the object comes back on its
    defaults. Broadcasting happens only in a ``materialize()`` pass, so pass the
    document explicitly (``materialize(fluid, context=document)``) when a flat
    config's keys must reach the object. (This paragraph exists because the
    omission is invisible: the object builds fine, just unconfigured.)

    Fresh flow/instance memos
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
    token = _ENGINE_STATE.set(
        _EngineState(context=ctx, flow_memo={}, instance_memo={}, memo_keepalive=[], report=_active_report())
    )
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
