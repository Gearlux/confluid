"""The dict-at-slot dispatch — ONE rule on both paths (BUGS-2026-08-13, C1/C1b).

A YAML mapping addressed at a slot means, by what the slot currently HOLDS:

* a ``Target`` marker         -> TUNE it (merge into its kwargs; it builds with them);
* a live ``@configurable``    -> walk INTO it and set its fields (the configure()
  object                         path always did this; the load path was missing the arm
                                 and assigned the raw dict — destroying the child);
* a plain dict / list / None  -> assign the mapping (a dict-typed slot receives its value);
  / scalar
* any OTHER live object       -> a located ``ConfigurationError`` (user decision 2026-08-13:
  (not @configurable)            raise, never silently replace an object with a dict).

The classes live in a real module file where noted — the AST body-slot scan needs source.
"""

import inspect
from typing import Any, Dict

import pytest

from confluid import (
    ConfigurationError,
    Partial,
    PartialClass,
    configurable,
    configure,
    flow,
    get_registry,
    load,
    register,
)
from confluid.fluid import Target
from confluid.validation import ValidationMode, reset_policy, set_policy


@pytest.fixture(autouse=True)
def setup_registry() -> None:
    get_registry().clear()


@configurable
class Engine:
    def __init__(self, power: int = -1) -> None:
        self.power = power


class PlainEngine:  # deliberately NOT @configurable
    def __init__(self, power: int = -1) -> None:
        self.power = power


@configurable
class Host:
    def __init__(self) -> None:
        self.engine = Engine(power=-1)  # a LIVE child, no marker


@configurable
class PlainHost:
    def __init__(self) -> None:
        self.engine = PlainEngine(power=-1)  # a live NON-configurable child


@configurable
class Encoder:
    def __init__(self, name: str = "", width: int = 0) -> None:
        self.name = name
        self.width = width


@configurable
class Trainer:
    def __init__(self, enc: Any = None) -> None:
        self.enc = enc


@configurable
class WithDict:
    def __init__(self, extras: Dict[str, int] = {}) -> None:
        self.extras = extras


# --------------------------------------------------------------------------- #
# The two fixes
# --------------------------------------------------------------------------- #


def test_a_mapping_at_a_LIVE_configurable_child_walks_into_it_on_the_load_path() -> None:
    """C1b: ``self.engine = Engine(-1)`` + ``engine: {power: 50}`` -> an Engine with
    power 50 — the load path gains the recurse arm configure() always had. The
    child OBJECT survives (same instance, field updated)."""
    register_all()
    host = load("h: {_target_: Host, engine: {power: 50}}")["h"]
    assert type(host.engine) is Engine
    assert host.engine.power == 50


def test_the_load_and_configure_paths_agree_on_the_live_child() -> None:
    register_all()
    via_load = load("h: {_target_: Host, engine: {power: 50}}")["h"]
    via_conf = Host()
    configure(via_conf, config="Host:\n  engine: {power: 50}\n")
    assert type(via_load.engine) is type(via_conf.engine) is Engine
    assert via_load.engine.power == via_conf.engine.power == 50


def test_a_class_block_mapping_TUNES_a_nested_marker_at_a_ctor_param_slot() -> None:
    """C1: the block override merges into the nested Encoder recipe — the recipe's
    other kwargs survive and the Encoder is BUILT with the merged set."""
    register_all()
    doc = "trainer: {_target_: Trainer, enc: {_target_: Encoder, name: enc}}\n" "Trainer:\n" "  enc: {width: 3}\n"
    trainer = load(doc)["trainer"]
    assert type(trainer.enc) is Encoder
    assert trainer.enc.width == 3
    assert trainer.enc.name == "enc"  # the recipe's own kwarg survived the tune


def test_a_nested_mapping_recurses_the_same_rule_one_level_down() -> None:
    """A mapping inside the mapping follows the same dispatch: Host.engine is a live
    configurable child, whose ``power`` is set; nothing is replaced anywhere."""
    register_all()

    @configurable
    class Outer:
        def __init__(self) -> None:
            self.host = Host()

    outer = load("o: {_target_: Outer, host: {engine: {power: 77}}}")["o"]
    assert type(outer.host) is Host
    assert type(outer.host.engine) is Engine
    assert outer.host.engine.power == 77


# --------------------------------------------------------------------------- #
# The raise (user decision 2026-08-13: never silently replace a live object)
# --------------------------------------------------------------------------- #


def test_a_mapping_at_a_live_NON_configurable_child_raises_located_on_the_load_path() -> None:
    register_all()
    with pytest.raises(ConfigurationError) as excinfo:
        load("h: {_target_: PlainHost, engine: {power: 50}}")
    message = str(excinfo.value)
    assert "engine" in message and "PlainEngine" in message
    assert ":" in message  # carries a location


def test_a_mapping_at_a_live_NON_configurable_child_raises_under_configure_too() -> None:
    register_all()
    host = PlainHost()
    with pytest.raises(ConfigurationError) as excinfo:
        configure(host, config="PlainHost:\n  engine: {power: 50}\n")
    assert "engine" in str(excinfo.value)
    assert host.engine.power == -1  # untouched — refused, not half-applied


# --------------------------------------------------------------------------- #
# Guards — behaviour that must NOT change
# --------------------------------------------------------------------------- #


def test_a_dict_TYPED_slot_still_receives_the_mapping_as_its_value() -> None:
    register_all()
    w = load("w: {_target_: WithDict}\nextras: {a: 1}")["w"]
    assert w.extras == {"a": 1}


