"""Tests for ``@configurable(group=)`` / ``register(group=)`` tagging and the
registry's ``_by_group`` index.

``group`` is a free-form, path-like presentation hint (FluxStudio nests a node's
palette folder by it) — orthogonal to the ``category`` / ``task`` / ``role``
discovery contract. It must set ``__confluid_group__``, index in the registry,
and survive a tagless re-register (navigaitor's snapshot restore)."""

from confluid import configurable, get_registry, register


def setup_function() -> None:
    get_registry().clear()


def test_group_sets_attr_and_indexes() -> None:
    @configurable(category="op", group="numpy")
    class StandardizeOp:
        pass

    assert getattr(StandardizeOp, "__confluid_group__") == "numpy"
    reg = get_registry()
    assert reg.list_classes(group="numpy") == {"StandardizeOp"}
    assert reg.list_groups() == {"numpy"}


def test_group_is_path_like_and_free_form() -> None:
    @configurable(category="op", group="fft/numpy")
    class FftOp:
        pass

    assert getattr(FftOp, "__confluid_group__") == "fft/numpy"
    assert get_registry().list_classes(group="fft/numpy") == {"FftOp"}


def test_group_intersects_with_category() -> None:
    @configurable(category="op", group="structure")
    class CopyInputOp:
        pass

    @configurable(category="source", group="structure")
    class WeirdSource:  # same group, different category — exercises the intersection
        pass

    reg = get_registry()
    assert reg.list_classes(group="structure") == {"CopyInputOp", "WeirdSource"}
    # category × group intersect, like task × role.
    assert reg.list_classes(category="op", group="structure") == {"CopyInputOp"}


def test_absent_group_is_none_and_unindexed() -> None:
    @configurable(category="op")
    class Untagged:
        pass

    assert getattr(Untagged, "__confluid_group__", None) is None
    assert get_registry().list_groups() == set()


def test_unknown_group_filter_returns_empty() -> None:
    @configurable(category="op", group="numpy")
    class StandardizeOp:
        pass

    assert get_registry().list_classes(group="torch") == set()


def test_register_third_party_class_with_group() -> None:
    class ThirdParty:
        pass

    register(ThirdParty, category="op", group="external")
    assert getattr(ThirdParty, "__confluid_group__") == "external"
    assert get_registry().list_classes(group="external") == {"ThirdParty"}


def test_group_survives_tagless_reregister() -> None:
    """A re-register that forwards only ``category`` (navigaitor snapshot restore)
    must not drop a ``group`` already stamped by the original ``@configurable``."""

    @configurable(category="op", group="numpy")
    class StandardizeOp:
        pass

    # Re-register without forwarding group — must fall back to the class attr.
    get_registry().register_class(StandardizeOp, category="op")
    assert getattr(StandardizeOp, "__confluid_group__") == "numpy"
    assert get_registry().list_classes(group="numpy") == {"StandardizeOp"}


def test_clear_drops_group_index() -> None:
    @configurable(category="op", group="numpy")
    class StandardizeOp:
        pass

    get_registry().clear()
    assert get_registry().list_groups() == set()
