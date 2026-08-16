from pathlib import Path
from typing import Any

import pytest

from confluid import ConfigFileNotFoundError, configurable, configure, configure_from_file, get_registry, load_config


@pytest.fixture(autouse=True)
def setup_registry() -> None:
    get_registry().clear()


def test_post_construction_configure() -> None:
    from confluid import ConfigurationReport

    @configurable
    class Model:
        def __init__(self, layers: int = 3, lr: float = 0.01):
            self.layers = layers
            self.lr = lr

    model = Model()
    assert model.layers == 3

    # Configure existing instance; the call returns the ConfigurationReport.
    report = configure(model, config={"Model": {"layers": 50, "lr": 0.001}})

    assert model.layers == 50
    assert model.lr == 0.001
    assert isinstance(report, ConfigurationReport)


def test_type_coercion() -> None:
    @configurable
    class Model:
        def __init__(self, layers: int = 3) -> None:
            self.layers = layers

    model = Model()

    # Pass string "100", Resolver should use parse_value to coerce to int 100
    configure(model, config={"Model": {"layers": "100"}})

    assert model.layers == 100
    assert isinstance(model.layers, int)


def test_scoped_configuration() -> None:
    @configurable
    class Model:
        def __init__(self, val: int = 1):
            self.val = val

    model = Model()

    # Nested data
    data = {"Model": {"val": 42}, "Other": {"val": 99}}
    configure(model, config=data)
    assert model.val == 42


def test_unscoped_configuration() -> None:
    @configurable
    class Model:
        def __init__(self, val: int = 1):
            self.val = val

    model = Model()

    # Direct dict
    configure(model, config={"val": 7})
    assert model.val == 7


def test_configure_none() -> None:
    @configurable
    class Model:
        def __init__(self, val: int = 1):
            self.val = val

    model = Model()
    configure(model, config=None)
    assert model.val == 1


def test_configure_dotted_keys() -> None:
    @configurable
    class Model:
        def __init__(self, layers: int = 3, dropout: float = 0.1):
            self.layers = layers
            self.dropout = dropout

    model = Model()
    # Helios style flat-dotted keys
    data = {"Model.layers": 10, "Model.dropout": 0.5}
    configure(model, config=data)
    assert model.layers == 10
    assert model.dropout == 0.5


def test_configure_non_dict() -> None:
    @configurable
    class Model:
        def __init__(self, val: int = 1):
            self.val = val

    model = Model()
    # Passing a reference that resolves to a class, not a dict
    configure(model, config="!class:Model")
    assert model.val == 1


def test_configure_from_file(tmp_path: Path) -> None:
    @configurable
    class Model:
        def __init__(self, layers: int = 3, lr: float = 0.01):
            self.layers = layers
            self.lr = lr

    cfg = tmp_path / "experiment.yaml"
    cfg.write_text("Model:\n  layers: 50\n  lr: 0.001\n")

    model = Model()
    configure_from_file(model, path=cfg)

    assert model.layers == 50
    assert model.lr == 0.001


def test_configure_from_file_accepts_str_path(tmp_path: Path) -> None:
    @configurable
    class Model:
        def __init__(self, val: int = 1):
            self.val = val

    cfg = tmp_path / "cfg.yaml"
    cfg.write_text("Model:\n  val: 7\n")

    model = Model()
    configure_from_file(model, path=str(cfg))  # str, not Path
    assert model.val == 7


def test_configure_from_file_equivalent_to_load_config_plus_configure(tmp_path: Path) -> None:
    @configurable
    class Model:
        def __init__(self, val: int = 1):
            self.val = val

    cfg = tmp_path / "cfg.yaml"
    cfg.write_text("Model:\n  val: 42\n")

    a, b = Model(), Model()
    configure_from_file(a, path=cfg)
    configure(b, config=load_config(cfg))
    assert a.val == b.val == 42


def test_configure_from_file_honours_includes(tmp_path: Path) -> None:
    @configurable
    class Model:
        def __init__(self, layers: int = 3, dropout: float = 0.1):
            self.layers = layers
            self.dropout = dropout

    (tmp_path / "base.yaml").write_text("Model:\n  layers: 8\n  dropout: 0.2\n")
    top = tmp_path / "top.yaml"
    top.write_text('include:\n  - "base.yaml"\nModel:\n  dropout: 0.5\n')

    model = Model()
    configure_from_file(model, path=top)
    assert model.layers == 8  # from the included base
    assert model.dropout == 0.5  # overridden by the top file


def test_configure_from_file_missing_path_raises(tmp_path: Path) -> None:
    @configurable
    class Model:
        def __init__(self, val: int = 1):
            self.val = val

    with pytest.raises(ConfigFileNotFoundError):
        configure_from_file(Model(), path=tmp_path / "does-not-exist.yaml")


