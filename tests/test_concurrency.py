"""Concurrency pins — X4/X5 (BUGS-2026-08-13).

X4: a concurrent pass's entry ``clear_pass_caches()`` may land between a cache's
``in`` check and its indexed read; every per-pass cache read must be ONE atomic
step. The hostile dict below reproduces the reported interleaving
deterministically: ``__contains__`` answers, then the clear lands, then the read
— which raised ``KeyError`` out of public ``materialize()``.

X5: ``to_pydantic``'s docstring contract ("each call with the same cls returns
the same model") must hold across threads — a bare ``lru_cache`` serialized
nothing, so ten first calls behind a barrier returned nine distinct model
classes and cross-thread ``isinstance`` checks failed.
"""

import threading
from typing import Any, Dict

import pytest

import confluid.broadcast as broadcast
import confluid.engine as engine
from confluid import configurable
from confluid.pydantic_export import to_pydantic


class _HostileDict(Dict[Any, Any]):
    """``__contains__`` answers, then a concurrent pass's clear lands."""

    def __contains__(self, key: Any) -> bool:
        present = super().__contains__(key)
        self.clear()
        return present


def _fresh_node() -> type:
    @configurable
    class Node:
        def __init__(self, power: int = 1) -> None:
            self.power = power

    return Node


def test_the_accept_list_read_survives_a_clear_landing_in_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    node_cls = _fresh_node()
    monkeypatch.setattr(broadcast, "_acceptable_keys_cache", _HostileDict())
    first = broadcast._get_acceptable_keys(node_cls)  # populates
    second = broadcast._get_acceptable_keys(node_cls)  # the check-then-read window
    assert second == first == frozenset({"power"})


def test_the_negative_name_read_survives_a_clear_landing_in_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """The unresolvable-STRING branch caches None — a real answer, so the atomic
    read needs the sentinel, not a None test."""
    monkeypatch.setattr(broadcast, "_acceptable_keys_cache", _HostileDict())
    assert broadcast._get_acceptable_keys("no.such.ClassAnywhere") is None  # populates
    assert broadcast._get_acceptable_keys("no.such.ClassAnywhere") is None  # the window


def test_the_param_kinds_read_survives_a_clear_landing_in_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    node_cls = _fresh_node()
    monkeypatch.setattr(broadcast, "_param_kind_cache", _HostileDict())
    first = broadcast._get_param_kinds(node_cls)  # populates
    second = broadcast._get_param_kinds(node_cls)  # the window
    assert second == first and "power" in second


def test_the_parent_blacklist_read_survives_a_clear_landing_in_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    node_cls = _fresh_node()
    monkeypatch.setattr(engine, "_parent_blacklist_cache", _HostileDict())
    first = engine._get_parent_attr_blacklist(node_cls)  # populates
    second = engine._get_parent_attr_blacklist(node_cls)  # the window
    assert second == first and isinstance(second, frozenset)


def test_concurrent_first_calls_to_to_pydantic_hand_out_ONE_model_class() -> None:
    """X5 — ten threads behind a barrier, first call for one class: one model,
    so a later ``isinstance(x, to_pydantic(cls))`` holds on every thread
    (measured before the fix: nine distinct classes)."""

    @configurable
    class Fresh:
        def __init__(self, lr: float = 0.1, name: str = "f") -> None:
            self.lr, self.name = lr, name

    thread_count = 10
    barrier = threading.Barrier(thread_count)
    results: list = []

    def worker() -> None:
        barrier.wait()
        results.append(to_pydantic(Fresh))

    threads = [threading.Thread(target=worker) for _ in range(thread_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == thread_count
    assert len({id(model) for model in results}) == 1
    assert results[0] is to_pydantic(Fresh)
