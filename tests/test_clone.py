from typing import Any

import pytest

from confluid import Clone, configurable, dump, flow, get_registry, load


@pytest.fixture(autouse=True)
def setup_registry() -> None:
    get_registry().clear()


def test_clone_basic_deepcopy() -> None:
    """!clone: produces an independent deep copy of the referenced value."""

    @configurable
    class Counter:
        def __init__(self, count: int = 0) -> None:
            self.count = count

    yaml_str = """
counter: !class:Counter()
  count: 5
copy1: !clone:counter
copy2: !clone:counter
"""
    result = load(yaml_str)
    assert result["counter"].count == 5
    assert result["copy1"].count == 5
    assert result["copy2"].count == 5

    # Verify independence — mutating one does not affect the others
    result["copy1"].count = 99
    assert result["counter"].count == 5
    assert result["copy2"].count == 5


def test_clone_with_kwargs() -> None:
    """!clone: with additional kwargs merges them into the cloned object."""

    @configurable
    class Widget:
        def __init__(self, size: int = 3, color: str = "red") -> None:
            self.size = size
            self.color = color

    yaml_str = """
base: !class:Widget()
  size: 10
  color: blue
modified: !clone:base
  color: green
"""
    result = load(yaml_str)
    assert result["base"].color == "blue"
    assert result["base"].size == 10
    assert result["modified"].color == "green"
    assert result["modified"].size == 10


def test_clone_list_value() -> None:
    """!clone: works with list values (deep copy of lists)."""
    yaml_str = """
items:
  - 1
  - 2
  - 3
copy: !clone:items
"""
    result = load(yaml_str)
    assert result["items"] == [1, 2, 3]
    assert result["copy"] == [1, 2, 3]

    # Verify deep independence
    result["copy"].append(99)
    assert 99 not in result["items"]


def test_clone_with_class_kwargs() -> None:
    """Pattern B: !clone: of a Class/Instance with kwargs override (MetricCollection pattern)."""

    @configurable
    class Collection:
        def __init__(self, metrics: Any = None, prefix: str = "") -> None:
            self.metrics = metrics
            self.prefix = prefix

    yaml_str = """
base: !class:Collection()
  metrics:
    - one
    - two
  prefix: ""
train: !clone:base
  prefix: "train/"
val: !clone:base
  prefix: "val/"
"""
    result = load(yaml_str)
    assert result["base"].prefix == ""
    assert result["train"].prefix == "train/"
    assert result["val"].prefix == "val/"
    assert result["train"].metrics == ["one", "two"]
    assert result["val"].metrics == ["one", "two"]

    # Independence
    result["train"].metrics.append("three")
    assert len(result["val"].metrics) == 2
    assert len(result["base"].metrics) == 2


def test_clone_dump_round_trip() -> None:
    """Clone objects that haven't been resolved yet survive dump/load."""
    clone = Clone("metrics", prefix="train/")
    output = dump(clone)
    assert "_clone_: metrics" in output
    assert "prefix" in output


def test_clone_flow_directly() -> None:
    """flow() resolves Clone by resolving the reference then deepcopying."""

    @configurable
    class Item:
        def __init__(self, value: int = 0) -> None:
            self.value = value

    # Set up a context with a resolvable reference via the public API
    from confluid import active_context

    item = Item(value=42)
    with active_context({"thing": item}):
        clone = Clone("thing")
        result = flow(clone)
        assert result.value == 42
        # Verify independence
        result.value = 100
        assert item.value == 42


# ---------------------------------------------------------------------------
# The ONE override semantic (2026-08-13) — same answer on both engine paths.
# ---------------------------------------------------------------------------


def test_clone_overrides_reach_the_constructor_on_BOTH_paths() -> None:
    """An eager class tells the two paths apart: 'built with the override' computes
    from it, 'copied then patched' does not. Direct flow used to deep-copy the BUILT
    referent and paste the override on afterwards (step_size stayed the template's);
    it now builds the clone from the merged kwargs, exactly as load() always has."""
    from confluid import active_context

    @configurable(eager=True)
    class EagerOpt:
        def __init__(self, lr: float = 0.1) -> None:
            self.step_size = lr / 2  # real work from the param — the eager family's point

    doc = load("proto: {_target_: EagerOpt, lr: 0.2}\ncopy: {_clone_: proto, lr: 0.8}")
    assert doc["copy"].step_size == 0.4  # document path — unchanged

    markers = load("proto: {_target_: EagerOpt, lr: 0.2}", flow=False)
    clone = Clone("proto", lr=0.8)
    with active_context(markers):
        flowed = flow(clone)
    assert flowed.step_size == 0.4  # direct flow — the fix (was 0.1)


def test_runtime_kwargs_on_a_flowed_clone_win() -> None:
    """flow()'s contract is 'runtime wins'; the clone's own kwarg used to beat it."""
    from confluid import active_context

    @configurable
    class Opt:
        def __init__(self, lr: float = 0.1) -> None:
            self.lr = lr

    markers = load("proto: {_target_: Opt, lr: 0.2}", flow=False)
    with active_context(markers):
        flowed = flow(Clone("proto", lr=0.8), lr=0.9)
    assert flowed.lr == 0.9  # was 0.8 — the runtime value was lost


def test_a_live_referents_override_is_applied_not_dropped() -> None:
    """A clone of a LIVE object applies its overrides as setattrs (the configure()
    semantic) — the document path used to drop them silently."""
    from confluid import materialize

    @configurable
    class Opt2:
        def __init__(self, lr: float = 0.1) -> None:
            self.lr = lr

    live = Opt2(lr=0.2)
    result = materialize({"proto": live, "copy": Clone("proto", lr=0.8)})
    assert result["copy"].lr == 0.8  # was 0.2 — the override vanished
    assert live.lr == 0.2  # the referent is untouched
    assert result["copy"] is not live


def test_clone_of_a_mapping_merges_overrides() -> None:
    """A dict clone merges its overrides (override wins) — they used to vanish."""
    result = load("defaults: {lr: 0.1, momentum: 0.9}\nvariant: {_clone_: defaults, lr: 0.5}")
    assert result["variant"] == {"lr": 0.5, "momentum": 0.9}
    assert result["defaults"] == {"lr": 0.1, "momentum": 0.9}  # template untouched


def test_clone_of_a_scalar_with_overrides_raises_located() -> None:
    """A scalar has no keys or attributes to override — refuse loudly, never drop
    silently (the failure mode the reserved-key format exists to end)."""
    from confluid import ConfigurationError

    with pytest.raises(ConfigurationError) as excinfo:
        load("base_lr: 0.1\nweird: {_clone_: base_lr, lr: 0.5}")
    message = str(excinfo.value)
    assert "cannot take" in message and "base_lr" in message


def test_clone_overrides_never_mutate_the_template_marker() -> None:
    """The referent MARKER survives the clone untouched, so the template stays
    reusable — the clone deep-copies before merging."""
    from confluid import active_context

    @configurable
    class Opt3:
        def __init__(self, lr: float = 0.1) -> None:
            self.lr = lr

    markers = load("proto: {_target_: Opt3, lr: 0.2}", flow=False)
    with active_context(markers):
        flowed = flow(Clone("proto", lr=0.8))
    assert flowed.lr == 0.8
    assert markers["proto"].kwargs["lr"] == 0.2  # template kwargs untouched
