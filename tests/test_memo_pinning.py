"""Every id()-keyed store pins the object whose address keys it (BUGS-2026-08-13: X1, X3, E1, E2, I7).

An ``id()`` is only unique while the object is ALIVE. Four stores recorded an id
without keeping the object alive; CPython freed it, recycled the address, and the
store answered for the OLD object when the NEW one arrived:

* ``configure()``'s visited set — whole subtrees silently skipped (450/512 objects
  unconfigured under DEFAULT gc, measured);
* the instance memo written by public ``flow()`` — an object handed ANOTHER
  object's dependency (``maker8`` got ``tag7``'s widget, measured);
* the instance memo written by the slot-tune path — tuned slots silently SHARING
  instances (5 distinct objects for 12 slots, measured), plus the ordering stamp
  landing on the BUILT instance (an ``AttributeError`` for ``__slots__`` targets);
* the slots cache for unhashable callables — stale slots served at a recycled id.

The rule was already written ("every marker a memo keys on MUST be pinned") —
these pins make it bind every id-keyed store.
"""

import gc
from typing import Any, List, Optional

import pytest

from confluid import Target, configurable, configure, flow, get_registry, load


@pytest.fixture(autouse=True)
def setup_registry() -> None:
    get_registry().clear()


@configurable
class Widget:
    def __init__(self, lr: float = 0.001, label: str = "?") -> None:
        self.lr = lr
        self.label = label


@configurable
class Stage:
    def __init__(self, dep: Optional[Widget] = None) -> None:
        self.dep = dep


@configurable
class Pipeline:
    def __init__(self, stages: Optional[List[Any]] = None) -> None:
        self.stages = stages if stages is not None else []


@configurable
class Engine:
    def __init__(self, power: int = -1) -> None:
        self.power = power


class SlottedEngine:
    __slots__ = ("power",)

    def __init__(self, power: int = 1) -> None:
        self.power = power


N_SLOTS = 12


@configurable
class Owner:
    # LITERAL assignments on purpose: the AST scan must see these names so they are
    # in the accept-list — a setattr-loop hides them and the tunes get ROUTED instead
    # of applied, which silently voids this fixture's premise.
    def __init__(self) -> None:
        self.slot_0 = Target(Engine, power=-1)
        self.slot_1 = Target(Engine, power=-1)
        self.slot_2 = Target(Engine, power=-1)
        self.slot_3 = Target(Engine, power=-1)
        self.slot_4 = Target(Engine, power=-1)
        self.slot_5 = Target(Engine, power=-1)
        self.slot_6 = Target(Engine, power=-1)
        self.slot_7 = Target(Engine, power=-1)
        self.slot_8 = Target(Engine, power=-1)
        self.slot_9 = Target(Engine, power=-1)
        self.slot_10 = Target(Engine, power=-1)
        self.slot_11 = Target(Engine, power=-1)


@configurable
class SlottedOwner:
    def __init__(self) -> None:
        self.engine = Target(SlottedEngine, power=7)


@configurable
class Maker:
    def __init__(self, tag: str = "?") -> None:
        self.tag = tag
        self.made = flow(Target(Widget, label=tag))  # ctor-local recipe — dies after this line


@configurable
class Optimizer:
    def __init__(self, lr: float = 0.0, name: str = "") -> None:
        self.lr = lr
        self.name = name


@configurable
class Trainer:
    def __init__(self, name: str = "", optimizer: Optional[Optimizer] = None) -> None:
        self.name = name
        self.optimizer = optimizer


def _register() -> None:
    from confluid import register

    for cls in (Widget, Stage, Pipeline, Engine, Owner, SlottedOwner, Maker, Optimizer, Trainer):
        register(cls)


# --------------------------------------------------------------------------- #
# X1 — configure()'s visited set
# --------------------------------------------------------------------------- #


def test_configure_reaches_every_object_even_when_gc_recycles_walk_temporaries() -> None:
    """A bare key must land on EVERY reachable Widget. The walk used to record the
    id of each flow temporary without keeping it alive; under an aggressive (or,
    at scale, the DEFAULT) gc the recycled addresses read as 'already visited' and
    whole subtrees were silently skipped — 450 of 512 objects, measured."""
    _register()
    thresholds = gc.get_threshold()
    gc.set_threshold(50)  # emulate a big pass; the 512-object variant fails on DEFAULT thresholds
    try:
        for _ in range(5):
            widgets = [Widget() for _ in range(64)]
            # configure() reads the object's DOCUMENT (record 19, phase 4): the markers must sit
            # in a declared slot — a body-slot list here — for the widgets inside them to be
            # reachable; a dynamically set attribute is not part of any document.
            pipeline = Pipeline([Target(Stage, dep=w) for w in widgets])
            configure(pipeline, config="lr: 5.0\n")
            missed = [i for i, w in enumerate(widgets) if w.lr != 5.0]
            assert missed == [], f"configure() silently skipped widgets {missed}"
    finally:
        gc.set_threshold(*thresholds)


# --------------------------------------------------------------------------- #
# X3 — the instance memo written by public flow()
# --------------------------------------------------------------------------- #


