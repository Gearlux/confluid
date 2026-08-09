"""Equivalence harness + unit pins for the one-scanner refactor (Phase A).

``_reference_prepare_kwargs`` below is a VERBATIM copy of ``_prepare_kwargs``
as it stood before the scanner rewrite (2026-08-08). The equivalence test
captures every real ``_prepare_kwargs`` invocation made while ``resolve()``-ing
a corpus of documents spanning every spelling family, replays the captured
arguments through BOTH implementations, and asserts the returned views are
identical — values, key order, and scope tags.

The reference implementation lives in TESTS only and is DELETED at the end of
Phase A (a retained copy would be a third implementation of the rule — the
exact failure class the refactor removes).
"""

from typing import Any, Dict, Optional

import pytest

from confluid import LazyClass, NoBroadcast, configurable, resolve
from confluid.broadcast import (
    _broadcast_blocked_keys,
    _expand_block_keys,
    _get_acceptable_keys,
    _get_param_kinds,
    _KeyScope,
    _prepare_kwargs,
    _same_target,
    _scope_of,
    _View,
)
from confluid.fluid import Fluid
from confluid.lazy import Lazy
from confluid.registry import resolve_class
from confluid.state import _ENGINE_STATE

# --------------------------------------------------------------------------- #
# Corpus classes — one per receiver shape the drivers discriminate on.
# --------------------------------------------------------------------------- #


class _ScOptim:
    """A deferred-slot target (accepts lr/weight_decay)."""

    def __init__(self, lr: float = 0.01, weight_decay: float = 0.0) -> None:
        self.lr, self.weight_decay = lr, weight_decay


@configurable
class _ScChild:
    def __init__(self, power: int = 0, name: str = "", lr: float = 0.0) -> None:
        """A nested receiver.

        Args:
            power: A scalar knob.
            name: Instance name (enables instance-name block matching).
            lr: A knob shared with the trainer (broadcast contention).
        """
        self.power, self.name, self.lr = power, name, lr


@configurable
class _ScTrainer:
    def __init__(
        self,
        lr: float = 0.1,
        name: str = "",
        child: Any = None,
        extras: Optional[Dict[str, int]] = None,
    ) -> None:
        """The main corpus receiver: scalar, marker slot, dict-typed param, body slot.

        Args:
            lr: A scalar knob.
            name: Instance name.
            child: A nested marker slot.
            extras: A dict-typed param (drives the param-kind dict rule).
        """
        self.lr, self.name, self.child, self.extras = lr, name, child, extras
        self.optimizer: Lazy[Any] = LazyClass(_ScOptim, weight_decay=0.05)


@configurable
class _ScForwards:
    def __init__(self, **kwargs: Any) -> None:
        """A permissive receiver — no accept-list at all."""
        self.kwargs = dict(kwargs)


@configurable
class _ScPinned:
    def __init__(self, secret: NoBroadcast[str] = "s", open_knob: int = 0) -> None:
        """A receiver with a NoBroadcast param.

        Args:
            secret: Excluded from bare/glob cascade (addressed forms still work).
            open_knob: An ordinary broadcastable knob.
        """
        self.secret, self.open_knob = secret, open_knob


