"""Phase 3 of record 19 — the runtime consumes hydraide's document.

The precedence rule runs ONCE, in pass 7 (``resolve()`` — what ``hydraide`` emits). Construction
is ``instantiate``: it walks the settled tree and builds every ``Target`` recursively — no second
broadcast into a marker's own kwargs, no late-bound top-level ``Reference``. Consequences pinned
here, each as a before/after the docstrings name:

* the plain artefact reloads to the identical graph — ``load(emit(x))`` builds what ``load(x)``
  builds (the contract the whole phase rests on);
* a marker at ANY depth is built (F4: inside a plain mapping / list);
* a reference is settled ONCE in pass 7 — nearest scope, then the root, never the reference
  itself (F6) — and a reference to a plain value is INLINED (F5): the emitted document is
  CLOSED — no ``_ref_``, no ``Reference``;
* an unresolvable reference is a located error at emit, not a marker that survives ``load()``;
* what must NOT change: a ``Partial`` stays deferred with its nested markers unbuilt until it is
  flowed with runtime kwargs; a body-slot marker created inside ``__init__`` still receives the
  document's bare keys (the one delivery the document cannot show — it is fed from the tree's
  own top-level keys, never from engine state).
"""

import textwrap
from typing import Any

import pytest
import yaml

from confluid import ConfigurationError, Partial, PartialClass, configurable, flow, load, materialize
from confluid.fluid import Reference, Target
from confluid.hydraide import emit


@configurable
class IM:
    built = 0

    def __init__(self, hidden: int = 1, lr: float = 0.0) -> None:
        self.hidden, self.lr = hidden, lr
        IM.built += 1


@configurable
class IA:
    def __init__(self, params: Any = None, lr: float = 0.0, model: Any = None) -> None:
        self.params, self.lr, self.model = params, lr, model


@configurable
class ITrainer:
    def __init__(self, epochs: int = 1) -> None:
        self.epochs = epochs
        self.optimizer: Partial[IA] = PartialClass(IA)  # a BODY slot holding a deferred marker


PROBE = textwrap.dedent(
    """\
    cfg:
      lr: 0.1
    use: !ref:cfg.lr
    fn: !ref:posixpath.join
    opt: !partial:IA
      model: !class:IM(hidden=3)
    group:
      node: !class:IM(hidden=5)
    items:
      - !class:IM(hidden=7)
    lr: 0.9
    """
)


