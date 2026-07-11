"""Pins the broadcast-engine fixes for cache collision, identity-based
self-target detection, type-aware dict/list broadcasting, and the widened
AST post-init scan (now catches ``setattr(self, "literal", ...)``).

Each test corresponds to one of the failure modes flagged in the broadcast
review — the tests would all have passed-by-accident before the fixes
because the old behaviour was either over-skipping (cache collision,
same-name guard) or under-broadcasting (dict/list rejection, AST missing
setattr).
"""

from typing import Any, Dict, List, Mapping, Optional, Sequence

from confluid import Instance, configurable, flow, materialize, register
from confluid.loader import _get_acceptable_keys, _get_param_kinds, _get_post_init_attrs


def _inst(target: str, /, **kwargs: Any) -> Instance:
    """Build an Instance marker with kwargs assigned post-construction.

    ``target`` is positional-only so test kwargs literally named ``name`` or
    ``target`` can't collide with it."""
    marker = Instance(target)
    marker.kwargs.update(kwargs)
    return marker


# ---------------------------------------------------------------------------
# Cache collision: two classes sharing a short name across modules.
# ---------------------------------------------------------------------------


def test_acceptable_keys_cache_keyed_by_qualname_not_short_name() -> None:
    """Two distinct classes with the same ``__name__`` must each get their
    own accept-list — short-name caching used to silently fold one into
    the other when looked up by string.
    """

    @configurable
    class Trainer:
        def __init__(self, max_epochs: int = 1) -> None:
            self.max_epochs = max_epochs

    @configurable
    class _OtherTrainer:
        def __init__(self, batch_size: int = 1) -> None:
            self.batch_size = batch_size

    # Pretend the second class is also called "Trainer" for resolver lookup.
    _OtherTrainer.__name__ = "Trainer"  # type: ignore[attr-defined]

    register(Trainer)
    # Same-name registration would clobber via the registry; we go straight
    # through ``_get_acceptable_keys`` with the class object to side-step
    # the registry collision and confirm the cache keys differ.
    keys_a = _get_acceptable_keys(Trainer)
    keys_b = _get_acceptable_keys(_OtherTrainer)

    assert keys_a is not None and keys_b is not None
    assert "max_epochs" in keys_a and "batch_size" not in keys_a
    assert "batch_size" in keys_b and "max_epochs" not in keys_b


# ---------------------------------------------------------------------------
# Type-aware dict/list broadcasting.
# ---------------------------------------------------------------------------


def test_dict_value_broadcasts_when_annotated_dict() -> None:
    """A top-level dict broadcasts into a target whose ctor annotates the
    key as ``dict`` / ``Dict`` / ``Mapping``.
    """

    @configurable
    class WithDictParam:
        def __init__(self, extras: Dict[str, int] = {}) -> None:
            self.extras = extras

    register(WithDictParam)

    context = {"extras": {"a": 1, "b": 2}}
    data = _inst("WithDictParam")
    result = materialize(data, context=context)

    assert isinstance(result, WithDictParam)
    assert result.extras == {"a": 1, "b": 2}


def test_list_value_broadcasts_when_annotated_sequence() -> None:
    @configurable
    class WithListParam:
        def __init__(self, callbacks: Sequence[Any] = ()) -> None:
            self.callbacks = list(callbacks)

    register(WithListParam)

    context = {"callbacks": ["a", "b"]}
    data = _inst("WithListParam")
    result = materialize(data, context=context)

    assert isinstance(result, WithListParam)
    assert result.callbacks == ["a", "b"]


def test_dict_value_does_NOT_broadcast_when_param_is_scalar() -> None:
    """Legacy behaviour preserved: dict values stay as config sub-blocks
    when the target param is not annotated as a mapping. Otherwise plain
    YAML nesting would accidentally land as a value.
    """

    @configurable
    class WithScalarParam:
        def __init__(self, name: str = "default") -> None:
            self.name = name

    register(WithScalarParam)

    # A dict value at a matching key MUST NOT be pushed in as the value —
    # `name` is annotated str, so the dict is treated as a config sub-block
    # (which has no class marker → effectively ignored).
    context = {"name": {"oops": "this is a dict"}}
    data = _inst("WithScalarParam")
    result = materialize(data, context=context)

    assert isinstance(result, WithScalarParam)
    assert result.name == "default"  # default preserved


