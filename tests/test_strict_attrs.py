"""``@configurable(strict_attrs=True)`` — refuse keys the class declares nowhere.

Confluid is permissive by default, and deliberately so: ``_apply_post_init_attrs``
exists to assign kwargs the constructor did not take, which is how the
post-construction toggle pattern works. Since 2026-08-12 an undeclared key WARNS
and records ``"unknown-attribute"`` on both paths (option B1), but it still lands.

This mark is the opt-in that turns that warning into a refusal, for a class whose
author wants its config surface closed. "Declared" is the accept-list —
constructor parameters (or the callable's own signature for a builder function),
public settable class attributes, and ``__init__``-body slots — i.e. exactly
``introspect.slot_names(target, _DECLARED_KINDS)``.

Two decisions, taken 2026-08-12:

* ``register()`` carries it too, for the reason it carries ``broadcast=False`` —
  a class you do not own is the one you cannot fix by editing its declaration.
* It binds ``configure()`` as well as the load path. A mark that meant different
  things on the two paths would be the exact asymmetry this round removed.
"""

from typing import Any, Optional

import pytest

from confluid import ConfigurationError, configurable, configure, get_registry, load, marks, register


@pytest.fixture(autouse=True)
def setup_registry() -> None:
    get_registry().clear()


def _strict_cls() -> type:
    """A closed class: two declared slots, nothing else may be set."""

    @configurable(strict_attrs=True)
    class Closed:
        def __init__(self, epochs: int = 10) -> None:
            self.epochs = epochs
            self.head: Optional[str] = None  # a body slot IS declared

    return Closed


def _open_cls() -> type:
    """The same class WITHOUT the mark — the default, and it must stay permissive."""

    @configurable
    class Open:
        def __init__(self, epochs: int = 10) -> None:
            self.epochs = epochs

    return Open


# --------------------------------------------------------------------------------------
# What the mark refuses
# --------------------------------------------------------------------------------------


def test_an_undeclared_key_on_the_marker_raises() -> None:
    """The commonest spelling — written on the node, aimed at it unambiguously."""
    _strict_cls()

    with pytest.raises(ConfigurationError) as excinfo:
        load("c:\n  _target_: Closed\n  epochz: 5\n")

    message = str(excinfo.value)
    assert "epochz" in message
    assert "strict_attrs" in message, "name the mark that caused the refusal"
    assert "epochs" in message, "and what the class DOES declare"


def test_an_undeclared_key_in_a_class_block_raises() -> None:
    """The other addressed spelling reaches a different code path — both are closed."""
    _strict_cls()

    with pytest.raises(ConfigurationError, match="epochz"):
        load("Closed:\n  epochz: 5\nc:\n  _target_: Closed\n")


def test_configure_refuses_it_too() -> None:
    """The mark binds BOTH paths, or it means two different things.

    ``configure()`` already warned and dropped the key; on a strict class it now
    raises, matching the load path.
    """
    Closed = _strict_cls()

    with pytest.raises(ConfigurationError, match="epochz"):
        configure(Closed(), config="Closed:\n  epochz: 5\n")


def test_the_refusal_names_the_yaml_position(tmp_path: Any) -> None:
    """A config error without a location is not a usable error (the workspace rule)."""
    _strict_cls()
    config = tmp_path / "exp.yaml"
    config.write_text("c:\n  _target_: Closed\n  epochz: 5\n")

    with pytest.raises(ConfigurationError) as excinfo:
        load(str(config))

    assert "exp.yaml" in str(excinfo.value)


# --------------------------------------------------------------------------------------
# What it must NOT refuse
# --------------------------------------------------------------------------------------


def test_a_declared_body_slot_still_lands() -> None:
    """A body slot is in the accept-list — closing the surface must not close THIS.

    The class-design convention's rule 4 exists to make body slots configurable;
    a mark that refused them would make the convention unusable.
    """
    _strict_cls()

    built = load("c:\n  _target_: Closed\n  head: hello\n")["c"]

    assert built.head == "hello"


