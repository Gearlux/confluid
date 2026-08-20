"""Phase 4 of record 19 — ``configure()`` goes through the document.

``configure(obj, config=…)`` used to re-derive the precedence rule over LIVE objects (a second
walker kept in parity with the load path). It is now three steps that already exist: the objects
become a marker document (``dumper.to_markers`` — the same reconstruction rule ``dump()`` uses),
the overrides are merged AFTER it, pass 7 settles the whole thing, and the settled values are
applied back onto the objects. One rule, one implementation. Pinned here:

* the answers are the ones ``configure()`` always gave (bare keys, class blocks, a body-slot
  marker tuned in place, document order);
* a NEW capability: a dotted override addresses a nested object by the NAME the caller gives
  (``configure(trainer=t, config={"trainer.model.lr": 0.7})``) — positional objects keep working;
* a shared object is configured once; a marker held as an attribute keeps its identity;
* an object the dumper cannot represent is warned about and skipped, never a crash;
* the report still says applied / failed / unused.
"""

from typing import Any, Optional

import pytest

from confluid import Partial, PartialClass, configurable, configure, configure_from_file, flow
from confluid.report import ConfigurationReport


@configurable
class CModel:
    def __init__(self, hidden: int = 16, lr: float = 0.0, name: Optional[str] = "m") -> None:
        self.hidden, self.lr, self.name = hidden, lr, name


@configurable
class CAdam:
    def __init__(self, params: Any = None, lr: float = 0.001) -> None:
        self.params, self.lr = params, lr


@configurable
class CTrainer:
    def __init__(self, model: Any = None, epochs: int = 1) -> None:
        self.model, self.epochs = model, epochs
        self.optimizer: Partial[CAdam] = PartialClass(CAdam)  # a body slot holding a deferred marker
        self.solidified = 0

    def solidify(self) -> None:
        self.solidified += 1


# --------------------------------------------------------------------------- #
# The answers are the ones configure() always gave
# --------------------------------------------------------------------------- #


def test_bare_keys_class_blocks_and_body_slot_markers_as_before() -> None:
    trainer = CTrainer(model=CModel(hidden=32))
    marker_before = trainer.optimizer
    assert isinstance(marker_before, PartialClass)
    report = configure(trainer, config={"lr": 0.3, "epochs": 5, "CModel": {"hidden": 64}})
    assert (trainer.epochs, trainer.model.hidden, trainer.model.lr) == (5, 64, 0.3)
    assert trainer.optimizer is marker_before, "the body-slot marker keeps its identity — tuned, not replaced"
    assert marker_before.kwargs == {"lr": 0.3}
    assert flow(trainer.optimizer, params=[1]).lr == 0.3
    assert isinstance(report, ConfigurationReport) and report.applied


def test_document_order_last_write_wins_across_a_class_block_and_a_bare_key() -> None:
    m = CModel(hidden=1)
    configure(m, config={"CModel": {"hidden": 8}, "hidden": 9})
    assert m.hidden == 9  # the bare key came later
    configure(m, config={"hidden": 9, "CModel": {"hidden": 8}})
    assert m.hidden == 8  # the block came later


def test_a_present_null_sets_none() -> None:
    m = CModel(name="x")
    configure(m, config={"name": None})
    assert m.name is None


def test_solidify_fires_after_the_values_are_applied() -> None:
    t = CTrainer()
    configure(t, config={"epochs": 3})
    assert (t.epochs, t.solidified) == (3, 1)


# --------------------------------------------------------------------------- #
# NEW — a dotted override addresses a nested object by the caller's name
# --------------------------------------------------------------------------- #


def test_a_named_object_is_addressable_by_a_dotted_path() -> None:
    t = CTrainer(model=CModel(hidden=32))
    configure(config={"trainer.model.lr": 0.7, "trainer.epochs": 9}, trainer=t)
    assert (t.model.lr, t.epochs) == (0.7, 9)


def test_a_named_block_addresses_the_object_and_its_children() -> None:
    t = CTrainer(model=CModel(hidden=32))
    configure(config={"trainer": {"epochs": 2, "model": {"hidden": 3}}}, trainer=t)
    assert (t.epochs, t.model.hidden) == (2, 3)


def test_positional_and_named_objects_mix() -> None:
    a, b = CModel(hidden=1), CModel(hidden=2)
    configure(a, config={"lr": 0.5, "second.hidden": 20}, second=b)
    assert (a.lr, b.lr, a.hidden, b.hidden) == (0.5, 0.5, 1, 20)


# --------------------------------------------------------------------------- #
# Identity and sharing
# --------------------------------------------------------------------------- #