# --- last-write-wins rebuild pins -------------------------------------------


def test_configure_sets_none() -> None:
    """A present key with value None SETS None (the old priority matcher used
    None as its no-match sentinel, making null values impossible to apply)."""

    @configurable
    class Model:
        def __init__(self, dropout: Any = 0.1):
            self.dropout = dropout

    model = Model()
    configure(model, config={"Model": {"dropout": None}})
    assert model.dropout is None


def test_configure_never_executes_property_getters() -> None:
    """configure() walks vars(obj) — a property getter must NEVER fire (the
    old dir()+getattr walk executed every getter, the documented gotcha)."""
    calls = {"n": 0}

    @configurable
    class Model:
        def __init__(self, layers: int = 3):
            self.layers = layers

        @property
        def expensive(self) -> int:
            calls["n"] += 1
            return 42

    model = Model()
    configure(model, config={"Model": {"layers": 7}})
    assert model.layers == 7
    assert calls["n"] == 0


def test_configure_unknown_block_key_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo'd (non-dict) key inside the object's own block warns and no-ops.

    The module logger is patched directly — loggair does not propagate into
    stdlib logging, so pytest's ``caplog`` cannot capture it.
    """
    from types import SimpleNamespace

    import confluid.configurator as configurator_module

    warnings_seen: list[str] = []
    monkeypatch.setattr(configurator_module, "logger", SimpleNamespace(warning=lambda msg: warnings_seen.append(msg)))

    @configurable
    class Model:
        def __init__(self, layers: int = 3):
            self.layers = layers

    model = Model()
    configure(model, config={"Model": {"layerz": 50}})
    assert model.layers == 3
    assert any("layerz" in msg and "no attribute" in msg for msg in warnings_seen)


def test_configure_settable_property_still_configured() -> None:
    """A property WITH a setter stays configurable (shared accept-list keeps
    writable properties; only setter-less ones are skipped)."""

    @configurable
    class Model:
        def __init__(self, base: int = 10):
            self._base = base

        @property
        def base(self) -> int:
            return self._base

        @base.setter
        def base(self, val: int) -> None:
            self._base = val

    model = Model()
    configure(model, config={"Model": {"base": 99}})
    assert model.base == 99


def test_configure_last_write_wins_document_order() -> None:
    """The confluid rule: NO priority tiers — whichever assignment comes last
    in document order wins, even when a generic key follows a specific block."""

    @configurable
    class Model:
        def __init__(self, lr: float = 0.01):
            self.lr = lr

    # Specific block first, generic broadcast LATER → the broadcast wins.
    m1 = Model()
    configure(m1, config={"Model": {"lr": 0.5}, "lr": 0.9})
    assert m1.lr == 0.9

    # Generic broadcast first, specific block LATER → the block wins.
    m2 = Model()
    configure(m2, config={"lr": 0.9, "Model": {"lr": 0.5}})
    assert m2.lr == 0.5


def test_configure_applies_values_before_solidify_fires() -> None:
    """``configure()`` finalizes AFTER applying — never with pre-configure values.

    ``_walk``'s ``flow(obj)`` fires ``solidify()`` on live objects since the
    record-2 pass-through change; unsuppressed it ran BEFORE ``_apply``, so an
    unsolidified object was finalized from its PRE-configure state and — the
    hook being idempotent by contract — never rebuilt. The walk now flows with
    ``solidify=False`` and re-fires the hook post-order, once the object and
    its subtree carry the new values (the load path's ordering: config final,
    then finalize).
    """

    @configurable
    class Model:
        def __init__(self, width: int = 8):
            self.width = width
            self.backbone: object = None

        def solidify(self) -> None:
            if self.backbone is None:
                self.backbone = f"backbone(width={self.width})"

    m = Model()
    configure(m, config={"width": 32})
    assert m.width == 32
    assert m.backbone == "backbone(width=32)"


def test_configure_does_not_rebuild_an_already_solidified_object() -> None:
    """An object solidified BEFORE ``configure()`` keeps its built state.

    The idempotency contract (build-once-and-cache) is the object author's;
    configure() re-fires the hook but must not force a rebuild — reconfiguring
    a built object and expecting fresh derived state is what the recompute-
    property convention exists for, not solidify().
    """

    @configurable
    class Model:
        def __init__(self, width: int = 8):
            self.width = width
            self.backbone: object = None
            self.builds = 0

        def solidify(self) -> None:
            if self.backbone is None:
                self.builds += 1
                self.backbone = f"backbone(width={self.width})"

    m = Model()
    from confluid import flow as _flow

    _flow(m)  # domain code finalized it first — width=8 is the built state
    configure(m, config={"width": 32})
    assert m.width == 32
    assert m.builds == 1
    assert m.backbone == "backbone(width=8)"


def test_configure_warns_when_config_is_not_a_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-mapping config is a no-op — but never a SILENT one.

    The canonical miss: ``configure(model, config="overrides.yaml")`` — a plain
    filename fails the YAML heuristic (no ``:`` / newline), stays a ``str``,
    and an empty report came back with no diagnostic anywhere, reading as
    "configured fine". The warning names the actual fix for the string case.
    """
    from types import SimpleNamespace

    import confluid.configurator as configurator_module

    seen: list = []
    monkeypatch.setattr(
        configurator_module,
        "logger",
        SimpleNamespace(warning=seen.append, debug=lambda m: None, trace=lambda m: None),
    )

    @configurable
    class Model:
        def __init__(self, lr: float = 0.01):
            self.lr = lr

    m = Model()
    report = configure(m, config="overrides.yaml")

    assert not report.applied
    assert any("configure_from_file" in msg for msg in seen), seen
    assert m.lr == 0.01

    # A mapping stays quiet — the warning must not fire on the ordinary path.
    seen.clear()
    configure(m, config={"lr": 0.5})
    assert not seen and m.lr == 0.5


