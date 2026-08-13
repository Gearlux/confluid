"""What each subsystem thinks a class's configurable slots are — and why they differ.

Six readers ask "which slots does this target have". They gave **five different
answers** for one class until 2026-08-12, because five of them hand-rolled their
own "minus self/cls" filter with a different set of parameter-kind exclusions.
They now project from the ONE ``introspect.slots()`` enumeration, and the answers
collapse to **three** — one per question actually being asked:

===========================================  =========================  ==============================
answer                                        readers                    question
===========================================  =========================  ==============================
``['head', 'pos_only', 'lr']``                accept-list, to_pydantic,  what is CONFIGURABLE at all
                                              get_hierarchy
``['pos_only', 'lr']``                        input_specs                what the SIGNATURE declares
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

import pytest

from confluid import configurable, input_specs, to_pydantic
from confluid.broadcast import _get_acceptable_keys, accepts_key, declares_key
from confluid.engine import _ctor_params
from confluid.schema import get_hierarchy, get_hierarchy_from_instance


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


@configurable
class OrdinaryNames:
    """Params LITERALLY NAMED ``args`` / ``kwargs`` — ordinary keywords, nothing variadic.

    ``to_pydantic`` used to filter parameters by NAME (a ``_SKIP_PARAMS`` set carrying
    ``"args"``/``"kwargs"``), so these two were dropped from the generated model while
    every other reader kept them — and ``extra="forbid"`` then made the strict init
    policy REFUSE a legal constructor call. Only KINDS may exclude a parameter; a name
    never does.
    """

    def __init__(
        self, args: Optional[List[int]] = None, kwargs: Optional[Dict[str, int]] = None, lr: float = 1.0
    ) -> None:
        self.args = args
        self.kwargs = kwargs
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
        "get_hierarchy": ["head", "lr", "pos_only"],
        "to_pydantic": ["head", "lr", "pos_only"],
    }

    # Compared as SETS: ``input_specs`` reports in signature order by contract (a
    # GUI renders fields in the order the author declared them), the others sort.
    distinct = {frozenset(answer or ()) for answer in _readers(Spread).values()}
    assert len(distinct) == 3, "three questions, three answers — a reader may differ, but only on purpose"
    # ``get_hierarchy`` MOVED between answers on 2026-08-12: it now reports body
    # slots (option B), so a CLI's ``--docs`` lists the same knobs a form editor
    # offers. It used to sit with ``input_specs`` on the signature-only answer.


def test_a_param_literally_named_args_or_kwargs_is_a_slot_for_EVERY_reader() -> None:
    """Only a parameter's KIND may exclude it — its name never does.

    ``to_pydantic`` was the one reader still filtering by name: an ordinary keyword
    parameter named ``args`` or ``kwargs`` vanished from the generated model alone,
    and the strict init policy then refused ``OrdinaryNames(args=[1])`` with
    ``extra_forbidden`` — a fatal disagreement the other five readers never had.
    """
    assert _readers(OrdinaryNames) == {
        "accept-list": ["args", "kwargs", "lr"],
        "_ctor_params": ["args", "kwargs", "lr"],
        "input_specs": ["args", "kwargs", "lr"],
        "get_hierarchy": ["args", "kwargs", "lr"],
        "to_pydantic": ["args", "kwargs", "lr"],
    }


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


def test_deliberate_only_input_specs_stops_at_the_signature() -> None:
    """A body slot IS a configurable slot (the class-design convention, rule 4).

    The accept-list needs it so a key can land there, ``to_pydantic`` so a form
    offers it, and ``get_hierarchy`` so a CLI's ``--docs`` lists it (option B,
    2026-08-12 — it used to stop at the signature, so ``--docs`` showed fewer
    knobs before a config was flowed than after).

    ``input_specs`` alone still stops there, and that IS its question: it reports
    the I/O CONTRACT — what a caller must supply to construct the object — which
    a body slot by definition is not.
    """
    answers = _readers(Spread)

    assert "head" in (answers["accept-list"] or [])
    assert "head" in answers["to_pydantic"]
    assert "head" in answers["get_hierarchy"]
    assert "head" not in answers["input_specs"], "the constructor contract stops at the signature"


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


# --------------------------------------------------------------------------------------
# The two hierarchy walkers — what each ANSWERS, before the enumeration swap
# --------------------------------------------------------------------------------------
#
# ``TASKS.md`` described these as "~150 near-parallel lines … merge into one walker
# with a static/live policy". Measured, that premise does not hold: they are 266
# lines and they answer DIFFERENT questions, with different path shapes —
#
#     get_hierarchy(Root)              -> Root.leaf, Root.name
#     get_hierarchy_from_instance(o)   -> run.leaf._Leaf.lr, run.name, run.toggle, …
#
# different rooting (class name vs INSTANCE name), different depth, different
# membership. Merging them would unify two things that deliberately differ. What
# they genuinely share is the per-node slot enumeration, which ``slots()`` now
# provides — so the traversals stay separate and only the enumeration is shared.
#
# Both feed user-visible surfaces (a CLI's --help/--docs, hyperparameter logging),
# so their output is pinned here before it is touched.


class _PlainChild:
    """NOT @configurable — the walkers treat it differently, deliberately."""

    def __init__(self, depth: int = 3) -> None:
        self.depth = depth


@configurable
class _Leaf:
    def __init__(self, lr: float = 0.1) -> None:
        """A leaf with a body slot.

        Args:
            lr: Learning rate.
        """
        self.lr = lr
        self.tuned = None


@configurable
class _Root:
    def __init__(self, leaf: Any = None, name: str = "run") -> None:
        """A root whose child is typed ``Any``.

        Args:
            leaf: A nested configurable.
            name: The instance name — the live walker roots paths at it.
        """
        self.leaf = leaf if leaf is not None else _Leaf()
        self.name = name
        self.plain = _PlainChild()
        self.toggle = False


def test_the_static_walker_reports_the_TYPES_declared_paths() -> None:
    """``get_hierarchy`` reports what the TYPE declares and recurses on annotations.

    ``leaf: Any`` carries no configurable type, so the walk stops there rather than
    descending — where the live walker, seeing a real ``_Leaf`` in the attribute,
    does descend. That is the difference between the two walkers, and it survives
    option B: this one still reads declarations, not objects.
    """
    assert set(get_hierarchy(_Root)) == {"_Root.leaf", "_Root.name", "_Root.plain", "_Root.toggle"}


def test_the_live_walker_reports_the_OBJECTS_actual_graph() -> None:
    """``get_hierarchy_from_instance`` walks real attributes, rooted at the instance name.

    It recurses into the live ``_Leaf`` (which the static walk could not see through
    an ``Any`` annotation), reports its body slot, and reports the non-configurable
    child and the post-construction toggle as leaves.
    """
    paths = set(get_hierarchy_from_instance(_Root()))

    assert paths == {"run.leaf._Leaf.lr", "run.leaf._Leaf.tuned", "run.name", "run.plain", "run.toggle"}


def test_the_two_walkers_answer_different_questions_by_design() -> None:
    """Pinned so "merge them" is recognised as the wrong instruction it is.

    If these two ever produce the same shape, something has changed that the
    callers depend on — a CLI renders one of them per invocation.
    """
    static = set(get_hierarchy(_Root))
    live = set(get_hierarchy_from_instance(_Root()))

    assert static != live
    assert all(p.startswith("_Root.") for p in static), "rooted at the CLASS name"
    assert all(p.startswith("run.") for p in live), "rooted at the INSTANCE name"


# --------------------------------------------------------------------------------------
# The static walker reports BODY SLOTS too (option B, 2026-08-12)
# --------------------------------------------------------------------------------------
#
# ``get_hierarchy`` walked the signature only, so a CLI's ``--docs`` showed fewer
# knobs BEFORE a config was flowed than after — measured on ``matrainer``'s
# TrainerRunnable: 10 paths reported, 3 missing (``metrics_sidecar_path``,
# ``predictions_sink``, ``val_set``). Body slots are configurable by the
# class-design convention's rule 4, so omitting them was the walker lying by
# omission about its own contract.
#
# It could not be fixed before ``slots()`` carried body-slot TYPES (the walker
# recurses on a type), which landed the same day.
#
# Option B — report them AND recurse into configurable ones, exactly as ctor
# params behave. Option A (report as leaves, never recurse) was rejected as an
# arbitrary inconsistency: measured across 314 registered classes and 859 body
# slots, ZERO carry a directly-configurable type, so the two options produce
# identical output on every real class and only B is a rule you can state.


@configurable
class _BodyLeaf:
    def __init__(self, lr: float = 0.1) -> None:
        """Args:
        lr: Learning rate.
        """
        self.lr = lr


@configurable
class _BodyHost:
    def __init__(self, name: str = "run") -> None:
        """Args:
        name: An ordinary ctor param.
        """
        self.name = name
        self.child: _BodyLeaf = _BodyLeaf()  # CONFIGURABLE-typed body slot -> recurses
        self.note: Optional[str] = None  # typed leaf
        self.untyped = None  # no annotation -> Any leaf

    @property
    def derived(self) -> int:
        """A setterless property is never a config knob."""
        return 1


@configurable
class _ViaParam:
    def __init__(self, child: _BodyLeaf = None) -> None:  # type: ignore[assignment]
        """The ctor-param twin of ``_BodyHost.child`` — same type, other half.

        Args:
            child: A configurable nested node.
        """
        self.child = child


def test_the_static_walker_reports_typed_body_slots() -> None:
    """A body slot is a configurable slot; ``--docs`` must say so before a flow."""
    paths = set(get_hierarchy(_BodyHost))

    assert "_BodyHost.note" in paths, "a typed body slot is a path"
    assert "_BodyHost.untyped" in paths, "an unannotated one is too — its type is just Any"
    assert "_BodyHost.name" in paths, "ctor params are unaffected"


def test_a_configurable_typed_body_slot_RECURSES_like_a_ctor_param() -> None:
    """Option B: a slot is a slot, whichever half of the class declares it.

    The workspace has zero classes of this shape today (measured), so this pins
    the RULE rather than a case — which is the point: option A would have made
    the same slot expand or not depending on where it was declared.
    """
    paths = set(get_hierarchy(_BodyHost))

    assert "_BodyHost.child.lr" in paths, "recursed through the body slot"
    assert "_BodyHost.child" not in paths, "...so the container itself is not also a leaf"
    # The SAME path shape a ctor param of that type produces — verified side by
    # side, because "consistent with a ctor param" is the entire claim of option B.
    assert set(get_hierarchy(_ViaParam)) == {"_ViaParam.child.lr"}


def test_a_setterless_property_is_still_not_a_path() -> None:
    """Derived state is recomputed, never configured — the convention's rule 3."""
    assert not any(p.endswith(".derived") for p in get_hierarchy(_BodyHost))


