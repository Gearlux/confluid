import enum
import pathlib as _pathlib
from typing import Any

import numpy as np
import pytest
import yaml

from confluid import configurable, configure, dump, get_registry, load, register


@pytest.fixture(autouse=True)
def setup_registry() -> None:
    get_registry().clear()


class _UnregisteredWidget:
    """Deliberately NOT @configurable — the registry has no key for it."""

    def __init__(self, size: int = 1) -> None:
        self.size = size


def test_basic_dump() -> None:
    @configurable
    class Model:
        def __init__(self, layers: int = 3) -> None:
            self.layers = layers

    model = Model(layers=10)
    output = dump(model)

    # Instances dump with () for instant construction on reload
    assert "_target_: Model" in output
    assert "layers: 10" in output

    # Round-trip via confluid.load produces a live instance
    from confluid import load

    data = load(output)
    assert isinstance(data, Model)
    assert data.layers == 10


def test_hierarchical_dump() -> None:
    @configurable
    class Model:
        def __init__(self, layers: int = 3) -> None:
            self.layers = layers

    @configurable
    class Trainer:
        def __init__(self, model: Model, lr: float = 0.01) -> None:
            self.model = model
            self.lr = lr

    model = Model(layers=5)
    trainer = Trainer(model=model, lr=0.001)

    output = dump(trainer)

    assert "_target_: Trainer" in output
    assert "_target_: Model" in output
    assert "lr: 0.001" in output
    assert "layers: 5" in output


def test_opaque_fallback_emits_class_marker() -> None:
    """Non-@configurable objects degrade to a ``!class:<name>`` scalar marker."""

    class InternalThing:
        def __str__(self) -> str:
            return "internal"

    @configurable
    class Model:
        def __init__(self, thing: Any) -> None:
            self.thing = thing

    model = Model(thing=InternalThing())
    output = dump(model)
    assert "_target_:" in output
    assert "InternalThing" in output


def test_circular_reference() -> None:
    @configurable
    class Node:
        def __init__(self, next_node: Any = None) -> None:
            self.next_node = next_node

    node1 = Node()
    node2 = Node(next_node=node1)
    node1.next_node = node2  # Cycle

    output = dump(node1)
    # Check for YAML anchors/aliases indicating circularity
    assert "&id" in output or "*id" in output


def test_dump_list_and_dict() -> None:
    @configurable
    class Container:
        def __init__(self, items: list[Any], mapping: dict[str, Any]) -> None:
            self.items = items
            self.mapping = mapping

    obj = Container(items=[1, 2], mapping={"a": 1})
    output = dump(obj)

    assert "_target_: Container" in output
    assert "- 1" in output
    assert "a: 1" in output


def test_dump_no_init() -> None:
    @configurable
    class Simple:
        pass

    obj = Simple()
    output = dump(obj)
    assert "_target_: Simple" in output


def test_dump_none() -> None:
    assert "null" in dump(None)


def test_dump_emits_params_in_signature_order() -> None:
    """Dump-key order is round-trip-pinned to SIGNATURE order — the property the
    dumper's ``slots()`` projection must preserve (``slots()`` returns signature
    order by construction)."""

    @configurable
    class Ordered:
        def __init__(self, beta: int = 2, alpha: int = 1, gamma: int = 3) -> None:
            self.beta = beta
            self.alpha = alpha
            self.gamma = gamma

    text = dump(Ordered(beta=5, alpha=6, gamma=7))
    assert text.index("beta") < text.index("alpha") < text.index("gamma")


def test_a_stored_variadic_bundle_is_not_dumped() -> None:
    """A ``*args`` name can never be passed by keyword and a ``**kwargs`` name is
    never a declared slot, so neither belongs in a dumped config — a ``kwargs: {...}``
    line never round-tripped anyway (on reload it lands INSIDE the catchall as a
    literal ``"kwargs"`` key, doubly nested)."""

    @configurable
    class StoresVariadics:
        def __init__(self, *args: int, lr: float = 0.5, **kwargs: Any) -> None:
            self.args = args
            self.kwargs = kwargs
            self.lr = lr

    text = dump(StoresVariadics(lr=0.9))
    assert "lr" in text
    assert "args" not in text
    assert "kwargs" not in text


def test_dump_non_configurable_with_confluid_origin() -> None:
    """Objects created via Target/flow() retain origin metadata for dump."""
    from confluid import Target, flow

    class Metric:
        def __init__(self, num_classes: int = 10) -> None:
            self.num_classes = num_classes

    inst = Target(Metric, num_classes=5)
    live = flow(inst)

    output = dump(live)
    assert "_target_:" in output
    assert "num_classes: 5" in output


