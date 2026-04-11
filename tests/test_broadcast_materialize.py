from confluid import configurable, materialize, register


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
    data = {"_confluid_class_": "Branch", "leaf": {"_confluid_class_": "Leaf"}}

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
    data_scoped = {"_confluid_class_": "Leaf"}
    res1 = materialize(data_scoped, context=context)
    assert not isinstance(res1, dict)
    assert res1.value == 20

    # 2. Explicit > Scoped > Broadcast
    data_explicit = {"_confluid_class_": "Leaf", "value": 30}
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
    data = {"_confluid_class_": "Outer", "inner": {"_confluid_class_": "Inner"}}

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
    data2 = {"_confluid_class_": "OuterDeferred"}
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
        "trainer": {"_confluid_class_": "TrainerLike"},
        "optimizer": {"_confluid_class_": "OptimizerLike"},
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


def test_dotted_broadcast_materialize() -> None:
    register(Leaf)
    register(Branch)
    # Verify that a dotted key at root can broadcast
    context = {"leaf.value": 99}
    data = {
        "_confluid_class_": "Branch",
        "name": "root",
        "leaf": {"_confluid_class_": "Leaf", "name": "leaf"},
    }

    result = materialize(data, context=context)
    # result.leaf.value should be 99
    assert not isinstance(result, dict)
    assert result.leaf.value == 99
