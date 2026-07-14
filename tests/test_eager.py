"""Pins first-class support for EAGER classes (plain Python constructors).

An eager class receives its params at construction and may do real work with
them (the "somewhat default behaviour of all classes"), instead of following
the lazy-init/zero-arg convention. These tests pin the three pillars:

  * the load path constructs eagerly — required params, work in ``__init__``,
    and a clear YAML-located error when a required param is missing;
  * ``dump()`` round-trips an eager class via the captured ctor kwargs
    (``__confluid_kwargs__``, stamped by the engine at flow AND by the
    ``@configurable`` validation wrap at direct Python construction), with the
    live same-named attribute still preferred when it exists;
  * the ``@configurable(eager=True)`` stamp powers the ``configure()``
    staleness warning — a post-construction setattr of a ctor param cannot
    re-run the ``__init__`` work.

See confluid ``AGENTS.md`` → "Lazy Initialization & Zero-Arg Construction"
(the convention remains the workspace mandate; eager classes are the
supported alternative for plain-Python consumers) and ``docs/eager-classes.md``.
"""

from types import SimpleNamespace
from typing import Any, Optional

import pytest

from confluid import ConfluidError, configurable, configure, dump, get_registry, load

# ---------------------------------------------------------------------------
# Load path: eager construction from YAML
# ---------------------------------------------------------------------------


def test_required_param_eager_class_loads_from_yaml() -> None:
    """A plain class — required param, work in __init__ — loads unchanged."""

    @configurable(eager=True)
    class EagerReq:
        def __init__(self, n: int) -> None:
            self._doubled = 2 * n  # real work; the param is NOT stored verbatim

    cfg = load("thing: !class:EagerReq()\n  n: 21")
    assert isinstance(cfg["thing"], EagerReq)
    assert cfg["thing"]._doubled == 42


def test_missing_required_param_raises_located_error() -> None:
    """A missing required param fails with the class name, the param, and the YAML location."""

    @configurable(eager=True)
    class EagerMissing:
        def __init__(self, n: int) -> None:
            self._doubled = 2 * n

    with pytest.raises(ConfluidError) as excinfo:
        load("thing: !class:EagerMissing()")
    msg = str(excinfo.value)
    assert "EagerMissing" in msg
    assert "n" in msg
    assert ":1:" in msg  # format_yaml_loc line:column context


# ---------------------------------------------------------------------------
# Dump round-trip via captured ctor kwargs
# ---------------------------------------------------------------------------


def test_eager_transform_dump_load_round_trip() -> None:
    """dump() of a param-transforming eager class carries the ORIGINAL kwargs."""

    @configurable(eager=True)
    class EagerRT:
        def __init__(self, n: int) -> None:
            self._doubled = 2 * n

    obj = load("thing: !class:EagerRT()\n  n: 21")["thing"]
    text = dump(obj)
    assert "n: 21" in text
    reloaded = load(text)
    assert isinstance(reloaded, EagerRT)
    assert reloaded._doubled == obj._doubled == 42


def test_dump_prefers_live_attr_falls_back_to_captured() -> None:
    """Per param: live same-named attribute wins; captured kwargs fill the gaps."""

    @configurable(eager=True)
    class Mixed:
        def __init__(self, kept: int = 1, transformed: int = 2) -> None:
            self.kept = kept  # convention-style: stored verbatim
            self._t = 10 * transformed  # eager-style: transformed away

    obj = load("thing: !class:Mixed()\n  kept: 5\n  transformed: 7")["thing"]
    obj.kept = 99  # post-construction reconfiguration must survive the dump
    text = dump(obj)
    assert "kept: 99" in text  # live attribute preferred
    assert "transformed: 7" in text  # captured ctor kwarg fallback


def test_direct_python_construction_captures_kwargs() -> None:
    """The validation wrap stamps the explicitly-passed kwargs — positionals normalized, defaults excluded."""

    @configurable(eager=True)
    class EagerDirect:
        def __init__(self, n: int, m: int = 3) -> None:
            self._sum = n + m

    obj = EagerDirect(5)  # positional call, ``m`` left at its default
    assert getattr(obj, "__confluid_kwargs__") == {"n": 5}  # normalized to names; no default-bloat
    text = dump(obj)
    assert "n: 5" in text
    assert "m:" not in text  # unpassed defaults stay out of the dump
    reloaded = load(text)
    assert reloaded._sum == 8


def test_capture_runs_with_validation_off() -> None:
    """The kwargs capture is independent of the validation mode."""
    from confluid.validation import override_init_mode

    @configurable(eager=True)
    class EagerOff:
        def __init__(self, n: int) -> None:
            self._doubled = 2 * n

    with override_init_mode("off"):
        obj = EagerOff(4)
    assert getattr(obj, "__confluid_kwargs__") == {"n": 4}


