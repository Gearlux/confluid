# mypy: disable-error-code="attr-defined,valid-type"
"""Tests for ``confluid.to_pydantic``.

Coverage targets:

* Scalar fields with and without defaults
* Optional / Union / Literal / List / Dict / Tuple annotations
* Nested ``@configurable`` recursion produces nested pydantic models
* Lists of ``@configurable`` produce ``List[NestedModel]``
* Params shadowed by a read-only ``@property`` are skipped
* Mutable defaults (list/dict) become ``default_factory``
* ``_confluid_class`` attribute carries the correct dotted path
* ``lru_cache`` returns the same model on repeated calls
* ``Partial[T]`` annotations are unwrapped to ``T`` and recorded
* ``confluid_class_of`` and ``partial_param_names_of`` helpers
"""

from typing import (
    Any,
    Dict,
    Generic,
    Iterable,
    List,
    Literal,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    TypeVar,
    Union,
    get_args,
)

import pytest
from pydantic import BaseModel, ValidationError

from confluid import PartialClass, configurable, confluid_class_of, get_registry, to_pydantic
from confluid.fluid import Fluid
from confluid.partial import Partial
from confluid.pydantic_export import _convert_annotation, _qualname, partial_param_names_of


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


def test_a_readonly_property_is_not_a_field_but_a_ctor_param_always_is() -> None:
    """The two halves of the rule that replaced ``@ignore_config`` (deleted 0.3.0).

    A derived read-only ``@property`` is not a config knob, so it is not a field —
    this is the exclusion the removed decorator was being used for, and it needs
    no marker.

    A declared constructor PARAMETER stays a field even when a read-only property
    shadows it, because this model describes the constructor: the property shadows
    the instance attribute after construction, not the argument. ``@ignore_config``
    used to suppress it, which is the one capability the removal drops — no class
    in the workspace used it (0 of 275).
    """

    @configurable
    class WithDerived:
        def __init__(self, visible: int = 1, shadowed: int = 2) -> None:
            self.visible = visible
            self._shadowed = shadowed
            self._total = visible + shadowed

        @property
        def shadowed(self) -> int:  # noqa: F811 — deliberately shadows the ctor param
            return self._shadowed

        @property
        def total(self) -> int:  # purely derived — never a ctor param
            return self._total

    Model = to_pydantic(WithDerived)
    assert "visible" in Model.model_fields
    assert "shadowed" in Model.model_fields, "a declared ctor param is always a field"
    assert "total" not in Model.model_fields, "derived read-only state is not"


# ---------------------------------------------------------------------------
# Partial[T] support
# ---------------------------------------------------------------------------


def test_lazy_annotation_is_unwrapped_and_recorded() -> None:
    @configurable
    class HasOptim:
        def __init__(self, optimizer: Partial[Any] = None) -> None:
            self.optimizer = optimizer

    Model = to_pydantic(HasOptim)
    # The Partial marker is stripped from the field type; the alias's honest
    # ``Union[T, Fluid]`` shape survives (the Fluid arm gains its generated
    # mirror per the configurable-union rule).
    field = Model.model_fields["optimizer"]
    assert getattr(field.annotation, "__metadata__", ()) == ()  # no Annotated wrapper
    assert Fluid in get_args(field.annotation)
    # The lazy marker is recorded on the generated model.
    assert "optimizer" in partial_param_names_of(Model)
    assert "optimizer" in partial_param_names_of(Model())


