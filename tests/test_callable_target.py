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

import pytest

import confluid
from confluid import LazyClass, configurable, dump, flow, get_registry, load, set_policy, to_pydantic
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


# --- @configurable / register on a FUNCTION (not just a class) ----------------


@pytest.fixture()
def clean_registry() -> Any:
    """Clear the registry so a test's own @configurable functions register fresh."""
    get_registry().clear()
    yield
    get_registry().clear()


def test_configurable_function_registers_and_marks(clean_registry: Any) -> None:
    @configurable
    def build(size: int = 4, color: str = "red") -> Dict[str, Any]:
        return {"size": size, "color": color}

    assert get_registry().get_class("build") is build
    assert resolve_class("build") is build
    assert getattr(build, "__confluid_configurable__", False) is True
    assert getattr(build, "__confluid_validated__", False) is True
    # Introspection survives the functools.wraps wrapper.
    assert set(to_pydantic(build).model_fields) == {"size", "color"}


def test_configurable_function_validates_calls(clean_registry: Any) -> None:
    from pydantic import ValidationError

    @configurable
    def build(size: int = 4) -> int:
        return size

    assert build(size=8) == 8  # happy path passes through
    with pytest.raises(ValidationError):
        build(bogus=1)  # type: ignore[call-arg]  # unknown kwarg → structured pydantic error
    with pytest.raises(ValidationError):
        build(size="not-an-int")  # type: ignore[arg-type]  # type-invalid → pydantic error


def test_configurable_function_validate_false_skips_wrap(clean_registry: Any) -> None:
    @configurable(validate=False)
    def raw(x: int = 1) -> int:
        return x

    assert getattr(raw, "__confluid_validated__", False) is False
    # No confluid validation — an unknown kwarg raises Python's native TypeError.
    with pytest.raises(TypeError):
        raw(nope=1)  # type: ignore[call-arg]


def test_configurable_function_off_policy_skips_validation(clean_registry: Any) -> None:
    from pydantic import ValidationError

    @configurable
    def build(size: int = 1) -> Dict[str, Any]:
        return {"size": size}

    # strict rejects a type-invalid value...
    with pytest.raises(ValidationError):
        build(size="bad")  # type: ignore[arg-type]
    # ...but under "off" the check is skipped and the value passes through.
    set_policy(init="off")
    try:
        assert build(size="bad") == {"size": "bad"}  # type: ignore[arg-type]
    finally:
        set_policy(init="strict")


def test_configurable_function_yaml_materialization_validates(clean_registry: Any) -> None:
    @configurable
    def build_thing(size: int = 1) -> Dict[str, Any]:
        return {"size": size}

    # !lazy: resolves the registered WRAPPER; flow swaps init→yaml mode (strict),
    # so a type-invalid stored kwarg fails validation — proving the wrapper is
    # what got registered. flow() wraps the pydantic error as ConstructionError.
    doc = load("x: !lazy:build_thing\n  size: not-a-number\n", flow=False)
    with pytest.raises(confluid.ConstructionError):
        flow(doc["x"])


def test_register_function_discovery(clean_registry: Any) -> None:
    def make_head(n: int = 2) -> Dict[str, Any]:
        return {"n": n}

    returned = confluid.register(make_head, task="detection", role="model")
    assert returned is make_head  # register returns the object unchanged (no wrap)
    assert getattr(make_head, "__confluid_validated__", False) is False
    assert get_registry().get_class("make_head") is make_head
    # list_classes returns registered NAMES.
    assert "make_head" in get_registry().list_classes(category="detection_model")


def test_configurable_function_round_trips(clean_registry: Any) -> None:
    """A marker referencing a @configurable function by name dumps and reloads identically."""

    @configurable
    def rt_builder(size: int = 1, color: str = "red") -> Dict[str, Any]:
        return {"size": size, "color": color}

    # A real config references the target by NAME (a string), as authored YAML does.
    marker = LazyClass("rt_builder", size=5, color="blue")
    reloaded = load(dump(marker), flow=False)  # !lazy:rt_builder → resolved via registry
    assert flow(reloaded) == flow(marker) == {"size": 5, "color": "blue"}