def test_dump_non_configurable_in_configurable_parent() -> None:
    """Non-configurable objects nested inside configurable ones serialize correctly."""
    from confluid import Target, flow

    class Metric:
        def __init__(self, average: str = "macro") -> None:
            self.average = average

    @configurable
    class Trainer:
        def __init__(self, metrics: Any = None) -> None:
            self.metrics = metrics

    metric = Target(Metric, average="weighted")
    trainer = Trainer(metrics=[flow(metric)])

    output = dump(trainer)
    assert "_target_: Trainer" in output
    assert "_target_:" in output
    assert "average: weighted" in output


def test_dump_non_configurable_round_trip() -> None:
    """Dump/load round-trip for non-configurable objects preserves kwargs."""
    from confluid import Target, flow

    class Widget:
        def __init__(self, size: int = 3, color: str = "red") -> None:
            self.size = size
            self.color = color

    inst = Target(Widget, size=7, color="blue")
    live = flow(inst)

    output = dump(live)
    assert "size: 7" in output
    assert "color: blue" in output


def test_dump_function_reference_round_trip() -> None:
    """Module-level callables dump as ``${ref:module.qualname}`` and reload as the live function.

    The spelling is a plain STRING, not a tag: a tag anywhere in the document costs the whole
    file its ``yaml.safe_load`` readability, which is the property the plain format exists for.
    """
    import os.path

    from confluid import load

    @configurable
    class Loader:
        def __init__(self, joiner: Any = None) -> None:
            self.joiner = joiner

    obj = Loader(joiner=os.path.join)
    output = dump(obj)

    assert "${ref:posixpath.join}" in output or "${ref:ntpath.join}" in output
    yaml.safe_load(output)  # the point of the spelling: a plain reader can still parse it

    reloaded = load(output)
    assert isinstance(reloaded, Loader)
    assert reloaded.joiner is os.path.join


def test_dump_lambda_rejected() -> None:
    """Anonymous callables (lambdas, closures) cannot be referenced — dump raises."""

    @configurable
    class Holder:
        def __init__(self, fn: Any = None) -> None:
            self.fn = fn

    obj = Holder(fn=lambda x: x)
    with pytest.raises(yaml.representer.RepresenterError):
        dump(obj)


def test_dump_builtin_function_reference() -> None:
    """Built-in callables (e.g., ``len``) dump as ``${ref:builtins.len}``."""

    @configurable
    class Holder:
        def __init__(self, fn: Any = None) -> None:
            self.fn = fn

    obj = Holder(fn=len)
    output = dump(obj)
    assert "${ref:builtins.len}" in output


def test_a_nested_non_configurable_class_value_dumps_its_qualname() -> None:
    """A class-valued kwarg dumps ``module.QualName``. The ``__name__`` spelling dumped
    ``pkg.Inner`` for a NESTED class — a path that resolves to nothing, or silently to
    an UNRELATED top-level class that happens to share the short name."""

    class Holder:
        class Inner:
            pass

    @configurable
    class HoldsClass:
        def __init__(self, activation: type = Holder.Inner) -> None:
            self.activation = activation

    text = dump(HoldsClass())
    assert "Holder.Inner" in text


# ---------------------------------------------------------------------------
# BODY SLOTS are dumped too (BUGS-2026-08-13 F2)
#
# `dump()` reconstructed a node from its CONSTRUCTOR params only, so an
# `__init__`-body attribute — a first-class configurable slot that `configure()`
# sets, `to_pydantic` types and the accept-list admits — was silently absent
# from the document. A config written beside a checkpoint as a reproducibility
# artifact therefore omitted every body-slot value the run actually used.
#
# The rule matches the one already applied to ctor params: dump the value
# ALWAYS, never "only when it differs from the default". That is what makes a
# dumped document self-contained — a later edit to a default in the source
# cannot change what an existing dump reloads to.
# ---------------------------------------------------------------------------


def test_a_body_slot_survives_dump_and_reload() -> None:
    from confluid import configure, load

    @configurable
    class BodyHost:
        def __init__(self) -> None:
            self.epochs = 1

    host = BodyHost()
    configure(host, config="epochs: 50")
    assert host.epochs == 50

    assert load(dump(host)).epochs == 50


