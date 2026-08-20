"""Last spec wins — the ONE precedence rule, across every spelling.

Confluid's stated rule (AGENTS.md → "Flat-View Ordered Matching"): values are
applied in document order and the last write wins. There is no
"explicit kwargs > broadcast" priority and no specificity tiers — a key aimed at
a node and a key broadcasting past it are ordered by POSITION alone.

These tests burn that in. Each case writes the same value two ways, in both
orderings, and asserts that whichever appears LATER in the file is the one that
takes effect. Nothing here is specific to a learning rate: the knob is a generic
`power` on a generic engine slot, because the rule is about ordering, not about
any particular parameter.

The matrix is (spelling × ordering). A spelling that honours the rule passes
both orderings; a spelling that ignores position fails exactly one of them,
which is what makes the pair — rather than either test alone — the real pin.
"""

from types import SimpleNamespace
from typing import Any

import pytest

from confluid import PartialClass, configurable, configure, flow, load
from confluid.registry import get_registry


@configurable
class OrderedEngine:
    """The deferred dependency. `power` is the contested knob."""

    def __init__(self, power: int = 1, fuel: str = "petrol") -> None:
        self.power = power
        self.fuel = fuel


@configurable
class OrderedCar:
    """Declares its engine slot IN CODE, the minimal-constructor pattern."""

    def __init__(self, name: str = "car") -> None:
        self.name = name
        self.engine: Any = PartialClass(OrderedEngine, power=1)


@pytest.fixture(autouse=True)
def _register() -> None:
    registry = get_registry()
    registry.register_class(OrderedEngine, name="OrderedEngine")
    registry.register_class(OrderedCar, name="OrderedCar")


# The slot-addressed spelling, in every form the grammar offers. Each sets `power`
# to 50 at the engine slot; `BARE` sets it to 99 tree-wide. All four must order
# identically — that they are four spellings of ONE operation is the point.
BARE = "power: 99"
SPELLINGS = {
    "marker": "vehicle: !class:OrderedCar()\n  engine: !lazy:OrderedEngine(power=50)",
    "mapping": "vehicle: !class:OrderedCar()\n  engine:\n    power: 50",
    "dotted": "vehicle: !class:OrderedCar()\nvehicle.engine.power: 50",
    "class_block": "vehicle: !class:OrderedCar()\nOrderedCar:\n  engine:\n    power: 50",
}


def _power(document: str) -> int:
    """Build the deferred engine the way a consumer's `run()` would, and read the knob."""
    return int(flow(load(document)["vehicle"].engine).power)


@pytest.mark.parametrize("spelling", sorted(SPELLINGS))
def test_a_bare_key_written_BEFORE_the_slot_loses_to_it(spelling: str) -> None:
    """The slot-addressed value comes later, so the slot wins.

    This is the half that protects an explicit per-node choice from a
    document-wide default declared above it.
    """
    document = f"{BARE}\n{SPELLINGS[spelling]}\n"

    assert _power(document) == 50


@pytest.mark.parametrize("spelling", sorted(SPELLINGS))
def test_a_bare_key_written_AFTER_the_slot_overrides_it(spelling: str) -> None:
    """The bare key comes later, so the bare key wins.

    This is the half that keeps a document-wide default useful as an override —
    the same reason a CLI `--power 99`, applied last of all, always takes effect.
    Without it, an addressed value would become unreachable rather than merely
    earlier, which is a specificity tier by another name.
    """
    document = f"{SPELLINGS[spelling]}\n{BARE}\n"

    assert _power(document) == 99


def test_the_rule_is_position_not_provenance() -> None:
    """The two orderings of ONE spelling must disagree — that IS the rule.

    Stated as a single assertion so a regression that makes a spelling
    order-INSENSITIVE (whichever constant it freezes on) fails here even if
    somebody "fixes" one of the two parametrized halves to match the frozen
    value.
    """
    marker = SPELLINGS["marker"]

    assert _power(f"{BARE}\n{marker}\n") != _power(f"{marker}\n{BARE}\n")