def test_lazy_typed_slot_validates_fluid_config_and_live_forms() -> None:
    @configurable
    class Leaf:
        def __init__(self, n: int = 1) -> None:
            self.n = n

    @configurable
    class HasTyped:
        def __init__(self, dep: Partial[Leaf] = PartialClass(Leaf, n=2)) -> None:
            self.dep = dep

    Model = to_pydantic(HasTyped)
    # All three legal runtime forms validate: a deferred Fluid, a live
    # instance of T, and the generated config mirror.
    Model(dep=PartialClass(Leaf, n=3))
    Model(dep=Leaf(n=4))
    Model(dep=to_pydantic(Leaf)(n=5))
    # The marker never leaks into the JSON schema, which stays generable.
    schema = Model.model_json_schema()
    assert "__confluid_partial__" not in str(schema)
    assert "dep" in partial_param_names_of(Model)


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
    assert partial_param_names_of(Model) == frozenset()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_a_param_literally_named_args_validates_end_to_end() -> None:
    """The S1 defect: ``_SKIP_PARAMS`` filtered by NAME, so an ordinary keyword
    parameter named ``args`` was dropped from the model and — the model being
    ``extra="forbid"`` — the strict init policy then REFUSED a legal call."""

    @configurable
    class OddlyNamed:
        def __init__(self, args: Optional[List[int]] = None, normal: int = 1) -> None:
            self.args = args
            self.normal = normal

    assert set(to_pydantic(OddlyNamed).model_fields) == {"args", "normal"}
    inst = OddlyNamed(args=[1, 2])  # the wrapped __init__ validates against the model
    assert inst.args == [1, 2]


def test_variadics_are_never_fields_whatever_they_are_named() -> None:
    """The KIND excludes ``*args`` / ``**kwargs`` — renaming them changes nothing."""

    @configurable
    class Variadic:
        def __init__(self, *loaders: int, lr: float = 0.1, **extra: Any) -> None:
            self.lr = lr

    assert set(to_pydantic(Variadic).model_fields) == {"lr"}


def test_a_body_slot_literally_named_args_is_an_optional_field() -> None:
    """The body-slot half of the same name-filter defect: ``_post_init_field_specs``
    ORed ``_SKIP_PARAMS`` into its seen-set, silently excluding a body slot that
    happens to be named ``args`` from every generated schema."""

    @configurable
    class BodyNames:
        def __init__(self) -> None:
            self.args: Optional[int] = None

    assert "args" in to_pydantic(BodyNames).model_fields


def test_an_unresolvable_annotation_still_raises_introspection_error() -> None:
    """The error contract survives the ``slots()`` migration: ``slots()`` degrades
    silently on an unreadable signature (best-effort by design), but ``to_pydantic``
    documents a raise — the probe exists solely to keep that promise."""
    from confluid.exceptions import IntrospectionError

    class Broken:
        def __init__(self, x: "NoSuchType" = None) -> None:  # type: ignore[name-defined]  # noqa: F821 - the point
            self.x = x

    with pytest.raises(IntrospectionError):
        to_pydantic(Broken)


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


class _Deferred:
    """A runtime-injection target for the ``Partial[T]`` body-slot pin below."""

    def __init__(self, lr: float = 0.1) -> None:
        self.lr = lr


@configurable
class _TypedBody:
    """Module-level so ``inspect.getsource`` can read the ``__init__`` body.

    Shaped like the real consumers this matters for — ``matrainer``'s runnables
    carry ~10 annotated body slots each (``Optional[Partial[RecordSource]]``,
    ``Partial[KerasOptimizer]``, …), which is 163 slots across the workspace.
    """

    def __init__(self, epochs: int = 10) -> None:
        self.epochs = epochs
        self.optimizer: Partial[_Deferred] = PartialClass(_Deferred)
        self.run_name: Optional[str] = None
        self.untyped = None  # no annotation — must stay Any


def test_body_slot_ANNOTATIONS_reach_the_generated_model() -> None:
    """The "before" snapshot for the slots() annotation consolidation.

    ``to_pydantic`` resolves a body slot's declared type by running its OWN AST
    scan (``pydantic_export._post_init_field_specs``), independently of the one
    ``introspect.slots()`` runs and of the one ``confluid.partial`` runs. Nothing
    checks that the three agree, and 163 production body slots ride on it.

    This pins what the schema says TODAY, so folding those scans into one
    enumeration shows up as a diff in this test rather than as a quietly
    different schema on every MCP tool and GUI form.
    """
    fields = to_pydantic(_TypedBody).model_fields

    assert fields["run_name"].annotation == Optional[str], "a typed body slot is NOT Any"
    assert fields["untyped"].annotation == Optional[Any], "an unannotated one is — body slots are optional"
    assert fields["epochs"].annotation is int, "signature params are unaffected"

    # ``Partial[T]`` is stripped to a union admitting the target, the deferred
    # marker, and its generated config model.
    optimizer_arms = get_args(fields["optimizer"].annotation)
    assert _Deferred in optimizer_arms, "the flow-target type survives into the schema"
    assert type(None) in optimizer_arms


