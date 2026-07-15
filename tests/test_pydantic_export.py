# mypy: disable-error-code="attr-defined,valid-type"
"""Tests for ``confluid.to_pydantic``.

Coverage targets:

* Scalar fields with and without defaults
* Optional / Union / Literal / List / Dict / Tuple annotations
* Nested ``@configurable`` recursion produces nested pydantic models
* Lists of ``@configurable`` produce ``List[NestedModel]``
* ``@ignore_config``-marked attributes are skipped
* Mutable defaults (list/dict) become ``default_factory``
* ``_confluid_class`` attribute carries the correct dotted path
* ``lru_cache`` returns the same model on repeated calls
* ``Lazy[T]`` annotations are unwrapped to ``T`` and recorded
* ``confluid_class_of`` and ``lazy_param_names_of`` helpers
"""

from typing import Any, Dict, Generic, List, Literal, Optional, Tuple, TypeVar, Union, get_args

import pytest
from pydantic import BaseModel, ValidationError

from confluid import LazyClass, configurable, confluid_class_of, get_registry, to_pydantic
from confluid.fluid import Fluid
from confluid.lazy import Lazy
from confluid.pydantic_export import _convert_annotation, _qualname, lazy_param_names_of


@pytest.fixture(autouse=True)
def _clear_registry_and_cache() -> None:
    """Reset global state between tests so configurables don't leak."""
    get_registry().clear()
    to_pydantic.cache_clear()


# ---------------------------------------------------------------------------
# Basic field types
# ---------------------------------------------------------------------------


def test_scalar_fields_with_defaults() -> None:
    @configurable
    class Optim:
        def __init__(self, lr: float = 1e-3, weight_decay: float = 0.0) -> None:
            self.lr = lr
            self.weight_decay = weight_decay

    Model = to_pydantic(Optim)
    assert issubclass(Model, BaseModel)

    instance = Model()  # both defaulted
    assert instance.lr == 1e-3
    assert instance.weight_decay == 0.0

    overridden = Model(lr=5e-4)
    assert overridden.lr == 5e-4


def test_required_fields_have_no_default() -> None:
    @configurable
    class Dataset:
        def __init__(self, repo_id: str, split: str = "train") -> None:
            self.repo_id = repo_id
            self.split = split

    Model = to_pydantic(Dataset)
    with pytest.raises(ValidationError):
        Model()  # missing required repo_id
    instance = Model(repo_id="foo/bar")
    assert instance.repo_id == "foo/bar"
    assert instance.split == "train"


def test_extra_fields_forbidden() -> None:
    @configurable
    class Tiny:
        def __init__(self, x: int = 0) -> None:
            self.x = x

    Model = to_pydantic(Tiny)
    with pytest.raises(ValidationError):
        Model(x=1, bogus="extra")


# ---------------------------------------------------------------------------
# Typing constructs
# ---------------------------------------------------------------------------


def test_optional_annotation() -> None:
    @configurable
    class Box:
        def __init__(self, label: Optional[str] = None) -> None:
            self.label = label

    Model = to_pydantic(Box)
    assert Model().label is None
    assert Model(label="x").label == "x"


def test_literal_annotation_constrains_values() -> None:
    @configurable
    class Split:
        def __init__(self, split: Literal["train", "validation", "test"] = "train") -> None:
            self.split = split

    Model = to_pydantic(Split)
    assert Model(split="train").split == "train"
    with pytest.raises(ValidationError):
        Model(split="bogus")  # type: ignore[arg-type]


def test_union_annotation() -> None:
    @configurable
    class Either:
        def __init__(self, value: Union[int, str] = 0) -> None:
            self.value = value

    Model = to_pydantic(Either)
    assert Model(value=5).value == 5
    assert Model(value="hi").value == "hi"


def test_list_of_primitives() -> None:
    @configurable
    class Layers:
        def __init__(self, sizes: List[int] = [16, 32]) -> None:
            self.sizes = sizes

    Model = to_pydantic(Layers)
    instance = Model()
    assert instance.sizes == [16, 32]
    # Mutating one instance's default must not affect another (default_factory).
    instance.sizes.append(64)
    assert Model().sizes == [16, 32]


