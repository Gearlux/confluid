"""Cross-path divergence pins — the contract for the one-scanner refactor.

Confluid's one precedence rule runs on two paths: the LOAD path
(``broadcast._prepare_kwargs`` merging a document onto markers) and the
CONFIGURE path (``configurator._apply`` walking live objects). A 2026-08-08
audit catalogued every behavioral difference between them (D1-D5; see
``docs/architecture.md`` records 3 and 8) ahead of unifying the walk itself.

This file pins the catalogue:

* the KEPT differences (D4, D5) get a pin **per path**, cross-referencing each
  other — so future drift in EITHER direction fails a named test instead of
  passing silently;
* the UNIFIED ones (D2, D3 — adjudicated to the load path's stricter gates)
  are pinned in their post-unification state and land with that change.

D1 (the routing-hoist merge condition) has no reachable end-to-end trigger
from legal YAML on either path; it is pinned at the unit level on the shared
``_merge_routing`` primitive in ``tests/test_scanner.py``.
"""

from typing import Any, Dict, Optional

from confluid import LazyClass, configurable, configure, load
from confluid.lazy import Lazy


class _Engine:
    """A plain deferred-slot target: accepts power/spare/stages."""

    def __init__(self, power: int = 0, spare: Any = None, stages: Optional[list] = None) -> None:
        self.power, self.spare, self.stages = power, spare, stages


def _holder_cls() -> type:
    """A fresh @configurable holder with a deferred body slot per test.

    Fresh per call so the per-class introspection caches never leak state
    between pins (the registry snapshot fixture undoes the registration).
    """

    @configurable
    class Holder:
        def __init__(self) -> None:
            self.engine: Lazy[Any] = LazyClass(_Engine)

    return Holder


# --------------------------------------------------------------------------- #
# D4 — a bare top-level dict at a dict-annotated param
# --------------------------------------------------------------------------- #


@configurable
class _D4Sink:
    def __init__(self, extras: Optional[Dict[str, int]] = None, tag: str = "") -> None:
        """A sink with one dict-annotated param.

        Args:
            extras: A mapping-typed knob (drives the load path's param-kind rule).
            tag: An unrelated scalar.
        """
        self.extras, self.tag = extras, tag


def test_d4_load_path_applies_a_bare_dict_at_a_dict_annotated_param() -> None:
    """LOAD: a bare top-level dict is a VALUE for a dict-annotated param.

    ``_accepts`` consults the param-kind scan, so ``extras: {a: 1}`` broadcasts
    into the marker like any bare scalar. KEPT difference — the configure path
    deliberately treats every top-level dict as a BLOCK (see the twin pin
    below); changing either side must be a decision, not drift.
    """
    cfg = load("extras: {a: 1}\nsink: !class:_D4Sink()\n")
    assert cfg["sink"].extras == {"a": 1}


def test_d4_configure_path_treats_the_same_dict_as_a_block() -> None:
    """CONFIGURE: a top-level dict is a block for others, never a bare value.

    The matching grammar depends on it — class-name and instance-name blocks
    are top-level dicts. Twin of the load-path pin above.
    """
    obj = _D4Sink()
    configure(obj, config="extras: {a: 1}")
    assert obj.extras is None


# --------------------------------------------------------------------------- #
# D5 — a glob-delivered dict at a declared (deferred) slot
# --------------------------------------------------------------------------- #


def test_d5_configure_path_glob_dict_tunes_a_deferred_slot() -> None:
    """CONFIGURE: ``'**': {engine: {...}}`` reaches the deferred slot and tunes it.

    The configure ladder's dict branch does not consult the ``gated`` flag.
    KEPT for the scanner refactor (declared as the receiver's
    ``gated_dict_reaches_slot`` knob); flagged for separate adjudication —
    aligning the LOAD path to this is a feature-sized semantic change.
    """
    holder = _holder_cls()()
    configure(holder, config="'**':\n  engine:\n    power: 5\n")
    assert holder.engine.kwargs == {"power": 5}


def test_d5_load_path_glob_dict_is_routing_not_slot_tuning() -> None:
    """LOAD: the same shape hoists as routing for descendants — the slot stays untouched.

    The marker ladder admits a gated dict only for a dict-TYPED param. Twin of
    the configure-path pin above.
    """
    holder_cls = _holder_cls()
    cfg = load(
        "holder: !class:{name}()\n'**':\n  engine:\n    power: 5\n".format(name=holder_cls.__name__),
    )
    assert cfg["holder"].engine.kwargs == {}


# --------------------------------------------------------------------------- #
# D2 / D3 — the UNIFIED gates (adjudicated to the engine's stricter cascade,
# 2026-08-08; both paths now share broadcast.merge_bare_pool_into_kwargs)
# --------------------------------------------------------------------------- #


def test_d2_a_bare_list_never_tunes_a_deferred_slot_on_either_path() -> None:
    """A bare LIST value is a definition, never a cascading value — both paths.

    Before unification the configure path merged it (its copy of the cascade
    skipped only dicts), so ``stages: [1, 2]`` silently rode into a deferred
    slot's kwargs on configure() alone.
    """
    holder = _holder_cls()()
    configure(holder, config="stages: [1, 2]")
    assert holder.engine.kwargs == {}

    holder_cls = _holder_cls()
    cfg = load("holder: !class:{name}()\nstages: [7]\n".format(name=holder_cls.__name__))
    assert cfg["holder"].engine.kwargs == {}


def test_d3_a_same_target_fluid_never_tunes_a_deferred_slot() -> None:
    """The self-broadcast guard now holds on the configure path too.

    A bare Fluid whose target IS the slot's own target would loop on
    re-materialization; the engine path always skipped it, the configure path
    merged it. A DIFFERENT-target Fluid at a declared key still tunes — the
    guard must not over-narrow.
    """
    from confluid import Class as ConfluidClass

    holder = _holder_cls()()
    configure(holder, config={"spare": ConfluidClass(_Engine)})
    assert holder.engine.kwargs == {}  # same target — guarded

    class _Other:
        def __init__(self, n: int = 0) -> None:
            self.n = n

    holder2 = _holder_cls()()
    configure(holder2, config={"spare": ConfluidClass(_Other)})
    tuned = holder2.engine.kwargs.get("spare")
    assert isinstance(tuned, ConfluidClass) and tuned.target is _Other  # declared key, other target — lands


def test_d3_a_fluid_never_rides_the_kwargs_catchall_into_a_deferred_slot() -> None:
    """A ``**kwargs`` deferred target accepts every scalar — but never a Fluid.

    The declared-key requirement is what keeps an outer marker from being
    pulled into every permissive nested target (and looping); it now gates the
    configure path exactly as it always gated the engine path.
    """
    from confluid import Class as ConfluidClass

    class _Forwards:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    @configurable
    class _CatchallHolder:
        def __init__(self) -> None:
            self.engine: Lazy[Any] = LazyClass(_Forwards)

    holder = _CatchallHolder()
    configure(holder, config={"anything": ConfluidClass(_Engine), "plain": 7})
    assert "anything" not in holder.engine.kwargs  # a Fluid never rides the catchall
    assert holder.engine.kwargs.get("plain") == 7  # a scalar still does