def test_a_declared_ctor_param_still_lands() -> None:
    _strict_cls()

    assert load("c:\n  _target_: Closed\n  epochs: 3\n")["c"].epochs == 3


def test_a_BARE_undeclared_key_is_still_ignored() -> None:
    """A bare key cascades tree-wide and legitimately matches nothing.

    Refusing it would make a strict class unusable in any document that also
    configures something else — a sweep's top-level ``lr:`` necessarily misses it.
    """
    _strict_cls()

    built = load("epochz: 5\nc:\n  _target_: Closed\n")["c"]

    assert not hasattr(built, "epochz")
    assert built.epochs == 10


def test_an_UNMARKED_class_is_unchanged() -> None:
    """The default stays permissive — the mark is opt-in, and this is what it opts out of."""
    _open_cls()

    built = load("o:\n  _target_: Open\n  epochz: 5\n")["o"]

    assert built.epochz == 5, "still lands, still warns — B1 behaviour, untouched"


def test_a_kwargs_class_is_never_refused_even_when_marked() -> None:
    """``**kwargs`` has no accept-list, so nothing is undeclared for it.

    Marking such a class is meaningless rather than wrong — there is no closed
    surface to enforce. It must not become an error.
    """

    @configurable(strict_attrs=True, validate=False)
    class Catchall:
        def __init__(self, **extra: Any) -> None:
            self.extra = dict(extra)

    built = load("c:\n  _target_: Catchall\n  anything: 1\n")["c"]

    assert built.extra == {"anything": 1}


# --------------------------------------------------------------------------------------
# The mark itself
# --------------------------------------------------------------------------------------


def test_marks_reports_it() -> None:
    """``confluid.marks`` is the ONE public read surface for the stamps."""
    assert marks(_strict_cls()).strict_attrs is True
    assert marks(_open_cls()).strict_attrs is False


def test_register_carries_it_for_a_class_you_do_not_own() -> None:
    """The reason ``register`` carries ``broadcast=False`` applies here identically.

    A third-party class is exactly the one you cannot close by editing its
    declaration, so the control has to live where you register it.
    """

    class ThirdParty:
        def __init__(self, alpha: int = 1) -> None:
            self.alpha = alpha

    register(ThirdParty, strict_attrs=True)

    assert marks(ThirdParty).strict_attrs is True
    with pytest.raises(ConfigurationError, match="alpah"):
        load("t:\n  _target_: ThirdParty\n  alpah: 2\n")


def test_the_mark_is_inherited_by_a_subclass() -> None:
    """A subclass of a closed class stays closed unless it says otherwise.

    The stamp is a plain class attribute, so inheritance is what a reader expects
    — and a subclass that widens its surface can simply declare the new slots.
    """
    Closed = _strict_cls()

    @configurable
    class Narrower(Closed):  # type: ignore[valid-type,misc]
        def __init__(self, epochs: int = 10) -> None:
            super().__init__(epochs)

    assert marks(Narrower).strict_attrs is True
    with pytest.raises(ConfigurationError, match="epochz"):
        load("n:\n  _target_: Narrower\n  epochz: 5\n")


def _collate_for_strict(batch: Any) -> Any:
    return batch


def test_strict_accepts_a_none_valued_and_an_assigned_callable_class_attr() -> None:
    """N6 (BUGS-2026-08-19) — the docs' accept-list ("public settable class
    attributes") includes `timeout = None` and an assigned function; strict mode
    refused both while accepting `batch_size = 32` only because its value
    happened to be a non-None non-callable."""

    @configurable(strict_attrs=True)
    class StrictLoader:
        timeout = None
        collate_fn = _collate_for_strict

        def method(self) -> int:
            return 1

        def __init__(self, path: str = "") -> None:
            self.path = path

    loaded = load("l: {_target_: StrictLoader, timeout: 5, collate_fn: len}")["l"]
    assert loaded.timeout == 5 and loaded.collate_fn == "len"
    with pytest.raises(ConfigurationError):
        load("l: {_target_: StrictLoader, method: 5}")