def test_validate_false_class_degrades_gracefully() -> None:
    """validate=False + direct construction: no capture (documented degradation); YAML loads still round-trip."""

    @configurable(validate=False, eager=True)
    class EagerNoVal:
        def __init__(self, n: int = 1) -> None:
            self._doubled = 2 * n

    direct = EagerNoVal(3)
    assert not hasattr(direct, "__confluid_kwargs__")  # no validation wrap → no stamp
    assert "n" not in dump(direct)  # degrades to the live-attribute heuristic

    loaded = load("thing: !class:EagerNoVal()\n  n: 6")["thing"]
    assert loaded.__confluid_kwargs__ == {"n": 6}  # engine stamp covers the YAML path
    assert "n: 6" in dump(loaded)


def test_slots_instance_survives_capture() -> None:
    """A __slots__ class constructs fine — the stamp is silently skipped, slot attrs still dump."""

    @configurable(eager=True)
    class Slotted:
        __slots__ = ("n",)

        def __init__(self, n: int = 1) -> None:
            self.n = n

    obj = Slotted(7)
    assert not hasattr(obj, "__confluid_kwargs__")  # setattr rejected, degraded gracefully
    assert "n: 7" in dump(obj)  # slot-stored param still dumps via the live attr


def test_none_valued_param_round_trips() -> None:
    """Explicit None on a non-None-defaulted param dumps as null; None-on-None-default stays omitted."""

    @configurable
    class Opt:
        def __init__(self, a: Optional[int] = 5, b: Optional[int] = None) -> None:
            self.a = a
            self.b = b

    obj = load("thing: !class:Opt()\n  a: null")["thing"]
    assert obj.a is None
    text = dump(obj)
    assert "a: null" in text  # lossy omission fixed: default is 5, value is None
    assert "b:" not in text  # lossless omission kept: default is None, value is None
    reloaded = load(text)
    assert reloaded.a is None and reloaded.b is None


def test_nested_configurable_in_captured_kwargs_round_trips() -> None:
    """A nested configurable reachable ONLY through captured kwargs gets represented and reloads."""

    @configurable
    class Child:
        def __init__(self, x: int = 0) -> None:
            self.x = x

    @configurable(eager=True)
    class Holder:
        def __init__(self, child: Any = None) -> None:
            self._c = child  # stored under a PRIVATE attr — invisible to the live-attr walk

    obj = load("thing: !class:Holder()\n  child: !class:Child()\n    x: 3")["thing"]
    assert isinstance(obj._c, Child)
    text = dump(obj)
    assert "!class:Child" in text
    assert "x: 3" in text
    reloaded = load(text)
    assert isinstance(reloaded._c, Child)
    assert reloaded._c.x == 3


# ---------------------------------------------------------------------------
# The eager=True mark + the configure() staleness warning
# ---------------------------------------------------------------------------


def test_eager_mark_stamped_and_survives_partial_reregister() -> None:
    @configurable(eager=True)
    class EagerMark:
        def __init__(self, n: int = 1) -> None:
            self._doubled = 2 * n

    assert getattr(EagerMark, "__confluid_eager__") is True
    # A partial re-register (e.g. a snapshot restore forwarding only
    # name/category) must not drop the mark — the stamping-authority fallback.
    get_registry().register_class(EagerMark, name="EagerMark", category="op")
    assert getattr(EagerMark, "__confluid_eager__") is True


def _collect_configurator_warnings(monkeypatch: pytest.MonkeyPatch) -> list:
    """Monkeypatch the configurator logger (loggair doesn't reach caplog)."""
    import confluid.configurator as configurator_module

    seen: list = []
    monkeypatch.setattr(
        configurator_module,
        "logger",
        SimpleNamespace(warning=lambda msg: seen.append(msg), trace=lambda msg: None),
    )
    return seen


def test_configure_ctor_param_on_eager_instance_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    """configure() of a ctor param on an eager instance warns (work won't re-run) but still applies."""
    seen = _collect_configurator_warnings(monkeypatch)

    @configurable(eager=True)
    class EagerWarn:
        def __init__(self, n: int = 1) -> None:
            self.n = n
            self._doubled = 2 * n

    obj = EagerWarn(2)
    configure(obj, config={"n": 9})
    assert obj.n == 9  # the value is applied — warned, not blocked
    assert obj._doubled == 4  # ...and the __init__ work indeed did NOT re-run
    stale_warnings = [m for m in seen if "stale" in m]
    assert len(stale_warnings) == 1
    assert "EagerWarn" in stale_warnings[0] and "'n'" in stale_warnings[0]


def test_configure_body_attr_on_eager_instance_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Body attributes of an eager class stay freely reconfigurable — no warning."""
    seen = _collect_configurator_warnings(monkeypatch)

    @configurable(eager=True)
    class EagerBody:
        def __init__(self, n: int = 1) -> None:
            self._doubled = 2 * n
            self.mode = "fast"  # body slot, not a ctor param

    obj = EagerBody()
    configure(obj, config={"mode": "slow"})
    assert obj.mode == "slow"
    assert seen == []


def test_configure_ctor_param_on_unmarked_class_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unmarked (convention-following) classes keep today's silent behavior."""
    seen = _collect_configurator_warnings(monkeypatch)

    @configurable
    class Plain:
        def __init__(self, n: int = 1) -> None:
            self.n = n

    obj = Plain()
    configure(obj, config={"n": 9})
    assert obj.n == 9
    assert seen == []
