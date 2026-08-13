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
    from confluid.state import _ENGINE_STATE, active_context

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
    from confluid.state import _ENGINE_STATE

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


def test_a_leaf_delivery_satisfies_the_glob_registered_spelling() -> None:
    """A rider's content delivered by the CASCADE marks ``**.leaf`` used.

    A ``'**'`` block registers per leaf under the glob prefix (``**.lr``), but
    the nested-marker cascade delivers from a pool in which rider contents are
    flattened to BARE keys — it can only mark the LEAF. Before this rule a
    rider whose content landed everywhere it aimed still reported ``**.lr``
    unused in the same report that showed the delivery
    (``applied=[('lr', 'nested-class')]``), measured through a consumer's CLI
    override pipeline.
    """
    report = ConfigurationReport()
    report.add_config_keys(["**.lr", "other"])
    report.mark_used("lr")
    assert report.unused == ["other"]


def test_marking_the_glob_spelling_itself_still_works() -> None:
    report = ConfigurationReport()
    report.add_config_keys(["**.lr"])
    report.mark_used("**.lr")
    assert report.unused == []


# --------------------------------------------------------------------------------------
# explain() — why this key has this value
# --------------------------------------------------------------------------------------


def _tuned_cls() -> type:
    """A two-knob receiver for the ordering contests below.

    Defined per test (this file clears the registry in an autouse fixture, the
    ``_model_cls`` convention above).
    """

    @configurable
    class _Tuned:
        def __init__(self, lr: float = 0.0, epochs: int = 1, name: Optional[str] = None) -> None:
            """
            Args:
                lr: The knob every contest below competes over.
                epochs: A knob nothing competes over.
                name: Instance name, so two receivers can be told apart.
            """
            self.lr, self.epochs, self.name = lr, epochs, name

    return _Tuned


def test_explain_names_the_winner_the_loser_and_the_positions() -> None:
    """The whole point: position decided it, so position is what the answer shows.

    A value written AT the node losing to a bare key below it is confluid's most
    surprising behaviour and its documented rule. Before ``explain`` the only way
    to watch it happen was ``LOGGAIR_CONSOLE_LEVEL=TRACE`` and a grep, even though
    both candidates already reached the report's sink — the loser was discarded.
    """
    _tuned_cls()  # registers `_Tuned`; the YAML below names it by string
    with collect_report() as report:
        cfg = load("_Tuned:\n  lr: 0.5\nt:\n  _target_: _Tuned\nlr: 0.9\n")

    assert cfg["t"].lr == 0.9
    text = report.explain("lr")
    assert "lr on _Tuned = 0.9" in text
    assert "block '_Tuned'" in text and "0.5" in text and "beaten" in text
    assert "bare" in text and "applied" in text
    # The loser must be reported EARLIER than the winner — that is the reason.
    entry = next(a for a in report.applied if a.key == "lr")
    positions = [c.pos for c in entry.contest]
    assert positions == sorted(positions) and len(positions) == 2
    assert entry.contest[-1].origin == "bare", "the last candidate is the winner"


def test_explain_covers_a_markers_own_kwarg_losing_to_a_later_bare_key() -> None:
    """An own kwarg is a competitor like any other, and loses by position like one.

    ``_MergeSink.apply`` returns early for own kwargs (they are definitions, not
    overrides, so they erase an origin) — the contest is therefore recorded BEFORE
    that return, or the single case readers most need explained would be the one
    case with no explanation.
    """
    _tuned_cls()  # registers `_Tuned`; the YAML below names it by string
    with collect_report() as report:
        cfg = load("t:\n  _target_: _Tuned\n  lr: 0.5\nlr: 0.9\n")

    assert cfg["t"].lr == 0.9
    entry = next(a for a in report.applied if a.key == "lr")
    assert [c.origin for c in entry.contest] == ["own", "bare"]
    assert "own" in report.explain("lr")


