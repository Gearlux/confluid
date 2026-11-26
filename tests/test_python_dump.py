"""``dump_python`` — a live object graph as a Python script that builds it again.

Every test runs the emitted text and compares the rebuilt graph with the original through ``dump()``, the one
reconstruction rule, so "the script builds the same object" is measured, never read off the text.
"""

import enum
import math
import pathlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import pytest

from confluid import PythonDumpError, configurable, dump, dump_python, register_dump_spelling
from confluid.fluid import Target


class Colour(enum.Enum):
    RED = "red"
    BLUE = "blue"


def scale(x: float) -> float:
    return 2.0 * x


@configurable
@dataclass(kw_only=True)
class Leaf:
    rate_hz: float = 1.0
    name: str = "leaf"


@configurable
@dataclass(kw_only=True)
class Branch:
    leaf: Optional[Leaf] = None
    leaves: List[Leaf] = field(default_factory=list)
    span: Tuple[float, float] = (0.0, 1.0)
    table: Dict[Any, Any] = field(default_factory=dict)
    colour: Colour = Colour.RED
    path: Optional[pathlib.Path] = None
    kind: Optional[type] = None
    hook: Any = None
    flag: bool = False
    count: Optional[int] = 3


@configurable
class Plain:
    def __init__(self, size: int = 1, label: str = "x") -> None:
        self.size = size
        self.label = label


class Opaque:
    """Not configurable: the script has no way to rebuild it."""


class Point:
    """Not configurable; registered with a dump spelling below."""

    def __init__(self, x: float, y: float) -> None:
        self.x = x
        self.y = y


def _run(text: str, name: str = "obj") -> Any:
    namespace: Dict[str, Any] = {"__name__": "rebuilt"}
    exec(compile(text, "<dump_python>", "exec"), namespace)
    return namespace[name]


def test_a_nested_graph_is_rebuilt_by_the_script() -> None:
    original = Branch(
        leaf=Leaf(rate_hz=2.5e6, name="first"),
        leaves=[Leaf(rate_hz=1.0), Leaf(name='it\'s "quoted"\nand $HOME')],
        span=(-3.0, 3.0),
        table={"a": 1, 2: [1.5, None], "nested": {"x": (1, 2)}},
        colour=Colour.BLUE,
        path=pathlib.Path("/data/run"),
        kind=Leaf,
        hook=scale,
        flag=True,
        count=None,
    )
    text = dump_python(original)
    rebuilt = _run(text)
    assert type(rebuilt) is Branch
    assert dump(rebuilt) == dump(original)
    assert rebuilt.colour is Colour.BLUE
    assert rebuilt.kind is Leaf and rebuilt.hook is scale
    assert rebuilt.span == (-3.0, 3.0) and isinstance(rebuilt.span, tuple)
    assert rebuilt.leaves[1].name == 'it\'s "quoted"\nand $HOME'
    assert "from pathlib import Path" in text  # never the flavour's module (pathlib._local on 3.13)


def test_the_script_imports_each_class_by_its_module_and_names_the_variable() -> None:
    text = dump_python(Branch(leaf=Leaf()), name="generator")
    assert f"from {Branch.__module__} import Branch, Colour, Leaf" in text  # Colour: the RED default is written
    assert "\ngenerator = Branch(" in text
    assert type(_run(text, "generator")) is Branch


def test_a_class_is_imported_from_the_public_package_above_its_private_module() -> None:
    text = dump_python(Branch(hook=Target), name="obj")  # confluid.fluid is public: kept
    assert "from confluid.fluid import Target" in text
    text = dump_python(Branch(table={"p": pathlib.PurePosixPath("/x")}))
    assert "from pathlib import PurePosixPath" in text
    assert _run(text).table["p"] == pathlib.PurePosixPath("/x")


def test_special_floats_survive() -> None:
    original = Branch(table={"inf": math.inf, "ninf": -math.inf, "zero": -0.0})
    rebuilt = _run(dump_python(original))
    assert rebuilt.table["inf"] == math.inf and rebuilt.table["ninf"] == -math.inf
    assert math.copysign(1.0, rebuilt.table["zero"]) == -1.0
    nan = _run(dump_python(Branch(table={"nan": math.nan})))
    assert math.isnan(nan.table["nan"])


def test_a_plain_configurable_class_is_rebuilt_too() -> None:
    rebuilt = _run(dump_python(Plain(size=7, label="seven")))
    assert (rebuilt.size, rebuilt.label) == (7, "seven")