def test_optional_dict_annotation_still_classifies_as_dict() -> None:
    """``Optional[Dict[...]]`` must classify as ``"dict"`` so the broadcast
    rule reaches union-typed params.
    """

    @configurable
    class WithOptionalDict:
        def __init__(self, extras: Optional[Mapping[str, int]] = None) -> None:
            self.extras = extras

    register(WithOptionalDict)
    kinds = _get_param_kinds(WithOptionalDict)
    assert kinds.get("extras") == "dict"

    context = {"extras": {"k": 9}}
    data = _inst("WithOptionalDict")
    result = materialize(data, context=context)
    assert isinstance(result, WithOptionalDict)
    assert result.extras == {"k": 9}


def test_optional_list_annotation_classifies_as_list() -> None:
    @configurable
    class WithOptionalList:
        def __init__(self, items: Optional[List[int]] = None) -> None:
            self.items = items

    register(WithOptionalList)
    kinds = _get_param_kinds(WithOptionalList)
    assert kinds.get("items") == "list"


# ---------------------------------------------------------------------------
# AST scan widening: setattr(self, "literal", ...).
# ---------------------------------------------------------------------------


def test_post_init_ast_scan_detects_literal_setattr() -> None:
    """A class that assigns post-init attrs via ``setattr(self, "x", ...)``
    must still expose ``x`` to the broadcaster.
    """

    @configurable
    class WithLiteralSetattr:
        def __init__(self, base: int = 0) -> None:
            self.base = base
            # Common pattern: bulk-set attrs from a config map.
            setattr(self, "extra_attr", 42)

    register(WithLiteralSetattr)
    attrs = _get_post_init_attrs(WithLiteralSetattr)
    assert "extra_attr" in attrs
    assert "base" in attrs  # plain assignment still detected

    # And it actually broadcasts:
    context = {"extra_attr": 99}
    data = _inst("WithLiteralSetattr")
    result = materialize(data, context=context)
    assert isinstance(result, WithLiteralSetattr)
    assert getattr(result, "extra_attr") == 99


def test_ast_scan_ignores_non_literal_setattr() -> None:
    """``setattr(self, variable_name, ...)`` is intentionally NOT detected —
    we don't try to guess what runtime strings will be. The attribute just
    won't show up in the broadcaster's accept-list; the user's domain code
    still works fine, it just can't be auto-overridden from YAML root.
    """

    @configurable
    class WithDynamicSetattr:
        def __init__(self) -> None:
            self.placeholder = None
            attr_name = "dynamic"
            setattr(self, attr_name, "value")  # not a string-literal arg

    register(WithDynamicSetattr)
    attrs = _get_post_init_attrs(WithDynamicSetattr)
    assert "placeholder" in attrs
    assert "dynamic" not in attrs


def test_ast_scan_skips_private_literal_setattr() -> None:
    @configurable
    class WithPrivateSetattr:
        def __init__(self) -> None:
            setattr(self, "_hidden", 1)

    register(WithPrivateSetattr)
    attrs = _get_post_init_attrs(WithPrivateSetattr)
    assert "_hidden" not in attrs


# ---------------------------------------------------------------------------
# Lazy: stays deferred, but receives broadcast kwargs.
# ---------------------------------------------------------------------------


def test_lazy_stays_deferred_through_materialize() -> None:
    """A ``LazyClass`` value at the root of a config must NOT be flowed by
    ``materialize`` — domain code is responsible for calling
    ``flow(value, **runtime_kwargs)`` later.
    """
    from confluid import LazyClass

    class _Adam:
        def __init__(self, params: Any = None, lr: float = 0.01) -> None:
            self.params = params
            self.lr = lr

    register(_Adam)

    lazy = LazyClass(_Adam, lr=0.005)
    result = materialize(lazy)
    # ``materialize`` may copy the Fluid while running the broadcast pass;
    # the contract is "still deferred", not Python identity. The result
    # must remain a LazyClass with the original kwargs intact.
    assert isinstance(result, LazyClass)
    assert result.kwargs.get("lr") == 0.005

    # Explicit flow with runtime kwargs constructs the target.
    live = flow(lazy, params=["w1", "w2"])
    assert isinstance(live, _Adam)
    assert live.params == ["w1", "w2"]
    assert live.lr == 0.005


