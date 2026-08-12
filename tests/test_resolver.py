import os
from typing import Any

import pytest

from confluid import configurable, get_registry
from confluid.resolver import Resolver


@pytest.fixture(autouse=True)
def setup_registry() -> None:
    get_registry().clear()

    @configurable
    class Model:
        def __init__(self, layers: int = 3) -> None:
            self.layers = layers

    @configurable
    class Trainer:
        def __init__(self, model: Any, lr: float = 0.01) -> None:
            self.model = model
            self.lr = lr


def test_resolve_env_var_default() -> None:
    resolver = Resolver()
    assert resolver.resolve("${MISSING_VAR:default_val}") == "default_val"


def test_resolve_string_reference() -> None:
    """Verify that !ref: strings are resolved against the context."""
    resolver = Resolver(context={"base_lr": 0.001})
    assert resolver.resolve("!ref:base_lr") == 0.001


def test_resolve_string_instantiation_marker() -> None:
    """Verify that !class: strings are resolved into eager Target Fluids."""
    from confluid.fluid import Target

    resolver = Resolver()
    marker = resolver.resolve("!class:Model(layers=10)")
    assert isinstance(marker, Target)
    assert marker.target == "Model"
    assert marker.kwargs["layers"] == 10


def test_recursive_string_instantiation_marker() -> None:
    """Verify nested !class: and !ref: strings produce nested Target Fluids."""
    from confluid.fluid import Target

    resolver = Resolver(context={"global_lr": 0.5})
    marker = resolver.resolve("!class:Trainer(model=!class:Model(layers=5), lr=!ref:global_lr)")

    assert isinstance(marker, Target)
    assert marker.target == "Trainer"
    assert marker.kwargs["lr"] == 0.5
    assert isinstance(marker.kwargs["model"], Target)
    assert marker.kwargs["model"].target == "Model"
    assert marker.kwargs["model"].kwargs["layers"] == 5


def test_resolve_empty_instantiation_marker() -> None:
    from confluid.fluid import Target

    resolver = Resolver()
    marker = resolver.resolve("!class:Model()")
    assert isinstance(marker, Target)
    assert marker.target == "Model"


def test_resolve_dict_and_list_strings() -> None:
    resolver = Resolver(context={"val": 42})
    data = {"a": "!ref:val", "b": ["!ref:val", "${HOME}"]}
    resolved = resolver.resolve(data)
    assert resolved["a"] == 42
    assert resolved["b"][0] == 42
    assert os.environ["HOME"] in resolved["b"][1]


# --- ${key.path} config-key string interpolation ---------------------------


def test_interpolate_config_key_whole_match_keeps_native_type() -> None:
    """A whole-string ``${a.b}`` returns the config value with its real type."""
    resolver = Resolver(context={"train": {"dataset": "RFUAV", "epochs": 5}})
    assert resolver.resolve("${train.dataset}") == "RFUAV"
    epochs = resolver.resolve("${train.epochs}")
    assert epochs == 5 and isinstance(epochs, int)


def test_interpolate_config_key_embedded_in_string() -> None:
    """Embedded ``${a.b}`` placeholders substitute as strings, mixing with env."""
    os.environ["CONFLUID_TEST_DATA_ROOT"] = "/data"
    try:
        resolver = Resolver(context={"train": {"dataset": "RFUAV", "version": "v3"}})
        out = resolver.resolve("${CONFLUID_TEST_DATA_ROOT}/${train.dataset}/${train.version}/x")
        assert out == "/data/RFUAV/v3/x"
    finally:
        del os.environ["CONFLUID_TEST_DATA_ROOT"]


def test_interpolate_config_key_bracket_index() -> None:
    resolver = Resolver(context={"items": [10, 20, 30]})
    assert resolver.resolve("val=${items[0]}") == "val=10"
    assert resolver.resolve("${items[-1]}") == 30


def test_interpolate_plain_name_is_still_env_var() -> None:
    """A name without a dot/bracket is an env var even if a config key exists."""
    resolver = Resolver(context={"HOME_DIR": "/from/config"})
    # No dot → env var lookup, NOT the config key of the same shape.
    assert resolver.resolve("${HOME_DIR:fallback}") == "fallback"


def test_interpolate_config_key_missing_uses_default() -> None:
    resolver = Resolver(context={"train": {"dataset": "RFUAV"}})
    assert resolver.resolve("${train.missing:fallback}") == "fallback"
    # A parsed default keeps its type on a whole match.
    port = resolver.resolve("${db.port:5432}")
    assert port == 5432 and isinstance(port, int)


def test_interpolate_config_key_missing_no_default_leaves_literal() -> None:
    resolver = Resolver(context={"train": {"dataset": "RFUAV"}})
    assert resolver.resolve("${train.nope}/x") == "${train.nope}/x"
    assert resolver.resolve("${train.nope}") == "${train.nope}"


def test_interpolate_config_key_prefers_local_context() -> None:
    """Sibling (local) keys win over global, mirroring ``!ref:``."""
    resolver = Resolver(context={"a": {"b": "global"}})
    data = {"a": {"b": "local"}, "out": "${a.b}"}
    resolved = resolver.resolve(data)
    assert resolved["out"] == "local"


def test_interpolate_non_scalar_target_left_literal() -> None:
    """Embedding a dict/list config value is a no-op (stays literal)."""
    resolver = Resolver(context={"cfg": {"nested": {"x": 1}}})
    assert resolver.resolve("prefix-${cfg.nested}") == "prefix-${cfg.nested}"


# --- bare $VAR environment expansion ---------------------------------------


