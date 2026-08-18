"""``configure()`` — a marker at a slot holding a LIVE child tunes it; every applied key is reported.

User ruling 2026-08-18. Measured before the change, on the README quick-start classes: a
load-shaped document (``trainer: !class:Trainer {model: !class:Model {layers: 10}}``) applied by
NAME landed its values but reported ``0 applied, 0 failed, 2 unused``, and the config's
``model: !class:Model`` REPLACED the live model with a new one instead of tuning it. Both are
pinned here as they should be, with the con cases that must not change.
"""

from typing import Any, Optional

from confluid import PartialClass, Target, configurable, configure, load


@configurable
class LCModel:
    def __init__(self, layers: int = 3, dropout: float = 0.1) -> None:
        self.layers = layers
        self.dropout = dropout


@configurable
class LCOther:
    def __init__(self, layers: int = 1) -> None:
        self.layers = layers


@configurable
class LCBox:
    def __init__(self, child: Any = None) -> None:  # an UNTYPED slot — any class may sit here
        self.child = child


@configurable
class LCTrainer:
    def __init__(self, model: Optional[LCModel] = None, lr: float = 0.001) -> None:
        self.model = model
        self.lr = lr
        self.optimizer: Any = PartialClass(LCModel, layers=2)  # a deferred body slot


DOC = """\
trainer: !class:LCTrainer
  lr: 0.0001
  model: !class:LCModel
    layers: 10
"""


def _applied(report: Any) -> set:  # type: ignore[type-arg]
    return {(a.target, a.key) for a in report.applied}


# --------------------------------------------------------------------------- finding 2: tune, not rebuild


def test_a_marker_at_a_live_child_slot_of_the_same_class_TUNES_the_child() -> None:
    """PRO (class-block form): ``model: !class:LCModel {layers: 10}`` configures the existing model."""
    model = LCModel()
    trainer = LCTrainer(model=model)
    report = configure(trainer, config="LCTrainer:\n  lr: 0.0001\n  model: !class:LCModel\n    layers: 10\n")
    assert trainer.model is model  # the SAME object …
    assert (model.layers, model.dropout) == (10, 0.1)  # … with the marker's kwargs applied
    assert trainer.lr == 0.0001
    assert report.failed == []
    assert {("LCTrainer", "lr"), ("LCTrainer", "model")} <= _applied(report)  # the block's keys, at the receiver


def test_a_marker_of_a_DIFFERENT_class_at_a_live_child_slot_replaces_it() -> None:
    """CON: the config asked for another object — build it, do not tune a class it is not."""
    model = LCModel()
    box = LCBox(child=model)
    configure(box, config="LCBox:\n  child: !class:LCOther\n    layers: 7\n")
    assert isinstance(box.child, LCOther) and box.child.layers == 7
    assert box.child is not model


def test_a_marker_of_a_different_class_at_a_TYPED_slot_is_refused_by_validation() -> None:
    """CON: on a slot typed ``Optional[LCModel]`` the replacement is not an LCModel — validation says so."""
    import pytest
    from pydantic import ValidationError

    trainer = LCTrainer(model=LCModel())
    with pytest.raises(ValidationError):
        configure(trainer, config="LCTrainer:\n  model: !class:LCOther\n    layers: 7\n")


def test_a_mapping_at_a_live_child_slot_still_tunes() -> None:
    """CON (unchanged): the mapping spelling tunes in place, as it always did."""
    model = LCModel()
    trainer = LCTrainer(model=model)
    report = configure(trainer, config="LCTrainer:\n  model:\n    layers: 10\n")
    assert trainer.model is model and model.layers == 10
    assert ("LCTrainer", "model") in _applied(report)  # recorded at the receiver the block addressed