def test_a_body_slot_is_dumped_even_when_it_equals_its_default() -> None:
    """The lose-nothing property, and the reason "dump only what changed" is wrong.

    A dumped document must reload to the value it RECORDS. If the value were
    omitted because it happened to match the default, then editing that default
    in the source later would silently change what every existing dump means.
    Constructor params already work this way; body slots now match.
    """
    import yaml as _yaml

    @configurable
    class BodyHost:
        def __init__(self) -> None:
            self.epochs = 1

    emitted = _yaml.safe_load(dump(BodyHost()))
    assert emitted["epochs"] == 1, "an untouched body slot is still recorded"


def test_a_MARKER_body_slot_survives_dump_and_reload() -> None:
    """The C4 round trip: a marker tuned by configure() must come back tuned."""
    import yaml as _yaml

    from confluid import PartialClass, Target, configure

    @configurable
    class Opt:
        def __init__(self, lr: float = 0.0) -> None:
            self.lr = lr

    @configurable
    class TargetHost:
        def __init__(self) -> None:
            self.opt = Target(Opt)

    @configurable
    class PartialHost:
        def __init__(self) -> None:
            self.opt = PartialClass(Opt)

    for cls in (TargetHost, PartialHost):
        host = cls()
        configure(host, config="lr: 0.75")
        assert host.opt.kwargs == {"lr": 0.75}

        emitted = _yaml.safe_load(dump(host))
        assert emitted["opt"]["lr"] == 0.75, f"{cls.__name__} lost its tuning"
        assert emitted["opt"]["_target_"].endswith("Opt")
        # `_partial_` rides along so the slot comes back DEFERRED, not built.
        assert emitted["opt"].get("_partial_", False) is (cls is PartialHost)

    # The reload half is asserted on a module-level class: a marker's target is
    # emitted as a raw dotted path (F3), which a `<locals>` test class cannot
    # resolve — an artifact of defining the class in a function, not of F2.


def test_a_constructor_param_is_unchanged_by_the_body_slot_dump() -> None:
    """The con case: params keep dumping exactly as before, in signature order."""
    import yaml as _yaml

    @configurable
    class Params:
        def __init__(self, epochs: int = 1, lr: float = 0.5, name: str = "base") -> None:
            self.epochs, self.lr, self.name = epochs, lr, name

    emitted = _yaml.safe_load(dump(Params(epochs=50)))
    assert emitted == {"_target_": "Params", "epochs": 50, "lr": 0.5, "name": "base"}


def test_a_setterless_property_is_still_not_dumped() -> None:
    """Derived state recomputes on reload; dumping it would be noise at best.

    Body slots are dumped, properties are not — the class-design convention's
    answer for derived state stays out of the document.
    """
    import yaml as _yaml

    @configurable
    class Derived:
        def __init__(self, frac: float = 0.8) -> None:
            self.frac = frac

        @property
        def n_train(self) -> int:
            return int(1000 * self.frac)

    emitted = _yaml.safe_load(dump(Derived(frac=0.6)))
    assert emitted == {"_target_": "Derived", "frac": 0.6}


# ---------------------------------------------------------------------------
# A MARKER's target is named by the REGISTRY, like a live instance's
# (BUGS-2026-08-13 F3)
#
# The live-instance branch asks `registry.key_for()` so the emitted name
# re-resolves to THIS class rather than to whichever namesake wins a bare
# lookup. `_target_name` bypassed it and emitted a raw dotted qualname, which
# fails outright for the two targets whose qualname is not importable.
# ---------------------------------------------------------------------------


def test_a_marker_targeting_a_factory_built_class_reloads() -> None:
    """A class built by a factory carries `<locals>` in its qualname, which the
    registry strips for its key and a raw dotted path keeps."""
    from confluid import Target, load

    def _make() -> type:
        @configurable
        class Widget:
            def __init__(self, size: int = 2) -> None:
                self.size = size

        return Widget

    Widget = _make()

    @configurable
    class Host:
        def __init__(self) -> None:
            self.child = Target(Widget, size=9)

    reloaded = load(dump(Host()))
    assert reloaded.child.size == 9


def test_a_marker_targeting_a_registered_FUNCTION_reloads() -> None:
    """It used to emit `<function build at 0x…>` — a memory address, so the
    document neither reloaded nor compared equal between two dumps."""
    from confluid import Target, load

    @configurable
    def build(size: int = 1) -> dict:
        return {"size": size}

    @configurable
    class Host:
        def __init__(self) -> None:
            self.child = Target(build, size=7)

    text = dump(Host())
    assert "0x" not in text, "a dumped document must not contain a memory address"
    assert load(text).child == {"size": 7}


