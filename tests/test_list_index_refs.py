"""Tests for list-index reference syntax in ``!ref:`` paths.

Covers the four shapes added to ``confluid.resolver._lookup_path``:

* ``items.0`` — numeric segment after a dot.
* ``items[0]`` / ``items[-1]`` — bracketed integer literal.
* ``items[idx]`` — bracketed name resolved against the same context.
* Combinations: ``packs[idx].name``, ``packs[0].sub[1]``.

End-to-end (load+materialize) cases verify the resolver hooks all the way
through to the late-bound ``Reference`` flow, which is where the user's
``!ref:drone_labels[drone_index]`` config exercises this code.
"""

from typing import Any, Dict

from confluid import configurable
from confluid.loader import load, materialize
from confluid.resolver import Resolver, _parse_path_segments

# ---------- Tokenizer ------------------------------------------------------


def test_parse_dotted_dict_keys() -> None:
    assert _parse_path_segments("a.b.c") == [("key", "a"), ("key", "b"), ("key", "c")]


def test_parse_numeric_after_dot_becomes_idx() -> None:
    assert _parse_path_segments("items.0") == [("key", "items"), ("idx", 0)]


def test_parse_bracketed_int_literal() -> None:
    assert _parse_path_segments("items[3]") == [("key", "items"), ("idx", 3)]


def test_parse_bracketed_negative_int() -> None:
    assert _parse_path_segments("items[-1]") == [("key", "items"), ("idx", -1)]


def test_parse_bracketed_barename_becomes_idxref() -> None:
    assert _parse_path_segments("items[idx]") == [("key", "items"), ("idxref", "idx")]


def test_parse_mixed_combinations() -> None:
    assert _parse_path_segments("packs[idx].name") == [
        ("key", "packs"),
        ("idxref", "idx"),
        ("key", "name"),
    ]
    assert _parse_path_segments("packs[0].sub[1]") == [
        ("key", "packs"),
        ("idx", 0),
        ("key", "sub"),
        ("idx", 1),
    ]


def test_parse_rejects_empty_brackets() -> None:
    """Malformed paths return None so the caller treats them as unresolved
    instead of raising mid-walk."""
    assert _parse_path_segments("items[]") is None


# ---------- Walker (via Resolver._lookup_path) -----------------------------


def _lookup(context: Dict[str, Any], path: str) -> Any:
    return Resolver(context=context)._lookup_path(path, context)


def test_lookup_dotted_numeric_indexes_list() -> None:
    ctx = {"items": ["alpha", "bravo", "charlie"]}
    assert _lookup(ctx, "items.0") == "alpha"
    assert _lookup(ctx, "items.2") == "charlie"


def test_lookup_bracketed_int_indexes_list() -> None:
    ctx = {"items": ["alpha", "bravo", "charlie"]}
    assert _lookup(ctx, "items[1]") == "bravo"
    assert _lookup(ctx, "items[-1]") == "charlie"


def test_lookup_bracketed_name_resolves_against_context() -> None:
    """The user's literal use case: a top-level int decides which list
    entry the outer ref returns."""
    ctx = {"drone_labels": ["YUNZHUO H16", "DAUTEL EVO NANO", "DJI MINI3"], "drone_index": 2}
    assert _lookup(ctx, "drone_labels[drone_index]") == "DJI MINI3"


def test_lookup_bracketed_name_into_dict() -> None:
    """When ``current`` is a dict and the resolved index value is a string,
    use it as a dict key. Same syntax handles both list and dict targets."""
    ctx = {"picks": {"alpha": 1, "beta": 2}, "which": "alpha"}
    assert _lookup(ctx, "picks[which]") == 1


def test_lookup_index_then_attribute_walk() -> None:
    ctx = {"packs": [{"name": "first"}, {"name": "second"}]}
    assert _lookup(ctx, "packs[0].name") == "first"
    assert _lookup(ctx, "packs[1].name") == "second"


def test_lookup_named_index_then_attribute() -> None:
    ctx = {"packs": [{"name": "first"}, {"name": "second"}], "idx": 1}
    assert _lookup(ctx, "packs[idx].name") == "second"


def test_lookup_returns_none_when_index_out_of_range() -> None:
    ctx = {"items": ["alpha", "bravo"]}
    assert _lookup(ctx, "items[5]") is None
    assert _lookup(ctx, "items.5") is None


