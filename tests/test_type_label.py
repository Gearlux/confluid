"""A parameter's type as a person reads it: the values of a choice, the members of a union, the items of a list."""

import inspect
from typing import Annotated, Any, Callable, Dict, List, Literal, Optional, Tuple, Union

import pytest
from annotated_types import Interval

from confluid import configurable, get_hierarchy, get_hierarchy_from_instance, type_label


@pytest.mark.parametrize(
    "annotation, label",
    [
        (int, "int"),
        (bool, "bool"),
        (Literal["text", "json"], "text | json"),
        (Literal[1, 2], "1 | 2"),
        (Optional[str], "str | None"),
        (str | None, "str | None"),
        (Union[int, Literal["resume"]], "int | resume"),
        (Union[int, Literal["auto"], None], "int | auto | None"),
        (List[str], "list[str]"),
        (list[int], "list[int]"),
        (Dict[str, float], "dict[str, float]"),
        (Tuple[float, float], "tuple[float, float]"),
        (Tuple[int, ...], "tuple[int, ...]"),
        (Optional[List[str]], "list[str] | None"),
        (Callable[[int], str], "Callable[[int], str]"),
        (Annotated[float, Interval(ge=0.0)], "float"),
        (Annotated[Optional[int], Interval(ge=0)], "int | None"),
        (Any, "Any"),
        (type(None), "None"),
        ("SomeForwardRef", "SomeForwardRef"),
    ],
)
def test_a_type_is_named_as_a_person_reads_it(annotation: Any, label: str) -> None:
    assert type_label(annotation) == label


def test_an_unannotated_parameter_keeps_the_name_it_had() -> None:
    assert type_label(inspect.Parameter.empty) == "_empty"


@configurable
class _Format:
    """A setting with a choice, a resumable start and an optional label.

    Args:
        plan_format: How the plan is printed.
        start: Where the run starts.
        desc: A label.
    """

    def __init__(
        self,
        plan_format: Literal["text", "json"] = "text",
        start: Union[int, Literal["resume"]] = 0,
        desc: Optional[str] = None,
    ) -> None:
        self.plan_format = plan_format
        self.start = start
        self.desc = desc


def test_the_hierarchy_of_a_class_names_each_type_readably() -> None:
    hierarchy = get_hierarchy(_Format)
    assert hierarchy["_Format.plan_format"][0] == "text | json"
    assert hierarchy["_Format.start"][0] == "int | resume"
    assert hierarchy["_Format.desc"][0] == "str | None"


def test_the_hierarchy_of_an_instance_names_each_type_readably() -> None:
    hierarchy = get_hierarchy_from_instance(_Format(plan_format="json"))
    assert hierarchy["_Format.plan_format"] == ("text | json", "json", "How the plan is printed.")
    assert hierarchy["_Format.start"][0] == "int | resume"


def test_the_hierarchy_of_a_function_names_each_type_readably() -> None:
    def run(dry_run: bool = False, plan_format: Literal["text", "json"] = "text") -> None:
        return None

    hierarchy = get_hierarchy(run)
    assert hierarchy["dry_run"][0] == "bool"
    assert hierarchy["plan_format"][0] == "text | json"