def test_the_LIVE_walker_is_unchanged_by_this() -> None:
    """It always reported body slots; option B only brings the static one level."""
    live = set(get_hierarchy_from_instance(_BodyHost()))

    assert {"run.note", "run.untyped", "run.name"} <= live
    assert "run.child._BodyLeaf.lr" in live


class _ForeignBase:
    """NOT @configurable — stands in for ``keras.Model`` / ``LightningModule``.

    Their ``__init__`` bodies assign a dozen public internals. Measured across the
    workspace: 522 of 859 body slots are owned by a base like this — 342 from one
    ``Metric`` class alone.
    """

    def __init__(self) -> None:
        self.compiled = False
        self.train_function = None


@configurable
class _OnForeign(_ForeignBase):
    def __init__(self, width: int = 8) -> None:
        """Args:
        width: The class's own knob.
        """
        super().__init__()
        self.width = width
        self.mine: Optional[str] = None  # its OWN body slot


def test_the_static_walker_excludes_a_foreign_bases_body_slots() -> None:
    """``--docs`` must list config knobs, not a framework's internal state.

    When ``get_hierarchy`` began reporting body slots (option B) it reported them
    from the WHOLE MRO, so ``sonair.KerasConvNet`` listed twelve ``keras.Model``
    internals — ``predict_function``, ``compiled``, ``supports_jit`` … — as
    configurable options. ``to_pydantic`` already filtered these via ``Slot.owner``;
    this walker did not, which is the same enumeration read with two different
    rules — the drift the consolidation exists to prevent.

    The live walker never had the bug: it reads ``get_configurable_attrs``, which
    subtracts non-``@configurable`` ancestors via ``_get_parent_attr_blacklist``.
    """
    paths = {p.rsplit(".", 1)[-1] for p in get_hierarchy(_OnForeign)}

    assert "mine" in paths, "the class's OWN body slot is a knob"
    assert "width" in paths, "so is its ctor param"
    assert not ({"compiled", "train_function"} & paths), "the foreign base's internals are not"