# ---------------------------------------------------------------------------
# configure() must not write to objects nobody keeps (BUGS-2026-08-13 C3, C4)
# ---------------------------------------------------------------------------


def test_configure_recurses_into_a_callable_child() -> None:
    """C3: the walk skipped every child defining ``__call__`` — which is every op
    and every framework module in a real tree.

    ``capture=False`` on the parent is load-bearing HERE: with the default, the
    ctor-kwargs capture sitting in ``__dict__`` is a plain dict the walk recurses
    into, which reaches the child by accident and hides the defect.
    """

    @configurable
    class CallableOp:
        def __init__(self, lr: float = 0.0) -> None:
            self.lr = lr

        def __call__(self, x: Any) -> Any:
            return x

    @configurable(capture=False)
    class Trainer:
        def __init__(self, op: Any = None) -> None:
            self.op = op

    trainer = Trainer(op=CallableOp())
    report = configure(trainer, config="lr: 0.5")

    assert trainer.op.lr == 0.5
    assert ("lr", "CallableOp") in [(a.key, a.target) for a in report.applied]
    assert "lr" not in report.unused


def test_a_transforming_constructor_configures_the_LIVE_child_not_the_capture() -> None:
    """C3's silent-wrong half: when the constructor stores something OTHER than
    what it captured, the walk configured the discarded object and reported success."""

    @configurable
    class CallableOp:
        def __init__(self, lr: float = 0.0) -> None:
            self.lr = lr

        def __call__(self, x: Any) -> Any:
            return x

    @configurable
    class Wrapper:
        def __init__(self, op: Any = None) -> None:
            self.op = CallableOp(lr=op.lr)  # a DIFFERENT object from the captured kwarg

    handed = CallableOp()
    wrapper = Wrapper(op=handed)
    assert getattr(wrapper, "__confluid_kwargs__")["op"] is not wrapper.op

    configure(wrapper, config="lr: 0.5")

    assert wrapper.op.lr == 0.5, "the LIVE child must be configured"


def test_the_ctor_kwargs_capture_is_also_walked() -> None:
    """RULED DELIBERATE (user, 2026-08-15) — see BUGS-2026-08-13.md F1.

    ``__confluid_kwargs__`` is engine bookkeeping that lives in ``__dict__``, so the
    walk recurses into it like any other dict and configures whatever the constructor
    was HANDED — including an argument it discarded. That reach is KEPT: an object the
    config is meant to configure still gets configured when a constructor consumed it
    rather than storing it.

    This test is the pin that makes removing it a DELIBERATE change. Skipping the
    capture dict is one line and breaks nothing else (prototyped: 1 failed — this
    test — 1499 passed), so without the pin it would be an easy silent "cleanup".
    """

    @configurable
    class Op:
        def __init__(self, lr: float = 0.0) -> None:
            self.lr = lr

    @configurable
    class Wrapper:
        def __init__(self, op: Any = None) -> None:
            self.op = Op(lr=op.lr)  # the handed object is NOT kept

    handed = Op()
    configure(Wrapper(op=handed), config="lr: 0.5")

    assert handed.lr == 0.5, "today the discarded ctor argument is configured too"


def test_a_function_attribute_is_still_skipped() -> None:
    """The filter narrows from ``callable()`` to routines-and-classes: a stored
    function has no configuration surface and must stay untouched."""
    import math

    @configurable(capture=False)
    class Holder:
        def __init__(self) -> None:
            self.fn = math.sqrt
            self.lr = 0.0

    holder = Holder()
    configure(holder, config="lr: 0.5")

    assert holder.lr == 0.5
    assert holder.fn is math.sqrt