def test_settability_predicates_read_a_functions_own_signature(clean_registry: Any) -> None:
    """A registered builder FUNCTION answers the predicates from its OWN signature.

    Reading ``target.__init__`` unconditionally resolved a function to
    ``object.__init__`` — signature ``(*args, **kwargs)`` — so all three
    predicates answered True for EVERY key (measured: ``accepts_key(builder,
    "run_name")`` was True on a builder declaring only ``weights`` /
    ``num_classes``), defeating for function targets the exact stray-CLI-key
    failure ``accepts_any_key`` exists to prevent.
    """
    from confluid import accepts_any_key, accepts_broadcast, accepts_key, register

    def build_widget(weights: str = "default", num_classes: int = 91) -> Dict[str, Any]:
        return {"weights": weights, "num_classes": num_classes}

    register(build_widget, task="detection", role="model")

    assert accepts_key(build_widget, "weights")
    assert accepts_broadcast(build_widget, "num_classes")
    assert not accepts_key(build_widget, "run_name")
    assert not accepts_broadcast(build_widget, "run_name")
    assert not accepts_any_key(build_widget)


def test_a_var_keyword_function_still_cannot_refuse_any_key(clean_registry: Any) -> None:
    """The ``**kwargs`` permissiveness is a signature fact, and a function may have it too."""
    from confluid import accepts_any_key, accepts_key, register

    def forwards(**kwargs: Any) -> Dict[str, Any]:
        return dict(kwargs)

    register(forwards)

    assert accepts_any_key(forwards)
    assert accepts_key(forwards, "anything")


def test_get_hierarchy_reads_a_registered_functions_own_signature(clean_registry: Any) -> None:
    """``get_hierarchy`` reported ``{}`` for every registered builder FUNCTION.

    ``register()`` / ``@configurable`` stamp ``__confluid_configurable__`` on the
    function object, which disqualified it from ``_build_hierarchy_recursive``'s
    callable branch; the class branch then read
    ``types.FunctionType.__init__`` = ``object.__init__`` = ``(*args, **kwargs)``
    and filtered every parameter away. The same target was fully visible to
    ``input_specs`` / ``to_pydantic`` / ``parse_param_docs``, so a CLI ``--help``
    view (the consumer of ``get_hierarchy``) was the one surface that lost it.
    """
    from confluid import configurable, get_hierarchy, input_specs, register

    def build_widget(num_classes: int = 91, pretrained: bool = False) -> Dict[str, Any]:
        """Build a widget.

        Args:
            num_classes: How many classes the head predicts.
            pretrained: Whether to load pretrained weights.
        """
        return {"num_classes": num_classes, "pretrained": pretrained}

    register(build_widget, task="detection", role="model")

    hierarchy = get_hierarchy(build_widget)
    assert set(hierarchy) == {"num_classes", "pretrained"}
    assert hierarchy["num_classes"][1] == 91  # the real default, not object.__init__'s
    assert hierarchy["num_classes"][2] == "How many classes the head predicts."
    # The surfaces must agree — the drift is what made this invisible.
    assert {spec["name"] for spec in input_specs(build_widget)} == set(hierarchy)

    @configurable
    def build_other(depth: int = 3) -> Dict[str, Any]:
        """Build another. Args: depth: How deep."""
        return {"depth": depth}

    # The @configurable wrapper is still a routine, and get_type_hints /
    # inspect.signature follow __wrapped__ — so the wrap must not hide the param.
    assert set(get_hierarchy(build_other)) == {"depth"}


def test_get_hierarchy_from_instance_walks_a_function_valued_slot(clean_registry: Any) -> None:
    """A function held in a live slot routes to the callable walk, not the instance walk."""
    from confluid import configurable, get_hierarchy_from_instance, register

    def collate(batch_size: int = 4) -> int:
        """Collate. Args: batch_size: Items per batch."""
        return batch_size

    register(collate)

    @configurable
    class Holder:
        def __init__(self, fn: Any = None) -> None:
            self.fn = fn

    paths = get_hierarchy_from_instance({"holder": Holder(fn=collate)})
    assert any(p.endswith("batch_size") for p in paths), sorted(paths)
