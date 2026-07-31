"""ConfigurationReport pins — applied / failed / unused tracking on both paths.

``configure()`` / ``configure_from_file()`` return a
:class:`confluid.ConfigurationReport` spanning the whole call;
:func:`confluid.collect_report` installs an ambient report on the engine state
so the YAML materialization path (``load`` / ``materialize`` / ``flow``)
reports too, and a nested ``configure()`` aggregates into it.

Log assertions monkeypatch the module logger with a ``SimpleNamespace``
collector — loggair does not propagate into stdlib logging, so ``caplog``
would be a false green (the ``test_configurator.py`` idiom).
"""

from types import SimpleNamespace
from typing import Any, Optional

import pytest

from confluid import (
    ConfigurationReport,
    collect_report,
    configurable,
    configure,
    dump,
    get_registry,
    load,
    materialize,
    reset_policy,
    set_policy,
)


@pytest.fixture(autouse=True)
def setup_registry() -> None:
    get_registry().clear()


@pytest.fixture(autouse=True)
def _policy_reset() -> Any:
    reset_policy()
    yield
    reset_policy()


def _model_cls() -> type:
    @configurable
    class Model:
        def __init__(self, layers: int = 3, lr: float = 0.01, name: Optional[str] = None):
            self.layers = layers
            self.lr = lr
            self.name = name

    return Model


# --- configure() return value + applied origins ------------------------------


def test_configure_returns_report_with_applied_block() -> None:
    Model = _model_cls()
    model = Model()
    report = configure(model, config={"Model": {"layers": 50}})
    assert isinstance(report, ConfigurationReport)
    assert model.layers == 50
    assert [(a.key, a.target, a.origin) for a in report.applied] == [("layers", "Model", "block 'Model'")]
    assert report.failed == []
    assert report.unused == []


def test_report_applied_origins_bare_block_glob_and_instance_label() -> None:
    Model = _model_cls()
    model = Model(name="encoder")
    report = configure(model, config={"lr": 0.5, "Model": {"layers": 7}, "**": {"layers": 9}})
    by_key = {(a.key, a.origin) for a in report.applied}
    # last write wins: the '**' glob block comes after the named block.
    assert by_key == {("lr", "bare"), ("layers", "glob '**'")}
    assert all(a.target == "Model 'encoder'" for a in report.applied)


def test_report_applied_addressed_recursion_origin() -> None:
    @configurable
    class Child:
        def __init__(self, lr: float = 0.1):
            self.lr = lr

    @configurable
    class Root:
        def __init__(self, mid: Any = None):
            self.mid = mid

    root = Root(mid=Child())
    report = configure(root, config={"Root": {"mid": {"lr": 0.7}}})
    assert root.mid.lr == 0.7
    assert [(a.key, a.target, a.origin) for a in report.applied] == [("lr", "Child", "addressed")]
    assert report.unused == []


def test_report_one_applied_record_per_attr_last_write_wins() -> None:
    Model = _model_cls()
    model = Model()
    # bare first, block later → ONE record, block origin (the final assignment).
    report = configure(model, config={"layers": 5, "Model": {"layers": 7}})
    records = [a for a in report.applied if a.key == "layers"]
    assert len(records) == 1
    assert records[0].origin == "block 'Model'"
    assert model.layers == 7


# --- failed keys --------------------------------------------------------------


def test_report_failed_unknown_attribute_and_warning_still_fires(monkeypatch: pytest.MonkeyPatch) -> None:
    import confluid.configurator as configurator_module

    warnings_seen: list[str] = []
    monkeypatch.setattr(
        configurator_module,
        "logger",
        SimpleNamespace(warning=lambda msg: warnings_seen.append(msg), trace=lambda msg: None),
    )
    Model = _model_cls()
    model = Model()
    report = configure(model, config={"Model": {"layerz": 50}})
    assert model.layers == 3
    assert [(f.key, f.target, f.reason) for f in report.failed] == [("layerz", "Model", "unknown-attribute")]
    assert any("layerz" in msg for msg in warnings_seen)
    assert report.unused == []  # the block matched — content typos are failed, not unused