def test_both_walkers_agree_about_foreign_body_slots() -> None:
    """The static and live walkers answer differently — but not about THIS."""
    static = {p.rsplit(".", 1)[-1] for p in get_hierarchy(_OnForeign)}
    live = {p.rsplit(".", 1)[-1] for p in get_hierarchy_from_instance(_OnForeign())}

    for leaked in ("compiled", "train_function"):
        assert leaked not in static and leaked not in live


# --------------------------------------------------------------------------------------
# The read-only property IS the opt-out — ``@ignore_config`` was removed (2026-08-13)
# --------------------------------------------------------------------------------------


@configurable
class _Protected:
    """The shape the removed ``@ignore_config`` decorator was always used on.

    Its one workspace usage was a doc example marking a read-only ``@property``.
    That marker was redundant: a setter-less property is skipped by
    ``introspect._non_signature_slots`` regardless, which is what ``@output``'s
    own docstring has always said ("``to_pydantic`` skips setter-less
    properties"). Measured across all four reader surfaces, the class below
    answered identically with and without the decorator.
    """

    def __init__(self, layers: int = 3) -> None:
        self.layers = layers
        self._secret = "hidden"

    @property
    def secret(self) -> str:
        return self._secret


def test_a_readonly_property_is_excluded_without_any_marker() -> None:
    """Every reader refuses it — no decorator involved.

    This is the behaviour that made deleting ``@ignore_config`` safe, so it is
    pinned here rather than inferred: if a future change starts reporting
    setter-less properties, the deletion becomes a regression and this fails.
    """
    assert not accepts_key(_Protected, "secret"), "a key must not be able to land on it"
    assert "secret" not in {p.rsplit(".", 1)[-1] for p in get_hierarchy(_Protected)}, "no CLI flag"
    assert "secret" not in to_pydantic(_Protected).model_fields, "no pydantic field"

    acceptable = _get_acceptable_keys(_Protected)
    assert acceptable is not None, "the class has a real accept-list (no **kwargs)"
    assert "secret" not in acceptable, "not in the accept-list"

    assert accepts_key(_Protected, "layers"), "the real knob beside it still works"


