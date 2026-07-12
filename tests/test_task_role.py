"""Tests for ``@configurable(task=, role=)`` tagging, registry task/role indices,
and ``to_pydantic`` preserving ``Annotated[T, Field(...)]`` constraints."""

from typing import Annotated, Any

import pytest
from pydantic import Field

from confluid import configurable, get_registry
from confluid.lazy import Lazy
from confluid.pydantic_export import to_pydantic


@pytest.fixture(autouse=True)
def clear_registry() -> None:
    get_registry().clear()


# --------------------------------------------------------------------------- #
# task / role tagging + derived category
# --------------------------------------------------------------------------- #


def test_task_role_sets_attrs_and_derives_category() -> None:
    @configurable(task="classification", role="model")
    class M:
        pass

    assert getattr(M, "__confluid_task__") == "classification"
    assert getattr(M, "__confluid_role__") == "model"
    # task + role derive the legacy category so existing discovery keeps working.
    assert getattr(M, "__confluid_category__") == "classification_model"


def test_explicit_category_still_supported() -> None:
    @configurable(category="op")
    class Op:
        pass

    assert getattr(Op, "__confluid_category__") == "op"
    assert "Op" in get_registry().list_classes(category="op")


def test_registry_indexes_task_and_role() -> None:
    @configurable(task="classification", role="model")
    class CM:
        pass

    @configurable(task="classification", role="loss")
    class CL:
        pass

    @configurable(task="segmentation", role="model")
    class SM:
        pass

    reg = get_registry()
    assert reg.list_classes(task="classification") == {"CM", "CL"}
    assert reg.list_classes(role="model") == {"CM", "SM"}
    # Intersection — equivalent to category="classification_model".
    assert reg.list_classes(task="classification", role="model") == {"CM"}
    assert reg.list_classes(category="classification_model") == {"CM"}
    assert reg.list_tasks() == {"classification", "segmentation"}
    assert reg.list_roles() == {"model", "loss"}


def test_unknown_task_filter_returns_empty() -> None:
    @configurable(task="classification", role="model")
    class CM:
        pass

    assert get_registry().list_classes(task="detection") == set()


# --------------------------------------------------------------------------- #
# to_pydantic preserves Annotated Field constraints (code-side steering)
# --------------------------------------------------------------------------- #


def test_to_pydantic_preserves_annotated_field_constraints() -> None:
    @configurable
    class C:
        def __init__(self, n: Annotated[int, Field(gt=0, le=10)] = 1) -> None:
            self.n = n

    schema = to_pydantic(C).model_json_schema()
    assert schema["properties"]["n"]["exclusiveMinimum"] == 0
    assert schema["properties"]["n"]["maximum"] == 10


def test_lazy_param_does_not_leak_marker_and_is_recorded() -> None:
    @configurable
    class C:
        def __init__(self, opt: Lazy[Any] = None) -> None:  # type: ignore[assignment]
            self.opt = opt

    model = to_pydantic(C)
    # The lazy marker is recorded separately, not left as schema metadata.
    assert "opt" in model.model_fields
    assert "opt" in getattr(model, "_confluid_lazy_params", frozenset())


# --------------------------------------------------------------------------- #
# lazy mark (__confluid_lazy__)
# --------------------------------------------------------------------------- #


def test_configurable_lazy_sets_marker() -> None:
    @configurable(category="optimizer", lazy=True)
    class Opt:
        pass

    assert getattr(Opt, "__confluid_lazy__") is True


def test_configurable_lazy_defaults_false_no_marker() -> None:
    @configurable(category="op")
    class Op:
        pass

    assert getattr(Op, "__confluid_lazy__", False) is False


def test_register_lazy_sets_marker_on_third_party_class() -> None:
    from confluid import register

    class _ThirdParty:
        pass

    register(_ThirdParty, category="loader", lazy=True)
    assert getattr(_ThirdParty, "__confluid_lazy__") is True


def test_lazy_marker_survives_reregister_without_lazy() -> None:
    """A re-register that doesn't forward ``lazy`` must not drop the marker."""

    @configurable(category="optimizer", lazy=True)
    class Opt:
        pass

    # Mirror navigaitor's snapshot-restore path (only forwards category).
    get_registry().register_class(Opt, category="optimizer")
    assert getattr(Opt, "__confluid_lazy__") is True