def test_a_shared_object_stays_one_object() -> None:
    leaf = Leaf(rate_hz=4.0)
    text = dump_python(Branch(leaf=leaf, leaves=[leaf]))
    rebuilt = _run(text)
    assert rebuilt.leaf is rebuilt.leaves[0]
    assert text.count("Leaf(") == 1


def test_a_long_call_wraps_one_argument_per_line_and_a_short_one_stays_on_one_line() -> None:
    text = dump_python(Branch(leaf=Leaf(), leaves=[Leaf(rate_hz=float(i)) for i in range(6)]))
    assert "obj = Branch(\n    leaf=Leaf(rate_hz=1.0, name='leaf'),\n    leaves=[\n" in text
    assert all(len(line) <= 120 for line in text.splitlines())


def test_the_header_is_written_as_comments_and_the_text_is_stable() -> None:
    original = Branch(leaf=Leaf())
    text = dump_python(original, header="Made by a test.\nRun: python this.py")
    assert text.startswith("# Made by a test.\n# Run: python this.py\n")
    assert dump_python(original, header="Made by a test.\nRun: python this.py") == text


def test_two_classes_of_one_name_are_both_imported(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "pydump_other_leaf.py").write_text(
        "from confluid import configurable\n\n\n"
        "@configurable\nclass Leaf:\n    def __init__(self, size: int = 1) -> None:\n        self.size = size\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    import pydump_other_leaf  # type: ignore[import-not-found]

    original = Branch(leaf=Leaf(rate_hz=3.0), hook=pydump_other_leaf.Leaf(size=9))
    text = dump_python(original)
    assert "from pydump_other_leaf import Leaf as pydump_other_leaf_Leaf" in text
    rebuilt = _run(text)
    assert type(rebuilt.leaf) is Leaf and type(rebuilt.hook) is pydump_other_leaf.Leaf
    assert rebuilt.hook.size == 9


def test_a_class_whose_path_imports_to_something_else_is_refused() -> None:
    @configurable
    class Mislabelled:
        def __init__(self, a: int = 1) -> None:
            self.a = a

    Mislabelled.__qualname__ = "Path"
    Mislabelled.__module__ = "pathlib"
    with pytest.raises(PythonDumpError, match=r"obj\.table\['x'\].*pathlib\.Path.*cannot be imported"):
        dump_python(Branch(table={"x": Mislabelled()}))


def test_a_registered_dump_spelling_is_used() -> None:
    register_dump_spelling(Point, lambda p: Target(Point, x=p.x, y=p.y))
    rebuilt = _run(dump_python(Branch(hook=Point(1.0, 2.0))))
    assert isinstance(rebuilt.hook, Point) and (rebuilt.hook.x, rebuilt.hook.y) == (1.0, 2.0)
    register_dump_spelling(Point, lambda p: [p.x, p.y])
    assert _run(dump_python(Branch(hook=Point(1.0, 2.0)))).hook == [1.0, 2.0]


def test_numpy_scalars_are_written_as_python_numbers() -> None:
    np = pytest.importorskip("numpy")
    rebuilt = _run(dump_python(Leaf(rate_hz=np.float64(2.5))))
    assert rebuilt.rate_hz == 2.5 and type(rebuilt.rate_hz) is float


@pytest.mark.parametrize(
    ("value", "where", "what"),
    [
        (Opaque(), r"obj\.hook", r"Opaque"),
        (Target(Leaf), r"obj\.hook", r"marker"),
        ({1, 2}, r"obj\.hook", r"set"),
    ],
)
def test_a_value_the_script_cannot_build_is_refused_naming_where_it_is(value: Any, where: str, what: str) -> None:
    with pytest.raises(PythonDumpError, match=rf"{where}.*{what}"):
        dump_python(Branch(hook=value))


def test_a_class_defined_in_a_function_is_refused() -> None:
    @configurable
    class Local:
        def __init__(self, a: int = 1) -> None:
            self.a = a

    with pytest.raises(PythonDumpError, match=r"obj\.hook.*Local.*cannot be imported"):
        dump_python(Branch(hook=Local()))


def test_a_cycle_is_refused() -> None:
    holder = Branch()
    holder.hook = holder
    with pytest.raises(PythonDumpError, match=r"obj\.hook.*refers back to itself"):
        dump_python(holder)


def test_the_error_is_a_value_error_too() -> None:
    assert issubclass(PythonDumpError, ValueError)


def test_a_variable_name_that_is_not_an_identifier_is_refused() -> None:
    with pytest.raises(PythonDumpError, match="not a Python name"):
        dump_python(Leaf(), name="my obj")
    with pytest.raises(PythonDumpError, match="not a Python name"):
        dump_python(Leaf(), name="class")
