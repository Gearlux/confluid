"""``!class:`` / ``!lazy:`` / ``flow()`` accept any CALLABLE target, not just classes.

Regression coverage for two fixes that let a plain builder *function* (the
canonical case: ``torchvision.models.detection.fasterrcnn_resnet50_fpn``) be a
first-class config target:

1. ``flow()`` used to introspect ``target.__init__`` for ALL targets. For a
   function that resolves to ``object.__init__`` → ``(*args, **kwargs)``, so the
   ctor kwarg filter kept only keys literally named ``args``/``kwargs`` and
   dropped every real kwarg — the function then built with its defaults. The
   fix introspects the callable's OWN signature for non-classes.
2. ``resolve_class()`` rejected non-class callables (``isinstance(_, type)``),
   so a function dotted-path raised "Cannot resolve class". The fix accepts any
   callable.
3. ``to_pydantic()`` rejected non-class targets (``isinstance(cls, type)``), so a
   builder function could not generate a config schema — meaning a registered
   builder (``register(fasterrcnn_*, role="model")``) could not be surfaced by
   navigaitor's class-based form-spec / MCP schema. The fix introspects the
   callable's OWN signature; two JSON-Schema landmines a real torchvision builder
   carries are coerced to ``Any`` (an Enum whose member VALUES are not JSON
   primitives — the ``*_Weights`` enums — and a ``Callable[...]`` param —
   ssdlite320's ``norm_layer``).

A local function stands in for torchvision so the test has no heavy deps.
"""

import enum
import os.path
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

import confluid
from confluid import LazyClass, flow, to_pydantic
from confluid.registry import resolve_class


def _make_widget(*, size: int = 1, color: str = "red", **kwargs: Any) -> Dict[str, Any]:
    """A builder FUNCTION (not a class) with named kwargs + ``**kwargs``."""
    return {"size": size, "color": color, "extra": dict(kwargs)}


@dataclass
class _Recipe:
    """A non-JSON-primitive enum-member value (mirrors torchvision's ``Weights``)."""

    factory: type


class _Preset(enum.Enum):
    """An Enum whose member VALUE is a dataclass — not a JSON primitive."""

    DEFAULT = _Recipe(factory=int)


def _make_model(
    *,
    num_classes: Optional[int] = None,
    preset: Optional[_Preset] = _Preset.DEFAULT,
    norm_layer: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    """A builder FUNCTION carrying both JSON-Schema landmines (enum + Callable)."""
    return {"num_classes": num_classes, "preset": preset, "norm_layer": norm_layer}


def test_flow_function_target_honors_stored_kwargs() -> None:
    out = flow(LazyClass(_make_widget, size=3, color="blue"))
    assert out == {"size": 3, "color": "blue", "extra": {}}


def test_flow_function_target_honors_runtime_kwargs() -> None:
    out = flow(LazyClass(_make_widget), size=5)
    assert out["size"] == 5 and out["color"] == "red"


def test_flow_function_runtime_overrides_stored() -> None:
    out = flow(LazyClass(_make_widget, size=3), size=9)
    assert out["size"] == 9


def test_resolve_class_resolves_function_dotted_path() -> None:
    """A function dotted path resolves (used to return None for non-types)."""
    assert resolve_class("os.path.join") is os.path.join


def test_resolve_class_still_resolves_class_dotted_path() -> None:
    """Class targets are unaffected by the callable-widening."""
    from collections import OrderedDict

    assert resolve_class("collections.OrderedDict") is OrderedDict


def test_yaml_lazy_function_target_materializes_and_flows() -> None:
    """``!lazy:<function path>`` resolves, stays deferred through load, then flows."""
    doc = confluid.load(
        f"widget: !lazy:{_make_widget.__module__}.{_make_widget.__qualname__}\n  size: 7\n",
        flow=False,
    )
    built = flow(doc["widget"], color="green")
    assert built == {"size": 7, "color": "green", "extra": {}}


def test_to_pydantic_accepts_function_target() -> None:
    """``to_pydantic`` introspects a function's OWN signature (``**kwargs`` skipped)."""
    model = to_pydantic(_make_widget)
    assert set(model.model_fields) == {"size", "color"}
    cfg = model(size=4, color="teal")
    assert cfg.model_dump() == {"size": 4, "color": "teal"}


def test_to_pydantic_function_records_dotted_target() -> None:
    """The generated model carries the function's importable path for the serializer."""
    from confluid.pydantic_export import confluid_class_of

    model = to_pydantic(_make_widget)
    assert confluid_class_of(model) == f"{_make_widget.__module__}.{_make_widget.__qualname__}"


def test_to_pydantic_function_schema_coerces_landmines_to_any() -> None:
    """A non-primitive-valued Enum and a ``Callable`` param become ``Any`` so the
    JSON Schema generates (the torchvision ``*_Weights`` / ``norm_layer`` shapes)."""
    model = to_pydantic(_make_model)
    assert set(model.model_fields) == {"num_classes", "preset", "norm_layer"}
    # The crux: model_json_schema() would raise on the raw Enum / Callable types.
    schema = model.model_json_schema()
    assert "preset" in schema["properties"] and "norm_layer" in schema["properties"]


def test_to_pydantic_rejects_non_callable() -> None:
    """A non-callable (e.g. an int) still raises ``TypeError``."""
    import pytest

    with pytest.raises(TypeError):
        to_pydantic(42)  # type: ignore[arg-type]