def test_dict_of_primitives() -> None:
    @configurable
    class TagMap:
        def __init__(self, tags: Dict[str, int] = {}) -> None:
            self.tags = tags

    Model = to_pydantic(TagMap)
    instance = Model(tags={"a": 1, "b": 2})
    assert instance.tags == {"a": 1, "b": 2}
    assert Model().tags == {}


def test_tuple_annotation() -> None:
    @configurable
    class Shape:
        def __init__(self, size: Tuple[int, int] = (224, 224)) -> None:
            self.size = size

    Model = to_pydantic(Shape)
    assert Model().size == (224, 224)
    assert Model(size=(112, 112)).size == (112, 112)


# ---------------------------------------------------------------------------
# Nested @configurable recursion
# ---------------------------------------------------------------------------


def test_nested_configurable_becomes_nested_model() -> None:
    @configurable
    class Backbone:
        def __init__(self, name: str = "resnet50", pretrained: bool = True) -> None:
            self.name = name
            self.pretrained = pretrained

    @configurable
    class Classifier:
        def __init__(self, backbone: Backbone, num_classes: int = 10) -> None:
            self.backbone = backbone
            self.num_classes = num_classes

    OuterModel = to_pydantic(Classifier)
    BackboneModel = to_pydantic(Backbone)

    # Nested type is ``Union[Backbone, BackboneModel]`` so the schema accepts
    # both the live source-class instance (Python / YAML flow path) and the
    # generated pydantic mirror (LLM / MCP composition path).
    from typing import Union as _Union
    from typing import get_args, get_origin

    field_type = OuterModel.model_fields["backbone"].annotation
    assert get_origin(field_type) is _Union
    assert set(get_args(field_type)) == {Backbone, BackboneModel}

    # Both forms must construct cleanly.
    via_model = OuterModel(backbone=BackboneModel(name="vit_base"), num_classes=37)
    assert via_model.backbone.name == "vit_base"
    via_instance = OuterModel(backbone=Backbone(name="vit_base"), num_classes=37)
    assert via_instance.backbone.name == "vit_base"
    assert via_model.num_classes == 37


def test_list_of_configurable_becomes_list_of_model() -> None:
    @configurable
    class Callback:
        def __init__(self, name: str = "default") -> None:
            self.name = name

    @configurable
    class Trainer:
        def __init__(self, callbacks: List[Callback] = []) -> None:
            self.callbacks = callbacks

    TrainerModel = to_pydantic(Trainer)
    CallbackModel = to_pydantic(Callback)

    # Element type is ``Union[Callback, CallbackModel]`` — see the nested
    # @configurable test above for the rationale.
    from typing import Union as _Union
    from typing import get_args, get_origin

    field_type = TrainerModel.model_fields["callbacks"].annotation
    assert get_origin(field_type) is list
    (elem_type,) = get_args(field_type)
    assert get_origin(elem_type) is _Union
    assert set(get_args(elem_type)) == {Callback, CallbackModel}

    via_model = TrainerModel(callbacks=[CallbackModel(name="ckpt"), CallbackModel(name="logger")])
    assert [cb.name for cb in via_model.callbacks] == ["ckpt", "logger"]
    via_instance = TrainerModel(callbacks=[Callback(name="ckpt"), Callback(name="logger")])
    assert [cb.name for cb in via_instance.callbacks] == ["ckpt", "logger"]


# ---------------------------------------------------------------------------
# Metadata and helpers
# ---------------------------------------------------------------------------


def test_confluid_class_attribute_holds_dotted_path() -> None:
    @configurable
    class Widget:
        def __init__(self, x: int = 0) -> None:
            self.x = x

    Model = to_pydantic(Widget)
    # The generated model's _confluid_class points back at the source class.
    assert Model._confluid_class == _qualname(Widget)  # type: ignore[attr-defined]
    assert confluid_class_of(Model) == _qualname(Widget)
    assert confluid_class_of(Model()) == _qualname(Widget)


def test_confluid_class_of_returns_none_for_unrelated_types() -> None:
    assert confluid_class_of(int) is None
    assert confluid_class_of("foo") is None
    assert confluid_class_of(None) is None


def test_lru_cache_returns_same_model() -> None:
    @configurable
    class Repeated:
        def __init__(self, x: int = 0) -> None:
            self.x = x

    assert to_pydantic(Repeated) is to_pydantic(Repeated)