_CORPUS = [
    # bare scalars + a marker
    "lr: 0.5\ntrainer: !class:_ScTrainer\n",
    # class-name block
    "trainer: !class:_ScTrainer\n_ScTrainer:\n  lr: 0.7\n",
    # instance-name block
    "trainer: !class:_ScTrainer\n  name: main\nmain:\n  lr: 0.8\n",
    # dotted top-level key
    "trainer: !class:_ScTrainer\n_ScTrainer.lr: 0.9\n",
    # in-marker dotted key reaching a nested marker
    "trainer: !class:_ScTrainer\n  child: !class:_ScChild\n  'child.power': 5\n",
    # '*' glob one level; '**' glob floating
    "trainer: !class:_ScTrainer\n  child: !class:_ScChild\n'*':\n  lr: 0.2\n",
    "trainer: !class:_ScTrainer\n  child: !class:_ScChild\n'**':\n  lr: 0.3\n",
    # '**' rider + wrapper shield (unaccepted kwarg on the wrapper)
    "'**':\n  lr: 0.1\ntrainer: !class:_ScTrainer\n  child: !class:_ScChild\n  shield_key: 9\n",
    # deferred slot tuned by an addressed mapping, bare key competing BEFORE
    "lr: 0.9\ntrainer: !class:_ScTrainer\n  optimizer:\n    lr: 0.5\n",
    # ... and competing AFTER
    "trainer: !class:_ScTrainer\n  optimizer:\n    lr: 0.5\nlr: 0.9\n",
    # bare dict at the dict-typed param (D4 load side)
    "extras: {a: 1}\ntrainer: !class:_ScTrainer\n",
    # nested markers: same-name contention, two trainers
    "t1: !class:_ScTrainer\n  name: a\nt2: !class:_ScTrainer\n  name: b\nlr: 0.4\n",
    # a Fluid-valued bare key aimed at a declared slot
    "child: !class:_ScChild\ntrainer: !class:_ScTrainer\n",
    # **kwargs receiver: bare keys + an addressed block
    "anything: 1\nfw: !class:_ScForwards\n_ScForwards:\n  addressed: 2\n",
    # NoBroadcast: bare + glob refused, addressed block lands
    "secret: leak\npinned: !class:_ScPinned\n'**':\n  secret: leak2\n_ScPinned:\n  secret: ok\n  open_knob: 3\n",
    # Cls.inst.attr nested form
    "trainer: !class:_ScTrainer\n  name: main\n_ScTrainer:\n  main:\n    lr: 0.6\n",
    # deep glob: trainer.**.lr expanded spelling
    "trainer: !class:_ScTrainer\n  child: !class:_ScChild\ntrainer.**.lr: 0.25\n",
    # empty-ish: marker only
    "trainer: !class:_ScTrainer\n",
]


# --------------------------------------------------------------------------- #
# The reference implementation — VERBATIM pre-scanner ``_prepare_kwargs``.
# Deleted at the end of Phase A.
# --------------------------------------------------------------------------- #