def test_a_marker_and_a_live_instance_name_the_same_class_alike() -> None:
    """The two dumper branches must agree; the live one was already correct."""
    import yaml as _yaml

    from confluid import Target

    @configurable(name="CustomName")
    class Renamed:
        def __init__(self, lr: float = 0.1) -> None:
            self.lr = lr

    @configurable
    class LiveHost:
        def __init__(self) -> None:
            self.child = Renamed(lr=0.5)

    @configurable
    class MarkerHost:
        def __init__(self) -> None:
            self.child = Target(Renamed, lr=0.5)

    live = _yaml.safe_load(dump(LiveHost()))["child"]["_target_"]
    marker = _yaml.safe_load(dump(MarkerHost()))["child"]["_target_"]
    assert live == marker == "CustomName"


def test_a_marker_targeting_an_UNREGISTERED_class_keeps_its_dotted_path() -> None:
    """The fallback: no registry key, so the importable dotted path is still the
    best available spelling."""
    import yaml as _yaml

    from confluid import Target

    @configurable
    class Host:
        def __init__(self) -> None:
            self.child = Target(_UnregisteredWidget, size=3)

    emitted = _yaml.safe_load(dump(Host()))["child"]["_target_"]
    assert emitted.endswith("_UnregisteredWidget")
    assert "0x" not in emitted


def test_a_STRING_marker_target_passes_through_verbatim() -> None:
    """A target written as a name in YAML is already the spelling to emit."""
    import yaml as _yaml

    from confluid import Target

    @configurable
    class Host:
        def __init__(self) -> None:
            self.child = Target("SomeName", size=3)

    assert _yaml.safe_load(dump(Host()))["child"]["_target_"] == "SomeName"


# --- a property-shadowed ctor param dumps the CAPTURED value, getter never runs
# (BUGS-2026-08-19 CD2) ----------------------------------------------------------


def test_a_property_shadowed_ctor_param_dumps_the_captured_value_without_running_the_getter() -> None:
    getter_runs: list = []

    @configurable
    class _Loop:
        def __init__(self, device: str = "auto", max_epochs: int = 1) -> None:
            self.device_choice = device  # the ctor param is TRANSFORMED, stored elsewhere
            self.max_epochs = max_epochs

        @property
        def device(self) -> object:
            getter_runs.append(1)
            return object()  # derived, not serializable — dumping it broke the round trip

    loop = _Loop(device="cpu")
    text = dump(loop)
    assert getter_runs == [], f"the getter ran during dump:\n{text}"
    assert "device: cpu" in text, text
    reloaded = load(text)
    assert reloaded.device_choice == "cpu"
    assert reloaded.max_epochs == 1


def test_configure_of_a_property_shadowed_param_class_touches_nothing_derived() -> None:
    getter_runs: list = []

    @configurable
    class _Loop2:
        def __init__(self, device: str = "auto", max_epochs: int = 1) -> None:
            self.device_choice = device
            self.max_epochs = max_epochs

        @property
        def device(self) -> object:
            getter_runs.append(1)
            return object()

    loop = _Loop2()
    configure(loop, config={"max_epochs": 2})
    assert loop.max_epochs == 2
    assert getter_runs == [], "configure() must not execute nor write back the getter"


# --- value-faithful opaque spellings, **kwargs extras, and the F3 rule's second
# branch (BUGS-2026-08-19 CD9 / CD11 / CD12) --------------------------------------


def test_a_path_valued_attribute_round_trips_as_its_string() -> None:
    """CD9 — `{_target_: pathlib.PosixPath}` reloaded as `PosixPath('.')`, silently:
    a sink pointing at the launch directory instead of the run's output."""

    @configurable
    class _Sink:
        def __init__(self, path: str = ".") -> None:
            self.path = _pathlib.Path(path)

    sink = load("_target_: _Sink\npath: /data/run42/out\n")
    text = dump(sink)
    assert "path: /data/run42/out" in text, text
    assert "_target_: pathlib" not in text
    assert load(text).path == _pathlib.Path("/data/run42/out")


def test_numpy_scalars_and_enums_round_trip_by_value() -> None:
    class _Color(enum.Enum):
        RED = "red"
        BLUE = "blue"

    @configurable
    class _Host:
        def __init__(self, scale: Any = None, color: Any = None) -> None:
            self.scale = scale
            self.color = color

    host = _Host(scale=np.float32(1.5), color=_Color.RED)
    text = dump(host)
    assert "scale: 1.5" in text, text
    assert "color: red" in text, text
    reloaded = load(text)
    assert reloaded.scale == 1.5
    assert reloaded.color == "red"


