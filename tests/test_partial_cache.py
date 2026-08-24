"""The deferred-build cache — one recipe + one argument set = ONE object.

A ``PartialClass`` marker remembers its LAST build: a later ``flow()`` with an
unchanged recipe and the same arguments returns the cached object; anything
else — a tuned recipe, a different argument, a different call shape — rebuilds
(and the new build replaces the remembered one; the cache holds ONE entry per
marker, never a table).

Arguments compare the way the design was approved: scalars by VALUE, everything
else by IDENTITY (``is``) — equality on arbitrary objects is not safe (``==``
on a tensor returns a tensor). A marker-valued recipe entry is snapshotted
RECURSIVELY, because ``configure()``'s tuning mutates nested markers in place
and a tune must always invalidate.

The cache lives in a ``WeakKeyDictionary`` keyed by the marker itself, which is
what makes two properties structural rather than rules: a marker COPY is a new
key (copies never share a build), and the entry dies with the marker.
"""

from typing import Any, List

from confluid import PartialClass, configurable, flow


def _make_class(counter: List[int]) -> Any:
    @configurable(name=f"CacheProbe{len(counter)}{id(counter)}")
    class Probe:
        def __init__(self, max_epochs: int = 1, logger: Any = None, params: Any = None) -> None:
            counter.append(1)
            self.max_epochs = max_epochs
            self.logger = logger
            self.params = params

    return Probe


def test_a_bare_reflow_returns_the_same_object() -> None:
    builds: List[int] = []
    marker = PartialClass(_make_class(builds), max_epochs=3)
    a = flow(marker)
    b = flow(marker)
    assert a is b
    assert len(builds) == 1


def test_the_same_runtime_kwargs_return_the_same_object() -> None:
    builds: List[int] = []
    marker = PartialClass(_make_class(builds), max_epochs=3)
    x = object()
    d = flow(marker, logger=x)
    e = flow(marker, logger=x)
    assert d is e
    assert len(builds) == 1


def test_a_different_runtime_kwarg_rebuilds_and_replaces() -> None:
    builds: List[int] = []
    marker = PartialClass(_make_class(builds), max_epochs=3)
    x, y = object(), object()
    d = flow(marker, logger=x)
    f = flow(marker, logger=y)
    assert f is not d
    g = flow(marker, logger=y)
    assert g is f, "the new build replaced the remembered one"
    h = flow(marker, logger=x)
    assert h is not d, "ONE entry per marker — the x build was forgotten"
    assert len(builds) == 3


def test_a_bare_flow_after_a_kwarg_flow_rebuilds() -> None:
    builds: List[int] = []
    marker = PartialClass(_make_class(builds), max_epochs=3)
    flow(marker, logger=object())
    flow(marker)
    assert len(builds) == 2


def test_a_recipe_change_invalidates() -> None:
    builds: List[int] = []
    marker = PartialClass(_make_class(builds), max_epochs=3)
    a = flow(marker)
    marker.kwargs["max_epochs"] = 5  # what configure()/tuning does
    c = flow(marker)
    assert c is not a
    assert c.max_epochs == 5
    assert len(builds) == 2


def test_a_nested_marker_tune_invalidates() -> None:
    builds: List[int] = []
    inner_builds: List[int] = []
    inner = PartialClass(_make_class(inner_builds), max_epochs=1)
    marker = PartialClass(_make_class(builds), logger=inner)
    flow(marker)
    inner.kwargs["max_epochs"] = 9  # tune the NESTED recipe in place
    flow(marker)
    assert len(builds) == 2, "a nested tune must not serve the pre-tune build"


def test_scalars_compare_by_value() -> None:
    builds: List[int] = []
    marker = PartialClass(_make_class(builds))
    first = "".join(["run-", "x" * 40])
    second = "".join(["run-", "x" * 40])  # equal value, distinct object (built at runtime)
    assert first is not second
    a = flow(marker, logger=first)
    b = flow(marker, logger=second)
    assert a is b
    assert len(builds) == 1


def test_objects_compare_by_identity_a_fresh_generator_rebuilds() -> None:
    builds: List[int] = []
    marker = PartialClass(_make_class(builds))
    flow(marker, params=iter([1, 2]))
    flow(marker, params=iter([1, 2]))  # a FRESH generator — provably-same is the bar
    assert len(builds) == 2


def test_positional_runtime_args_participate_in_the_key() -> None:
    builds: List[int] = []

    @configurable(name=f"CachePosProbe{id(builds)}")
    class Positional:
        def __init__(self, *values: Any) -> None:
            builds.append(1)
            self.values = values

    marker = PartialClass(Positional)
    shared = object()
    a = flow(marker, shared)
    b = flow(marker, shared)
    assert a is b and len(builds) == 1
    flow(marker, object())
    assert len(builds) == 2


def test_a_random_class_always_rebuilds() -> None:
    builds: List[int] = []

    @configurable(name=f"CacheRandomProbe{id(builds)}", random=True)
    class Rolls:
        def __init__(self) -> None:
            builds.append(1)

    marker = PartialClass(Rolls)
    a = flow(marker)
    b = flow(marker)
    assert a is not b
    assert len(builds) == 2


def test_a_marker_copy_does_not_share_the_cache() -> None:
    from copy import copy

    builds: List[int] = []
    marker = PartialClass(_make_class(builds), max_epochs=3)
    a = flow(marker)
    twin = copy(marker)
    b = flow(twin)
    assert b is not a
    assert len(builds) == 2


def test_the_solidify_flag_participates_in_the_key() -> None:
    builds: List[int] = []
    marker = PartialClass(_make_class(builds), max_epochs=3)
    flow(marker)
    flow(marker, solidify=False)
    assert len(builds) == 2


def test_an_eager_target_outside_a_pass_is_not_cached() -> None:
    """The con case: the cache is the DEFERRED marker's contract. An eager marker
    flowed twice from code keeps today's behaviour (per-pass memoization only)."""
    from confluid import Target

    builds: List[int] = []
    marker = Target(_make_class(builds), max_epochs=3)
    a = flow(marker)
    b = flow(marker)
    assert a is not b
    assert len(builds) == 2