def test_lookup_returns_none_when_idxref_unknown() -> None:
    ctx = {"items": ["a", "b"], "drone_index": 0}
    # `nope` doesn't exist → graceful None
    assert _lookup(ctx, "items[nope]") is None


def test_lookup_returns_none_when_idxref_resolves_to_wrong_type() -> None:
    """``items[which]`` where ``items`` is a list and ``which`` resolves to
    a string can't index — return None instead of crashing."""
    ctx = {"items": ["a", "b"], "which": "alpha"}
    assert _lookup(ctx, "items[which]") is None


def test_lookup_preserves_legacy_dotted_dict_walk() -> None:
    ctx = {"a": {"b": {"c": 42}}}
    assert _lookup(ctx, "a.b.c") == 42


def test_lookup_literal_full_key_still_wins() -> None:
    """Keys that contain dots / brackets in their literal form keep
    matching the old behavior (direct dict lookup before tokenizing)."""
    ctx = {"weird.key[name]": "literal"}
    assert _lookup(ctx, "weird.key[name]") == "literal"


# ---------- End-to-end through load() / materialize() ---------------------


def test_e2e_drone_labels_index_pattern(tmp_path: Any) -> None:
    """The user's actual pattern: a labels list + a single index key →
    one resolved ``drone`` value used everywhere downstream.

    Scalar-target References are deferred to flow time (so CLI overrides
    of the source key can flow through) — we materialize the Reference
    explicitly to get the final string.
    """
    from confluid.fluid import Reference

    cfg = tmp_path / "main.yaml"
    cfg.write_text(
        """
drone_labels:
  - YUNZHUO H16
  - DAUTEL EVO NANO
  - DJI MINI3
  - DJI FPV COMBO
drone_index: 2
drone: !ref:drone_labels[drone_index]
"""
    )
    result = load(str(cfg))
    assert isinstance(result["drone"], Reference)
    drone = materialize(result["drone"], context=result)
    assert drone == "DJI MINI3"
    # Override-flowthrough: bumping drone_index AFTER load and re-flowing
    # the Reference must pick up the new value. This is exactly the path
    # liquifai uses when CLI ``--drone_index 8`` lands as a deep_merge
    # into ``config_data`` after ``confluid.load(flow=False)``.
    result["drone_index"] = 3
    drone2 = materialize(result["drone"], context=result)
    assert drone2 == "DJI FPV COMBO"


def test_e2e_index_ref_inside_class_kwargs(tmp_path: Any) -> None:
    """The Reference is resolved when the receiving class is materialized.
    Verifies the bracket syntax survives the full Reference → flow path,
    not just the early-load walk."""

    @configurable
    class _Pick:
        def __init__(self, name: str) -> None:
            self.name = name

    from confluid import register

    register(_Pick)
    cfg = tmp_path / "main.yaml"
    cfg.write_text(
        """
labels:
  - alpha
  - bravo
  - charlie
idx: 1
chosen: !class:_Pick
  name: !ref:labels[idx]
"""
    )
    raw = load(str(cfg), flow=False)
    chosen = materialize(raw["chosen"], context=raw)
    assert isinstance(chosen, _Pick)
    assert chosen.name == "bravo"


def test_e2e_combined_index_and_attribute(tmp_path: Any) -> None:
    cfg = tmp_path / "main.yaml"
    cfg.write_text(
        """
packs:
  - name: first
    fft: 256
  - name: second
    fft: 512
which: 1
selected_name: !ref:packs[which].name
selected_fft: !ref:packs[which].fft
"""
    )
    result = load(str(cfg))
    assert materialize(result["selected_name"], context=result) == "second"
    assert materialize(result["selected_fft"], context=result) == 512


def test_e2e_negative_index(tmp_path: Any) -> None:
    cfg = tmp_path / "main.yaml"
    cfg.write_text(
        """
items: [a, b, c]
last: !ref:items[-1]
"""
    )
    result = load(str(cfg))
    assert materialize(result["last"], context=result) == "c"


# ---------- Regression: top-level ``!ref:`` to whole list still works ------


def test_whole_list_ref_unchanged(tmp_path: Any) -> None:
    """The pre-existing behavior — referencing an entire list — must not
    regress when the new bracket syntax is added."""
    cfg = tmp_path / "main.yaml"
    cfg.write_text(
        """
items: [a, b, c]
copy: !ref:items
"""
    )
    result = load(str(cfg))
    assert result["copy"] == ["a", "b", "c"]
