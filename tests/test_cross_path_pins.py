"""Cross-path divergence pins — the contract for the one-scanner refactor.

Confluid's one precedence rule runs on two paths: the LOAD path
(``broadcast._prepare_kwargs`` merging a document onto markers) and the
CONFIGURE path (``configurator._apply`` walking live objects). A 2026-08-08
audit catalogued every behavioral difference between them (D1-D5; see
``docs/architecture.md`` records 3 and 8) ahead of unifying the walk itself.

This file pins the catalogue:

* the ONE remaining kept difference (D4) gets a pin **per path**,
  cross-referencing its twin — so future drift in EITHER direction fails a
  named test instead of passing silently;
* the UNIFIED ones are pinned in their post-unification state: D2, D3
  (adjudicated to the load path's stricter gates, 2026-08-08) and D5 plus its
  uncatalogued mirror (adjudicated 2026-08-09 to "rider content aimed at a
  declared deferred slot reaches it — both paths, both value shapes", as a
  full spelling×path matrix below, because the mirror cell had NO pin: the
  parity suites compared live-attribute application and the deferred-slot
  cascade sat outside the audited walk).

D1 (the routing-hoist merge condition) has no reachable end-to-end trigger
from legal YAML on either path; it is pinned at the unit level on the shared
``_merge_routing`` primitive in ``tests/test_scanner.py``.
"""

from typing import Any, Dict, Optional

import pytest

from confluid import NoBroadcast, PartialClass, configurable, configure, flow, load
from confluid.partial import Partial


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
            self.engine: Partial[Any] = PartialClass(_Engine)

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
# D5 (+ its mirror) — rider content at a declared deferred slot: the MATRIX
# --------------------------------------------------------------------------- #

# Every spelling that aims a value at the deferred ``engine`` slot's ``power``
# knob. Adjudicated 2026-08-09: ALL of them apply on BOTH paths. Before, the
# rider cells were CROSSED — the mapping worked on configure only (D5) and the
# scalar on load only (the uncatalogued mirror; measured: the identical
# ``'**.optimizer.lr': 0.01`` document trained at the code default on one path
# and at 0.01 on the other, silently either way. This matrix exists so a new
# spelling or path can never again ship without a cell.
_DELIVERY_SPELLINGS = {
    "bare_scalar": "power: 5",
    "rider_scalar": "'**.power': 5",
    "rider_mapping": "'**.engine.power': 5",
    "addressed_mapping": "{name}:\n  engine:\n    power: 5",
}


@pytest.mark.parametrize("spelling", sorted(_DELIVERY_SPELLINGS))
def test_d5_matrix_every_spelling_reaches_the_deferred_slot_on_the_LOAD_path(spelling: str) -> None:
    holder_cls = _holder_cls()
    doc = _DELIVERY_SPELLINGS[spelling].format(name=holder_cls.__name__)
    cfg = load(f"holder: !class:{holder_cls.__name__}()\n{doc}\n")
    assert flow(cfg["holder"].engine).power == 5


@pytest.mark.parametrize("spelling", sorted(_DELIVERY_SPELLINGS))
def test_d5_matrix_every_spelling_reaches_the_deferred_slot_on_the_CONFIGURE_path(spelling: str) -> None:
    holder_cls = _holder_cls()
    holder = holder_cls()
    configure(holder, config=_DELIVERY_SPELLINGS[spelling].format(name=holder_cls.__name__))
    assert flow(holder.engine).power == 5


def test_d5_gated_delivery_respects_the_no_broadcast_opt_out_on_both_paths() -> None:
    """A rider delivery is a CASCADE form — the NoBroadcast opt-out gates it.

    Each shield gates deliveries AT ITS OWN KEY, mirroring the bare-key
    contract exactly: a ``NoBroadcast`` SLOT param refuses the rider MAPPING
    (delivered at the slot's key on the holder — the live path never
    consulted ``blocked`` in its dict branch before the adjudication), and a
    ``NoBroadcast`` param on the TARGET refuses the rider SCALAR (delivered
    at that key inside the marker's kwargs). The ADDRESSED spelling still
    tunes — addressed delivery is never gated.
    """

    class _ShieldedEngine:
        def __init__(self, power: NoBroadcast[int] = 0) -> None:
            self.power = power

    @configurable
    class Shielded:
        def __init__(self, engine: NoBroadcast[Any] = None) -> None:
            self.engine = engine if engine is not None else PartialClass(_ShieldedEngine)

    # Rider MAPPING at the NoBroadcast slot key — refused, both paths.
    cfg = load("holder: !class:Shielded()\n'**.engine.power': 5\n")
    assert flow(cfg["holder"].engine).power == 0, "load path let the rider mapping through"
    shielded = Shielded()
    configure(shielded, config="'**.engine.power': 5")
    assert flow(shielded.engine).power == 0, "configure path let the rider mapping through"

    # Rider SCALAR at the target's NoBroadcast param — refused, both paths.
    cfg = load("holder: !class:Shielded()\n'**.power': 5\n")
    assert flow(cfg["holder"].engine).power == 0, "load path let the rider scalar through"
    shielded = Shielded()
    configure(shielded, config="'**.power': 5")
    assert flow(shielded.engine).power == 0, "configure path let the rider scalar through"

    # Addressed delivery is never gated by NoBroadcast.
    shielded = Shielded()
    configure(shielded, config="Shielded:\n  engine:\n    power: 5\n")
    assert flow(shielded.engine).power == 5


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
    from confluid import Target as ConfluidClass

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