def test_the_partial_body_slot_scan_agrees_with_the_schema_today() -> None:
    """The second private scan, pinned beside the first.

    ``confluid.partial`` resolves the SAME annotations to decide which body slots
    are deferred. It agrees with ``to_pydantic`` today; nothing enforces that, and
    a consolidation must keep it true.
    """
    from confluid.partial import partial_param_names

    assert "optimizer" in partial_param_names(_TypedBody), "declared Partial[T] in the body"
    assert "run_name" not in partial_param_names(_TypedBody)


def test_slots_reports_a_body_slots_DECLARED_type() -> None:
    """The gap the consolidation closed, pinned from the other side.

    ``introspect.slots()`` is the ONE slot enumeration, and until 2026-08-12 it
    carried names and kinds only — a body slot's ``annotation`` was ``Any`` even
    where the class plainly declared ``Optional[str]``. Nothing was broken by that
    (no reader asked), but two other scans resolved the same annotations privately
    and nothing checked they agreed, across 163 production body slots.

    An unannotated slot is still ``Any``: that is the class's own answer, not a
    missing one.
    """
    from confluid.introspect import slots

    by_name = {s.name: s for s in slots(_TypedBody)}

    assert by_name["epochs"].annotation is int, "signature slots were always typed"
    assert by_name["run_name"].annotation == Optional[str], "body slots are too, now"
    assert by_name["untyped"].annotation is Any, "...unless the class did not say"
    assert by_name["optimizer"].kind == "body_slot"


def test_to_pydantic_surfaces_post_init_body_slots() -> None:
    """Body-attribute config slots appear as OPTIONAL fields (default None)."""
    model = to_pydantic(_TrainerLike)
    assert {"model", "train_set", "run_name", "optimizer", "batch_size"} <= set(model.model_fields)
    assert "_private" not in model.model_fields
    # Required ctor params stay required; body slots are optional.
    inst = model(model=object(), train_set=[])
    assert inst.optimizer is None
    assert inst.batch_size is None


class _FrameworkBase:
    """NOT @configurable — stands in for ``nn.Module`` / ``LightningModule``.

    Their ``__init__`` bodies assign a dozen internal attributes
    (``self.training = True``, ``self.prepare_data_per_node = True``, …).
    """

    def __init__(self) -> None:
        self.training = True
        self.prepare_data_per_node = True


@configurable
class _OnFramework(_FrameworkBase):
    def __init__(self, width: int = 8) -> None:
        super().__init__()
        self.width = width
        self.head: Optional[str] = None


def test_a_non_configurable_bases_body_slots_never_become_schema_fields() -> None:
    """The OWNER filter — the reason ``Slot`` carries which class declared it.

    ``slots()`` walks the WHOLE MRO because the accept-list wants those names: a
    bare key may legitimately set ``training`` on an instance, and the engine
    subtracts non-configurable ancestors later (``_get_parent_attr_blacklist``).
    A generated SCHEMA must not carry them, or every model in a torch/Lightning
    tree grows ``training`` and ``prepare_data_per_node`` fields that no config
    should ever set.

    Two readers, two scopes, one enumeration — decided by the reader, not by a
    second walk. Projecting naively would have added exactly these (measured).
    """
    from confluid.introspect import slots

    fields = set(to_pydantic(_OnFramework).model_fields)
    body_slots = {s.name for s in slots(_OnFramework) if s.kind == "body_slot"}

    assert fields == {"width", "head"}, "the schema sees only what @configurable classes declare"
    assert {"training", "prepare_data_per_node"} <= body_slots, "...while the enumeration sees all of them"
    assert not ({"training", "prepare_data_per_node"} & fields)


