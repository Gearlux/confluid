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


# ---------------------------------------------------------------------------
# A mapping merged OVER a marker tunes it (BUGS-2026-08-13 P1)
#
# `deep_merge` recursed only when BOTH sides were dicts, and a Fluid is not a
# dict — so an overlay mapping replaced the whole marker. That is the third site
# of the rule C1/C1b settled ("a mapping addressed at a slot holding a marker
# TUNES it, never replaces it"); the two materialization paths dispatch on
# `broadcast.dict_at_slot_kind`, and document COMPOSITION was a path nobody
# counted.
# ---------------------------------------------------------------------------


def test_an_overlay_mapping_tunes_a_marker_instead_of_replacing_it() -> None:
    from confluid.fluid import Target
    from confluid.merger import deep_merge

    base = {"model": Target("Counter", red=1)}
    merged = deep_merge(base, {"model": {"blue": 3}})

    assert isinstance(merged["model"], Target)
    assert merged["model"].kwargs == {"red": 1, "blue": 3}


def test_the_merge_recurses_into_a_NESTED_marker() -> None:
    """Single-level tuning is not enough: the dotted and class-block spellings
    both reach a marker nested inside the overridden one, so this must too."""
    from confluid.fluid import Target
    from confluid.merger import deep_merge

    base = {"model": Target("Outer", red=1, sub=Target("Inner", keep="kept"))}
    merged = deep_merge(base, {"model": {"sub": {"k": 5}}})

    sub = merged["model"].kwargs["sub"]
    assert isinstance(sub, Target), "the nested marker must survive"
    assert sub.kwargs == {"keep": "kept", "k": 5}
    assert merged["model"].kwargs["red"] == 1


def test_the_merge_does_not_mutate_the_BASE_marker() -> None:
    """`deep_merge` returns a new document; an included file's markers may be
    reused elsewhere, so tuning must copy."""
    from confluid.fluid import Target
    from confluid.merger import deep_merge

    original = Target("Counter", red=1)
    base = {"model": original}
    deep_merge(base, {"model": {"blue": 3}})

    assert original.kwargs == {"red": 1}, "the base document must be untouched"


def test_an_overlay_MARKER_still_replaces_the_base_marker() -> None:
    """Re-declaring the node is a replacement, not a tune — last spec wins."""
    from confluid.fluid import Target
    from confluid.merger import deep_merge

    merged = deep_merge({"model": Target("Counter", red=1)}, {"model": Target("Deque", blue=3)})

    assert merged["model"].target == "Deque"
    assert merged["model"].kwargs == {"blue": 3}


def test_a_SCALAR_overlay_still_replaces_a_marker() -> None:
    from confluid.fluid import Target
    from confluid.merger import deep_merge

    assert deep_merge({"model": Target("Counter", red=1)}, {"model": 7})["model"] == 7


def test_two_plain_dicts_still_deep_merge() -> None:
    from confluid.merger import deep_merge

    merged = deep_merge({"a": {"x": 1, "y": 2}}, {"a": {"y": 9}})
    assert merged["a"] == {"x": 1, "y": 9}


def test_two_scope_blocks_with_the_SAME_dims_merge_their_contents() -> None:
    """BUGS-2026-08-19 PA4 — the same wrapper key in an included file and the
    including file, same dimension: the base file's block was silently
    REPLACED (a ScopeBlock is neither a dict nor a Target). Written flat in one
    file the duplicate key is refused; through an include it must merge."""
    from confluid.fluid import ScopeBlock
    from confluid.merger import deep_merge

    base = {"torch": ScopeBlock({"framework": "torch"}, False, {"lr": 0.1})}
    overlay = {"torch": ScopeBlock({"framework": "torch"}, False, {"epochs": 5})}
    merged = deep_merge(base, overlay)["torch"]
    assert isinstance(merged, ScopeBlock)
    assert (merged.dims, merged.negate) == ({"framework": "torch"}, False)
    assert merged.contents == {"lr": 0.1, "epochs": 5}
    assert base["torch"].contents == {"lr": 0.1}, "the base block must be untouched"


def test_two_scope_blocks_with_DIFFERENT_dims_still_replace() -> None:
    """The con case: a different condition is a different block — last spec wins."""
    from confluid.fluid import ScopeBlock
    from confluid.merger import deep_merge

    base = {"w": ScopeBlock({"framework": "torch"}, False, {"lr": 0.1})}
    overlay = {"w": ScopeBlock({"framework": "keras"}, False, {"epochs": 5})}
    merged = deep_merge(base, overlay)["w"]
    assert merged.dims == {"framework": "keras"}
    assert merged.contents == {"epochs": 5}
    negated = deep_merge(base, {"w": ScopeBlock({"framework": "torch"}, True, {"epochs": 5})})["w"]
    assert negated.negate is True and negated.contents == {"epochs": 5}


# ---------------------------------------------------------------------------
# A dotted key competes on POSITION, like every other spelling
# (BUGS-2026-08-13 P7)
#
# The expansion ran in TWO passes — plain keys first, then every dotted key,
# sorted by depth and then alphabetically — so a dotted leaf always landed last
# and could not lose to anything. The head was already anchored where written
# (fixed in August); this is the same bug one level down, at the LEAF.
# ---------------------------------------------------------------------------


def test_a_nested_block_written_AFTER_a_dotted_key_wins() -> None:
    from confluid.merger import expand_dotted_keys

    assert expand_dotted_keys({"a.b": 1, "a": {"b": 0}})["a"] == {"b": 0}


def test_a_dotted_key_written_AFTER_a_nested_block_wins() -> None:
    from confluid.merger import expand_dotted_keys

    assert expand_dotted_keys({"a": {"b": 0}, "a.b": 1})["a"] == {"b": 1}


def test_the_two_orderings_DISAGREE() -> None:
    """The rule-level pin: if these ever match, position has stopped deciding."""
    from confluid.merger import expand_dotted_keys

    first = expand_dotted_keys({"a.b": 1, "a": {"b": 0}})["a"]
    second = expand_dotted_keys({"a": {"b": 0}, "a.b": 1})["a"]
    assert first != second, "two orderings of one leaf must not give the same answer"


def test_a_deeper_dotted_key_still_merges_into_a_shallower_one() -> None:
    """The depth sort is gone; document order must still compose these."""
    from confluid.merger import expand_dotted_keys

    assert expand_dotted_keys({"a.b": {"x": 1}, "a.b.c": 2})["a"]["b"] == {"x": 1, "c": 2}
    assert expand_dotted_keys({"a.b.c": 2, "a.b": {"x": 1}})["a"]["b"] == {"c": 2, "x": 1}


def test_a_dotted_key_still_descends_into_a_MARKER() -> None:
    """The P1 partner: a dotted path walks into a marker's kwargs, not over it."""
    from confluid.fluid import Target
    from confluid.merger import expand_dotted_keys

    expanded = expand_dotted_keys({"model": Target("Counter", red=1), "model.blue": 3})

    assert isinstance(expanded["model"], Target)
    assert expanded["model"].kwargs == {"red": 1, "blue": 3}


def test_an_unrelated_dotted_key_keeps_its_written_position() -> None:
    """Position is the arbitration, so the merged ORDER is part of the contract."""
    from confluid.merger import expand_dotted_keys

    assert list(expand_dotted_keys({"z": 1, "a.b": 2, "m": 3})) == ["z", "a", "m"]
