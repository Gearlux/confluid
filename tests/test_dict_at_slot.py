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

from typing import Any, Dict

import pytest

from confluid import ConfigurationError, configurable, configure, get_registry, load


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