def test_untouched_marker_kwargs_survive_either_ordering() -> None:
    """Ordering decides the CONTESTED key only; it never discards the rest.

    `fuel` is set once, at the slot, and nothing competes with it — so it stands
    whichever side of it the bare `power` is written on. This is what separates
    "last write wins per key" from "the last source replaces the value wholesale".
    """
    spelling = "vehicle: !class:OrderedCar()\n  engine: !lazy:OrderedEngine(power=50,fuel=diesel)"

    for document in (f"{BARE}\n{spelling}\n", f"{spelling}\n{BARE}\n"):
        assert flow(load(document)["vehicle"].engine).fuel == "diesel"


@pytest.mark.parametrize("spelling", sorted(SPELLINGS))
def test_every_spelling_reaches_the_slot_with_nothing_competing(spelling: str) -> None:
    """DELIVERY, pinned apart from precedence: does the value arrive at all?

    Written with NOTHING competing, so a failure here can only mean the value never
    arrived — which is the failure the ordering tests cannot distinguish from losing
    a contest. It is a real distinction: `class_block` used to leave the constructor
    default (`power=1`) because `_consume_block` admitted a dict only for a
    dict-TYPED param, so a mapping aimed at a deferred body slot was hoisted as
    routing for the children and silently dropped, while the inline spelling of the
    same thing worked. Two paths, one grammar, two answers.
    """
    assert _power(f"{SPELLINGS[spelling]}\n") == 50


def test_configure_delivers_a_class_block_to_a_deferred_slot() -> None:
    """The post-construction path must reach a deferred body slot too.

    `configure()` used to recurse INTO the marker — a `Fluid` reports
    `__confluid_configurable__`, so it looked like a live configurable child — and
    set attributes on the marker object, where nothing reads them. The value
    vanished with no diagnostic, exactly as it did on the load path.
    """
    car = OrderedCar()

    configure(car, config="OrderedCar:\n  engine:\n    power: 50\n")

    assert flow(car.engine).power == 50


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        ("power: 99\nOrderedCar:\n  engine:\n    power: 50\n", 50),
        ("OrderedCar:\n  engine:\n    power: 50\npower: 99\n", 99),
    ],
    ids=["bare_before_the_block_loses", "bare_after_the_block_wins"],
)
def test_configure_follows_document_order_like_the_load_path(document: str, expected: int) -> None:
    """The same contest as the matrix above, over a live object instead of markers.

    This is the parity `configurator.py`'s docstring claims ("whichever assignment
    comes LAST in document order wins — no priority tiers"), and it did not hold:
    the bare key won either way. Two separate causes, both fixed —

    * `_assign` flowed the tuned marker, because `Partial` subclasses `Class`, so a
      deferred slot was built EAGERLY on this path alone;
    * `_walk` then flowed the slot again to walk into it and configured the
      resulting object, which is never written back — so every bare key applied to
      a deferred slot was discarded. It is now tuned into the marker's kwargs, which
      is where the load path puts it.
    """
    car = OrderedCar()

    configure(car, config=document)

    assert flow(car.engine).power == expected


def test_a_second_configure_is_not_bound_by_the_first_ones_verdict() -> None:
    """An ordering verdict must not outlive the document that produced it.

    Layering a base config then an override is ordinary usage. The first call decides
    `power` is beaten by a block; if that verdict is stamped on the marker rather than
    scoped to the call, the second call's bare `power` arrives already marked "lost"
    against a document it never saw, and silently keeps the first value.
    """
    car = OrderedCar()

    configure(car, config="power: 99\nOrderedCar:\n  engine:\n    power: 50\n")
    assert flow(car.engine).power == 50  # the block out-positioned the bare key

    configure(car, config="power: 77\n")  # nothing competes now

    assert flow(car.engine).power == 77