def test_an_unknown_own_kwarg_MAPPING_stays_routing_metadata() -> None:
    """An own-kwarg mapping at a key the class does NOT declare is a sub-block
    addressing a child by name (documented routing) — it neither becomes an
    attribute nor raises. Unchanged by the dispatch fix; pinned so the new raise
    can never leak into the routing path."""
    register_all()

    @configurable
    class Bare:
        def __init__(self, x: int = 0) -> None:
            self.x = x

    b = load("b: {_target_: Bare, meta: {note: hi}}")["b"]
    assert not hasattr(b, "meta")  # routing, not a value — today's behaviour, kept


def test_a_dict_valued_ctor_param_without_a_marker_is_unchanged() -> None:
    """A class block delivering a dict at a ctor-param slot whose own kwargs hold NO
    marker keeps today's behaviour: the dict IS the slot value."""
    register_all()
    w = load("w: {_target_: WithDict}\nWithDict:\n  extras: {b: 2}")["w"]
    assert w.extras == {"b": 2}


def register_all() -> None:
    """Re-register the module-level classes (the autouse fixture cleared the registry)."""
    from confluid import register

    for cls in (Engine, Host, PlainHost, Encoder, Trainer, WithDict):
        register(cls)


# --------------------------------------------------------------------------- #
# BUGS-2026-08-22 ENG-1 — a mapping at a CONSTRUCTOR param whose DEFAULT is a
# marker was handed to the constructor as a plain dict: the deferred optimizer
# became `{'lr': 0.1}` (warn/off) or construction failed (strict), while the
# body-slot spelling of the same setting tuned its marker. Both spellings now tune
# a copy of the code-built marker, through the ONE engine._tune_slot_marker.
# --------------------------------------------------------------------------- #


class Adam:  # stands in for torch.optim.Adam
    def __init__(self, params: Any = None, lr: float = 0.001, weight_decay: float = 0.0) -> None:
        self.lr, self.weight_decay = lr, weight_decay


class SGD:
    def __init__(self, params: Any = None, lr: float = 0.01) -> None:
        self.lr = lr


def _optimizer_hosts() -> Any:
    register(Adam)
    register(SGD)

    @configurable
    class CtorDefault:  # the spelling docs/targets.md teaches
        def __init__(self, optimizer: Partial[Adam] = PartialClass(Adam, lr=0.001, weight_decay=0.01)) -> None:
            self.optimizer = optimizer

    @configurable
    class BodySlot:  # the same setting, written in the __init__ body
        def __init__(self) -> None:
            self.optimizer: Partial[Adam] = PartialClass(Adam, lr=0.001, weight_decay=0.01)

    return CtorDefault, BodySlot


@pytest.mark.parametrize("mode", ["strict", "warn"])
@pytest.mark.parametrize(
    "spelling",
    [
        "trainer: !class:{cls}\n  optimizer: {{lr: 0.1}}\n",
        "trainer: !class:{cls}\ntrainer.optimizer.lr: 0.1\n",
        "trainer: !class:{cls}\n{cls}: {{optimizer: {{lr: 0.1}}}}\n",
    ],
)
def test_a_mapping_tunes_a_partial_ctor_default_like_the_body_slot(spelling: str, mode: ValidationMode) -> None:
    ctor_default, _ = _optimizer_hosts()
    set_policy(yaml=mode)
    try:
        answers = [load(spelling.format(cls=cls))["trainer"].optimizer for cls in ("CtorDefault", "BodySlot")]
    finally:
        reset_policy()
    for optimizer in answers:
        assert isinstance(optimizer, PartialClass) and optimizer.target is Adam
        assert optimizer.kwargs == {"lr": 0.1, "weight_decay": 0.01}
    default = inspect.signature(ctor_default.__init__).parameters["optimizer"].default
    assert default.kwargs == {"lr": 0.001, "weight_decay": 0.01}  # a COPY was tuned


@pytest.mark.parametrize(
    "doc, lr",
    [
        ("lr: 0.9\ntrainer: !class:{cls}\n  optimizer: {{lr: 0.1}}\n", 0.1),  # the mapping is later
        ("trainer: !class:{cls}\n  optimizer: {{lr: 0.1}}\nlr: 0.9\n", 0.9),  # the bare key is later
    ],
)
def test_the_tuned_ctor_default_competes_with_a_bare_key_by_document_order(doc: str, lr: float) -> None:
    _optimizer_hosts()
    for cls in ("CtorDefault", "BodySlot"):
        assert load(doc.format(cls=cls))["trainer"].optimizer.kwargs == {"lr": lr, "weight_decay": 0.01}, cls


def test_a_full_marker_at_the_ctor_default_slot_still_replaces_it() -> None:
    _optimizer_hosts()
    optimizer = load("trainer: !class:CtorDefault\n  optimizer: !partial:SGD {lr: 0.1}\n")["trainer"].optimizer
    built = flow(optimizer)
    assert type(built) is SGD and built.lr == 0.1


def test_a_mapping_tunes_an_eager_ctor_default_marker_and_builds_it() -> None:
    """The same rule for a non-deferred default: the slot holds a marker, so the
    mapping tunes it and the child is BUILT with the merged kwargs."""

    @configurable
    class Car:
        def __init__(self, engine: Any = Target(Engine, power=7)) -> None:
            self.engine = engine

    car = load("car: !class:Car\n  engine: {power: 50}\n")["car"]
    assert type(car.engine) is Engine and car.engine.power == 50