def test_a_shared_child_is_configured_once_and_stays_shared() -> None:
    m = CModel(hidden=1)
    t1, t2 = CTrainer(model=m), CTrainer(model=m)
    configure(t1, t2, config={"hidden": 7})
    assert t1.model is t2.model is m and m.hidden == 7


def test_a_mapping_addressed_at_a_slot_holding_a_marker_tunes_it_in_place() -> None:
    """The dict-at-slot rule ("a mapping addressed at a slot already holding a deferred marker
    TUNES it") — through a class block and through a dotted path — and the marker keeps its identity."""
    t = CTrainer()
    marker = t.optimizer
    assert isinstance(marker, PartialClass)
    configure(t, config={"CTrainer": {"optimizer": {"lr": 0.05}}})
    assert t.optimizer is marker and marker.kwargs == {"lr": 0.05}
    configure(config={"t.optimizer.lr": 0.07}, t=t)
    assert t.optimizer is marker and marker.kwargs == {"lr": 0.07}


# --------------------------------------------------------------------------- #
# The limit: an object the dumper cannot represent is skipped, loudly
# --------------------------------------------------------------------------- #


class _Opaque:
    def __init__(self) -> None:
        self.x = 1


def test_an_undumpable_object_is_warned_about_and_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    import confluid.configurator as configurator

    warnings: list = []
    monkeypatch.setattr(
        configurator,
        "logger",
        SimpleNamespace(
            warning=lambda m: warnings.append(m), debug=lambda m: None, trace=lambda m: None, info=lambda m: None
        ),
    )
    o = _Opaque()
    report = configure(o, config={"x": 2})
    assert o.x == 1  # nothing applied to a plain object
    assert any("cannot" in w or "skip" in w for w in warnings), warnings
    assert "x" in report.unused


def test_an_opaque_value_inside_a_configurable_is_left_alone() -> None:
    t = CTrainer(model=_Opaque())  # a non-configurable in a slot
    configure(t, config={"epochs": 4, "x": 99})
    assert t.epochs == 4 and t.model.x == 1


# --------------------------------------------------------------------------- #
# The report
# --------------------------------------------------------------------------- #


def test_the_report_names_applied_and_unused_keys() -> None:
    t = CTrainer(model=CModel())
    report = configure(t, config={"epochs": 5, "nothing_takes_this": 1})
    assert "epochs" in {a.key for a in report.applied}
    assert "nothing_takes_this" in report.unused


def test_a_validation_failure_is_recorded_under_warn_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    from confluid import set_policy

    monkeypatch.setenv("CONFLUID_VALIDATE_INIT", "warn")
    set_policy(init="warn")
    m = CModel()
    report = configure(m, config={"hidden": "not-an-int"})
    assert any(f.key == "hidden" and f.reason == "validation" for f in report.failed)


def test_the_second_walker_is_gone() -> None:
    import confluid.configurator as configurator

    for name in ("_walk", "_LiveSink", "_tune_deferred", "_assign"):
        assert not hasattr(configurator, name), name


# --- configure() has a scopes= channel (BUGS-2026-08-19 CD18 / SR4's channel) ----


def test_configure_resolves_scope_blocks_under_the_given_activation() -> None:
    @configurable
    class _ScModel:
        def __init__(self, lr: float = 0.0) -> None:
            self.lr = lr

    config = (
        "torch: !scope:framework=torch\n  lr: 0.3\n"
        "keras: !scope:framework=keras\n  lr: 0.5\n"
        "no_fw: !notscope:framework\n  lr: 0.9\n"
    )
    torch_model = _ScModel()
    report = configure(torch_model, config=config, scopes=["framework=torch"])
    assert torch_model.lr == 0.3
    assert not report.unused, "a consumed scope wrapper is structure, never an unused override"

    keras_model = _ScModel()
    configure(keras_model, config=config, scopes=["framework=keras"])
    assert keras_model.lr == 0.5

    default_model = _ScModel()
    report = configure(default_model, config=config)
    assert default_model.lr == 0.9, "the !notscope: default fires when nothing is activated"
    assert not report.unused


def test_configure_from_file_forwards_the_activation(tmp_path: Any) -> None:
    @configurable
    class _ScModel2:
        def __init__(self, lr: float = 0.0) -> None:
            self.lr = lr

    path = tmp_path / "exp.yaml"
    path.write_text("torch: !scope:framework=torch\n  lr: 0.3\nno_fw: !notscope:framework\n  lr: 0.9\n")
    model = _ScModel2()
    configure_from_file(model, path=str(path), scopes=["framework=torch"])
    assert model.lr == 0.3