def test_configure_does_not_build_a_deferred_slot() -> None:
    """`configure()` must leave a `!lazy:` slot deferred, exactly as loading does.

    The whole point of the slot is that the owner flows it later WITH the runtime
    argument (`params=model.parameters()`). Building it here yields an object
    constructed without that argument — the failure lands far away, at use.
    """
    car = OrderedCar()

    configure(car, config="OrderedCar:\n  engine:\n    power: 50\n")

    assert isinstance(car.engine, PartialClass(OrderedEngine).__class__)
    assert car.engine.kwargs["power"] == 50


def test_an_overridden_value_is_reported_at_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    """ "My knob did not take" must be one grep, not a bisect.

    Every silent misconfiguration in this area looked the same from outside: the run
    used a value the author did not write at the node they wrote it on, with nothing
    in the log. The merge's single write path reports a value being REPLACED — at
    DEBUG, because overriding is normal operation and the point of bare keys.
    """
    import confluid.broadcast as broadcast_module

    seen: list = []
    monkeypatch.setattr(
        broadcast_module,
        "logger",
        SimpleNamespace(debug=seen.append, trace=lambda m: None, warning=lambda m: None),
    )

    _power(f"{SPELLINGS['marker']}\n{BARE}\n")  # the bare key is later, so it replaces

    assert any("'power'" in m and "50" in m and "99" in m for m in seen), seen


def test_an_uncontested_value_reports_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The diagnostic must stay quiet on the ordinary path, or it is noise.

    Negative assertions check the collected list, never `caplog` — loggair does not
    propagate into stdlib logging, so an empty `caplog` is a false green.
    """
    import confluid.broadcast as broadcast_module

    seen: list = []
    monkeypatch.setattr(
        broadcast_module,
        "logger",
        SimpleNamespace(debug=seen.append, trace=lambda m: None, warning=lambda m: None),
    )

    _power(f"{SPELLINGS['marker']}\n")  # nothing competes with the slot value

    assert not [m for m in seen if "override" in m], seen


def test_the_rule_holds_for_a_non_numeric_key() -> None:
    """Nothing about this is arithmetic — a string knob orders identically.

    Guards against a fix that special-cases numbers (a "take the smaller/later
    number" heuristic would pass every test above and fail this one).
    """
    slot = "vehicle: !class:OrderedCar()\n  engine: !lazy:OrderedEngine(fuel=diesel)"
    bare = "fuel: kerosene"

    assert flow(load(f"{bare}\n{slot}\n")["vehicle"].engine).fuel == "diesel"
    assert flow(load(f"{slot}\n{bare}\n")["vehicle"].engine).fuel == "kerosene"


# A single-segment address whose HEAD exists in no other key — the one shape
# where the dotted spelling created its top-level block by APPENDING it
# (``merger.expand_dotted_keys``), so the dotted form could never lose to
# anything written after it while the block form of the same address could.
FRESH_HEAD_SPELLINGS = {
    "dotted": "OrderedEngine.power: 50",
    "class_block": "OrderedEngine:\n  power: 50",
}
FRESH_HEAD_MARKER = "e: !class:OrderedEngine(power=10)"


@pytest.mark.parametrize("spelling", sorted(FRESH_HEAD_SPELLINGS))
def test_a_fresh_head_class_address_written_BEFORE_the_marker_loses(spelling: str) -> None:
    """The marker's own kwargs sit later in the document, so the marker wins.

    The dotted half of this pair is the regression pin: ``expand_dotted_keys``
    used to append a fresh head at the END of the document, so ``Cls.attr``
    written FIRST still beat everything — while ``Cls: {attr}`` in the same
    position lost, splitting one documented rule into two answers.
    """
    document = f"{FRESH_HEAD_SPELLINGS[spelling]}\n{FRESH_HEAD_MARKER}\n"

    assert int(load(document)["e"].power) == 10


@pytest.mark.parametrize("spelling", sorted(FRESH_HEAD_SPELLINGS))
def test_a_fresh_head_class_address_written_AFTER_the_marker_wins(spelling: str) -> None:
    """Written later, either spelling overrides the marker's own kwargs."""
    document = f"{FRESH_HEAD_MARKER}\n{FRESH_HEAD_SPELLINGS[spelling]}\n"

    assert int(load(document)["e"].power) == 50


# --------------------------------------------------------------------------- #
# D7 — a '**' RIDER is a bare delivery and orders by ITS document position
# (adjudicated 2026-08-10; docs/architecture.md record 8). The rider cells were
# position-INSENSITIVE on both paths with OPPOSITE winners: the load path's
# late-keys set could not see rider contents at all (the mapping always won),
# the configure scan's beaten set skipped every dict entry including the rider
# (the rider always won). Both verdicts now read the ONE candidate set,
# ``broadcast._cascade_scalar_positions``.
# --------------------------------------------------------------------------- #

RIDER = "'**.power': 99"


@pytest.mark.parametrize("spelling", sorted(SPELLINGS))
def test_a_rider_written_BEFORE_the_slot_loses_to_it(spelling: str) -> None:
    """The slot-addressed value comes later, so the slot wins — rider edition."""
    assert _power(f"{RIDER}\n{SPELLINGS[spelling]}\n") == 50


@pytest.mark.parametrize("spelling", sorted(SPELLINGS))
def test_a_rider_written_AFTER_the_slot_overrides_it(spelling: str) -> None:
    """The rider comes later, so the rider wins — a sweep's '**' override works."""
    assert _power(f"{SPELLINGS[spelling]}\n{RIDER}\n") == 99


def test_the_rider_rule_is_position_not_provenance() -> None:
    """The two orderings of the rider × slot-mapping spelling must DISAGREE.

    The single-assertion form of the matrix above, for the one spelling pair
    that was position-insensitive in BOTH directions: freeze on either constant
    and this fails, even if one parametrized half is "fixed" to match.
    """
    mapping = SPELLINGS["mapping"]

    assert _power(f"{RIDER}\n{mapping}\n") != _power(f"{mapping}\n{RIDER}\n")


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        ("'**.power': 99\nOrderedCar:\n  engine:\n    power: 50\n", 50),
        ("OrderedCar:\n  engine:\n    power: 50\n'**.power': 99\n", 99),
    ],
    ids=["rider_before_the_block_loses", "rider_after_the_block_wins"],
)
def test_configure_orders_a_rider_against_a_block_like_the_load_path(document: str, expected: int) -> None:
    """The same rider contest over a live object — both paths, one winner per ordering.

    Before the D7 adjudication the configure path handed the rider the win in
    both orderings (its beaten set skipped dict-valued view entries, so rider
    contents were never marked beaten), while the load path handed the mapping
    the win in both — the same document, opposite winners, nothing logged.
    """
    car = OrderedCar()

    configure(car, config=document)

    assert flow(car.engine).power == expected


