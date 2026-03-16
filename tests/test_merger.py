from confluid.merger import deep_merge, expand_dotted_keys


def test_deep_merge() -> None:
    base = {"a": {"b": 1}, "c": 2}
    overlay = {"a": {"d": 3}, "c": 4}
    result = deep_merge(base, overlay)
    assert result == {"a": {"b": 1, "d": 3}, "c": 4}


def test_expand_dotted_keys() -> None:
    data = {
        "model.layers": 10,
        "model.activation": "relu",
        "trainer.lr": 0.001,
        "simple": 1,
    }
    expanded = expand_dotted_keys(data)
    assert expanded == {
        "model": {"layers": 10, "activation": "relu"},
        "trainer": {"lr": 0.001},
        "simple": 1,
    }


def test_expand_dotted_keys_nested() -> None:
    data = {"a.b.c": 42}
    expanded = expand_dotted_keys(data)
    assert expanded == {"a": {"b": {"c": 42}}}


def test_expand_dotted_keys_collision() -> None:
    # Test that it merges if base already exists
    data = {"model": {"layers": 3}, "model.dropout": 0.1}
    expanded = expand_dotted_keys(data)
    assert expanded == {"model": {"layers": 3, "dropout": 0.1}}