def test_lazy_inside_a_class_attribute_is_left_deferred() -> None:
    from confluid import LazyClass

    class _Optim:
        def __init__(self, params: Any = None, lr: float = 0.01) -> None:
            self.params = params
            self.lr = lr

    @configurable
    class TrainerLike:
        def __init__(self, optimizer: Any = None) -> None:
            self.optimizer = optimizer

    register(_Optim)
    register(TrainerLike)

    data = _inst("TrainerLike", optimizer=LazyClass(_Optim, lr=0.001))
    result = materialize(data)
    assert isinstance(result, TrainerLike)
    assert isinstance(result.optimizer, LazyClass)


def test_lazy_receives_broadcast_kwargs_like_class() -> None:
    """``!lazy:`` participates in broadcast just like ``!class:`` — the
    deferral only blocks construction, not kwarg merging.
    """
    from confluid import LazyClass

    class _Adam:
        def __init__(self, params: Any = None, lr: float = 0.01) -> None:
            self.params = params
            self.lr = lr

    register(_Adam)

    context = {"lr": 0.5, "optimizer": LazyClass(_Adam)}
    result = materialize(context, context=context)
    assert isinstance(result["optimizer"], LazyClass)
    # Broadcast pulled `lr` into the Lazy's kwargs.
    assert result["optimizer"].kwargs.get("lr") == 0.5


def test_lazy_yaml_tag_round_trip() -> None:
    """``!lazy:Foo(lr=1e-3)`` parses to a ``LazyClass`` and dumps back to
    ``!lazy:`` (not ``!class:``)."""
    from confluid import LazyClass, dump, load

    class _Adam:
        def __init__(self, params: Any = None, lr: float = 0.01) -> None:
            self.params = params
            self.lr = lr

    register(_Adam)

    yaml_text = "optimizer: !lazy:tests.test_broadcast_robustness._Adam\n  lr: 0.001\n"
    loaded = load(yaml_text, flow=False)
    assert isinstance(loaded["optimizer"], LazyClass)
    assert loaded["optimizer"].kwargs == {"lr": 0.001}

    # Round-trip through dump → load preserves the Lazy semantics.
    rendered = dump({"optimizer": loaded["optimizer"]})
    assert "!lazy:" in rendered
    assert "!class:" not in rendered

    reloaded = load(rendered, flow=False)
    assert isinstance(reloaded["optimizer"], LazyClass)


def test_lazy_inline_kwargs_form() -> None:
    """``!lazy:Adam(lr=0.01)`` inline-kwargs form works (mirrors !class:)."""
    from confluid import LazyClass, load

    class _Adam:
        def __init__(self, lr: float = 0.0) -> None:
            self.lr = lr

    register(_Adam)
    yaml_text = "opt: !lazy:tests.test_broadcast_robustness._Adam(lr=0.001)\n"
    loaded = load(yaml_text, flow=False)
    assert isinstance(loaded["opt"], LazyClass)
    assert loaded["opt"].kwargs == {"lr": 0.001}  # inline scalars are coerced (parse_value)


# ---------------------------------------------------------------------------
# Identity-based _same_target: two distinct classes with the same name in
# different modules must NOT be conflated.
# ---------------------------------------------------------------------------


def test_same_target_uses_class_identity_not_name() -> None:
    """A Fluid whose ``target`` is a bare short name shared with another
    registered class must NOT be considered "same" as that other class.

    Before the fix, ``"A" in (cls.__name__, qualified)`` matched any class
    whose short name was ``"A"`` — so a top-level ``A`` Fluid would be
    skipped from broadcasting into a sibling class ``A`` defined in a
    different module.
    """
    from confluid.loader import _same_target

    @configurable
    class A:
        pass

    @configurable
    class B:
        pass

    # Pretend B's short name happens to be "A" (bare-name collision). Only
    # A is registered under "A"; B's qualified name is still distinct.
    B.__name__ = "A"  # type: ignore[attr-defined]

    # Class objects always compare by identity — even with matching names.
    assert _same_target(B, A) is False
    assert _same_target(A, A) is True

    # Resolved fully-qualified names match their exact target only.
    a_q = f"{A.__module__}.{A.__qualname__}"
    b_q = f"{B.__module__}.{B.__qualname__}"
    assert _same_target(a_q, A) is True
    assert _same_target(b_q, B) is True
    # Cross-class qualified name does NOT match.
    assert _same_target(a_q, B) is False
    assert _same_target(b_q, A) is False

    # The bare short name "A" resolves to A (registry winner), so
    # ``_same_target("A", A)`` is True — but ``_same_target("A", B)`` is
    # NOT — exactly the cross-skip bug the fix targets.
    assert _same_target("A", B) is False