def test_a_CLASS_attribute_is_still_skipped() -> None:
    """The reason ``isclass`` is in the predicate: a class object's ``__dict__``
    is a TRUTHY mappingproxy of its own attributes, so recursing into one would
    walk class internals."""

    @configurable
    class Op:
        def __init__(self, lr: float = 0.0) -> None:
            self.lr = lr

    @configurable(capture=False)
    class Holder:
        def __init__(self) -> None:
            self.op_cls = Op
            self.lr = 0.0

    holder = Holder()
    configure(holder, config="lr: 0.5")

    assert holder.lr == 0.5
    assert holder.op_cls is Op
    assert "lr" not in vars(Op), "no key may be written onto the CLASS"


def test_a_functools_partial_attribute_does_not_break_the_walk() -> None:
    """It stops being skipped (it is neither a routine nor a class), so pin that
    walking it is harmless — it carries no configuration surface."""
    import functools

    @configurable(capture=False)
    class Holder:
        def __init__(self) -> None:
            self.fn = functools.partial(max, 0)
            self.lr = 0.0

    holder = Holder()
    configure(holder, config="lr: 0.5")

    assert holder.lr == 0.5
    assert holder.fn(5) == 5


def test_a_plain_Target_body_slot_is_TUNED_not_flowed_into_a_throwaway() -> None:
    """C4: the walk flowed the marker into a temporary, applied the config to the
    temporary, discarded it, and recorded the key as applied — so a later
    ``flow(obj.opt)`` built with the DEFAULTS.

    The ``Partial`` early return names this exact hazard in its own comment; the
    next line committed it for every non-partial marker.
    """
    from confluid import Target, flow

    @configurable
    class Opt:
        def __init__(self, lr: float = 0.0) -> None:
            self.lr = lr

    @configurable
    class Host:
        def __init__(self) -> None:
            self.opt = Target(Opt)

    host = Host()
    configure(host, config="lr: 0.75")

    assert host.opt.kwargs == {"lr": 0.75}, "the marker itself must carry the value"
    assert flow(host.opt).lr == 0.75


def test_a_PartialClass_body_slot_is_unchanged() -> None:
    """The shape C4 aligns with — it already worked and must keep working."""
    from confluid import PartialClass, flow

    @configurable
    class Opt:
        def __init__(self, lr: float = 0.0) -> None:
            self.lr = lr

    @configurable
    class Host:
        def __init__(self) -> None:
            self.opt = PartialClass(Opt)

    host = Host()
    configure(host, config="lr: 0.75")

    assert host.opt.kwargs == {"lr": 0.75}
    assert flow(host.opt).lr == 0.75


def test_a_Reference_body_slot_still_raises() -> None:
    """The scope guard: ``Partial`` IS a ``Target`` but ``Reference``/``Clone`` are
    NOT, so widening the check to ``Target`` must leave them on the flow path.

    Swallowing them into the tune path would turn this loud failure into a silent
    no-op — the exact degradation this whole family is about.
    """
    from confluid.exceptions import ReferenceResolutionError
    from confluid.fluid import Clone, Reference

    @configurable
    class HostRef:
        def __init__(self) -> None:
            self.opt = Reference("nowhere")

    @configurable
    class HostClone:
        def __init__(self) -> None:
            self.opt = Clone("nowhere")

    for cls in (HostRef, HostClone):
        with pytest.raises(ReferenceResolutionError):
            configure(cls(), config="lr: 0.75")


def test_a_marker_body_slot_IS_emitted_by_dump() -> None:
    """The C4 tuning reaches a dumped document (BUGS-2026-08-13 F2).

    This test asserted the opposite when C4 landed: ``dump()`` modelled the
    constructor alone, so the value configure() merged into a marker lived on the
    object but never in the artifact. Fixed by adding ``body_slot`` to the
    dumper's kind projection.
    """
    import yaml as _yaml

    from confluid import PartialClass, Target, dump

    @configurable
    class Opt:
        def __init__(self, lr: float = 0.0) -> None:
            self.lr = lr

    @configurable
    class HostTarget:
        def __init__(self) -> None:
            self.opt = Target(Opt)

    @configurable
    class HostPartial:
        def __init__(self) -> None:
            self.opt = PartialClass(Opt)

    for cls in (HostTarget, HostPartial):
        host = cls()
        configure(host, config="lr: 0.75")
        assert host.opt.kwargs == {"lr": 0.75}, "the marker itself carries the value"
        assert _yaml.safe_load(dump(host))["opt"]["lr"] == 0.75, "and the document carries it too"