def test_report_failed_validation_warn_mode_value_still_applied() -> None:
    pytest.importorskip("pydantic")
    set_policy(init="warn")
    Model = _model_cls()
    model = Model()
    report = configure(model, config={"Model": {"layers": "not-an-int"}})
    assert model.layers == "not-an-int"  # warn mode applies anyway
    failed = [f for f in report.failed if f.reason == "validation"]
    assert len(failed) == 1
    assert failed[0].key == "layers" and failed[0].detail is not None
    # the assignment DID land, so it is also recorded as applied
    assert any(a.key == "layers" for a in report.applied)


def test_report_failed_validation_strict_records_then_raises() -> None:
    pytest.importorskip("pydantic")
    set_policy(init="strict")
    Model = _model_cls()
    model = Model()
    with collect_report() as ambient:
        with pytest.raises(ValueError):  # pydantic.ValidationError is a ValueError
            configure(model, config={"Model": {"layers": "not-an-int"}})
    assert [(f.key, f.reason) for f in ambient.failed] == [("layers", "validation")]
    assert model.layers == 3  # strict mode never applied the value


def test_report_eager_staleness_is_applied_with_note(monkeypatch: pytest.MonkeyPatch) -> None:
    import confluid.configurator as configurator_module

    monkeypatch.setattr(
        configurator_module, "logger", SimpleNamespace(warning=lambda msg: None, trace=lambda msg: None)
    )

    @configurable(eager=True)
    class Eager:
        def __init__(self, size: int = 2):
            self.size = size
            self.doubled = size * 2

    obj = Eager()
    report = configure(obj, config={"Eager": {"size": 5}})
    assert obj.size == 5
    assert report.failed == []
    (record,) = report.applied
    assert record.key == "size" and record.note is not None and "__init__" in record.note


# --- unused keys ---------------------------------------------------------------


def test_report_unused_bare_key_and_used_not_listed() -> None:
    Model = _model_cls()
    report = configure(Model(), config={"lr": 0.5, "ghost": 1})
    assert report.unused == ["ghost"]


def test_report_unused_block() -> None:
    Model = _model_cls()
    report = configure(Model(), config={"Other": {"lr": 0.5}})
    assert report.unused == ["Other"]
    assert report.applied == [] and report.failed == []


def test_report_unused_glob_leaf() -> None:
    Model = _model_cls()
    report = configure(Model(), config={"**": {"lr": 0.5, "nope": 1}})
    assert report.unused == ["**.nope"]
    assert [(a.key, a.origin) for a in report.applied] == [("lr", "glob '**'")]


def test_report_unused_spans_all_instances() -> None:
    @configurable
    class A:
        def __init__(self, a_only: int = 1):
            self.a_only = a_only

    @configurable
    class B:
        def __init__(self, b_only: int = 2):
            self.b_only = b_only

    a, b = A(), B()
    report = configure(a, b, config={"a_only": 10, "b_only": 20, "neither": 30})
    assert a.a_only == 10 and b.b_only == 20
    # b_only matched only the SECOND instance — still used; one report spans the call.
    assert report.unused == ["neither"]


def test_report_dotted_keys_expand_to_block() -> None:
    Model = _model_cls()
    model = Model()
    report = configure(model, config={"Model.layers": 10})
    assert model.layers == 10
    assert report.unused == []
    assert [(a.key, a.origin) for a in report.applied] == [("layers", "block 'Model'")]


def test_report_routing_metadata_never_unused() -> None:
    @configurable
    class Child:
        def __init__(self, lr: float = 0.1):
            self.lr = lr

    @configurable
    class Root:
        def __init__(self, mid: Any = None):
            self.mid = mid

    root = Root(mid=Child())
    report = configure(root, config={"Root": {"mid": {"lr": 0.9}}})
    assert root.mid.lr == 0.9
    # 'mid' is block content (routing to the child), never a registered
    # document key — it can't appear in unused; the matched 'Root' is used.
    assert report.unused == []


# --- engine path (collect_report) ----------------------------------------------


def _register_tree_classes() -> None:
    @configurable
    class Inner:
        def __init__(self, lr: float = 0.1):
            self.lr = lr

    @configurable
    class Outer:
        def __init__(self, child: Any = None, depth: int = 1):
            self.child = child
            self.depth = depth