def test_a_genuinely_opaque_value_still_emits_the_placeholder_but_AUDIBLY(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The con + the loudness: the placeholder keeps a dump complete, and the lie it
    tells on reload (a default-constructed object) is now announced once per type."""
    from types import SimpleNamespace

    import confluid.dumper as dumper_mod

    class _Callback:
        pass

    @configurable
    class _Trainer:
        def __init__(self, callback: Any = None) -> None:
            self.callback = callback

    records: list = []
    monkeypatch.setattr(
        dumper_mod, "logger", SimpleNamespace(warning=records.append, debug=lambda m: None, trace=lambda m: None)
    )
    dumper_mod._OPAQUE_WARNED.discard(_Callback)
    trainer = _Trainer(callback=_Callback())
    text = dump(trainer)
    assert "_Callback" in text
    assert any("DEFAULT-constructed" in r for r in records), records
    records.clear()
    dump(trainer)
    assert not records, "once per type"


def test_a_kwargs_classes_captured_extras_survive_the_round_trip() -> None:
    """CD11 — `Wrap(a=2, foo=3)` dumped as `{a: 2}`; the reload lost `foo`."""

    @configurable
    class _Wrap:
        def __init__(self, a: int = 1, **kwargs: Any) -> None:
            self.a = a
            self.kwargs = dict(kwargs)

    wrapped = load("_target_: _Wrap\na: 2\nfoo: 3\n")
    assert wrapped.kwargs == {"foo": 3}
    text = dump(wrapped)
    assert "foo: 3" in text, text
    assert load(text).kwargs == {"foo": 3}


def test_a_stamped_function_target_dumps_its_registry_key_not_an_address() -> None:
    """CD12 — the `__confluid_class__` branch carried its own inline copy of the
    naming rule and emitted `<function build_widget at 0x…>` for a registered
    FUNCTION target (F3 fixed the marker branch only)."""

    class _Widget:
        def __init__(self, size: int) -> None:
            self.size = size

    def build_widget(size: int = 1) -> _Widget:
        return _Widget(size)

    register(build_widget, name="build_widget")
    widget = load("_target_: build_widget\nsize: 7\n")
    text = dump(widget)
    assert "0x" not in text, text
    assert "_target_: build_widget" in text
    assert load(text).size == 7


# --------------------------------------------------------------------------- the $$ escape (CD10)


def test_a_literal_dollar_survives_the_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    """CD10 (BUGS-2026-08-19) — dump() wrote `$RUN_USER` unescaped and the reload
    interpolated it; the dumper now emits `$$` and the loader collapses it."""
    monkeypatch.setenv("RUN_USER", "gert")

    @configurable
    class Job:
        def __init__(self, command: str = "") -> None:
            self.command = command

    job = Job(command="echo $RUN_USER")
    text = dump(job)
    assert "echo $$RUN_USER" in text
    assert load(text).command == "echo $RUN_USER"
    tricky = Job(command="${a.b} and $$ money")
    assert load(dump(tricky)).command == "${a.b} and $$ money"


def test_a_dollar_in_a_mapping_key_round_trips() -> None:
    """CD10 — the escape is uniform (a representer cannot tell keys from values),
    so the loader collapses `$$` in keys too."""

    @configurable
    class Job:
        def __init__(self, command: str = "") -> None:
            self.command = command

    reloaded = load(dump({"a$b": Job(command="x")}))
    assert list(reloaded.keys()) == ["a$b"]


def test_a_slots_class_round_trips_its_body_slot() -> None:
    """N8 (BUGS-2026-08-19) — the member descriptor read as a class_attr, so
    dump() dropped the configured value and the reload restored the default."""

    @configurable
    class SlottedPoint:
        __slots__ = ("x", "label")

        def __init__(self, x: float = 0.0) -> None:
            self.x = x
            self.label = "p"

    reloaded = load(dump(load("p: {_target_: SlottedPoint, label: q}")["p"]))
    assert reloaded.label == "q"


def test_dollar_keys_nested_in_a_markers_kwargs_round_trip() -> None:
    """CD5 (BUGS-2026-08-22) — the key collapse lived in the plain-dict branch only, so a
    dict INSIDE a marker's kwargs reloaded with `$$k`."""

    @configurable
    class BoxCD5:
        def __init__(self, a: Any = None) -> None:
            self.a = a

    reloaded = load(dump(BoxCD5(a={"$k": 1, "a$b": 2, "lit": "$$ money"})))
    assert reloaded.a == {"$k": 1, "a$b": 2, "lit": "$$ money"}
