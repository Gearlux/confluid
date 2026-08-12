"""What each subsystem thinks a class's configurable slots are — and why they differ.

Six readers ask "which slots does this target have". They gave **five different
answers** for one class until 2026-08-12, because five of them hand-rolled their
own "minus self/cls" filter with a different set of parameter-kind exclusions.
They now project from the ONE ``introspect.slots()`` enumeration, and the answers
collapse to **three** — one per question actually being asked:

===========================================  =========================  ==============================
answer                                        readers                    question
===========================================  =========================  ==============================
``['head', 'pos_only', 'lr']``                accept-list, to_pydantic   what is CONFIGURABLE at all
``['pos_only', 'lr']``                        input_specs, get_hierarchy what the SIGNATURE declares
``['lr']``                                    _ctor_params               what the CONSTRUCTOR can take
===========================================  =========================  ==============================

This file was written as the BASELINE before that change (recording five answers,
with the drift pinned under ``test_DRIFT_*`` names), which is what made the
refactor reviewable: every line below that moved is a decision, recorded here with
its reason, rather than a behaviour that shifted unnoticed.

The readers, and what each is FOR:

============================  ==========================================================
``broadcast._get_acceptable_keys``  may a key land on this target at all (the accept-list)
``engine._ctor_params``             which keys go to the CONSTRUCTOR vs a post-init setattr
``broadcast.declares_key``          does the target NAME this key (the ``**kwargs`` question)
``schema.input_specs``              the I/O contract a GUI renders required/optional from
``schema.get_hierarchy``            the dotted paths a CLI builds flags from
``pydantic_export.to_pydantic``     the validating model + form spec every AI surface reads
============================  ==========================================================

They legitimately differ — an accept-list is not a form spec. What is NOT
legitimate is differing on *which parameter kinds exist*, which is where the
drift lives: three of them hand-roll their own "minus self/cls" filter with a
different set of exclusions.
"""

from typing import Any, Dict, FrozenSet, List, Optional

from confluid import configurable, input_specs, to_pydantic
from confluid.broadcast import _get_acceptable_keys, accepts_key, declares_key
from confluid.engine import _ctor_params
from confluid.schema import get_hierarchy


@configurable
class Spread:
    """One class exercising every parameter kind a reader can disagree about.

    Args:
        pos_only: POSITIONAL_ONLY — can never be passed by keyword.
        loaders: VAR_POSITIONAL — likewise, and not even a single slot.
        lr: an ordinary keyword parameter, the uncontroversial case.
    """

    def __init__(self, pos_only: int = 0, /, *loaders: Any, lr: float = 1.0) -> None:
        self.lr = lr
        self.head = None  # an __init__-BODY slot: configurable, absent from the signature


@configurable
class Forwards:
    """A ``**kwargs`` catch-all — the accept-list is unknowable for it."""

    def __init__(self, *loaders: Any, lr: float = 1.0, **extra: Any) -> None:
        self.lr = lr


def _leaves(paths: Dict[str, Any]) -> List[str]:
    """``get_hierarchy`` returns dotted paths; compare the leaf names."""
    return sorted(path.rsplit(".", 1)[-1] for path in paths)


def _readers(cls: type) -> Dict[str, Any]:
    """Every reader's answer for ``cls``, as comparable sorted names."""
    acceptable: Optional[FrozenSet[str]] = _get_acceptable_keys(cls)
    return {
        "accept-list": sorted(acceptable) if acceptable is not None else None,
        "_ctor_params": sorted(_ctor_params(cls) or []),
        "input_specs": [spec["name"] for spec in input_specs(cls)],
        "get_hierarchy": _leaves(get_hierarchy(cls)),
        "to_pydantic": sorted(to_pydantic(cls).model_fields),
    }


# --------------------------------------------------------------------------------------
# The headline: five readers, five answers
# --------------------------------------------------------------------------------------


def test_the_readers_give_one_answer_per_question_they_ask() -> None:
    """Five answers became three, and the three are explainable.

    Was, before the ``slots()`` consolidation::

        accept-list      ['head', 'loaders', 'lr', 'pos_only']   <- 'loaders' is a *args name
        _ctor_params     ['lr']
        input_specs      ['pos_only', 'lr']
        get_hierarchy    ['loaders', 'lr', 'pos_only']            <- likewise
        to_pydantic      ['head', 'lr', 'pos_only']

    If a line here changes, a reader's answer changed — which is allowed, and is
    exactly what must be REVIEWED rather than absorbed. Do not "fix" this test by
    updating the numbers; update it by deciding what the new answer should be and
    saying so in the commit.
    """
    assert _readers(Spread) == {
        "accept-list": ["head", "lr", "pos_only"],
        "_ctor_params": ["lr"],
        "input_specs": ["pos_only", "lr"],
        "get_hierarchy": ["lr", "pos_only"],
        "to_pydantic": ["head", "lr", "pos_only"],
    }

    # Compared as SETS: ``input_specs`` reports in signature order by contract (a
    # GUI renders fields in the order the author declared them), the others sort.
    distinct = {frozenset(answer or ()) for answer in _readers(Spread).values()}
    assert len(distinct) == 3, "three questions, three answers — a reader may differ, but only on purpose"