# --------------------------------------------------------------------------- #
# D6 — a function-OBJECT target is introspected as ITSELF (both paths,
# adjudicated 2026-08-10). Five of six target-normalization sites degraded a
# code-built ``PartialClass(builder_fn, …)`` slot to an unresolvable target
# (``resolve_class`` is string/type-only): the engine cascade then ran with NO
# NoBroadcast gates while the public ``accepts_broadcast`` said the key was
# refused, and ``configure()`` could not tune the slot at all — while the
# identical class-target slot behaved on both paths. All sites now normalize
# through the one ``broadcast._settability_target``.
# --------------------------------------------------------------------------- #


def _shielded_builder(lr: float = 0.0, run_name: NoBroadcast[str] = "") -> Dict[str, Any]:
    """A builder FUNCTION with a NoBroadcast param — the registered-zoo marker shape."""
    return {"lr": lr, "run_name": run_name}


def _fn_holder_cls() -> type:
    """A fresh holder with a function-target deferred body slot per test."""

    @configurable
    class FnHolder:
        def __init__(self) -> None:
            self.maker: Partial[Any] = PartialClass(_shielded_builder, lr=1e-4)

    return FnHolder


def test_d6_load_cascade_uses_a_function_targets_own_gates() -> None:
    """LOAD: a bare key cascades into a function-target slot under the fn's own gates.

    The declared ``lr`` lands; the ``NoBroadcast`` ``run_name`` is refused —
    matching what ``accepts_broadcast(fn, …)`` already answered. Before the
    normalizer unification the slot's target degraded to ``None``, so blocked
    was EMPTY and ``run_name`` rode in against the published predicate.
    """
    holder_cls = _fn_holder_cls()
    cfg = load(f"holder: !class:{holder_cls.__name__}()\nlr: 0.5\nrun_name: cascaded\n")
    marker = cfg["holder"].maker
    assert marker.kwargs.get("lr") == 0.5
    assert "run_name" not in marker.kwargs


def test_d6_configure_tunes_a_function_target_slot_like_a_class_target_one() -> None:
    """CONFIGURE: a function-target deferred slot is tunable, same gates.

    ``_tune_deferred`` resolved the target with the string/type-only idiom, got
    ``None``, and early-returned — so ``configure(holder, {"lr": 0.5})``
    silently left the code default while the identical class-target slot tuned.
    """
    holder = _fn_holder_cls()()
    configure(holder, config={"lr": 0.5, "run_name": "cascaded"})
    assert holder.maker.kwargs.get("lr") == 0.5
    assert "run_name" not in holder.maker.kwargs


def test_d3_a_fluid_never_rides_the_kwargs_catchall_into_a_deferred_slot() -> None:
    """A ``**kwargs`` deferred target accepts every scalar — but never a Fluid.

    The declared-key requirement is what keeps an outer marker from being
    pulled into every permissive nested target (and looping); it now gates the
    configure path exactly as it always gated the engine path.
    """
    from confluid import Target as ConfluidClass

    class _Forwards:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    @configurable
    class _CatchallHolder:
        def __init__(self) -> None:
            self.engine: Partial[Any] = PartialClass(_Forwards)

    holder = _CatchallHolder()
    configure(holder, config={"anything": ConfluidClass(_Engine), "plain": 7})
    assert "anything" not in holder.engine.kwargs  # a Fluid never rides the catchall
    assert holder.engine.kwargs.get("plain") == 7  # a scalar still does


# ---------------------------------------------------------------------------
# The block-vs-bare contest agrees across paths, in EVERY ordering
# (BUGS-2026-08-13 C2)
#
# One deferred slot, two competing specs — a bare sweep key and a class block
# addressing the slot — written in the three possible orders. The two EDGE
# orderings were already pinned and already agreed; the MIDDLE one (node first,
# then the bare key, then the block) is the ordinary way anyone writes this, had
# no pin, and diverged: load() answered 99 where configure() answered 50.
#
# The cause was two implementations of one rule. The shared scanner hands every
# `dict_at_slot` emission a `bare_before` computed at the BLOCK's position;
# `_LiveSink` records it, and `_MergeSink` ignored it while the engine computed a
# second verdict from the MARKER's splice position instead.
# ---------------------------------------------------------------------------


@configurable
class C2Engine:
    def __init__(self, power: int = 1) -> None:
        self.power = power


@configurable
class C2Car:
    def __init__(self) -> None:
        self.engine = PartialClass(C2Engine, power=1)


_C2_NODE = "vehicle: {_target_: C2Car}\n"
_C2_BARE = "power: 99\n"
_C2_BLOCK = "C2Car:\n  engine: {power: 50}\n"

C2_LAYOUTS = {
    # name: (document, the value the LAST-written spec asks for)
    "bare_node_BLOCKlast": (_C2_BARE + _C2_NODE + _C2_BLOCK, 50),
    "node_bare_BLOCKlast": (_C2_NODE + _C2_BARE + _C2_BLOCK, 50),
    "node_block_BARElast": (_C2_NODE + _C2_BLOCK + _C2_BARE, 99),
}


@pytest.mark.parametrize("layout", sorted(C2_LAYOUTS))
def test_the_block_vs_bare_contest_follows_document_order_on_the_LOAD_path(layout: str) -> None:
    document, expected = C2_LAYOUTS[layout]

    assert flow(load(document)["vehicle"].engine).power == expected


@pytest.mark.parametrize("layout", sorted(C2_LAYOUTS))
def test_the_block_vs_bare_contest_agrees_across_BOTH_paths(layout: str) -> None:
    """The parity pin. `node_bare_BLOCKlast` is the cell that diverged."""
    document, expected = C2_LAYOUTS[layout]

    via_load = flow(load(document)["vehicle"].engine).power

    live = C2Car()
    configure(live, config=document.replace(_C2_NODE, ""))
    via_configure = flow(live.engine).power

    assert via_load == via_configure == expected
