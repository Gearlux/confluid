"""``@configurable(broadcast_attrs=[...])`` — the AST-scan hardening escape hatch.

The broadcasting engine discovers post-init body attributes (``self.loss_fn = …``
inside ``__init__``) by AST-scanning the constructor SOURCE. In compiled /
frozen / zip deployments ``inspect.getsource`` fails, the scan silently returns
empty, and broadcasting silently stops working — a dev-vs-packaged behavioral
divergence. Two mitigations under test here:

* ``broadcast_attrs`` declares the names explicitly (stamped as
  ``__confluid_broadcast_attrs__``) and is UNIONED with the scanned names —
  redundant in dev checkouts, load-bearing when there is no source.
* When a ``@configurable`` class's own ``__init__`` has no readable source AND
  no declaration, the engine logs ONE ``logger.warning`` per class per process
  (not re-fired by materialize/resolve's per-pass cache clears).

Sourceless classes are built via ``exec()`` (``getsource`` raises ``OSError``
on the fake filename) — the same failure mode as a compiled deployment.
Loggair does not propagate into stdlib logging, so warnings are asserted by
monkeypatching the engine module logger with a ``SimpleNamespace`` collector
(the ``test_configurator.py`` pattern), never ``caplog``.
"""

from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

import confluid.engine as engine_module
from confluid import configurable, flow, load, materialize
from confluid.engine import _get_acceptable_keys, _get_post_init_attrs
from confluid.introspect import init_source_available

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _compile_sourceless(name: str) -> type:
    """Build a class whose ``__init__`` has NO retrievable source (via ``exec``).

    Mirrors a compiled/frozen deployment: ``inspect.getsource`` raises
    ``OSError`` for the fake filename, so the AST body scan sees nothing.
    The body assigns ``self.loss_fn`` — invisible to the scan by construction.
    """
    src = (
        f"class {name}:\n"
        f"    def __init__(self, model: str = 'default_model') -> None:\n"
        f"        self.model = model\n"
        f"        self.loss_fn = 'default_loss'\n"
    )
    ns: Dict[str, Any] = {}
    exec(compile(src, f"<sourceless-{name}>", "exec"), ns)
    cls: type = ns[name]
    return cls


def _capture_engine_warnings(monkeypatch: pytest.MonkeyPatch) -> List[str]:
    """Swap the engine module logger for a collector (loggair ≠ caplog)."""
    seen: List[str] = []
    monkeypatch.setattr(
        engine_module,
        "logger",
        SimpleNamespace(
            warning=lambda msg: seen.append(msg),
            trace=lambda msg: None,
            debug=lambda msg: None,
            info=lambda msg: None,
            error=lambda msg: None,
        ),
    )
    return seen


# ---------------------------------------------------------------------------
# Fixture classes (module-level: the AST scan needs importable source)
# ---------------------------------------------------------------------------


@configurable(broadcast_attrs=["declared_only"])
class _DeclaredPlusScanned:
    """Declares one extra broadcast attr on top of a scannable body."""

    def __init__(self, model: str = "default_model") -> None:
        self.model = model
        self.scanned_slot = "scanned_default"


# ---------------------------------------------------------------------------
# init_source_available — the probe itself
# ---------------------------------------------------------------------------


def test_init_source_available_true_for_module_level_class() -> None:
    assert init_source_available(_DeclaredPlusScanned.__dict__["__init__"]) is True


def test_init_source_available_false_for_exec_compiled_class() -> None:
    cls = configurable(_compile_sourceless("_ProbeSourceless"))
    # The validation wrapper is probed — getsource follows __wrapped__ to the
    # exec'd original, whose fake filename has no source on disk.
    assert init_source_available(cls.__dict__["__init__"]) is False


def test_init_source_available_false_for_builtins() -> None:
    assert init_source_available(dict.__init__) is False


# ---------------------------------------------------------------------------
# Union semantics: declared + scanned coexist, declaration never replaces
# ---------------------------------------------------------------------------


def test_declared_attrs_union_with_scanned_names() -> None:
    attrs = _get_post_init_attrs(_DeclaredPlusScanned)
    # Scanned body slots survive...
    assert {"model", "scanned_slot"}.issubset(attrs)
    # ...AND the declared-only name joins them (union, not replacement).
    assert "declared_only" in attrs


