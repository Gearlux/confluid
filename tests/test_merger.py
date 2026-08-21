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


# ---------------------------------------------------------------------------
# Live leaves keep their IDENTITY through a merge (BUGS-2026-08-19 PA5 / PA12 /
# CD3 / CD4). `_preserve_identity_copy` deep-copied every non-marker value, so a
# dataset or a model handed into a document came out as a COPY, an uncopyable one
# (a lock, an open file) crashed with a raw TypeError, and a ScopeBlock's inner
# anchored markers were duplicated. A merge must not mutate its inputs — that is
# the CONTAINERS' job, which are still rebuilt; a leaf is never mutated, so it is
# never copied.
# ---------------------------------------------------------------------------


class _Live:
    """A stand-in for a dataset / model object that is not a marker."""


def test_a_live_leaf_keeps_identity_through_deep_merge() -> None:
    from confluid.merger import deep_merge

    live = _Live()
    merged = deep_merge({"ds": live, "a": {"x": 1}}, {"a": {"y": 2}})
    assert merged["ds"] is live
    overlaid = deep_merge({"a": 1}, {"ds": live})
    assert overlaid["ds"] is live


def test_a_live_leaf_keeps_identity_through_expand_dotted_keys() -> None:
    from confluid.merger import expand_dotted_keys

    live = _Live()
    assert expand_dotted_keys({"ds": live, "a.b": 1})["ds"] is live


def test_an_uncopyable_leaf_merges_instead_of_raising() -> None:
    """A lock is the canonical uncopyable object (`TypeError: cannot pickle`)."""
    import threading

    from confluid.merger import deep_merge, expand_dotted_keys

    lock = threading.Lock()
    assert deep_merge({"lock": lock}, {"x": 1})["lock"] is lock
    assert expand_dotted_keys({"lock": lock, "a.b": 1})["lock"] is lock


def test_a_scope_blocks_inner_markers_keep_identity_through_deep_merge() -> None:
    """PA5 — the anchored marker INSIDE a block (`shared: *p`) must stay the same
    object as the one at the top level, or one instance becomes two."""
    from confluid.fluid import ScopeBlock, Target
    from confluid.merger import deep_merge

    proto = Target("Box", size=3)
    base = {"proto": proto, "blk": ScopeBlock({"x": None}, False, {"shared": proto})}
    merged = deep_merge(base, {"unrelated": 1})
    assert merged["proto"] is proto
    assert merged["blk"].contents["shared"] is proto
    assert merged["blk"] is not base["blk"], "the block itself is a container — rebuilt, not shared"
    assert merged["blk"].dims == {"x": None} and merged["blk"].negate is False


def test_containers_are_still_rebuilt_so_the_base_is_never_mutated() -> None:
    """The con case: what the copy exists for is unchanged."""
    from confluid.merger import deep_merge, expand_dotted_keys

    base = {"a": {"x": [1, 2], "t": (1, [2])}}
    merged = deep_merge(base, {"a": {"y": 1}})
    merged["a"]["x"].append(3)
    merged["a"]["t"][1].append(9)
    assert base["a"]["x"] == [1, 2]
    assert base["a"]["t"] == (1, [2])
    assert isinstance(merged["a"]["t"], tuple)
    expanded = expand_dotted_keys({"a": {"x": [1]}, "a.y": 2})
    expanded["a"]["x"].append(5)


# --------------------------------------------------------------------------- marker over marker (CD5)


def test_a_marker_over_a_marker_of_the_same_target_tunes_it() -> None:
    """CD5 — the marker spelling of an override must compose like the mapping
    spelling: same target, kwargs merge (the base marker itself is never mutated)."""
    from confluid.fluid import Target

    base_marker = Target("Model")
    base_marker.kwargs["layers"] = 5
    overlay_marker = Target("Model")
    overlay_marker.kwargs["lr"] = 0.1
    merged = deep_merge({"model": base_marker}, {"model": overlay_marker})
    assert merged["model"].kwargs == {"layers": 5, "lr": 0.1}
    assert base_marker.kwargs == {"layers": 5}


def test_a_marker_over_a_marker_of_a_different_target_still_replaces() -> None:
    """CD5 con — a different class is a genuine swap, not a tune."""
    from confluid.fluid import Target

    adam = Target("Adam")
    adam.kwargs["betas"] = 0.8
    sgd = Target("SGD")
    sgd.kwargs["lr"] = 0.5
    merged = deep_merge({"opt": adam}, {"opt": sgd})
    assert merged["opt"].target == "SGD" and merged["opt"].kwargs == {"lr": 0.5}


def test_a_partial_over_a_class_marker_still_replaces() -> None:
    """CD5 con — a different marker KIND changes intent (deferral), so it replaces."""
    from confluid.fluid import PartialClass, Target

    eager = Target("Model")
    eager.kwargs["layers"] = 5
    deferred = PartialClass("Model")
    deferred.kwargs["lr"] = 0.1
    merged = deep_merge({"model": eager}, {"model": deferred})
    assert isinstance(merged["model"], PartialClass) and merged["model"].kwargs == {"lr": 0.1}