def test_ignore_config_attributes_are_skipped() -> None:
    from confluid import ignore_config

    @configurable
    class WithHidden:
        def __init__(self, visible: int = 1, hidden: int = 2) -> None:
            self.visible = visible
            self._hidden = hidden

        # ``@ignore_config`` marks the class-level ``hidden`` lookup so the
        # pydantic generator skips the matching ``__init__`` param.
        @ignore_config
        def hidden(self) -> int:  # noqa: F811
            return self._hidden

    Model = to_pydantic(WithHidden)
    assert "visible" in Model.model_fields
    assert "hidden" not in Model.model_fields


# ---------------------------------------------------------------------------
# Lazy[T] support
# ---------------------------------------------------------------------------


def test_lazy_annotation_is_unwrapped_and_recorded() -> None:
    @configurable
    class HasOptim:
        def __init__(self, optimizer: Lazy[Any] = None) -> None:
            self.optimizer = optimizer

    Model = to_pydantic(HasOptim)
    # The Lazy marker is stripped from the field type; the alias's honest
    # ``Union[T, Fluid]`` shape survives (the Fluid arm gains its generated
    # mirror per the configurable-union rule).
    field = Model.model_fields["optimizer"]
    assert getattr(field.annotation, "__metadata__", ()) == ()  # no Annotated wrapper
    assert Fluid in get_args(field.annotation)
    # The lazy marker is recorded on the generated model.
    assert "optimizer" in lazy_param_names_of(Model)
    assert "optimizer" in lazy_param_names_of(Model())


def test_lazy_typed_slot_validates_fluid_config_and_live_forms() -> None:
    @configurable
    class Leaf:
        def __init__(self, n: int = 1) -> None:
            self.n = n

    @configurable
    class HasTyped:
        def __init__(self, dep: Lazy[Leaf] = LazyClass(Leaf, n=2)) -> None:
            self.dep = dep

    Model = to_pydantic(HasTyped)
    # All three legal runtime forms validate: a deferred Fluid, a live
    # instance of T, and the generated config mirror.
    Model(dep=LazyClass(Leaf, n=3))
    Model(dep=Leaf(n=4))
    Model(dep=to_pydantic(Leaf)(n=5))
    # The marker never leaks into the JSON schema, which stays generable.
    schema = Model.model_json_schema()
    assert "__confluid_lazy__" not in str(schema)
    assert "dep" in lazy_param_names_of(Model)


def test_range_marks_survive_inside_marker_union_arms() -> None:
    """A range mark composed with a union-carrying marker alias (``Mandatory[DbPower]``,
    where the Interval sits on a Union ARM instead of flattening to the top) still
    reaches the pydantic model: schema bounds present, out-of-range rejected, and a
    marked ``(min, max)`` CONTAINER arm relocates element-wise."""
    from typing import Annotated

    from annotated_types import Interval

    from confluid import Mandatory

    DbPower = Annotated[float, Interval(ge=-200.0, le=50.0)]
    WattRange = Annotated[Tuple[float, float], Interval(ge=0.0)]

    @configurable
    class Marked:
        def __init__(
            self,
            power: Mandatory[DbPower] = -30.0,
            rng: Mandatory[WattRange] = (0.0, 1.0),
        ) -> None:
            self.power = power
            self.rng = rng

    Model = to_pydantic(Marked)
    schema = Model.model_json_schema()
    power_arms = schema["properties"]["power"]["anyOf"]
    numeric_arm = next(a for a in power_arms if a.get("type") == "number")
    assert (numeric_arm["minimum"], numeric_arm["maximum"]) == (-200.0, 50.0)
    rng_arm = next(a for a in schema["properties"]["rng"]["anyOf"] if a.get("type") == "array")
    assert all(item["minimum"] == 0.0 for item in rng_arm["prefixItems"])
    Model(power=-100.0)
    with pytest.raises(ValidationError):
        Model(power=99.0)
    with pytest.raises(ValidationError):
        Model(rng=(-1.0, 1.0))


def test_no_lazy_params_means_empty_set() -> None:
    @configurable
    class Plain:
        def __init__(self, x: int = 0) -> None:
            self.x = x

    Model = to_pydantic(Plain)
    assert lazy_param_names_of(Model) == frozenset()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_class_without_init_produces_empty_model() -> None:
    @configurable
    class Marker:
        pass

    Model = to_pydantic(Marker)
    assert Model.model_fields == {}
    instance = Model()
    assert confluid_class_of(instance) == _qualname(Marker)