def test_a_yaml_key_aimed_at_a_readonly_property_does_not_change_it() -> None:
    """The user-visible half of the pin above, stated as a config.

    ``secret: leaked`` is warned about (``has no attribute 'secret'``) and the
    property keeps computing its own value — the protection a reader expects
    from the removed decorator, delivered by the property itself.
    """
    from confluid import load

    obj = load("p:\n  _target_: _Protected\n  layers: 5\n  secret: leaked\n")["p"]

    assert obj.layers == 5, "the real knob was applied"
    assert obj.secret == "hidden", "the read-only property is untouched"


def test_ignore_config_is_gone() -> None:
    """The decorator was DELETED, not deprecated (confluid 0.3.0).

    It was a no-op on every class that existed: 0 of 275 registered classes used
    it, and on the ``@property`` shape it was applied to it changed no reader's
    answer. Where it *did* bite — a class attribute shadowing an ``__init__``
    body slot — it silently discarded the user's YAML value instead of warning,
    which is the behaviour its removal fixes.

    An import must fail LOUDLY: a name that resolves to something quietly
    different is the failure mode this pin exists to prevent.
    """
    import confluid

    assert not hasattr(confluid, "ignore_config"), "should have been deleted"
    assert "ignore_config" not in confluid.__all__

    with pytest.raises(ImportError):
        from confluid import ignore_config  # type: ignore[attr-defined]  # noqa: F401 — deleted
