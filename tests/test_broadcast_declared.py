"""``broadcast="declared"`` — closing a ``**kwargs`` accept-list to the declared/scanned slots.

A third-party ``**kwargs`` constructor has no accept-list, so confluid errs
permissive and EVERY bare key in the document cascades onto its instances
(pinned by ``test_broadcast_scoping.py::test_var_keyword_class_receives_every_bare_key``).
``broadcast="declared"`` is the registrar's middle setting between ``True``
(everything) and ``False`` (nothing): the accept-list is built from
``introspect.slots()`` exactly as for a class without ``**kwargs`` — signature
params, public class attributes, and ``__init__``-body slots MRO-wide — so a
base class that consumes its kwargs via ``self.x = kwargs.pop("x", ...)``
keeps ``x`` broadcastable while unrelated document keys stop landing.

Constructor ROUTING is deliberately untouched: which keys reach a ``**kwargs``
constructor follows the ADDRESSING rule (``engine._var_keyword_extras``), never
the accept-list — so marker-own kwargs and runtime kwargs behave identically
with and without the mark.
"""

from typing import Any, Tuple, Type

import pytest

from confluid import (
    ConfigurableDefinitionError,
    accepts_any_key,
    accepts_broadcast,
    configurable,
    configure,
    declares_key,
    dump,
    load,
    marks,
    register,
)