def test_a_ctor_local_flow_never_hands_an_object_anothers_dependency() -> None:
    """Every Maker's widget must carry the Maker's own tag. The public flow()
    memo write was unpinned, so a dead ctor-local recipe's recycled address
    served the PREVIOUS maker's widget (measured: maker8 got 'tag7')."""
    _register()
    for attempt in range(1, 21):
        built = load({f"maker{i}": Target(Maker, tag=f"tag{i}") for i in range(100)})
        wrong = [
            (i, built[f"maker{i}"].tag, built[f"maker{i}"].made.label)
            for i in range(100)
            if built[f"maker{i}"].made.label != built[f"maker{i}"].tag
        ]
        assert wrong == [], f"attempt {attempt}: cross-wired dependencies {wrong[:3]}"


# --------------------------------------------------------------------------- #
# E1 — the instance memo written by the slot-tune path
# --------------------------------------------------------------------------- #


def test_tuned_slots_get_distinct_instances_with_their_own_values() -> None:
    """Twelve slots tuned with twelve different powers must yield twelve DISTINCT
    engines carrying exactly those powers (measured before: 5 distinct objects,
    values scrambled — the tuned marker keyed the memo unpinned)."""
    _register()
    doc = (
        "owner:\n  _target_: Owner\n"
        + "".join(f"  slot_{i}: {{power: {100 + i}}}\n" for i in range(N_SLOTS))
        + "noise: 5\n"  # a later bare key routes every tune through the memo path
    )
    for trial in range(3):
        owner = load(doc)["owner"]
        engines = [getattr(owner, f"slot_{i}") for i in range(N_SLOTS)]
        powers = [e.power for e in engines]
        assert powers == [100 + i for i in range(N_SLOTS)], f"trial {trial}: {powers}"
        assert len({id(e) for e in engines}) == N_SLOTS, f"trial {trial}: slots share instances"


# --------------------------------------------------------------------------- #
# BC1 (BUGS-2026-08-19) — the PASS-7 flow memo written for a tune_marker copy
# --------------------------------------------------------------------------- #

N_TRAINERS = 300


def test_a_class_block_tune_never_hands_one_node_anothers_settled_child() -> None:
    """``Trainer: {optimizer: {lr: 9.0}}`` over 300 trainers: pass 7 tunes each
    trainer's ``optimizer`` marker through ``tune_marker`` (a short-lived COPY),
    and ``flow_memo`` keyed on that copy's id without pinning it. The copy died
    with its ``merged_kwargs``, CPython reused the address, and the next marker
    to land there read the memo as a HIT — measured: 22 of 300 trainers holding
    ANOTHER trainer's optimizer (278 distinct objects for 300 slots). Every
    trainer must get its own optimizer carrying its own name."""
    _register()
    doc = (
        "".join(
            f"t{i}:\n  _target_: Trainer\n  name: t{i}\n  optimizer: {{_target_: Optimizer, name: o{i}, lr: 1.0}}\n"
            for i in range(N_TRAINERS)
        )
        + "Trainer: {optimizer: {lr: 9.0}}\n"
    )
    for trial in range(3):
        built = load(doc)
        optimizers = [built[f"t{i}"].optimizer for i in range(N_TRAINERS)]
        names = [o.name for o in optimizers]
        assert names == [f"o{i}" for i in range(N_TRAINERS)], f"trial {trial}: a trainer holds another's optimizer"
        assert len({id(o) for o in optimizers}) == N_TRAINERS, f"trial {trial}: optimizers are shared"
        assert all(o.lr == 9.0 for o in optimizers), f"trial {trial}: the block did not land"


# --------------------------------------------------------------------------- #
# E2 — the ordering stamp must land on markers only
# --------------------------------------------------------------------------- #


def test_tuning_a_slotted_target_neither_crashes_nor_pollutes() -> None:
    """The tune path stamped ``_order_resolved`` onto the BUILT instance — an
    ``AttributeError`` for a ``__slots__`` target, a stray attribute otherwise."""
    _register()
    owner = load("owner:\n  _target_: SlottedOwner\n  engine: {power: 20}\nnoise: 5\n")["owner"]
    assert type(owner.engine) is SlottedEngine  # did not crash
    assert owner.engine.power == 20

    plain = load("owner:\n  _target_: Owner\n  slot_0: {power: 42}\nnoise: 5\n")["owner"]
    assert "_order_resolved" not in vars(plain.slot_0)  # no pollution on the built object


# --------------------------------------------------------------------------- #
# I7 — the slots cache pins an id-keyed unhashable target
# --------------------------------------------------------------------------- #


def test_the_slots_cache_entry_pins_the_unhashable_target_it_keys_on() -> None:
    """An unhashable callable keys the cache by bare id(); the entry must hold the
    target itself so a freed callable's recycled address can never serve stale
    slots (the same rule every memo follows)."""
    from confluid import introspect

    class Functor:
        def __eq__(self, other: Any) -> bool:  # __eq__ without __hash__ -> unhashable
            return self is other

        __hash__ = None  # type: ignore[assignment]

        def __call__(self, x: int = 1) -> int:
            return x

    u = Functor()
    introspect.slots(u)
    pinned = [entry for entry in introspect._slots_cache.values() if isinstance(entry, tuple) and entry[0] is u]
    assert pinned, "the cache entry does not pin the unhashable target it is keyed on"