# --------------------------------------------------------------------------- #
# A grouping dict is transparent for POSITION too (BUGS-2026-08-19 BC2 / BC9).
#
# The docs say a plain grouping dict "consumes no nesting level"; its entries
# are therefore spliced into the parent view AT THE GROUP KEY'S SLOT — never
# appended after every later line. Appending gave a marker inside a group an
# unbeatable position (its own kwargs sat after a later bare key, class block
# and rider alike), and a key restated inside a group kept the position of an
# EARLIER root key of the same name, so adding an earlier LOSING line flipped
# which later line won (E4, re-opened as BC9).
# --------------------------------------------------------------------------- #

_GROUPED = "group:\n  node: !class:OrderedEngine(power=1)\n"


@pytest.mark.parametrize(
    "label, document, expected",
    [
        ("later bare key wins", _GROUPED + "power: 99\n", 99),
        ("later class block wins", _GROUPED + "OrderedEngine: {power: 99}\n", 99),
        ("later rider wins", _GROUPED + "'**': {power: 99}\n", 99),
        ("earlier bare key loses to the own kwarg (con)", "power: 99\n" + _GROUPED, 1),
    ],
)
def test_a_marker_inside_a_grouping_dict_competes_at_the_GROUPS_position(
    label: str, document: str, expected: int
) -> None:
    assert load(document)["group"]["node"].power == expected, label


