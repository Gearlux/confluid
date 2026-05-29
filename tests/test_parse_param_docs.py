"""Tests for ``confluid.parse_param_docs`` and the multi-line ``_parse_docstring`` fix.

``parse_param_docs`` is the single source of per-parameter help reused across the
workspace: navigaitor turns it into pydantic ``Field(description=...)`` (via
``to_pydantic``) for the visual editor, and FluxStudio turns it into ComfyUI
widget tooltips. The multi-line fix matters because ``re.MULTILINE`` made the old
terminator (``$``) match the end of every physical line, truncating every
description to its first line in BOTH surfaces.
"""

from confluid import parse_param_docs, to_pydantic
from confluid.schema import _parse_docstring


def test_parse_param_docs_reads_class_docstring() -> None:
    class WithClassDoc:
        """A thing.

        Args:
            alpha: The alpha knob.
            beta: The beta knob.
        """

        def __init__(self, alpha: int, beta: str = "x") -> None: ...

    assert parse_param_docs(WithClassDoc) == {"alpha": "The alpha knob.", "beta": "The beta knob."}


def test_parse_param_docs_prefers_init_docstring() -> None:
    class WithInitDoc:
        """Class-level summary (no Args here)."""

        def __init__(self, alpha: int) -> None:
            """Init.

            Args:
                alpha: From the init docstring.
            """

    assert parse_param_docs(WithInitDoc)["alpha"] == "From the init docstring."


def test_parse_param_docs_on_function() -> None:
    def fn(value: str, sample: object) -> str:
        """Do a thing.

        Args:
            value: The expression string.
            sample: The sample object.
        """
        return value

    assert parse_param_docs(fn) == {"value": "The expression string.", "sample": "The sample object."}


def test_parse_param_docs_class_without_init_uses_class_doc() -> None:
    class NoInit:
        """Summary.

        Args:
            ignored: documented but there is no __init__ to bind it to.
        """

    # object.__init__ is not introspected; we fall back to the class docstring.
    assert parse_param_docs(NoInit) == {"ignored": "documented but there is no __init__ to bind it to."}


def test_parse_param_docs_empty_when_no_docstring() -> None:
    class Bare:
        def __init__(self, a: int) -> None: ...

    assert parse_param_docs(Bare) == {}


def test_parse_docstring_captures_multiline_description() -> None:
    doc = """Summary.

    Args:
        x: first line
            continues onto a second line.
        y: single line.
    """
    parsed = _parse_docstring(doc)
    # Continuation line is joined in (the \\Z-vs-$ fix); not truncated at "first line".
    assert parsed["x"] == "first line continues onto a second line."
    assert parsed["y"] == "single line."


def test_to_pydantic_carries_multiline_description() -> None:
    class Documented:
        """Thing.

        Args:
            wrapped: a description that
                spans two physical lines.
        """

        def __init__(self, wrapped: int = 1) -> None: ...

    schema = to_pydantic(Documented).model_json_schema()
    assert schema["properties"]["wrapped"]["description"] == "a description that spans two physical lines."


def test_parse_docstring_single_line_unchanged() -> None:
    # Regression pin for the historical behavior.
    assert _parse_docstring("Args:\n  x: d") == {"x": "d"}
