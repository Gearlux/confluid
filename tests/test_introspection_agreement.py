"""What each subsystem thinks a class's configurable slots are — the BASELINE.

Five subsystems answer "which slots does this target have", and for one class they
give **five different answers**. This file records those answers exactly as they
are today, so the `slots()` consolidation (``TASKS.md``) lands as a visible diff
rather than a surprise: every line that changes is a decision someone made, not a
behaviour that moved on its own.

That is the whole purpose here. These are NOT assertions that the current
behaviour is right — several of them assert that it is wrong (see the
``test_DRIFT_*`` group, which names the defect each one pins). Pinning a defect
before touching the layer that produces it is what makes the refactor reviewable.

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


def test_the_five_readers_give_five_different_answers_for_one_class() -> None:
    """The measurement that motivates the consolidation, pinned verbatim.

    If a line here changes, the `slots()` refactor changed a reader's answer —
    which is allowed, and is exactly what must be reviewed rather than absorbed.
    Do not "fix" this test by updating the numbers; update it by deciding what
    the new answer should be and saying so in the commit.
    """
    assert _readers(Spread) == {
        "accept-list": ["head", "loaders", "lr", "pos_only"],
        "_ctor_params": ["lr"],
        "input_specs": ["pos_only", "lr"],
        "get_hierarchy": ["loaders", "lr", "pos_only"],
        "to_pydantic": ["head", "lr", "pos_only"],
    }

    distinct = {tuple(answer or ()) for answer in _readers(Spread).values()}
    assert len(distinct) == 5, "five readers, five answers — this is the baseline, not the goal"


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
# Differences that are DRIFT — a consolidation should REMOVE these
# --------------------------------------------------------------------------------------


def test_DRIFT_get_hierarchy_reports_a_var_positional_as_a_configurable_path() -> None:
    """``loaders`` is a ``*args`` name: it can never be passed by keyword.

    ``get_hierarchy`` is what a CLI builds dotted override flags from, so this
    publishes ``--loaders`` — a flag whose value Python rejects at the call.
    ``_ctor_params`` reasons about this exact parameter name in its docstring and
    excludes it; ``get_hierarchy``'s own filter (``self``/``cls``/``args``/``kwargs``
    by NAME, not by kind) never got the same treatment.

    EXPECTED FIX: ``slots()`` classifies by ``inspect.Parameter.kind``, and this
    assertion flips to ``not in``.
    """
    assert "loaders" in _leaves(get_hierarchy(Spread)), "pinning the defect, not blessing it"


def test_DRIFT_declares_key_answers_differently_for_the_same_parameter_kind() -> None:
    """One function, two code paths, opposite answers about ``*loaders``.

    ``declares_key`` short-circuits to the accept-list for a target WITH an
    accept-list, and runs its own kind-filtered walk only for a ``**kwargs``
    target. So the identical ``*loaders`` parameter is "declared" or "not
    declared" depending on whether the class also happens to take ``**kwargs``.

    It is the public predicate ``AGENTS.md`` tells front-ends to call instead of
    re-deriving settability — so the drift is in the answer confluid hands out to
    stop exactly this kind of drift.

    EXPECTED FIX: both paths project from one ``slots()`` enumeration and agree.
    """
    assert declares_key(Spread, "loaders") is True, "via the accept-list"
    assert declares_key(Forwards, "loaders") is False, "via the inline kind-filtered walk"


def test_DRIFT_the_accept_list_admits_keys_the_constructor_can_never_receive() -> None:
    """``accepts_key`` says yes to ``loaders`` / ``pos_only``; the engine never passes them.

    They fall through to a post-init ``setattr``, creating an attribute nothing
    reads. For ``pos_only`` that is deliberate and documented (``configure()`` has
    always done it, so both paths agree); for ``loaders`` it is not — a ``*args``
    name is not a slot at all.

    EXPECTED FIX: ``slots()`` distinguishes the two kinds, and the accept-list
    keeps ``positional_only`` while dropping ``var_positional``.
    """
    assert accepts_key(Spread, "loaders") is True
    assert accepts_key(Spread, "pos_only") is True
    assert "loaders" not in (_ctor_params(Spread) or set())
    assert "pos_only" not in (_ctor_params(Spread) or set())