def test_non_class_argument_raises_type_error() -> None:
    with pytest.raises(TypeError):
        to_pydantic("not a class")  # type: ignore[arg-type]


def test_third_party_class_without_configurable_marker_works() -> None:
    """Auto-gen does not require the @configurable marker — useful for ad-hoc mirrors."""

    class External:
        def __init__(self, port: int = 8080) -> None:
            self.port = port

    Model = to_pydantic(External)
    assert Model(port=9090).port == 9090


def test_convert_annotation_handles_pep604_union() -> None:
    """PEP 604 ``X | Y`` syntax should normalize to ``Union[X, Y]``."""
    converted = _convert_annotation(int | str)  # type: ignore[operator]
    # Union[int, str] equals int | str under typing.get_origin
    assert converted == Union[int, str]


def test_convert_annotation_coerces_parameterized_opaque_generic_to_any() -> None:
    """A parameterized generic whose ORIGIN is an opaque (torch/numpy) type —
    e.g. ``Dataset[Any]`` (origin ``torch.utils.data.Dataset``) — must coerce to
    ``Any``, symmetric with a BARE opaque type (``Module`` / ``Tensor``, handled in
    the ``origin is None`` branch).

    Regression pin: a narrowed ``Union[Dataset[Any], Fluid]`` slot must stay as
    permissive as ``Union[Module, Fluid]``. Before the fix the parameterized
    generic skipped ``_is_opaque_type`` and was rebuilt as a strict ``Dataset[Any]``
    isinstance arm, so it rejected the config / live-instance forms the ``Module``
    slot accepted — which broke navigaitor's typed-composition escape hatch. Uses a
    stand-in tagged with a ``torch`` ``__module__`` so the confluid suite stays
    torch-free.
    """
    T = TypeVar("T")

    class _OpaqueDataset(Generic[T]):
        pass

    _OpaqueDataset.__module__ = "torch.utils.data._stub"  # make _is_opaque_type fire

    # Symmetry: the bare class is already coerced; its parameterized generic must be too.
    assert _convert_annotation(_OpaqueDataset) is Any
    assert _convert_annotation(_OpaqueDataset[Any]) is Any
    # Inside the narrowed-slot ``Union`` shape, the opaque arm collapses to ``Any`` —
    # so no strict isinstance arm survives to reject an arbitrary config.
    converted = _convert_annotation(Union[_OpaqueDataset[Any], int])
    assert Any in get_args(converted)
    assert _OpaqueDataset not in get_args(converted)


# ---------------------------------------------------------------------------
# Post-init body slots (minimal-ctor / post-construction pattern)
# ---------------------------------------------------------------------------


@configurable
class _TrainerLike:
    """Module-level so ``inspect.getsource`` can read the ``__init__`` body."""

    def __init__(self, model: Any, train_set: Any, run_name: str = "train") -> None:
        self.model = model
        self.train_set = train_set
        self.optimizer: Any = None  # post-init body slot, not a ctor param
        self.batch_size: int = 32  # annotated body slot
        self._private = 1  # underscore -> never surfaced


@configurable
class _SubTrainer(_TrainerLike):
    def __init__(self, model: Any, train_set: Any) -> None:
        super().__init__(model, train_set)
        self.extra_knob: Any = None


def test_to_pydantic_surfaces_post_init_body_slots() -> None:
    """Body-attribute config slots appear as OPTIONAL fields (default None)."""
    model = to_pydantic(_TrainerLike)
    assert {"model", "train_set", "run_name", "optimizer", "batch_size"} <= set(model.model_fields)
    assert "_private" not in model.model_fields
    # Required ctor params stay required; body slots are optional.
    inst = model(model=object(), train_set=[])
    assert inst.optimizer is None
    assert inst.batch_size is None


def test_to_pydantic_body_slots_inherited_across_configurable_chain() -> None:
    """A subclass's generated model carries both its own and the parent's body slots."""
    model = to_pydantic(_SubTrainer)
    assert {"optimizer", "batch_size", "extra_knob"} <= set(model.model_fields)


def test_to_pydantic_signature_param_wins_over_body_slot() -> None:
    """When a name is both a ctor param and a body setattr, the param spec wins."""

    @configurable
    class _C:
        def __init__(self, lr: float = 0.1) -> None:
            self.lr = lr  # also assigned in body — must not become optional/None

    model = to_pydantic(_C)
    # The ctor param default (0.1) is preserved, not overwritten by the body None.
    assert model().lr == 0.1


