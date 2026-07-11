"""Tests for the I/O contract: ``@output`` properties + ``Mandatory[T]`` inputs.

The contract is what FluxStudio runnable nodes and navigaitor's form-spec read to
render output sockets and required/optional inputs. It lives entirely in confluid
(``confluid.output`` / ``confluid.Mandatory`` + the ``output_specs`` / ``input_specs``
introspection helpers) so every consumer reads one source of truth.
"""

from typing import Any, Optional, Union

import pytest

from confluid import (
    Mandatory,
    configurable,
    dump,
    get_registry,
    input_specs,
    load,
    mandatory_param_names,
    output,
    output_specs,
    to_pydantic,
)
from confluid.mandatory import is_mandatory_annotation


@pytest.fixture(autouse=True)
def setup_registry() -> None:
    get_registry().clear()


# --------------------------------------------------------------------------- #
# @output decorator + ordering
# --------------------------------------------------------------------------- #
def test_output_stamps_the_getter_not_the_property() -> None:
    """``@output`` UNDER ``@property`` stamps the getter (``fget``), the read path of output_specs."""

    class Runner:
        @property
        @output
        def result(self) -> int:
            return 1

    # The marker rides on the getter function, reachable via property.fget.
    assert getattr(Runner.__dict__["result"].fget, "__confluid_output__", False) is True


def test_output_specs_reports_name_type_description() -> None:
    class Runner:
        @property
        @output
        def trained_model(self) -> str:
            """The produced artifact, first line only.

            Extra docstring lines are not part of the description.
            """
            return "m"

    specs = output_specs(Runner)
    assert specs == [{"name": "trained_model", "type": "str", "description": "The produced artifact, first line only."}]


def test_output_specs_excludes_plain_and_readonly_properties() -> None:
    class Runner:
        @property
        def derived(self) -> int:  # a normal derived property, NOT an output
            return 2

        @property
        @output
        def out(self) -> int:
            return 3

    assert [s["name"] for s in output_specs(Runner)] == ["out"]


def test_output_specs_walks_mro_and_subclass_override_wins() -> None:
    class Base:
        @property
        @output
        def out(self) -> int:
            """base."""
            return 1

        @property
        @output
        def base_only(self) -> int:
            """base only."""
            return 0

    class Sub(Base):
        @property
        @output
        def out(self) -> str:  # type: ignore[override]  # override changes the type (intentional)
            """sub override."""
            return "x"

    specs = {s["name"]: s for s in output_specs(Sub)}
    assert set(specs) == {"out", "base_only"}
    assert specs["out"]["type"] == "str"  # most-derived wins
    assert specs["out"]["description"] == "sub override."
    assert specs["base_only"]["type"] == "int"  # inherited output still found


# --------------------------------------------------------------------------- #
# Mandatory[T] marker + input_specs
# --------------------------------------------------------------------------- #
def test_is_mandatory_annotation_and_param_names() -> None:
    class Runner:
        def __init__(self, model: Mandatory[Any], lr: float = 1e-3) -> None:
            self.model = model
            self.lr = lr

    assert mandatory_param_names(Runner) == {"model"}
    # The marker is detected on the raw annotation form.
    assert is_mandatory_annotation(Mandatory[int]) is True
    assert is_mandatory_annotation(int) is False


def test_input_specs_three_way_required_and_nullable() -> None:
    class Runner:
        def __init__(
            self,
            no_default: int,  # required (no default), non-nullable
            marked: Mandatory[Any] = None,  # required via marker, even though defaulted
            opt: Optional[int] = None,  # optional + nullable
            concrete: int = 5,  # optional + non-nullable
        ) -> None:
            self.no_default = no_default
            self.marked = marked
            self.opt = opt
            self.concrete = concrete

    specs = {s["name"]: s for s in input_specs(Runner)}
    assert (specs["no_default"]["required"], specs["no_default"]["nullable"]) == (True, False)
    assert (specs["marked"]["required"], specs["marked"]["nullable"]) == (True, False)
    assert (specs["opt"]["required"], specs["opt"]["nullable"]) == (False, True)
    assert (specs["concrete"]["required"], specs["concrete"]["nullable"]) == (False, False)


def test_input_specs_pep604_optional_is_nullable() -> None:
    class Runner:
        def __init__(self, x: "int | None" = None) -> None:
            self.x = x

    spec = input_specs(Runner)[0]
    assert spec["nullable"] is True and spec["required"] is False


def test_input_specs_skips_self_args_kwargs() -> None:
    class Runner:
        def __init__(self, a: int, *args: Any, **kwargs: Any) -> None:
            self.a = a

    assert [s["name"] for s in input_specs(Runner)] == ["a"]


# --------------------------------------------------------------------------- #
# An @output property never becomes a config field; round-trips cleanly.
# --------------------------------------------------------------------------- #
def test_output_property_is_not_a_config_field() -> None:
    @configurable
    class Runner:
        def __init__(self, lr: float = 1e-3) -> None:
            self.lr = lr

        @property
        @output
        def result(self) -> float:
            return self.lr * 2

    fields = to_pydantic(Runner).model_fields
    assert "result" not in fields
    assert "lr" in fields


def test_output_property_survives_dump_load_round_trip() -> None:
    @configurable
    class Runner:
        def __init__(self, lr: float = 1e-3) -> None:
            self.lr = lr

        @property
        @output
        def result(self) -> float:
            """Derived from lr, never serialized."""
            return self.lr * 2

    live = Runner(lr=0.5)
    assert live.result == 1.0

    reloaded = load(dump(live))
    assert isinstance(reloaded, Runner)
    assert reloaded.lr == 0.5
    # The derived output recomputes on the reconstructed object (never dumped).
    assert reloaded.result == 1.0
    assert "result" not in dump(live)


def test_mandatory_marker_does_not_leak_into_pydantic_schema() -> None:
    @configurable
    class Runner:
        def __init__(self, model: Mandatory[Union[int, str]] = 0) -> None:
            self.model = model

    schema = to_pydantic(Runner).model_json_schema()
    # The marker string must not appear anywhere in the generated JSON Schema.
    assert "__confluid_mandatory__" not in str(schema)