def test_slot_owner_names_the_declaring_class() -> None:
    """``owner`` is the MRO class that declared the slot, not the target."""
    from confluid.introspect import slots

    by_name = {s.name: s for s in slots(_OnFramework)}

    assert by_name["head"].owner is _OnFramework
    assert by_name["training"].owner is _FrameworkBase
    assert by_name["width"].owner is _OnFramework, "a signature slot owns itself"


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
    """An outer range mark on a (min, max) container — the convention StreamStudio's
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


# ---------------------------------------------------------------------------
# Abstract collection annotations: re-iterable kinds keep their element type
# ---------------------------------------------------------------------------


def test_a_sequence_slot_keeps_its_element_type() -> None:
    """``Sequence[X]`` must reach the schema as ``Sequence[X]``, not a bare ``Any``.

    A slot is annotated ``Sequence[Metric]`` precisely so a form-spec / MCP schema knows
    what it holds; coercing it to ``Any`` silently defeats the annotation. ``Sequence`` is
    safe to keep because pydantic validates it into a real ``list`` — see the re-iterability
    test below for the property that distinguishes it from ``Iterable``.
    """

    @configurable
    class _Metric:
        def __init__(self, name: str = "acc") -> None:
            self.name = name

    @configurable
    class _Runner:
        def __init__(self, metrics: Optional[Sequence[_Metric]] = None) -> None:
            self.metrics = metrics

    field = to_pydantic(_Runner).model_fields["metrics"]
    assert "Any" not in str(field.annotation), f"element type was discarded: {field.annotation}"
    assert _Metric.__name__ in str(field.annotation)


def test_a_mapping_slot_keeps_its_key_and_value_types() -> None:
    """``Mapping[str, str]`` (the shape a `tags` knob takes) survives as itself."""

    @configurable
    class _Tagged:
        def __init__(self, tags: Optional[Mapping[str, str]] = None) -> None:
            self.tags = tags

    field = to_pydantic(_Tagged).model_fields["tags"]
    assert "Any" not in str(field.annotation), f"element types were discarded: {field.annotation}"


def test_an_iterable_slot_is_still_coerced_to_any() -> None:
    """``Iterable[X]`` MUST stay coerced — pydantic validates it lazily.

    The coercion is not stylistic: pydantic wraps an ``Iterable[X]`` input in a one-shot
    ``ValidatorIterator``, so a slot read twice sees an EMPTY collection the second time.
    Coercing to ``Any`` hands the caller's own object back untouched. This test is the
    boundary of the narrowing — it fails if someone "completes the set" by dropping the
    remaining lazy kinds too.
    """

    @configurable
    class _Streamer:
        def __init__(self, rows: Optional[Iterable[str]] = None) -> None:
            self.rows = rows

    assert to_pydantic(_Streamer).model_fields["rows"].annotation == Optional[Any]


def test_a_validated_sequence_is_re_iterable_while_an_iterable_is_not() -> None:
    """The measured property the split is based on, pinned against pydantic itself.

    If a future pydantic validated ``Sequence`` lazily too, keeping its element type would
    reintroduce the one-shot bug — this fails first and says why.
    """

    class _Thing:
        pass

    @configurable
    class _Both:
        def __init__(
            self,
            seq: Optional[Sequence[int]] = None,
            it: Optional[Iterable[int]] = None,
        ) -> None:
            self.seq = seq
            self.it = it

    model = to_pydantic(_Both)
    built = model(seq=[1, 2], it=[1, 2])
    assert list(built.seq) == [1, 2] and list(built.seq) == [1, 2], "Sequence must be re-iterable"
    # `it` is coerced to Any, so it is handed back as the original list untouched.
    assert built.it == [1, 2]
