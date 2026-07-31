from typing import Any

from confluid import Instance, configurable, materialize, register


def _inst(target: str, /, **kwargs: Any) -> Instance:
    """Build an Instance marker with kwargs assigned post-construction.

    ``target`` is positional-only so test kwargs literally named ``name`` or
    ``target`` can't collide with it."""
    marker = Instance(target)
    marker.kwargs.update(kwargs)
    return marker


@configurable
class Leaf:
    def __init__(self, value: int = 0, name: str = "default") -> None:
        self.value = value
        self.name = name


@configurable
class Branch:
    def __init__(self, leaf: Leaf, branch_val: int = 1) -> None:
        self.leaf = leaf
        self.branch_val = branch_val


def test_broadcast_materialize() -> None:
    # Register here to avoid being cleared by other tests
    register(Leaf)
    register(Branch)

    # Context with flat keys at root
    # These should be "broadcast" to any matching parameter name in any class
    context = {
        "value": 42,
        "name": "broadcasted",
        "branch_val": 100,
    }

    # Data to materialize (a Branch containing a Leaf)
    data = _inst("Branch", leaf=_inst("Leaf"))

    result = materialize(data, context=context)

    assert isinstance(result, Branch)
    assert result.branch_val == 100
    assert isinstance(result.leaf, Leaf)
    assert result.leaf.value == 42
    assert result.leaf.name == "broadcasted"


def test_broadcast_priority() -> None:
    register(Leaf)
    # Verify that explicit arguments and scoped settings have higher priority than broadcast
    context = {
        "value": 10,  # Broadcast (lowest)
        "Leaf": {"value": 20},  # Scoped (middle)
    }

    # 1. Scoped > Broadcast
    data_scoped = _inst("Leaf")
    res1 = materialize(data_scoped, context=context)
    assert not isinstance(res1, dict)
    assert res1.value == 20

    # 2. Explicit > Scoped > Broadcast
    data_explicit = _inst("Leaf", value=30)
    res2 = materialize(data_explicit, context=context)
    assert not isinstance(res2, dict)
    assert res2.value == 30


def test_deep_broadcast_propagation() -> None:
    """Root-level scalars propagate through nested class instances (mnist_train_minimal scenario)."""
    from confluid import Class, flow

    @configurable
    class Inner:
        def __init__(self, max_epochs: int = 1, name: str = "inner") -> None:
            self.max_epochs = max_epochs
            self.name = name

    @configurable
    class Outer:
        def __init__(self, inner: "Inner" = None, name: str = "outer") -> None:  # type: ignore[assignment]
            self.inner = inner
            self.name = name

    register(Inner)
    register(Outer)

    # Case 1: Inner is in the config dict
    config = {"max_epochs": 10}
    data = _inst("Outer", inner=_inst("Inner"))

    result = materialize(data, context=config)
    assert isinstance(result, Outer)
    assert isinstance(result.inner, Inner)
    assert result.inner.max_epochs == 10

    # Case 2: Inner is a Python default Class (not in config) — the mnist_train_minimal scenario
    @configurable
    class OuterDeferred:
        def __init__(self, inner: "Inner" = Class(Inner), name: str = "outer") -> None:  # type: ignore[assignment]
            self.inner = inner
            self.name = name

    register(OuterDeferred)

    config2 = {"max_epochs": 10}
    data2 = _inst("OuterDeferred")
    result2 = materialize(data2, context=config2)
    assert isinstance(result2, OuterDeferred)
    # inner is deferred — flow it to materialize
    assert isinstance(result2.inner, Class)
    inner = flow(result2.inner)
    assert isinstance(inner, Inner)
    assert inner.max_epochs == 10


def test_parameter_aware_broadcast_filtering() -> None:
    """Non-matching scalars must NOT be broadcast into classes that don't accept them."""

    @configurable
    class TrainerLike:
        def __init__(self, max_epochs: int = 1, name: str = "trainer") -> None:
            self.max_epochs = max_epochs
            self.name = name

    class OptimizerLike:
        """Non-configurable class — only constructor params, no setattr."""

        def __init__(self, lr: float = 0.01) -> None:
            self.lr = lr

    register(TrainerLike)
    register(OptimizerLike)

    # Root config has scalars that only match some classes
    context = {
        "experiment_name": "mnist",  # matches nobody
        "max_epochs": 10,  # matches TrainerLike only
        "lr": 0.001,  # matches OptimizerLike only
    }

    data = {
        "trainer": _inst("TrainerLike"),
        "optimizer": _inst("OptimizerLike"),
    }

    result = materialize(data, context=context)

    # TrainerLike gets max_epochs but NOT experiment_name or lr
    assert isinstance(result["trainer"], TrainerLike)
    assert result["trainer"].max_epochs == 10
    assert not hasattr(result["trainer"], "experiment_name") or result["trainer"].name != "mnist"

    # OptimizerLike gets lr but NOT experiment_name or max_epochs
    assert isinstance(result["optimizer"], OptimizerLike)
    assert result["optimizer"].lr == 0.001
    assert not hasattr(result["optimizer"], "max_epochs")
    assert not hasattr(result["optimizer"], "experiment_name")