def test_declared_attrs_enter_accept_list() -> None:
    keys = _get_acceptable_keys(_DeclaredPlusScanned)
    assert keys is not None
    assert {"model", "scanned_slot", "declared_only"}.issubset(keys)


def test_stamp_is_a_tuple_on_the_class() -> None:
    assert _DeclaredPlusScanned.__confluid_broadcast_attrs__ == ("declared_only",)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Sourceless class WITH declaration: broadcasting works, no warning
# ---------------------------------------------------------------------------


def test_sourceless_declared_attr_broadcasts_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    warnings_seen = _capture_engine_warnings(monkeypatch)
    cls = configurable(broadcast_attrs=["loss_fn"])(_compile_sourceless("_SourcelessDeclaredE2E"))

    # Premise: the scan alone cannot see loss_fn (no source)...
    yaml_text = "loss_fn: custom_loss\ntrainer: !class:_SourcelessDeclaredE2E\n  model: my_model\n"
    cfg = load(yaml_text)
    trainer: Any = flow(cfg["trainer"])
    assert trainer.model == "my_model"
    # ...but the declaration keeps the top-level key flowing into the slot.
    assert trainer.loss_fn == "custom_loss"
    # (isinstance LAST — narrowing Any via a `type`-typed variable degrades to object.)
    assert isinstance(trainer, cls)
    assert warnings_seen == []


def test_sourceless_undeclared_scan_is_empty() -> None:
    # The premise of the whole feature: without a declaration the sourceless
    # class's body slots are invisible (scan returns nothing for loss_fn).
    cls = configurable(_compile_sourceless("_SourcelessScanPremise"))
    attrs = _get_post_init_attrs(cls)
    assert "loss_fn" not in attrs


def test_sourceless_empty_declaration_silences_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    # broadcast_attrs=[] is a DELIBERATE "no post-init broadcast attrs"
    # declaration — distinct from undeclared (None); it must not warn.
    warnings_seen = _capture_engine_warnings(monkeypatch)
    cls = configurable(broadcast_attrs=[])(_compile_sourceless("_SourcelessEmptyDecl"))
    attrs = _get_post_init_attrs(cls)
    assert "loss_fn" not in attrs
    assert warnings_seen == []


# ---------------------------------------------------------------------------
# Sourceless class WITHOUT declaration: ONE warning per class per process
# ---------------------------------------------------------------------------


def test_sourceless_undeclared_warns_exactly_once_across_two_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    warnings_seen = _capture_engine_warnings(monkeypatch)
    cls = configurable(_compile_sourceless("_SourcelessUndeclaredWarns"))

    config = {"loss_fn": "custom_loss", "trainer": load("trainer: !class:_SourcelessUndeclaredWarns")["trainer"]}
    # Two materialize passes: each clears the per-pass attr caches, so
    # _get_post_init_attrs recomputes — but the warned-set is NOT cleared,
    # so the diagnostic fires exactly once.
    materialize(dict(config), context=dict(config))
    materialize(dict(config), context=dict(config))

    mine = [msg for msg in warnings_seen if "_SourcelessUndeclaredWarns" in msg]
    assert len(mine) == 1
    assert "cannot scan __init__ body" in mine[0]
    assert "broadcast_attrs" in mine[0]

    # And the invisible slot indeed did NOT receive the broadcast (the
    # divergence the warning is about). The no-paren ``!class:`` marker stays
    # a deferred Class through materialize — flow it explicitly to inspect.
    result = materialize(dict(config), context=dict(config))
    trainer: Any = flow(result["trainer"])
    assert trainer.loss_fn == "default_loss"
    assert isinstance(trainer, cls)


def test_scannable_class_never_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    # A normal module-level class (readable source, undeclared) stays silent —
    # the warning is packaged-mode-only. MRO parents with unreadable source
    # (builtins) are also silent by construction.
    warnings_seen = _capture_engine_warnings(monkeypatch)

    @configurable
    class _ScannableQuiet:
        def __init__(self, x: int = 1) -> None:
            self.x = x
            self.slot = "s"

    attrs = _get_post_init_attrs(_ScannableQuiet)
    assert "slot" in attrs
    assert warnings_seen == []


if __name__ == "__main__":
    pytest.main([__file__])