def _shape(value: Any) -> Any:
    """A structural, identity-free view of a loaded graph, for cross-load comparison."""
    if isinstance(value, dict):
        return {k: _shape(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_shape(v) for v in value]
    if isinstance(value, Target):  # a Partial, or a marker nested inside one
        kind = "Partial" if value.partial else "Target"
        return (kind, value.target if isinstance(value.target, str) else value.target.__name__, _shape(value.kwargs))
    if hasattr(value, "__confluid_class__"):
        return (type(value).__name__, {k: _shape(v) for k, v in vars(value).items() if not k.startswith("__confluid")})
    if callable(value):
        return f"<callable {value.__module__}.{value.__qualname__}>"
    return value


# --------------------------------------------------------------------------- #
# The contract: the plain artefact reloads to the identical graph
# --------------------------------------------------------------------------- #


def test_load_of_the_emitted_document_builds_what_load_of_the_source_builds() -> None:
    assert _shape(load(emit(PROBE))) == _shape(load(PROBE))


def test_the_emitted_document_is_closed_no_ref_marker_survives() -> None:
    out = emit(PROBE)
    assert "_ref_" not in out
    doc = yaml.safe_load(out)
    assert doc["use"] == 0.1, "a reference to a plain VALUE is inlined by pass 7"
    assert doc["fn"] == "${ref:posixpath.join}", "an import-path ref keeps dump()'s function spelling"


# --------------------------------------------------------------------------- #
# F4 — a marker at ANY depth is built
# --------------------------------------------------------------------------- #


def test_a_marker_inside_a_plain_mapping_is_built() -> None:
    g = load(PROBE)
    assert isinstance(g["group"]["node"], IM) and g["group"]["node"].hidden == 5
    assert g["group"]["node"].lr == 0.9, "the bare key reached it in pass 7"


def test_a_marker_inside_a_plain_list_is_built() -> None:
    g = load(PROBE)
    assert isinstance(g["items"][0], IM) and g["items"][0].hidden == 7


def test_a_marker_three_levels_down_is_built() -> None:
    g = load("a:\n  b:\n    node: !class:IM(hidden=2)\n")
    assert isinstance(g["a"]["b"]["node"], IM)


# --------------------------------------------------------------------------- #
# F5 — a top-level reference to a value is inlined; the CLI-override contract moves
# --------------------------------------------------------------------------- #


def test_a_top_level_reference_to_a_value_is_the_value_after_load() -> None:
    g = load(PROBE)
    assert g["use"] == 0.1 and not isinstance(g["use"], Reference)


def test_a_top_level_list_index_reference_is_the_value_after_load() -> None:
    g = load("labels: [a, b, c]\nidx: 2\npick: !ref:labels[idx]\n")
    assert g["pick"] == "c"


def test_the_cli_override_contract_is_load_flow_false_then_materialize() -> None:
    """A CLI merges overrides into the DOCUMENT and materializes it — the shape the app framework
    uses; the reference is re-resolved against the merged document, no late-bound marker needed."""
    doc = load("labels: [a, b, c]\nidx: 2\npick: !ref:labels[idx]\n", flow=False)
    assert materialize(doc)["pick"] == "c"
    doc["idx"] = 0  # the override lands in the document, before pass 7
    assert materialize(doc)["pick"] == "a"


# --------------------------------------------------------------------------- #
# F6 — a scope never answers a reference with the reference itself
# --------------------------------------------------------------------------- #


def test_a_plain_mappings_own_key_never_shadows_the_root_key_it_references() -> None:
    g = load("val_fraction: 0.2\nr:\n  val_fraction: !ref:val_fraction\n")
    assert g["r"] == {"val_fraction": 0.2}


def test_a_sibling_key_still_shadows_the_root_nearest_scope_wins() -> None:
    """Scoping is unchanged: the enclosing mapping's OWN keys answer first (an included fragment's
    internal reference must find the fragment's key), then the root. Only the self-hit is skipped."""
    g = load("x: outer\nr:\n  x: inner\n  y: !ref:x\n")
    assert g["r"]["y"] == "inner"


def test_a_self_reference_with_no_root_key_is_a_located_error() -> None:
    with pytest.raises(ConfigurationError) as exc:
        load("r:\n  x: !ref:x\n")
    assert "!ref:x" in str(exc.value) and ":2:6" in str(exc.value)


def test_an_unresolvable_reference_is_a_located_error_at_emit_and_at_load() -> None:
    doc = "m: !class:IM\nuse: !ref:nothing_here\n"
    with pytest.raises(ConfigurationError) as via_emit:
        emit(doc)
    with pytest.raises(ConfigurationError) as via_load:
        load(doc)
    assert "nothing_here" in str(via_emit.value) and ":2:6" in str(via_emit.value)
    assert str(via_emit.value) == str(via_load.value)


# --------------------------------------------------------------------------- #
# What must NOT change
# --------------------------------------------------------------------------- #


def test_a_partial_stays_deferred_with_its_nested_markers_unbuilt() -> None:
    IM.built = 0
    g = load(PROBE)
    opt = g["opt"]
    assert isinstance(opt, PartialClass)
    assert isinstance(opt.kwargs["model"], Target), "nested markers wait for the runtime flow"
    assert opt.kwargs["lr"] == 0.9, "…but the bare key was delivered in pass 7"
    built = flow(opt, params=[1])
    assert isinstance(built, IA) and isinstance(built.model, IM) and built.model.hidden == 3
    assert built.params == [1] and built.lr == 0.9


def test_a_shared_marker_builds_once_through_the_plain_artefact() -> None:
    IM.built = 0
    doc = "m: !class:IM(hidden=4)\na: !ref:m\nb: !ref:m\n"
    plain = emit(doc)
    assert plain.count("&") == 1 and plain.count("*") == 2, plain  # one anchor, two aliases
    g = load(plain)
    assert g["a"] is g["b"] is g["m"] and IM.built == 1


def test_a_body_slot_marker_created_in_init_still_receives_the_documents_bare_keys() -> None:
    """The ONE delivery the document cannot show: `self.optimizer = PartialClass(IA)` is born inside
    the constructor, so pass 7 never sees it. It is fed from the tree's own top-level keys at
    construction — and `load(emit(x))` reproduces it, because those keys are in the document."""
    for text in (
        "trainer: !class:ITrainer\nlr: 0.3\nepochs: 5\n",
        emit("trainer: !class:ITrainer\nlr: 0.3\nepochs: 5\n"),
    ):
        t = load(text)["trainer"]
        assert t.epochs == 5
        assert isinstance(t.optimizer, PartialClass) and t.optimizer.kwargs == {"lr": 0.3}
        assert flow(t.optimizer, params=[1]).lr == 0.3


def test_construction_does_not_rerun_the_precedence_rule() -> None:
    """A marker whose kwargs pass 7 settled is built from those kwargs alone: handing `flow()` a
    context with a DIFFERENT bare value changes nothing, because the contest was settled once."""
    settled = load("m: !class:IM(hidden=4)\nlr: 0.9\n", flow=False)  # markers, unsettled
    from confluid import resolve

    marker = resolve(settled)["m"]
    assert marker.kwargs["lr"] == 0.9
    assert flow(marker).lr == 0.9
    assert materialize(marker, context={"lr": 0.1}).lr == 0.9  # a second broadcast would say 0.1