def test_to_pydantic_self_referential_body_slot_degrades_to_any() -> None:
    """A self-referential forward-ref body slot must not leave the model 'not fully defined'.

    ``self.child: Optional["Node"] = None`` evaluates to a ForwardRef whose
    referent (a function-local class) pydantic cannot resolve. The slot must
    degrade to ``Any`` so ``model_validate`` works instead of raising
    ``PydanticUserError: NodeConfig is not fully defined``.
    """

    @configurable
    class Node:
        def __init__(self, name: str) -> None:
            self.name = name
            self.child: Optional["Node"] = None  # noqa: F821 — self-ref forward ref

    model = to_pydantic(Node)
    # Validates cleanly — no unresolved forward ref leaking into the schema.
    model.model_validate({"name": "a"})


def test_to_pydantic_scalar_range_mark_validates_and_bounds_schema() -> None:
    """A PEP-593 range mark on a scalar param validates the value and lands in the
    JSON schema (minimum/maximum) — the workspace range-mark convention."""
    from typing import Annotated

    import pydantic
    from annotated_types import Interval

    @configurable
    class _Op:
        def __init__(self, noise_power_db: Annotated[float, Interval(ge=-200.0, le=50.0)] = -30.0) -> None:
            self.noise_power_db = noise_power_db

    model = to_pydantic(_Op)
    assert model(noise_power_db=-120.0).noise_power_db == -120.0
    schema = model.model_json_schema()["properties"]["noise_power_db"]
    assert schema["minimum"] == -200.0 and schema["maximum"] == 50.0
    try:
        model(noise_power_db=-500.0)
        raise AssertionError("out-of-range value must be rejected")
    except pydantic.ValidationError:
        pass


def test_to_pydantic_container_range_mark_relocates_to_elements() -> None:
    """An outer range mark on a (min, max) container — the convention FluxStudio's
    widget bounds read — must NOT be applied to the tuple VALUE (pydantic raises
    ``TypeError: Unable to apply constraint`` on first validation); it relocates
    element-wise, validating each endpoint and bounding the schema prefixItems."""
    from typing import Annotated, Tuple

    import pydantic
    from annotated_types import Interval

    @configurable
    class _Op:
        def __init__(self, power_range: Annotated[Tuple[float, float], Interval(ge=0.0)] = (0.01, 10.0)) -> None:
            self.power_range = power_range

    model = to_pydantic(_Op)
    assert model(power_range=(0.5, 2.0)).power_range == (0.5, 2.0)  # the pre-fix crash path
    schema = model.model_json_schema()["properties"]["power_range"]
    assert [item.get("minimum") for item in schema["prefixItems"]] == [0.0, 0.0]
    try:
        model(power_range=(-1.0, 2.0))
        raise AssertionError("negative element must be rejected by the relocated mark")
    except pydantic.ValidationError:
        pass


def test_to_pydantic_variadic_tuple_range_mark_skips_ellipsis() -> None:
    """``Annotated[Tuple[float, ...], Interval(...)]`` marks the element type and
    leaves the Ellipsis untouched."""
    from typing import Annotated, Tuple

    import pydantic
    from annotated_types import Interval

    @configurable
    class _Op:
        def __init__(self, levels: Annotated[Tuple[float, ...], Interval(ge=0.0)] = (1.0,)) -> None:
            self.levels = levels

    model = to_pydantic(_Op)
    assert model(levels=(1.0, 2.0, 3.0)).levels == (1.0, 2.0, 3.0)
    try:
        model(levels=(1.0, -2.0))
        raise AssertionError("negative element must be rejected")
    except pydantic.ValidationError:
        pass


def test_to_pydantic_non_range_metadata_on_container_left_untouched() -> None:
    """Only range marks relocate — other Annotated metadata on a container stays put."""
    from typing import Annotated, Tuple

    @configurable
    class _Op:
        def __init__(self, pair: Annotated[Tuple[float, float], "doc-tag"] = (1.0, 2.0)) -> None:
            self.pair = pair

    model = to_pydantic(_Op)
    assert model(pair=(3.0, 4.0)).pair == (3.0, 4.0)