def test_explain_agrees_across_the_load_and_configure_paths() -> None:
    """One rule, one explanation — the two paths must not answer differently.

    The ordered-merge rule was implemented twice before 2026-08-03 and the copies
    diverged four ways in a day. A diagnostic that reported the contest differently
    per path would be the same failure wearing a different hat.
    """
    _Tuned = _tuned_cls()
    document = "_Tuned:\n  lr: 0.5\nlr: 0.9\n"

    with collect_report() as load_report:
        load(f"t:\n  _target_: _Tuned\n{document}")
    live_report = configure(_Tuned(), config=document)

    def shape(report: ConfigurationReport) -> list:
        entry = next(a for a in report.applied if a.key == "lr")
        return [(c.origin, c.value) for c in entry.contest]

    assert shape(load_report) == shape(live_report) == [("block '_Tuned'", "0.5"), ("bare", "0.9")]


def test_explain_says_so_when_a_key_overrode_nothing() -> None:
    """ "Not applied" is not "not set" — conflating them sends the reader hunting.

    A marker's own kwarg and a constructor default both produce a value nothing
    overrode, which is exactly what having no override record means.
    """
    _tuned_cls()  # registers `_Tuned`; the YAML below names it by string
    with collect_report() as report:
        load("t:\n  _target_: _Tuned\n  epochs: 3\nlr: 0.9\n")

    text = report.explain("epochs")
    assert "overrode nothing" in text
    assert "constructor default" in text, "must name the innocent explanations"
    assert "lr" in text, "and list what DID override, so the reader can compare spellings"


def test_explain_narrows_to_one_receiver() -> None:
    """Two instances of one class both take the key; ``target`` picks one."""
    _tuned_cls()  # registers `_Tuned`; the YAML below names it by string
    with collect_report() as report:
        load("a:\n  _target_: _Tuned\n  name: first\nb:\n  _target_: _Tuned\n  name: second\nlr: 0.9\n")

    targets = {a.target for a in report.applied if a.key == "lr"}
    assert len(targets) >= 1
    one = sorted(targets)[0]
    assert report.explain("lr", target=one).count("lr on ") == 1


def test_a_contest_value_is_a_bounded_string_never_the_object() -> None:
    """The ledger must not keep a config VALUE alive, and must not raise rendering one.

    A config value is an arbitrary object — a dataset, a model, an array — so
    holding one for the report's lifetime turns a diagnostic into a leak, and
    calling its ``__repr__`` runs user code that may raise or be enormous.
    """
    from confluid.report import _short_repr

    class Exploding:
        def __repr__(self) -> str:
            raise RuntimeError("nope")

    assert _short_repr(Exploding()) == "<Exploding>"
    assert len(_short_repr("x" * 500)) <= 48
    assert _short_repr(0.9) == "0.9"


def test_an_uncontested_key_still_explains_itself_and_stores_no_candidates() -> None:
    """One source is not a contest — it must print, and it must not pay to render.

    A single candidate says nothing ``origin`` does not already say, and building
    one costs a ``repr()`` per applied key: measured at 4.6 ms of the 5.7 ms this
    ledger first added to a 2,500-marker ``configure()`` pass. So a lone candidate
    is dropped, and the same empty contest also covers the paths with no view to
    order at all (the deferred-slot cascade, a direct ``flow()``).
    """
    report = ConfigurationReport()
    report.record_applied("lr", "AdamW", "deferred slot")
    report.record_applied("wd", "AdamW", "bare", candidates=[("bare", 0.1, 3)])

    assert next(a for a in report.applied if a.key == "wd").contest == (), "one source stores nothing"
    for key in ("lr", "wd"):
        text = report.explain(key)
        assert f"{key} on AdamW" in text
        assert "nothing else competed" in text

    # Two sources DO get rendered — that is the case explain() exists for.
    report.record_applied("mom", "AdamW", "bare", candidates=[("block 'AdamW'", 0.8, 1), ("bare", 0.9, 4)])
    contested = next(a for a in report.applied if a.key == "mom")
    assert [c.value for c in contested.contest] == ["0.8", "0.9"]


