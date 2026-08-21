import os
from typing import Any

import pytest

from confluid import ConfigurationError, configurable, get_registry
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


def test_resolve_dict_and_list_strings() -> None:
    resolver = Resolver(context={"c": {"val": 42}})
    data = {"a": "${c.val}", "b": ["${c.val}", "${HOME}"]}  # a dotted name is a config key; a bare one an env var
    resolved = resolver.resolve(data)
    assert resolved["a"] == 42
    assert resolved["b"][0] == 42
    assert os.environ["HOME"] in resolved["b"][1]


def test_a_marker_written_as_a_string_is_refused_at_every_depth() -> None:
    """A marker is a tag or a reserved-key mapping; text that starts with a marker prefix is refused."""
    resolver = Resolver(context={"val": 42})
    for data in ("!ref:val", {"a": "!class:Model(layers=10)"}, {"b": ["!partial:Model"]}):
        with pytest.raises(ConfigurationError, match="quoted STRING"):
            resolver.resolve(data)


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


def test_bare_env_pass_expands_in_any_ordinary_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bare-``$`` pass has no exemptions: an ordinary value starting with ``!`` expands too
    (a tag TARGET's ``@axis=$key`` selector lives on the marker, never in a string value)."""
    monkeypatch.setenv("FRAMEWORK", "/env-value")
    assert Resolver(context={}).resolve("!important $FRAMEWORK") == "!important /env-value"


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


# --------------------------------------------------------------------------- found-null and int-keyed paths


def test_a_placeholder_to_a_NULL_value_resolves_to_none() -> None:
    """SR6 (BUGS-2026-08-19) — the walker said None for FOUND-null and for a miss
    alike, so `${a.b}` to a legal null stayed the literal text."""
    from confluid import load

    assert load("a: {b: null}\nuse: ${a.b}\n", until="document")["use"] is None


def test_int_keyed_tables_are_addressable_by_every_path_spelling() -> None:
    """SR7 — the literal-int segment stepped lists only; the same dict step the
    `idxref` branch always had covers `{1: DJI}` and `{'1': DJI}` alike."""
    from confluid import load

    doc = load("class_names: {1: DJI, 2: MAVIC}\nuse: ${class_names.1}\nuse2: ${class_names[2]}\n", until="document")
    assert doc["use"] == "DJI"
    assert doc["use2"] == "MAVIC"
    assert load("names: {'1': DJI}\nuse: ${names.1}\n", until="document")["use"] == "DJI"
    assert load("items: [a, b]\nuse: ${items.1}\n", until="document")["use"] == "b", "list indexing unchanged"


# --------------------------------------------------------------------------- interpolation family (PA6/PA7 + P8/P9)


def test_whole_string_container_hit_is_resolved_not_raw(monkeypatch: pytest.MonkeyPatch) -> None:
    """PA6 (BUGS-2026-08-19) — `${a.b}` naming a container returned the RAW subtree,
    unresolved placeholders and all; a config-path container hit now resolves."""
    monkeypatch.setenv("CONFLUID_TEST_ROOT", "/store")
    resolver = Resolver(context={"a": {"b": {"path": "${CONFLUID_TEST_ROOT}/x"}}})
    assert resolver.resolve("${a.b}") == {"path": "/store/x"}


def test_circular_container_placeholder_is_refused() -> None:
    """PA6 guard — a container reached through the placeholder currently resolving
    it is a cycle: refused, never silently emitted raw (it used to self-nest once)."""
    context = {"a": {"b": {"x": "${a.b}"}}}
    with pytest.raises(ConfigurationError, match="circular"):
        Resolver(context=context).resolve("${a.b}")


def test_two_placeholders_naming_one_container_both_resolve() -> None:
    """PA6 guard hygiene — the guard releases the container after each hit, so a
    second placeholder naming the same subtree is not a false cycle."""
    resolver = Resolver(context={"a": {"b": {"v": 1}}})
    out = resolver.resolve({"c": "${a.b}", "d": "${a.b}"})
    assert out["c"] == {"v": 1} and out["d"] == {"v": 1}


def test_empty_env_var_is_the_empty_string_in_every_spelling(monkeypatch: pytest.MonkeyPatch) -> None:
    """PA7 — a SET-but-empty variable read as unset: `${EMPTY}` -> None, `${EMPTY}/x`
    and `${env:EMPTY}/x` stayed literal; only bare `$EMPTY/x` already worked."""
    monkeypatch.setenv("CONFLUID_TEST_EMPTY", "")
    resolver = Resolver()
    assert resolver.resolve("${CONFLUID_TEST_EMPTY}") == ""
    assert resolver.resolve("${CONFLUID_TEST_EMPTY}/x") == "/x"
    assert resolver.resolve("${env:CONFLUID_TEST_EMPTY}/x") == "/x"
    assert resolver.resolve("$CONFLUID_TEST_EMPTY/x") == "/x"


def test_substituted_values_are_never_rescanned_for_bare_dollar(monkeypatch: pytest.MonkeyPatch) -> None:
    """P8 (BUGS-2026-08-13) — the embedded branch fed substituted VALUES back through
    the bare-`$` pass, expanding a `$HOME` inside an env value (injection channel)."""
    monkeypatch.setenv("CONFLUID_TEST_PW", "pa$HOME")
    resolver = Resolver()
    assert resolver.resolve("${env:CONFLUID_TEST_PW}/x") == "pa$HOME/x"
    assert resolver.resolve("${env:CONFLUID_TEST_PW}") == "pa$HOME"


def test_author_written_bare_dollar_still_expands_beside_a_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    """P8 con — only SUBSTITUTED text is exempt; a `$OTHER` the author wrote expands."""
    monkeypatch.setenv("CONFLUID_TEST_ROOT", "/store")
    monkeypatch.setenv("CONFLUID_TEST_OTHER", "zz")
    assert Resolver().resolve("${env:CONFLUID_TEST_ROOT}/$CONFLUID_TEST_OTHER") == "/store/zz"


def test_marker_kwargs_interpolate_once_per_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    """P9 (BUGS-2026-08-13) — a marker aliased at two slots was walked twice, and the
    second walk expanded placeholder-shaped text the FIRST walk substituted."""
    from confluid.fluid import Target

    monkeypatch.setenv("CONFLUID_TEST_INDIRECT", "${env:CONFLUID_TEST_SECRET}")
    monkeypatch.setenv("CONFLUID_TEST_SECRET", "hunter2")
    marker = Target("Whatever")
    marker.kwargs["path"] = "${env:CONFLUID_TEST_INDIRECT}x"
    Resolver(context={}).resolve({"proto": marker, "other": marker})
    assert marker.kwargs["path"] == "${env:CONFLUID_TEST_SECRET}x"


def test_double_dollar_is_a_literal_dollar(monkeypatch: pytest.MonkeyPatch) -> None:
    """CD10 — `$$` escapes interpolation: `$$NAME` is `$NAME` even when NAME is
    set, `$${a.b}` is the literal placeholder text, `$$5` is `$5`."""
    monkeypatch.setenv("CONFLUID_TEST_ROOT", "/store")
    resolver = Resolver(context={"a": {"b": 7}})
    assert resolver.resolve("$$CONFLUID_TEST_ROOT/x") == "$CONFLUID_TEST_ROOT/x"
    assert resolver.resolve("$${a.b}") == "${a.b}"
    assert resolver.resolve("$$5") == "$5"
    assert resolver.resolve("a$$b") == "a$b"
    # beside the escape, live spellings still fire
    assert resolver.resolve("${a.b} costs $$5") == "7 costs $5"