def test_a_marker_at_a_deferred_slot_still_tunes_the_marker() -> None:
    """CON (unchanged): a marker landing on a slot that HOLDS a marker tunes that marker in place."""
    trainer = LCTrainer()
    marker = trainer.optimizer
    configure(trainer, config="LCTrainer:\n  optimizer: !class:LCModel\n    layers: 9\n")
    assert trainer.optimizer is marker and isinstance(marker, PartialClass)
    assert marker.kwargs["layers"] == 9


def test_a_marker_at_an_EMPTY_slot_is_built() -> None:
    """CON (unchanged): nothing to tune — the config introduced the object."""
    trainer = LCTrainer()  # model is None
    configure(trainer, config="LCTrainer:\n  model: !class:LCModel\n    layers: 4\n")
    assert isinstance(trainer.model, LCModel) and trainer.model.layers == 4


# --------------------------------------------------------------------------- finding 1: the report


def test_configuring_a_NAMED_object_from_a_load_shaped_document_reports_what_it_applied() -> None:
    """PRO: the same document that ``load()`` builds from, applied by name — values land AND
    the report says so; the naming key is used, not "unused"."""
    model = LCModel()
    trainer = LCTrainer(model=model)
    report = configure(trainer=trainer, config=load(DOC, until="raw"))
    assert (trainer.lr, model.layers) == (0.0001, 10)
    assert trainer.model is model  # tuned, not rebuilt
    # the scanner's vocabulary: the keys the naming overlay handed the object, at "Class 'name'"
    assert _applied(report) == {("LCTrainer 'trainer'", "lr"), ("LCTrainer 'trainer'", "model")}
    assert {a.origin for a in report.applied} == {"addressed"}
    assert report.failed == []
    assert report.unused == [] and report.summary() == "2 applied, 0 failed, 0 unused"


def test_a_load_shaped_document_with_an_unrelated_top_level_key_reports_it_unused() -> None:
    """CON: a key naming nothing is still unused — the fix does not mark everything used."""
    trainer = LCTrainer(model=LCModel())
    report = configure(trainer=trainer, config=load(DOC + "defaults:\n  n_layers: 10\n", until="raw"))
    assert report.unused == ["defaults"]


def test_a_bare_key_delivered_by_pass_7_is_recorded_ONCE() -> None:
    """CON: the fallback recording in ``_set`` never duplicates a record pass 7 already wrote."""
    trainer = LCTrainer(model=LCModel())
    report = configure(trainer, config={"lr": 0.5})
    assert [a.key for a in report.applied].count("lr") == 1
    assert trainer.lr == 0.5


def test_a_dotted_key_naming_an_object_is_used_and_recorded() -> None:
    """``trainer.model.layers: 8`` names the object by attribute path — used, and one record at it."""
    model = LCModel()
    trainer = LCTrainer(model=model)
    report = configure(trainer=trainer, config={"trainer.model.layers": 8, "trainer.lr": 0.3})
    assert (model.layers, trainer.lr) == (8, 0.3) and trainer.model is model
    assert _applied(report) == {("LCTrainer 'trainer'", "model"), ("LCTrainer 'trainer'", "lr")}
    assert report.unused == []


def test_a_key_the_named_object_cannot_take_is_not_reported_applied() -> None:
    """CON: only keys the object accepts are records — a typo is not "applied"."""
    trainer = LCTrainer(model=LCModel())
    report = configure(trainer=trainer, config={"trainer": {"lr": 0.3, "no_such_knob": 1}})
    assert _applied(report) == {("LCTrainer 'trainer'", "lr")}


def test_configure_by_name_with_a_target_marker_built_in_code() -> None:
    """The same, from an in-memory document (a ``Target`` the way a DI framework builds one)."""
    model = LCModel()
    trainer = LCTrainer(model=model)
    report = configure(trainer=trainer, config={"trainer": Target(LCTrainer, lr=0.2, model=Target(LCModel, layers=6))})
    assert (trainer.lr, model.layers) == (0.2, 6) and trainer.model is model
    assert report.summary() == "2 applied, 0 failed, 0 unused"
