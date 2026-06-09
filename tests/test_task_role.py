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
