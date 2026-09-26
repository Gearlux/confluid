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
from confluid import Target, configurable, load
from confluid.pydantic_export import to_pydantic
from confluid.validation import ValidationPolicy, get_policy, reset_policy, set_policy


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


# --------------------------------------------------------------------------- #
# X2 — the YAML-mode switch around a document-built constructor was a swap of the
# PROCESS-global policy: a direct construction on another thread validated under
# the YAML mode while a load() ran, and two overlapping loads restoring out of
# order left the YAML mode behind for the rest of the process. The switch is now
# context-local; the process policy is never written by a load().
# --------------------------------------------------------------------------- #


@pytest.fixture
def _init_strict_yaml_off() -> Any:
    reset_policy()
    set_policy(init="strict", yaml="off")
    yield
    reset_policy()


def _checked_and_slow(gates: Dict[str, Any]) -> Any:
    @configurable
    class Checked:
        def __init__(self, lr: float = 0.1) -> None:
            self.lr = lr

    @configurable
    class Slow:  # a constructor that takes a while — a dataset scan, a download
        def __init__(self, gate: str = "a") -> None:
            entered, release = gates[gate]
            entered.set()
            release.wait(10)

    return Checked, Slow


def test_a_load_on_another_thread_does_not_relax_direct_construction(_init_strict_yaml_off: Any) -> None:
    pydantic = pytest.importorskip("pydantic")
    gates = {"a": (threading.Event(), threading.Event())}
    checked, slow = _checked_and_slow(gates)
    loader = threading.Thread(target=load, args=({"s": Target(slow, gate="a")},))
    loader.start()
    assert gates["a"][0].wait(10)
    try:
        with pytest.raises(pydantic.ValidationError):
            checked(lr="oops")  # accepted, silently, before the fix
    finally:
        gates["a"][1].set()
        loader.join()


def test_overlapping_loads_leave_the_process_policy_unchanged(_init_strict_yaml_off: Any) -> None:
    pydantic = pytest.importorskip("pydantic")
    gates = {g: (threading.Event(), threading.Event()) for g in "ab"}
    checked, slow = _checked_and_slow(gates)
    first = threading.Thread(target=load, args=({"s": Target(slow, gate="a")},))
    second = threading.Thread(target=load, args=({"s": Target(slow, gate="b")},))
    first.start()
    assert gates["a"][0].wait(10)
    second.start()
    assert gates["b"][0].wait(10)
    gates["a"][1].set()
    first.join()  # the first load finishes first ...
    gates["b"][1].set()
    second.join()  # ... the second one last — the order that left 'off' behind
    assert get_policy() == ValidationPolicy(init="strict", yaml="off", tool="strict")
    with pytest.raises(pydantic.ValidationError):
        checked(lr="oops")


def test_a_document_built_object_still_follows_the_yaml_mode(_init_strict_yaml_off: Any) -> None:
    """The con case: the switch still applies INSIDE a load()."""
    checked, _ = _checked_and_slow({})
    assert load({"c": Target(checked, lr="oops")})["c"].lr == "oops"