def test_unregistered_class_broadcast_filtering() -> None:
    """Deferred defaults with unregistered class objects must filter by actual constructor params."""
    from confluid import Class, flow

    # Unregistered classes — not in confluid registry
    class PlainTrainer:
        def __init__(self, max_epochs: int = 1, accelerator: str = "auto") -> None:
            self.max_epochs = max_epochs
            self.accelerator = accelerator

    class PlainLoader:
        def __init__(self, batch_size: int = 32, shuffle: bool = False) -> None:
            self.batch_size = batch_size
            self.shuffle = shuffle

    @configurable
    class Pipeline:
        def __init__(
            self,
            trainer: Any = Class(PlainTrainer),
            loader: Any = Class(PlainLoader),
            experiment_name: str = "default",
        ) -> None:
            self.trainer = trainer
            self.loader = loader
            self.experiment_name = experiment_name

    register(Pipeline)

    config = {"max_epochs": 10, "batch_size": 64, "experiment_name": "mnist"}
    data = _inst("Pipeline")
    result = materialize(data, context=config)

    assert isinstance(result, Pipeline)
    assert result.experiment_name == "mnist"

    # Deferred defaults — flow them
    trainer = flow(result.trainer)
    loader = flow(result.loader)

    assert isinstance(trainer, PlainTrainer)
    assert trainer.max_epochs == 10  # matches PlainTrainer constructor
    assert trainer.accelerator == "auto"  # default preserved
    assert not hasattr(trainer, "batch_size")  # NOT broadcast

    assert isinstance(loader, PlainLoader)
    assert loader.batch_size == 64  # matches PlainLoader constructor
    assert loader.shuffle is False  # default preserved
    assert not hasattr(loader, "max_epochs")  # NOT broadcast


def test_broadcast_into_body_assigned_class_attribute() -> None:
    """Broadcasting reaches Class attrs assigned in __init__'s body.

    Mirrors the Marainer Trainer pattern: deferred injection points are
    assigned inside __init__ rather than pre-declared in the constructor
    signature. The broadcaster must reach them just like it does for
    constructor defaults.
    """
    from confluid import Class, flow

    @configurable
    class InnerCfg:
        def __init__(self, max_epochs: int = 1, batch_size: int = 1) -> None:
            self.max_epochs = max_epochs
            self.batch_size = batch_size

    @configurable
    class OuterCfg:
        def __init__(self, name: str = "outer") -> None:
            # Body-assigned: no `inner` ctor param; broadcaster must still reach it.
            self.name = name
            self.inner = Class(InnerCfg)

    register(InnerCfg)
    register(OuterCfg)

    config = {"max_epochs": 7, "batch_size": 16}
    data = _inst("OuterCfg")
    result = materialize(data, context=config)

    assert isinstance(result, OuterCfg)
    assert isinstance(result.inner, Class)
    assert result.inner.kwargs.get("max_epochs") == 7
    assert result.inner.kwargs.get("batch_size") == 16

    inner = flow(result.inner)
    assert isinstance(inner, InnerCfg)
    assert inner.max_epochs == 7
    assert inner.batch_size == 16

    # Idempotency: materializing the same config again produces the same result.
    result2 = materialize(data, context=config)
    inner2 = flow(result2.inner)
    assert inner2.max_epochs == 7
    assert inner2.batch_size == 16


def test_dotted_broadcast_materialize() -> None:
    register(Leaf)
    register(Branch)
    # Verify that a dotted key at root can broadcast
    context = {"leaf.value": 99}
    data = _inst("Branch", name="root", leaf=_inst("Leaf", name="leaf"))

    result = materialize(data, context=context)
    # result.leaf.value should be 99
    assert not isinstance(result, dict)
    assert result.leaf.value == 99