def test_a_marker_in_a_dict_valued_kwarg_competes_at_its_slot_too() -> None:
    document = (
        "car: !class:OrderedCar\n"
        "  engine: !lazy:OrderedEngine(power=1)\n"
        "table:\n"
        "  spare: !class:OrderedEngine(power=1)\n"
        "power: 99\n"
    )
    assert load(document)["table"]["spare"].power == 99


def test_a_key_restated_inside_a_group_lands_at_the_GROUPS_later_position() -> None:
    """BC9 — an earlier, LOSING root key must not change which later line wins."""
    group = "group:\n  node: !class:OrderedEngine(power=1)\n  power: 50\n"
    assert load(group)["group"]["node"].power == 50
    assert load("power: 99\n" + group)["group"]["node"].power == 50, "the earlier root key flipped the contest"
    assert load(group + "power: 99\n")["group"]["node"].power == 99, "a LATER root key still wins (con)"


# --------------------------------------------------------------------------- #
# A dotted key landing DIRECTLY on a marker's kwarg competes at the DOTTED
# LINE's position — per key (BUGS-2026-08-19 BC4, user ruling 2026-08-19,
# "option B").
#
# Pass 6 folds `t.lr: 9.0` into the marker's kwargs, which used to move the
# value to the MARKER's line: written as the last line of the file it lost to
# a bare `lr: 5.0` between (`explain` even said "own 9.0 — beaten, earlier").
# The fold now stamps the marker (kwarg -> the sibling keys the dotted line
# out-positioned) and the scanner's sink consults the stamp: a cascade
# delivery the dotted line out-positioned is skipped; one written after it
# still wins. The marker's OTHER kwargs keep the marker's position — the
# block spelling and the dotted spelling of one override agree (the momentum
# case), which is what option B was chosen for over moving the whole marker.
# --------------------------------------------------------------------------- #

_BC4_NODE = "t: !class:OrderedEngine\n  power: 1\n  fuel: diesel\n"


@pytest.mark.parametrize(
    "label, document, expected_power",
    [
        ("dotted LAST beats a bare between", _BC4_NODE + "power: 99\nt.power: 50\n", 50),
        ("bare LAST still wins (con)", _BC4_NODE + "t.power: 50\npower: 99\n", 99),
        ("dotted LAST beats a class block between", _BC4_NODE + "OrderedEngine: {power: 99}\nt.power: 50\n", 50),
        ("dotted LAST beats a rider between", _BC4_NODE + "'**': {power: 99}\nt.power: 50\n", 50),
        ("bare BEFORE the marker never mattered (con)", "power: 99\n" + _BC4_NODE + "t.power: 50\n", 50),
    ],
)
def test_a_dotted_marker_kwarg_competes_at_the_dotted_lines_position(
    label: str, document: str, expected_power: int
) -> None:
    assert load(document)["t"].power == expected_power, label


def test_the_dotted_line_moves_ONE_kwarg_not_the_whole_marker() -> None:
    """The momentum case — the reason option B was chosen: the marker's other
    kwargs keep the marker's position, so the dotted spelling agrees with the
    block spelling (`t: {power: 50}` via instance name) on every key."""
    document = _BC4_NODE + "fuel: electric\nt.power: 50\n"
    engine = load(document)["t"]
    assert engine.power == 50
    assert engine.fuel == "electric", "the untouched kwarg must NOT move to the dotted line"


def test_a_dotted_kwarg_through_TWO_markers_competes_at_its_line_too() -> None:
    document = (
        "car: !class:OrderedCar\n" "  spare: !class:OrderedEngine {power: 1}\n" "power: 99\n" "car.spare.power: 50\n"
    )
    assert load(document)["car"].spare.power == 50


def test_the_dotted_position_survives_the_document_stage() -> None:
    document = _BC4_NODE + "power: 99\nt.power: 50\n"
    assert load(load(document, until="document"))["t"].power == 50