# --------------------------------------------------------------------------- #
# random mark (__confluid_random__)
# --------------------------------------------------------------------------- #


def test_configurable_random_flag_sets_attribute() -> None:
    @configurable(category="op", random=True)
    class StochasticOp:
        pass

    assert getattr(StochasticOp, "__confluid_random__") is True


def test_configurable_default_has_no_random_attribute() -> None:
    @configurable(category="op")
    class DeterministicOp:
        pass

    assert getattr(DeterministicOp, "__confluid_random__", False) is False


# --------------------------------------------------------------------------- #
# constant mark (__confluid_constant__)
# --------------------------------------------------------------------------- #


def test_configurable_constant_flag_sets_attribute() -> None:
    @configurable(category="op", constant=True)
    class PureConfig:
        pass

    assert getattr(PureConfig, "__confluid_constant__") is True


def test_configurable_default_has_no_constant_attribute() -> None:
    @configurable(category="op")
    class OrdinaryOp:
        pass

    assert getattr(OrdinaryOp, "__confluid_constant__", False) is False


def test_configurable_constant_and_random_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="contradictory"):

        @configurable(category="op", constant=True, random=True)
        class Impossible:
            pass


# --------------------------------------------------------------------------- #
# register_class is the ONE stamping authority (Part 4 pins)
# --------------------------------------------------------------------------- #

_ALL_MARKS = (
    "__confluid_configurable__",
    "__confluid_name__",
    "__confluid_category__",
    "__confluid_group__",
    "__confluid_task__",
    "__confluid_role__",
    "__confluid_lazy__",
    "__confluid_random__",
    "__confluid_constant__",
    "__confluid_strict_typing__",
    "__confluid_display_name__",
    "__confluid_no_broadcast__",
)


def _mark_set(cls: type) -> dict:
    return {m: getattr(cls, m) for m in _ALL_MARKS if hasattr(cls, m)}


def test_reregister_with_only_category_preserves_stamp_only_marks() -> None:
    """The widened fallback template: a partial re-register (the navigaitor
    snapshot-restore shape — name + category only) must not drop the
    stamp-only marks set by the original ``@configurable``."""

    @configurable(category="op", random=True, broadcast=False, strict_typing=True, display_name="Fancy Op")
    class R:
        pass

    get_registry().register_class(R, name="R", category="op")

    assert getattr(R, "__confluid_random__") is True
    assert getattr(R, "__confluid_no_broadcast__") is True
    assert getattr(R, "__confluid_strict_typing__") is True
    assert getattr(R, "__confluid_display_name__") == "Fancy Op"


def test_register_class_stamps_same_mark_set_as_decorator() -> None:
    """register_class with the full argument set stamps the exact mark set the
    decorator path produces for the same inputs (single stamping authority)."""

    @configurable(category="op", group="g", task="t", role="r", lazy=True, random=True, display_name="D")
    class ViaDecorator:
        pass

    class ViaRegistry:
        pass

    get_registry().register_class(
        ViaRegistry,
        name="ViaRegistry",
        category="op",
        group="g",
        task="t",
        role="r",
        lazy=True,
        random=True,
        display_name="D",
    )

    dec_marks = _mark_set(ViaDecorator)
    reg_marks = _mark_set(ViaRegistry)
    dec_marks.pop("__confluid_name__")
    reg_marks.pop("__confluid_name__")
    assert dec_marks == reg_marks


def test_register_class_stamps_marks_on_third_party_class() -> None:
    """A register_class-ed third-party class can now carry every mark — the
    five stamp-only marks are no longer decorator-exclusive."""

    class ThirdParty:
        pass

    get_registry().register_class(ThirdParty, category="op", constant=True, strict_typing=True, no_broadcast=True)

    assert getattr(ThirdParty, "__confluid_constant__") is True
    assert getattr(ThirdParty, "__confluid_strict_typing__") is True
    assert getattr(ThirdParty, "__confluid_no_broadcast__") is True
    assert not hasattr(ThirdParty, "__confluid_random__")