def test_no_reader_reports_a_var_positional_name() -> None:
    """``*loaders`` addresses nothing, so no surface may offer it.

    It cannot be passed by keyword, so a config key of that name reaches no
    constructor: it used to pass the accept-list and land as a post-init attribute
    nothing reads, and ``get_hierarchy`` used to publish it as a CLI flag Python
    rejects at the call. This is the rule the three former ``test_DRIFT_*`` cases
    were pinning the absence of.
    """
    for reader, answer in _readers(Spread).items():
        assert "loaders" not in (answer or []), f"{reader} still reports a *args name"


# --------------------------------------------------------------------------------------
# Differences that are DELIBERATE — a consolidation must preserve these
# --------------------------------------------------------------------------------------


def test_deliberate_only_the_accept_list_and_to_pydantic_see_body_slots() -> None:
    """A body slot IS a configurable slot (the class-design convention, rule 4).

    The accept-list needs it so a key can land there; ``to_pydantic`` needs it so
    a form offers it. ``input_specs`` and ``get_hierarchy`` describe the SIGNATURE
    and legitimately stop at it — but that means a CLI built from `get_hierarchy`
    cannot offer a knob the form editor can, for the same class.
    """
    answers = _readers(Spread)

    assert "head" in (answers["accept-list"] or [])
    assert "head" in answers["to_pydantic"]
    assert "head" not in answers["input_specs"]
    assert "head" not in answers["get_hierarchy"]


def test_deliberate_ctor_params_keeps_var_keyword_and_drops_var_positional() -> None:
    """The asymmetry is load-bearing, not an oversight (``engine._ctor_params``).

    A ``**kwargs`` name being present is what makes the returned set non-empty,
    and a non-empty set is what routes every unmatched key to a post-init
    setattr — the documented behaviour of a ``**kwargs`` ``@configurable`` class.
    A ``*args`` name can never be passed by keyword, so it is not a keyword slot.
    """
    assert "extra" in (_ctor_params(Forwards) or set()), "VAR_KEYWORD is kept"
    assert "loaders" not in (_ctor_params(Forwards) or set()), "VAR_POSITIONAL is dropped"


def test_deliberate_a_var_keyword_class_has_no_accept_list_at_all() -> None:
    """``None`` means accept-EVERYTHING, and it is a sentinel the readers must keep.

    It is the reason the accept-list cannot simply BE a name set — the shared-helper
    objection recorded in ``introspect.py``. A consolidation keeps it as a
    projection ("None when any slot is VAR_KEYWORD"), never loses it.
    """
    assert _get_acceptable_keys(Forwards) is None
    assert accepts_key(Forwards, "anything_at_all") is True


# --------------------------------------------------------------------------------------
# Drift the consolidation REMOVED — each of these was a ``test_DRIFT_*`` pin
# --------------------------------------------------------------------------------------


def test_get_hierarchy_no_longer_publishes_a_var_positional_as_a_cli_flag() -> None:
    """``get_hierarchy`` is what a CLI builds dotted override flags from.

    It used to publish ``--loaders`` for a ``*args`` parameter — a flag whose
    value Python rejects at the call. Its filter skipped ``self``/``cls``/``args``/
    ``kwargs`` by NAME, which is wrong twice: it misses a variadic spelled
    anything else, and it would wrongly drop an ordinary parameter named ``args``.
    It now filters by KIND.
    """
    assert "loaders" not in _leaves(get_hierarchy(Spread))
    assert "extra" not in _leaves(get_hierarchy(Forwards)), "a **kwargs name is not a path either"


def test_declares_key_answers_the_same_for_the_same_parameter_kind() -> None:
    """One function, one answer — it used to have two code paths that disagreed.

    ``declares_key`` short-circuited to the accept-list for a target WITH an
    accept-list and ran its own kind-filtered walk only for a ``**kwargs`` target,
    so the identical ``*loaders`` parameter was "declared" on one class and not on
    the other. It is the public predicate ``AGENTS.md`` tells front-ends to call
    INSTEAD of re-deriving settability, so the drift sat in the answer that exists
    to prevent exactly this.
    """
    assert declares_key(Spread, "loaders") is False
    assert declares_key(Forwards, "loaders") is False
    assert declares_key(Spread, "lr") is True and declares_key(Forwards, "lr") is True


def test_the_accept_list_admits_only_keys_that_can_actually_land() -> None:
    """``accepts_key`` and the engine now agree about which names are slots.

    A ``positional_only`` name stays accepted deliberately — it falls through to a
    post-init ``setattr``, which is what ``configure()`` has always done for it, so
    both paths agree. A ``var_positional`` name does not: it is not a slot at all.
    """
    assert accepts_key(Spread, "loaders") is False, "a *args name addresses nothing"
    assert accepts_key(Spread, "pos_only") is True, "reaches a post-init setattr, as on the configure path"
    assert "pos_only" not in (_ctor_params(Spread) or set()), "...but never the constructor"