def test_bare_env_var_expands_embedded_in_a_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """``$DATA_ROOT/sub`` expands like ``os.path.expandvars`` — one spelling, every entry path."""
    monkeypatch.setenv("DATA_ROOT", "/data")
    assert Resolver().resolve("$DATA_ROOT/sub") == "/data/sub"


def test_bare_env_var_unset_stays_literal(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unset variable leaves the ``$name`` text in place, mirroring ``os.path.expandvars``."""
    monkeypatch.delenv("CONFLUID_TEST_UNSET_VAR", raising=False)
    assert Resolver().resolve("$CONFLUID_TEST_UNSET_VAR/sub") == "$CONFLUID_TEST_UNSET_VAR/sub"


def test_bare_env_pass_leaves_marker_strings_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    """A marker STRING keeps its ``$`` text — the selector grammar owns it.

    The ``@axis=$key`` document-selector spells ``$`` in a target, and it is
    resolved at FLOW time against the active document. Expanding it here would
    burn an environment variable in over a config key, so the bare-``$`` pass
    skips every ``!``-prefixed string.

    The vehicle is a TOP-LEVEL string: the same text inside a marker's own kwargs
    is now refused outright (nothing ever honoured it there — see
    ``tests/test_plain_format.py`` → the quoted-string group).
    """
    from confluid.fluid import Target

    monkeypatch.setenv("FRAMEWORK", "/env-value")
    out = Resolver(context={}).resolve("!class:X@framework=$FRAMEWORK")

    assert isinstance(out, Target)
    assert out.target == "X@framework=$FRAMEWORK", "the selector survives to flow time"


def test_braced_default_behavior_unchanged_by_bare_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """``${VAR:default}`` keeps its meaning, and an unresolved ``${...}`` literal survives."""
    monkeypatch.delenv("CONFLUID_TEST_UNSET_VAR", raising=False)
    resolver = Resolver()
    assert resolver.resolve("${CONFLUID_TEST_UNSET_VAR:fallback}") == "fallback"
    # The bare regex cannot match ``${`` (the ``{`` sits outside its identifier
    # class), so the braced literal a ${...} miss leaves behind stays untouched.
    assert resolver.resolve("${CONFLUID_TEST_UNSET_VAR}/x") == "${CONFLUID_TEST_UNSET_VAR}/x"


def test_bare_env_var_expands_in_marker_kwargs_walk(monkeypatch: pytest.MonkeyPatch) -> None:
    """A marker kwargs mapping value ``"$DATA_ROOT/x"`` expands in place — and burns in."""
    from confluid.fluid import Target

    monkeypatch.setenv("DATA_ROOT", "/data")
    marker = Target("Whatever")
    marker.kwargs["path"] = "$DATA_ROOT/x"
    out = Resolver(context={}).resolve(marker)
    assert out is marker  # identity preserved — the flow memo keys on id()
    assert marker.kwargs["path"] == "/data/x"


def test_resolver_interpolates_marker_kwargs_in_place_and_keeps_references_late_bound(
    monkeypatch: "pytest.MonkeyPatch",
) -> None:
    """The kwargs walk substitutes text IN PLACE (identity kept) and touches nothing deferred."""
    import pytest  # noqa: F401  (annotation only)

    from confluid.fluid import Reference, Target
    from confluid.resolver import Resolver

    monkeypatch.setenv("CONFLUID_TEST_ROOT", "/store")
    marker = Target("Whatever")
    marker.kwargs.update(
        path="${CONFLUID_TEST_ROOT}/x",
        ref=Reference("elsewhere"),
        plain="not ${CONFLUID_TEST_ROOT} a marker",  # ordinary text still substitutes
    )
    out = Resolver(context={}).resolve(marker)
    assert out is marker  # identity preserved — the flow memo keys on id()
    assert marker.kwargs["path"] == "/store/x"
    assert isinstance(marker.kwargs["ref"], Reference)  # late-bound, untouched
    assert marker.kwargs["plain"] == "not /store a marker"
    # Idempotent: a second pass changes nothing.
    Resolver(context={}).resolve(marker)
    assert marker.kwargs["path"] == "/store/x"


def test_resolver_survives_a_cyclic_hand_built_marker() -> None:
    """A marker whose kwargs reach itself is walked once, not forever."""
    from confluid.fluid import Target
    from confluid.resolver import Resolver

    a = Target("A")
    b = Target("B")
    a.kwargs["child"] = b
    b.kwargs["parent"] = a  # cycle
    assert Resolver(context={}).resolve(a) is a


def test_quoted_class_string_uses_the_one_target_call_grammar() -> None:
    """The quoted-string marker parser matches ``_TARGET_CALL_RE`` — one grammar.

    It used to hand-roll ``"(" in s and s.endswith(")")``, accepting names no
    tag can carry (spaces, braces). A legal spelling parses identically; an
    illegal one now falls to a deferred ``Target`` marker instead of minting an
    eager ``Target`` under a name the registry can never resolve.
    """
    from confluid.fluid import Target

    resolver = Resolver(context={})

    legal = resolver.resolve("!class:a.b.Widget@role=metric(k=3)")
    assert isinstance(legal, Target)
    assert legal.target == "a.b.Widget@role=metric"
    assert legal.kwargs == {"k": 3}

    # A name the grammar rejects keeps the whole string as the target and parses
    # NO inline kwargs — the marker type is the same either way now that eager and
    # deferred are one class.
    illegal = resolver.resolve("!class:not a name(k=3)")
    assert isinstance(illegal, Target)