def test_collect_report_engine_path_applied_and_unused() -> None:
    _register_tree_classes()
    yaml_text = """
outer: !class:Outer
  child: !class:Inner
lr: 0.5
depth: 3
ghost: 9
Outer:
  depth: 7
"""
    with collect_report() as report:
        load(yaml_text)
    by_key = {(a.key, a.target, a.origin) for a in report.applied}
    # bare depth was overwritten by the Outer block — one final record each.
    assert ("depth", "Outer", "block 'Outer'") in by_key
    assert ("lr", "Inner", "bare") in by_key
    assert report.unused == ["ghost"]


def test_collect_report_engine_glob_leaves() -> None:
    _register_tree_classes()
    with collect_report() as report:
        load("outer: !class:Outer\n'**':\n  depth: 4\n  nope: 1\n")
    assert [(a.key, a.origin) for a in report.applied] == [("depth", "glob '**'")]
    assert report.unused == ["**.nope"]


def test_collect_report_survives_materialize_and_active_context() -> None:
    from confluid.engine import _ENGINE_STATE, active_context

    _register_tree_classes()
    with collect_report() as report:
        with active_context({"lr": 0.5}):
            assert _ENGINE_STATE.get().report is report  # fresh state carries it
        materialize({"outer": {"_target_": None}} if False else {"lr": 0.9, "outer": "!class:Outer()"})
        assert _ENGINE_STATE.get().report is report
    assert _ENGINE_STATE.get().report is None


def test_collect_report_aggregates_configure() -> None:
    Model = _model_cls()
    with collect_report() as ambient:
        returned = configure(Model(), config={"lr": 0.5})
    assert returned is ambient
    assert [(a.key, a.origin) for a in ambient.applied] == [("lr", "bare")]


def test_collect_report_nesting_reuses_outer_report() -> None:
    with collect_report() as outer:
        with collect_report() as inner:
            assert inner is outer


def test_no_report_active_is_default() -> None:
    from confluid.engine import _ENGINE_STATE

    _register_tree_classes()
    tree = load("outer: !class:Outer()\ndepth: 5\n")
    assert tree["outer"].depth == 5  # behavior unchanged without a report
    assert _ENGINE_STATE.get().report is None
    # each bare configure() call returns a FRESH report
    Model = _model_cls()
    assert configure(Model(), config={"lr": 1.0}) is not configure(Model(), config={"lr": 2.0})


# --- DEBUG summary line ---------------------------------------------------------


def test_unused_summary_logs_one_debug_line(monkeypatch: pytest.MonkeyPatch) -> None:
    import confluid.report as report_module

    lines: list[str] = []
    monkeypatch.setattr(report_module, "logger", SimpleNamespace(debug=lambda msg: lines.append(msg)))
    Model = _model_cls()
    configure(Model(), config={"lr": 0.5, "ghost": 1, "phantom": 2})
    assert len(lines) == 1
    assert "ghost" in lines[0] and "phantom" in lines[0] and "2 unused" in lines[0]


def test_no_debug_line_when_all_used(monkeypatch: pytest.MonkeyPatch) -> None:
    import confluid.report as report_module

    lines: list[str] = []
    monkeypatch.setattr(report_module, "logger", SimpleNamespace(debug=lambda msg: lines.append(msg)))
    Model = _model_cls()
    configure(Model(), config={"lr": 0.5})
    assert lines == []


def test_collect_report_logs_summary_on_exit_only_once(monkeypatch: pytest.MonkeyPatch) -> None:
    import confluid.report as report_module

    lines: list[str] = []
    monkeypatch.setattr(report_module, "logger", SimpleNamespace(debug=lambda msg: lines.append(msg)))
    Model = _model_cls()
    with collect_report():
        configure(Model(), config={"ghost": 1})  # adopted report → configure does NOT log
        assert lines == []
    assert len(lines) == 1  # the owning collect_report logs once on exit


# --- summary / repr / round-trip -------------------------------------------------


def test_report_summary_and_repr() -> None:
    Model = _model_cls()
    report = configure(Model(), config={"lr": 0.5, "ghost": 1})
    assert report.summary() == "1 applied, 0 failed, 1 unused"
    assert repr(report) == "<ConfigurationReport applied=1 failed=0 unused=1>"


def test_report_round_trip() -> None:
    """The mandated dump→load pin: reporting must not perturb serialization."""
    Model = _model_cls()
    model = Model()
    report = configure(model, config={"Model": {"layers": 11, "lr": 0.25}})
    assert report.summary().startswith("2 applied")
    reloaded = load(dump(model))
    assert reloaded.layers == 11
    assert reloaded.lr == 0.25
