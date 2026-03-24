from confluid import configurable, get_registry, materialize, register


@configurable
class Leaf:
    def __init__(self, value: int = 0, name: str = "default"):
        self.value = value
        self.name = name


@configurable
class Branch:
    def __init__(self, leaf: Leaf, branch_val: int = 1):
        self.leaf = leaf
        self.branch_val = branch_val


register(Leaf)
register(Branch)


def test_broadcast_materialize() -> None:
    # Verify registry
    reg = get_registry()
    assert "Branch" in reg.list_classes()
    assert "Leaf" in reg.list_classes()

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


def test_dotted_broadcast_materialize() -> None:
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