def _kwargs_family() -> Tuple[Type[Any], Type[Any]]:
    """A fresh torchmetrics-shaped pair per test — marks die with the classes.

    ``SinkBase`` consumes its kwargs the way ``torchmetrics.Metric`` does:
    ``self.x = kwargs.pop("x", default)`` then REFUSE the leftovers. The body
    assignment is what the slot scan parses, so ``on_cpu`` is a declared slot
    of every subclass without appearing in any signature.
    """

    class SinkBase:
        def __init__(self, **kwargs: Any) -> None:
            self.on_cpu = kwargs.pop("on_cpu", False)
            if kwargs:
                raise ValueError(f"Unexpected keyword arguments: {sorted(kwargs)}")

    class Gauge(SinkBase):
        def __init__(self, average: str = "macro", **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self.average = average

    return SinkBase, Gauge


def test_an_unrelated_bare_key_no_longer_lands_on_a_declared_kwargs_class() -> None:
    """The case the knob exists for: sweep keys aimed at OTHER nodes stop cascading in."""
    _, Gauge = _kwargs_family()
    register(Gauge, name="GaugeUnrelatedBare", broadcast="declared")

    objs = load(
        """
batch_size: 32
run_name: exp1
meter: !class:GaugeUnrelatedBare
  average: micro
"""
    )
    meter = objs["meter"]
    assert meter.average == "micro"
    assert not hasattr(meter, "batch_size")
    assert not hasattr(meter, "run_name")


def test_a_bare_key_matching_a_ctor_param_still_cascades() -> None:
    _, Gauge = _kwargs_family()
    register(Gauge, name="GaugeBareParam", broadcast="declared")

    objs = load(
        """
average: micro
meter: !class:GaugeBareParam
"""
    )
    assert objs["meter"].average == "micro"


def test_a_bare_key_matching_a_base_body_slot_still_cascades() -> None:
    """The 'parse' half: the base consumes ``on_cpu`` from ``**kwargs`` via a body
    assignment, and that assignment is exactly what keeps the key broadcastable."""
    _, Gauge = _kwargs_family()
    register(Gauge, name="GaugeBareBodySlot", broadcast="declared")

    objs = load(
        """
on_cpu: true
batch_size: 32
meter: !class:GaugeBareBodySlot
"""
    )
    meter = objs["meter"]
    assert meter.on_cpu is True
    assert not hasattr(meter, "batch_size")


def test_an_unmarked_kwargs_class_still_receives_every_bare_key() -> None:
    """The con case beside the pro: without the mark, nothing changes."""
    _, Gauge = _kwargs_family()
    register(Gauge, name="GaugeUnmarked")

    objs = load(
        """
batch_size: 32
meter: !class:GaugeUnmarked
"""
    )
    assert objs["meter"].batch_size == 32


def test_marker_own_kwargs_reach_the_constructor_unchanged() -> None:
    """Ctor routing follows ADDRESSING, not the accept-list — an addressed kwarg
    the signature does not name still rides ``**kwargs`` into the constructor."""
    _, Gauge = _kwargs_family()
    register(Gauge, name="GaugeOwnKwargs", broadcast="declared")

    objs = load(
        """
meter: !class:GaugeOwnKwargs
  on_cpu: true
"""
    )
    assert objs["meter"].on_cpu is True


def test_an_addressed_typo_still_crashes_in_the_constructor() -> None:
    """The base's own refusal stays the authority for a typo written ON the marker."""
    _, Gauge = _kwargs_family()
    register(Gauge, name="GaugeOwnTypo", broadcast="declared")

    with pytest.raises(ValueError, match="Unexpected keyword arguments"):
        load(
            """
meter: !class:GaugeOwnTypo
  averge: micro
"""
        )


def test_a_block_delivered_undeclared_key_follows_the_declared_class_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """A class-name block key the closed list does not admit takes the route a
    normal declared class gives it — a located WARNING, value dropped — instead
    of silently riding ``**kwargs`` into a constructor crash."""
    from types import SimpleNamespace

    from confluid import broadcast

    _, Gauge = _kwargs_family()
    register(Gauge, name="GaugeBlockTypo", broadcast="declared")

    warnings: list = []
    monkeypatch.setattr(
        broadcast,
        "logger",
        SimpleNamespace(
            warning=lambda msg, *a, **k: warnings.append(msg),
            trace=lambda *a, **k: None,
            debug=lambda *a, **k: None,
        ),
    )
    objs = load(
        """
GaugeBlockTypo:
  averge: micro
meter: !class:GaugeBlockTypo
"""
    )
    meter = objs["meter"]
    assert not hasattr(meter, "averge")
    assert meter.average == "macro"
    assert any("averge" in w for w in warnings), warnings


def test_the_predicates_answer_per_key_for_a_marked_class() -> None:
    _, Gauge = _kwargs_family()
    register(Gauge, name="GaugePredicates", broadcast="declared")

    assert not accepts_any_key(Gauge), "the class now discriminates between keys"
    assert declares_key(Gauge, "average")
    assert accepts_broadcast(Gauge, "average")
    assert accepts_broadcast(Gauge, "on_cpu")
    assert not accepts_broadcast(Gauge, "batch_size")


def test_declared_is_a_noop_for_a_class_without_var_keyword() -> None:
    class Plain:
        def __init__(self, lr: float = 0.0) -> None:
            self.lr = lr

    register(Plain, name="PlainDeclared", broadcast="declared")

    assert accepts_broadcast(Plain, "lr")
    assert not accepts_broadcast(Plain, "batch_size")
    assert not accepts_any_key(Plain)


def test_an_unknown_broadcast_spelling_is_refused() -> None:
    """The knob is a closed three-state set — a typo raises at registration, never
    silently registers an open class."""

    class Victim:
        def __init__(self, **kwargs: Any) -> None:
            pass

    with pytest.raises(ConfigurableDefinitionError, match="declared"):
        register(Victim, name="VictimTypo", broadcast="declred")  # type: ignore[arg-type]

    with pytest.raises(ConfigurableDefinitionError, match="declared"):
        configurable(broadcast="declred")(Victim)  # type: ignore[call-overload]


def test_the_decorator_spelling_closes_the_list_too() -> None:
    @configurable(name="OwnedGaugeDeclared", broadcast="declared")
    class OwnedGauge:
        def __init__(self, average: str = "macro", **kwargs: Any) -> None:
            self.extra = dict(kwargs)
            self.average = average

    objs = load(
        """
batch_size: 32
average: micro
meter: !class:OwnedGaugeDeclared
"""
    )
    meter = objs["meter"]
    assert meter.average == "micro"
    assert not hasattr(meter, "batch_size")
    assert meter.extra == {}, "the bare keys never rode **kwargs into the constructor"


def test_marks_reports_broadcast_declared() -> None:
    _, marked = _kwargs_family()
    _, unmarked = _kwargs_family()
    register(marked, name="GaugeMarksOn", broadcast="declared")
    register(unmarked, name="GaugeMarksOff")

    assert marks(marked).broadcast_declared is True
    assert marks(marked).no_broadcast is False
    assert marks(unmarked).broadcast_declared is False


def test_configure_filters_bare_keys_by_the_declared_list_too() -> None:
    """The accept-list is shared by both paths, so ``configure()`` gives one answer."""
    _, Gauge = _kwargs_family()
    register(Gauge, name="GaugeConfigure", broadcast="declared")

    gauge = Gauge()
    configure(gauge, config={"batch_size": 32, "average": "micro"})
    assert gauge.average == "micro"
    assert not hasattr(gauge, "batch_size")


def test_dump_reload_round_trips_a_declared_kwargs_object() -> None:
    _, Gauge = _kwargs_family()
    register(Gauge, name="GaugeRoundTrip", broadcast="declared")

    built = load(
        """
meter: !class:GaugeRoundTrip
  average: micro
  on_cpu: true
"""
    )["meter"]
    reloaded = load(dump(built))
    assert reloaded.average == built.average == "micro"
    assert reloaded.on_cpu is built.on_cpu is True
