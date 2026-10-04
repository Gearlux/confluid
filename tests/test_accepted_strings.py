# mypy: disable-error-code="attr-defined,arg-type"
"""A string the schema accepts for a number or a true/false field becomes that number or that bool.

YAML 1.1 reads ``5220e6`` and ``40.0e6`` as text — a float needs a dot AND a signed exponent, ``5220.0e+6`` — and the
schema accepts such text for a ``float`` or ``int`` field; it accepts ``"false"`` for a ``bool`` field, which as text
is truthy. The object then holds the value the text stands for: at construction (direct, positional, or from a
document), on a ``@configurable`` function's call, and through ``configure()``. Only a string that validated into an
``int``, ``float`` or ``bool`` is replaced: a ``str`` field, a ``Union[float, str]`` or ``Union[bool, str]``, a value of
another type, a value the schema refuses under ``warn``, and every value under ``off`` stay exactly as given.
"""

from dataclasses import dataclass
from typing import Iterator, Optional, Union

import pytest
from annotated_types import Interval
from pydantic import ValidationError
from typing_extensions import Annotated

import confluid
from confluid import configurable, configure, reset_policy, set_policy, to_pydantic
from confluid.fluid import Target


@configurable
@dataclass(kw_only=True)
class _Radio:
    centre_hz: float = 5180e6
    span_hz: Optional[float] = None
    gain_db: Annotated[float, Interval(ge=-50.0, le=50.0)] = 0.0
    count: int = 1
    label: str = "a"
    either: Union[float, str] = 0.0
    enabled: bool = True
    flag_or_name: Union[bool, str] = True


@configurable
class _Tuner:
    """A plain class: a positional parameter, and a child that may arrive as a deferred marker."""

    def __init__(self, step_hz: float = 1e6, radio: Optional[_Radio] = None) -> None:
        self.step_hz = step_hz
        self.radio = radio


@configurable
def _span(low_hz: float = 0.0, high_hz: float = 1.0) -> float:
    return high_hz - low_hz


@pytest.fixture(autouse=True)
def _strict(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for var in ("CONFLUID_VALIDATE_INIT", "CONFLUID_VALIDATE_YAML", "CONFLUID_VALIDATE_TOOL"):
        monkeypatch.delenv(var, raising=False)
    to_pydantic.cache_clear()
    reset_policy()
    yield
    reset_policy()


def test_a_number_string_becomes_the_number() -> None:
    radio = _Radio(centre_hz="5220e6", span_hz="40.0e6", gain_db="-3e0", count="3")
    assert (radio.centre_hz, radio.span_hz, radio.gain_db, radio.count) == (5220e6, 40e6, -3.0, 3)
    assert type(radio.centre_hz) is float and type(radio.span_hz) is float and type(radio.count) is int


def test_a_positional_string_becomes_the_number() -> None:
    assert _Tuner("2.5e6").step_hz == 2.5e6
    assert type(_Tuner("2.5e6").step_hz) is float


def test_a_function_is_called_with_the_number() -> None:
    assert _span("1e3", high_hz="5e3") == 4000.0


def test_a_document_number_becomes_the_number_and_dumps_as_one() -> None:
    radio = confluid.load("!class:test_accepted_strings._Radio {centre_hz: 5220e6, span_hz: 40.0e6, count: 2}")
    assert type(radio.centre_hz) is float and radio.centre_hz == 5220e6 and radio.span_hz == 40e6
    again = confluid.load(confluid.dump(radio))
    assert type(again.centre_hz) is float and again.centre_hz == 5220e6


def test_a_string_beside_a_deferred_marker_becomes_the_number() -> None:
    tuner = _Tuner(step_hz="5e5", radio=Target(_Radio, centre_hz=5240e6))
    assert tuner.step_hz == 5e5 and type(tuner.step_hz) is float
    assert isinstance(tuner.radio, Target)


def test_the_captured_arguments_hold_the_number() -> None:
    """The dumper falls back on the captured constructor arguments: they hold what the object holds."""
    assert _Tuner(step_hz="5e5").__confluid_kwargs__ == {"step_hz": 5e5}


def test_configure_sets_the_number() -> None:
    radio = _Radio()
    configure(radio, config={"_Radio": {"centre_hz": "5240e6", "count": "4"}})
    assert (radio.centre_hz, radio.count) == (5240e6, 4) and type(radio.centre_hz) is float


def test_a_true_false_string_becomes_the_bool() -> None:
    assert _Radio(enabled="false").enabled is False
    assert _Radio(enabled="true").enabled is True
    assert _Radio(enabled="0").enabled is False and _Radio(enabled="off").enabled is False


def test_a_quoted_document_false_becomes_false_and_dumps_as_one() -> None:
    radio = confluid.load('!class:test_accepted_strings._Radio {enabled: "false"}')
    assert radio.enabled is False
    assert confluid.load(confluid.dump(radio)).enabled is False


def test_configure_sets_the_bool() -> None:
    radio = _Radio()
    configure(radio, config={"_Radio": {"enabled": "false"}})
    assert radio.enabled is False


def test_a_warned_string_becomes_the_number_too() -> None:
    set_policy(init="warn")
    assert type(_Radio(centre_hz="5220e6").centre_hz) is float


# --- what stays as given ---------------------------------------------------------------------------------------------


def test_a_string_field_keeps_its_text() -> None:
    radio = _Radio(label="5e6", either="5e6", flag_or_name="false")
    assert radio.label == "5e6" and radio.either == "5e6" and radio.flag_or_name == "false"


def test_a_number_of_another_type_is_left_alone() -> None:
    """Only strings are replaced: an int given to a float field stays the int it was."""
    radio = _Radio(centre_hz=5)
    assert radio.centre_hz == 5 and type(radio.centre_hz) is int


def test_a_string_the_schema_refuses_still_raises() -> None:
    with pytest.raises(ValidationError, match="unable to parse string as a number"):
        _Radio(centre_hz="abc")
    with pytest.raises(ValidationError, match="less than or equal to 50"):
        _Radio(gain_db="6e1")
    with pytest.raises(ValidationError, match="valid boolean"):
        _Radio(enabled="maybe")


def test_a_refused_string_under_warn_is_kept_as_given() -> None:
    set_policy(init="warn")
    assert _Radio(centre_hz="abc").centre_hz == "abc"


def test_nothing_is_converted_when_validation_is_off() -> None:
    set_policy(init="off")
    assert _Radio(centre_hz="5220e6").centre_hz == "5220e6"
    assert _Radio(enabled="false").enabled == "false"
    radio = _Radio()
    configure(radio, config={"_Radio": {"centre_hz": "5240e6"}})
    assert radio.centre_hz == "5240e6"