def _reference_prepare_kwargs(  # noqa: C901
    cls_name: str,
    own_kwargs: Dict[str, Any],
    parent_context: Dict[str, Any],
    target: Any = None,
    self_obj: Any = None,
) -> "_View":
    if cls_name.endswith("()"):
        cls_name = cls_name[:-2]
    instance_name = own_kwargs.get("name")

    acceptable = _get_acceptable_keys(target or cls_name)
    target_cls = target if isinstance(target, type) else resolve_class(cls_name) if cls_name else None
    param_kinds = _get_param_kinds(target_cls or cls_name) if (target_cls or cls_name) else {}
    broadcast_blocked = _broadcast_blocked_keys(target_cls)
    block_names = {cls_name}
    if target_cls is not None:
        registered = target_cls.__dict__.get("__confluid_name__") if hasattr(target_cls, "__dict__") else None
        block_names.add(str(registered or getattr(target_cls, "__name__", "")))
    block_names.discard("")

    def _accepts(k: str, v: Any) -> bool:
        if isinstance(v, Fluid):
            if acceptable is None or k not in acceptable:
                return False
            if target_cls is not None and _same_target(v.target, target_cls):
                return False
            return True
        if isinstance(v, dict):
            if param_kinds.get(k) == "dict":
                return acceptable is None or k in acceptable
            return False
        if isinstance(v, list):
            if param_kinds.get(k) == "list":
                return acceptable is None or k in acceptable
            return False
        if acceptable is not None and k not in acceptable:
            return False
        return True

    merged = _View()
    self_unrolled = False
    report = _ENGINE_STATE.get().report
    origins: Dict[str, str] = {}

    def _mark_used(k: str, origin: str) -> None:
        if report is not None:
            report.mark_used(f"**.{k}" if origin == "glob '**'" else f"*.{k}" if origin == "glob '*'" else k)

    def _apply_gated(k: str, v: Any, origin: str, scope: _KeyScope) -> None:
        if broadcast_blocked is not None and k not in broadcast_blocked and _accepts(k, v):
            merged.set(k, v, scope)
            if report is not None:
                origins[k] = origin
                _mark_used(k, origin)

    def _hoist_routing(k: str, v: Dict[str, Any]) -> None:
        prev = merged.get(k)
        if isinstance(prev, dict):
            v = {**prev, **v}
        merged.set(k, v, _KeyScope.BARE if k == "**" else _KeyScope.STRICT)

    def _consume_block(block: Dict[str, Any], *, origin: str, gated: bool, floating: bool = False) -> None:
        for bk, bv in _expand_block_keys(block).items():
            if bk == "**" and isinstance(bv, dict):
                _consume_block(bv, origin="glob '**'", gated=True, floating=True)
                _hoist_routing("**", bv)
                continue
            if bk == "*" and isinstance(bv, dict):
                _hoist_routing("*", bv)
                continue
            if isinstance(bv, dict) and bk in (cls_name, instance_name) and (floating or not gated):
                _consume_block(bv, origin=f"block {bk!r}", gated=False)
                continue
            if isinstance(bv, dict) and not _accepts(bk, bv):
                if not gated and not floating and acceptable is not None and bk in acceptable:
                    merged.set(bk, bv, _KeyScope.EXACT)
                    if report is not None:
                        origins[bk] = origin
                    continue
                if not floating:
                    _hoist_routing(bk, bv)
                continue
            if gated:
                _apply_gated(bk, bv, origin, _KeyScope.EXACT)
            elif _accepts(bk, bv):
                merged.set(bk, bv, _KeyScope.EXACT)
                if report is not None:
                    origins[bk] = origin

    def _apply_own(kwargs: Dict[str, Any]) -> None:
        for k, v in _expand_block_keys(kwargs).items():
            if k == "**" and isinstance(v, dict):
                _consume_block(v, origin="glob '**'", gated=True, floating=True)
                _hoist_routing("**", v)
            elif k == "*" and isinstance(v, dict):
                _hoist_routing("*", v)
            elif isinstance(v, dict) and acceptable is not None and k not in acceptable:
                _hoist_routing(k, v)
            else:
                merged.set(k, v, _KeyScope.EXACT)
                origins.pop(k, None)

    for k, v in parent_context.items():
        if self_obj is not None and v is self_obj and not self_unrolled:
            _apply_own(own_kwargs)
            self_unrolled = True
            continue
        if isinstance(v, Fluid) and target_cls is not None and _same_target(v.target, target_cls):
            continue
        scope = _scope_of(parent_context, k)
        if scope is _KeyScope.EXACT:
            continue
        if k == "**" and isinstance(v, dict):
            _consume_block(v, origin="glob '**'", gated=True, floating=True)
            continue
        if k == "*" and isinstance(v, dict):
            _consume_block(v, origin="glob '*'", gated=True)
            continue
        if (k in block_names or k == instance_name) and isinstance(v, dict):
            if report is not None:
                report.mark_used(k)
            _consume_block(v, origin=f"block {k!r}", gated=False)
            continue
        if scope is _KeyScope.STRICT:
            continue
        _apply_gated(k, v, "bare", _KeyScope.BARE)

    if not self_unrolled:
        _apply_own(own_kwargs)

    return merged


# --------------------------------------------------------------------------- #
# The equivalence test — captures real invocations, replays both impls.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("doc_index", range(len(_CORPUS)))
def test_scanner_prepare_kwargs_matches_the_reference(doc_index: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every real ``_prepare_kwargs`` call over the corpus yields the reference view.

    Compared: values (``dict(view)``), KEY ORDER (document order is the
    precedence rule), and the ``_KeyScope`` side-table (it drives the child
    splice, ``_addressed_keys`` and the STRICT filter downstream).
    """
    from confluid import engine

    captured: list = []
    real = engine._prepare_kwargs

    def _spy(*args: Any, **kwargs: Any) -> Any:
        captured.append((args, kwargs))
        return real(*args, **kwargs)

    # Engine binds the name at import, so patch the ENGINE's reference.
    monkeypatch.setattr(engine, "_prepare_kwargs", _spy)
    resolve(_CORPUS[doc_index])
    assert captured, "corpus document produced no _prepare_kwargs calls"

    for args, kwargs in captured:
        ref = _reference_prepare_kwargs(*args, **kwargs)
        new = _prepare_kwargs(*args, **kwargs)
        assert dict(new) == dict(ref)
        assert list(new) == list(ref)  # document order IS the rule
        assert new.scopes == ref.scopes
        assert isinstance(new, _View)
