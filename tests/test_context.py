"""Tests for the public ``active_context()`` API and the ContextVar engine state.

The engine state rides a ``contextvars.ContextVar`` (not ``threading.local``),
so an active materialization context is inherited by asyncio tasks and
``asyncio.to_thread`` workers — the review's headline failure mode ("flow()
inside an event-loop task can't resolve !ref:") is fixed. A raw ``Thread`` /
``run_in_executor`` still does NOT inherit contextvars; the documented contract
is ``contextvars.copy_context().run(...)`` or ``active_context`` in the worker.
"""

import asyncio
import threading
from typing import Any

from confluid import Reference, active_context, configurable, flow
from confluid.engine import get_active_context


@configurable
class Holder:
    def __init__(self, value: int = 0) -> None:
        self.value = value


def _resolve_thing() -> Any:
    """Bare flow() of a Reference — needs an active context to resolve."""
    return flow(Reference("thing"))


def test_active_context_enables_bare_flow_ref_resolution() -> None:
    item = Holder(value=7)
    with active_context({"thing": item}):
        assert _resolve_thing() is item


def test_active_context_restores_previous_state_on_exit() -> None:
    outer = Holder(value=1)
    inner = Holder(value=2)
    with active_context({"thing": outer}):
        with active_context({"thing": inner}):
            assert _resolve_thing() is inner
        # Nesting restores — the outer context is active again.
        assert _resolve_thing() is outer
    assert get_active_context() is None


def test_active_context_expands_dotted_keys() -> None:
    with active_context({"a.b": 5}):
        ctx = get_active_context()
        assert ctx == {"a": {"b": 5}}


def test_asyncio_task_inherits_active_context() -> None:
    """The headline fix: a task spawned inside the loop sees the parent's context."""
    item = Holder(value=42)

    async def main() -> Any:
        task = asyncio.create_task(asyncio.to_thread(_resolve_thing))
        return await task

    with active_context({"thing": item}):
        result = asyncio.run(main())
    assert result is item


def test_asyncio_to_thread_inherits_active_context() -> None:
    item = Holder(value=13)

    async def main() -> Any:
        return await asyncio.to_thread(_resolve_thing)

    with active_context({"thing": item}):
        result = asyncio.run(main())
    assert result is item


def test_raw_thread_does_not_inherit_but_active_context_inside_worker_works() -> None:
    """Documents the boundary contract: raw Thread drops the context."""
    item = Holder(value=99)
    results: dict = {}

    def worker_without() -> None:
        results["without"] = get_active_context()

    def worker_with() -> None:
        with active_context({"thing": item}):
            results["with"] = _resolve_thing()

    with active_context({"thing": item}):
        t1 = threading.Thread(target=worker_without)
        t1.start()
        t1.join()
        t2 = threading.Thread(target=worker_with)
        t2.start()
        t2.join()

    assert results["without"] is None  # raw Thread: not inherited
    assert results["with"] is item  # active_context inside the worker: works


def test_copy_context_run_propagates_into_raw_thread() -> None:
    """The other documented remedy: contextvars.copy_context().run(...)."""
    import contextvars

    item = Holder(value=5)
    results: dict = {}

    def worker() -> None:
        results["resolved"] = _resolve_thing()

    with active_context({"thing": item}):
        ctx = contextvars.copy_context()
        t = threading.Thread(target=lambda: ctx.run(worker))
        t.start()
        t.join()

    assert results["resolved"] is item


def test_active_context_none_is_a_noop_context() -> None:
    with active_context(None):
        assert get_active_context() is None


def test_active_context_exported_from_top_level() -> None:
    import confluid

    assert "active_context" in confluid.__all__
    assert confluid.active_context is active_context