# --------------------------------------------------------------------------------------
# An undeclared key is reported on BOTH paths (B1)
# --------------------------------------------------------------------------------------
#
# One typo, three behaviours, until 2026-08-12: `load()` with the key on the marker
# SET it silently; `load()` with the key in a class block IGNORED it silently; only
# `configure()` reported it. The load path's sink justified its silence with
# "constructor validation is this path's typo enforcement" — measured false: the key
# is not a ctor param, so `_ctor_params` filters it out and it reaches a post-init
# setattr, going AROUND the constructor. Validation never sees it.
#
# B1 (chosen 2026-08-12 over full parity): both load-path forms now WARN and record a
# ``unknown-attribute`` failure, and the own-kwarg form still APPLIES the value. The
# post-init attribute mechanism is documented behaviour — `_apply_post_init_attrs`
# exists to assign kwargs the constructor did not take — so B1 makes it audible
# without removing it. Refusing outright is the opt-in `strict_attrs` mark (TASKS.md).


def _node_cls() -> type:
    """A receiver with a ctor param AND a body slot, so 'declared' has both shapes."""

    @configurable
    class Node:
        def __init__(self, path: str = "") -> None:
            self.path = path
            self.enabled = False  # a body slot — DECLARED, and must stay silent

    return Node


def test_an_undeclared_key_on_the_marker_warns_records_and_still_applies() -> None:
    """B1's own-kwarg half: audible, but the value still lands.

    Dropping it instead would be full `configure()` parity (option B2) and would
    remove the post-init attribute mechanism from the commonest spelling. Measured
    across 96 workspace configs / 412 markers: zero rely on it today — but it is
    documented behaviour, so it changes only behind the opt-in mark.
    """
    _node_cls()
    with collect_report() as report:
        built = load("n:\n  _target_: Node\n  pathh: /x\n")["n"]

    assert built.pathh == "/x", "B1 still applies it — that is what distinguishes B1 from B2"
    assert [(f.key, f.reason) for f in report.failed] == [("pathh", "unknown-attribute")]


def test_an_undeclared_key_in_a_class_block_warns_and_records_on_the_load_path() -> None:
    """B1's class-block half: it was already dropped, and is now reported.

    This is the form `configure()` has always reported; the load path reached the
    same sink method and did nothing there.
    """
    _node_cls()
    with collect_report() as report:
        built = load("Node:\n  pathh: /x\nn:\n  _target_: Node\n")["n"]

    assert not hasattr(built, "pathh"), "still dropped, exactly as before"
    assert [(f.key, f.reason) for f in report.failed] == [("pathh", "unknown-attribute")]


def test_the_two_paths_now_report_the_same_failure_for_the_same_typo() -> None:
    """The point of the change: one document, one mistake, one answer."""
    Node = _node_cls()
    document = "Node:\n  pathh: /x\n"

    with collect_report() as load_report:
        load(f"n:\n  _target_: Node\n{document}")
    live_report = configure(Node(), config=document)

    assert [(f.key, f.reason) for f in load_report.failed] == [("pathh", "unknown-attribute")]
    assert [(f.key, f.reason) for f in live_report.failed] == [("pathh", "unknown-attribute")]


def test_a_DECLARED_body_slot_is_silent_on_the_load_path() -> None:
    """The control that keeps this from being a nuisance.

    A body slot is in the accept-list, so it is declared — the class-design
    convention's rule 4 exists to make exactly these configurable.
    """
    _node_cls()
    with collect_report() as report:
        built = load("n:\n  _target_: Node\n  enabled: true\n")["n"]

    assert built.enabled is True
    assert report.failed == []


def test_a_kwargs_class_never_reports_an_undeclared_key() -> None:
    """A ``**kwargs`` constructor has no accept-list — it accepts everything by design."""

    @configurable(validate=False)
    class Catchall:
        def __init__(self, **extra: Any) -> None:
            self.extra = dict(extra)

    with collect_report() as report:
        built = load("c:\n  _target_: Catchall\n  anything: 1\n")["c"]

    assert built.extra == {"anything": 1}
    assert report.failed == []


def test_a_BARE_key_matching_nothing_is_not_a_failure() -> None:
    """A bare key legitimately matches nothing — it is `unused`, never `failed`.

    Reporting bare misses would fire on every sweep document: a top-level `lr:`
    aimed at one node necessarily misses every other node in the tree.
    """
    _node_cls()
    with collect_report() as report:
        built = load("pathh: /x\nn:\n  _target_: Node\n")["n"]

    assert not hasattr(built, "pathh")
    assert report.failed == []
    assert "pathh" in report.unused, "it is an unused override, which is the honest bucket"
