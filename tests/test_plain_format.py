"""The reserved-key format — plain YAML, no tags.

Every construct is pinned twice: once for the behaviour itself, and once as a
PARITY test asserting the tag spelling and the reserved-key spelling produce the
same result. The parity pins are what make the two front-ends safe to keep side
by side during the migration — a divergence is a test failure, not a surprise in
someone's training run.
"""

from typing import Any, Optional

import pytest
import yaml

from confluid import configurable, dump, flow, load
from confluid.exceptions import ConfigurationError, ScopeError
from confluid.fluid import Instance, Partial
from confluid.loader import ConfluidLoader


@configurable
class Box:
    def __init__(self, size: int = 1, label: str = "b", seed: int = 0) -> None:
        self.size, self.label, self.seed = size, label, seed


@configurable
class Opt:
    def __init__(self, params: Any = None, lr: float = 0.01) -> None:
        self.params, self.lr = params, lr


@configurable
class Holder:
    def __init__(self, box: Optional[Box] = None, seed: int = 0) -> None:
        self.box, self.seed = box, seed


# --------------------------------------------------------------------------- #
# The point of the whole exercise
# --------------------------------------------------------------------------- #


def test_a_full_document_is_readable_by_plain_yaml() -> None:
    """The acceptance test: a stock ``yaml.safe_load`` parses the format.

    This is exactly what fails on a tagged document (``ConstructorError: could
    not determine a constructor for the tag '!class:...'``), and it is why the
    format exists — external tooling (yq, editor schemas, linters) can read it.
    """
    doc = """
    seed: 7
    model: {_target_: Box, size: 3}
    opt: {_target_: Opt, _partial_: true, lr: 0.5}
    alias: ${ref:model}
    copy: {_clone_: model}
    variant:
      _scope_: mode=big
      model: {_target_: Box, size: 99}
    """
    raw = yaml.safe_load(doc)
    assert raw["model"] == {"_target_": "Box", "size": 3}
    assert raw["opt"]["_partial_"] is True


def test_a_tagged_document_still_fails_plain_yaml() -> None:
    """The baseline the new format improves on — kept so the contrast is pinned."""
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load("model: !class:Box(size=3)")


# --------------------------------------------------------------------------- #
# Construction: _target_ / _partial_
# --------------------------------------------------------------------------- #


def test_target_builds_eagerly() -> None:
    graph = load("model: {_target_: Box, size: 3}")
    assert isinstance(graph["model"], Box)
    assert graph["model"].size == 3


def test_partial_true_stays_deferred_and_flows_with_runtime_args() -> None:
    graph = load("opt: {_target_: Opt, _partial_: true, lr: 0.5}")
    assert isinstance(graph["opt"], Partial)
    built = flow(graph["opt"], params="MODEL_PARAMS")
    assert (built.params, built.lr) == ("MODEL_PARAMS", 0.5)


def test_partial_false_is_the_default() -> None:
    explicit = load("m: {_target_: Box, _partial_: false, size: 2}")["m"]
    implicit = load("m: {_target_: Box, size: 2}")["m"]
    assert isinstance(explicit, Box) and isinstance(implicit, Box)
    assert explicit.size == implicit.size == 2


def test_a_bare_key_broadcasts_into_a_reserved_key_marker() -> None:
    """Broadcasting is pass 7 and construction is pass 8, so an eagerly-built
    node receives cascading keys before its constructor runs — the property that
    made the third ``Class``-stub state unnecessary."""
    graph = load("seed: 7\nh: {_target_: Holder, box: {_target_: Box}}")
    assert graph["h"].seed == 7
    assert graph["h"].box.seed == 7


# --------------------------------------------------------------------------- #
# References
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "spelling",
    ["{_ref_: proto}", "${ref:proto}"],
    ids=["mapping", "scalar"],
)
def test_ref_shares_one_instance(spelling: str) -> None:
    graph = load(f"proto: {{_target_: Box, size: 3}}\na: {spelling}\nb: {spelling}")
    assert graph["a"] is graph["b"]
    assert graph["a"].size == 3


@pytest.mark.parametrize(
    "spelling",
    ["{_clone_: proto}", "${clone:proto}"],
    ids=["mapping", "scalar"],
)
def test_clone_makes_an_independent_copy(spelling: str) -> None:
    graph = load(f"proto: {{_target_: Box, size: 3}}\na: {{_ref_: proto}}\nc: {spelling}")
    assert graph["a"] is not graph["c"]
    assert graph["c"].size == 3


def test_clone_mapping_form_overrides_kwargs_on_the_copy() -> None:
    """The reason the mapping form is kept alongside the scalar shorthand: a
    scalar has nowhere to put the overrides."""
    graph = load("proto: {_target_: Box, size: 3}\nc: {_clone_: proto, size: 9}")
    assert graph["proto"].size == 3
    assert graph["c"].size == 9


def test_a_reference_cannot_be_embedded_in_a_string() -> None:
    with pytest.raises(ConfigurationError, match="cannot be embedded"):
        load('x: {_target_: Box}\ny: "pre-${ref:x}-post"')


def test_a_marker_resolver_needs_a_path() -> None:
    # ``${ref: }`` matches the placeholder grammar with a blank argument. A fully
    # empty ``${ref:}`` does not match ``${...}`` at all and stays literal, which
    # is how every unresolvable placeholder has always behaved.
    with pytest.raises(ConfigurationError, match="needs a target path"):
        load('y: "${ref: }"')


# --------------------------------------------------------------------------- #
# ${env:...}
# --------------------------------------------------------------------------- #


def test_env_resolver_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFLUID_TEST_ROOT", "/store")
    assert load("p: ${env:CONFLUID_TEST_ROOT}/sub")["p"] == "/store/sub"


def test_env_resolver_applies_a_comma_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONFLUID_TEST_ABSENT", raising=False)
    value = load("p: ${env:CONFLUID_TEST_ABSENT,8080}")["p"]
    assert value == 8080 and isinstance(value, int)


def test_env_resolver_leaves_an_unset_variable_literal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONFLUID_TEST_ABSENT", raising=False)
    assert load("p: ${env:CONFLUID_TEST_ABSENT}")["p"] == "${env:CONFLUID_TEST_ABSENT}"


def test_oc_env_alias_matches_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """``oc.env`` contains a dot, so it must be routed to the env resolver BEFORE
    the dotted-name test sends it to config-key lookup."""
    monkeypatch.setenv("CONFLUID_TEST_ROOT", "/store")
    assert load("p: ${oc.env:CONFLUID_TEST_ROOT}")["p"] == load("p: ${env:CONFLUID_TEST_ROOT}")["p"]


def test_env_resolver_composes_with_a_config_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFLUID_TEST_ROOT", "/store")
    graph = load("train: {dataset: RFUAV}\np: ${env:CONFLUID_TEST_ROOT}/${train.dataset}")
    assert graph["p"] == "/store/RFUAV"


# --------------------------------------------------------------------------- #
# Scopes
# --------------------------------------------------------------------------- #


SCOPED = """
default_block:
  _notscope_: model
  model: {_target_: Box, label: mlp}
cnn_block:
  _scope_: model=cnn
  model: {_target_: Box, label: cnn}
"""


def test_notscope_supplies_the_default() -> None:
    assert load(SCOPED)["model"].label == "mlp"


def test_scope_activation_swaps_the_block() -> None:
    assert load(SCOPED, scopes=["model=cnn"])["model"].label == "cnn"


def test_scope_function_call_form() -> None:
    doc = "b:\n  _scope_: model(cnn)\n  model: {_target_: Box, label: cnn}"
    assert load(doc, scopes=["model=cnn"])["model"].label == "cnn"


def test_boolean_scope() -> None:
    doc = "b:\n  _scope_: debug\n  level: DEBUG"
    assert "level" not in load(doc)
    assert load(doc, scopes=["debug"])["level"] == "DEBUG"


def test_content_key_extends_a_list() -> None:
    """A sequence body is the only way to write a conditional list ITEM, and a
    mapping cannot express it — which is what ``_content_`` is for."""
    doc = "ops:\n  - first\n  - {_scope_: extra=yes, _content_: [a, b]}\n  - last"
    assert load(doc)["ops"] == ["first", "last"]
    assert load(doc, scopes=["extra=yes"])["ops"] == ["first", "a", "b", "last"]


def test_content_key_substitutes_a_scalar() -> None:
    doc = "ops:\n  - first\n  - {_scope_: v, _content_: 42}"
    assert load(doc, scopes=["v"])["ops"] == ["first", 42]


def test_asking_for_an_undeclared_scope_value_still_raises() -> None:
    with pytest.raises(ScopeError, match="No scope block matches"):
        load(SCOPED, scopes=["model=typo"])


# --------------------------------------------------------------------------- #
# Malformed markers fail LOUDLY
#
# The tag syntax this replaces had the opposite failure mode: one space in
# ``!class:Model(a=1, b=2)`` produced a mangled target with both kwargs silently
# dropped and no error at load.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "doc, match",
    [
        ("x: {_target_: Box, _ref_: y}", "Conflicting reserved keys"),
        ("x: {_partial_: true, lr: 1}", "needs a discriminator key"),
        ("x: {_content_: [a]}", "needs a discriminator key"),
        ("x: {_target_: 42}", "_target_ must be a non-empty string"),
        ("x: {_target_: ''}", "_target_ must be a non-empty string"),
        ("x: {_target_: Box, _partial_: sometimes}", "_partial_ must be true or false"),
        ("x: {_ref_: 42}", "_ref_ must be a non-empty string"),
        ("x: {_scope_: ''}", "_scope_ must be a non-empty string"),
        ("x: {_scope_: a=b, _content_: [1], other: 2}", "mutually exclusive"),
    ],
)
def test_malformed_markers_raise(doc: str, match: str) -> None:
    with pytest.raises(ConfigurationError, match=match):
        load(doc)


def test_the_error_names_the_yaml_location() -> None:
    with pytest.raises(ConfigurationError, match=r"at .*:\d+:\d+"):
        load("x: {_target_: 42}")


# --------------------------------------------------------------------------- #
# The ordinary path is untouched
# --------------------------------------------------------------------------- #


def test_a_plain_mapping_stays_a_dict() -> None:
    graph = load("plain: {just: a, normal: mapping}")
    assert graph["plain"] == {"just": "a", "normal": "mapping"}


def test_yaml_anchors_and_aliases_still_work() -> None:
    """The reserved-key pass rides the DEFAULT MAPPING tag, so PyYAML's own
    construction must survive untouched for ordinary mappings.

    Asserted at the PARSE layer against stock ``SafeLoader``, because that is
    where the override could regress: ``load()`` rebuilds dicts in the resolver
    pass, so alias identity is not preserved end-to-end either way.
    """
    doc = "base: &base {a: 1}\nuse: *base"
    parsed = yaml.load(doc, Loader=ConfluidLoader)
    assert parsed["use"] is parsed["base"]
    assert parsed == yaml.safe_load(doc)
    assert load(doc)["use"] == {"a": 1}


def test_a_key_named_target_without_underscores_is_ordinary() -> None:
    """``target`` is a real parameter name in the wild; only ``_target_`` is reserved."""
    graph = load("plain: {target: something}")
    assert graph["plain"] == {"target": "something"}


# --------------------------------------------------------------------------- #
# Parity with the tag spelling — the migration safety net
# --------------------------------------------------------------------------- #


def _shape(value: Any) -> Any:
    """A structural, identity-free view of a loaded value, for cross-load comparison.

    Two separate ``load()`` calls necessarily produce distinct objects, so the
    graphs are compared by TYPE and attribute shape rather than by identity.
    """
    if isinstance(value, (Instance, Partial)):
        return (type(value).__name__, value.target, {k: _shape(v) for k, v in value.kwargs.items()})
    if isinstance(value, dict):
        return {k: _shape(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_shape(v) for v in value]
    if hasattr(value, "__dict__"):
        return (type(value).__name__, {k: _shape(v) for k, v in vars(value).items() if not k.startswith("__")})
    return value


@pytest.mark.parametrize(
    "tagged, plain",
    [
        ("m: !class:Box(size=3)", "m: {_target_: Box, size: 3}"),
        ("m: !class:Box()\n  size: 3", "m: {_target_: Box, size: 3}"),
        ("m: !lazy:Opt(lr=0.5)", "m: {_target_: Opt, _partial_: true, lr: 0.5}"),
        (
            "p: !class:Box(size=3)\na: !ref:p\nb: !ref:p",
            "p: {_target_: Box, size: 3}\na: {_ref_: p}\nb: {_ref_: p}",
        ),
        (
            "p: !class:Box(size=3)\na: !ref:p\nc: !clone:p",
            "p: {_target_: Box, size: 3}\na: ${ref:p}\nc: ${clone:p}",
        ),
        (
            "seed: 7\nh: !class:Holder()\n  box: !class:Box()",
            "seed: 7\nh: {_target_: Holder, box: {_target_: Box}}",
        ),
    ],
    ids=["inline-kwargs", "block-body", "lazy", "ref-identity", "ref-vs-clone", "broadcast"],
)
def test_both_spellings_agree(tagged: str, plain: str) -> None:
    """Both front-ends must produce the same object graph, key for key."""
    from_tag, from_plain = load(tagged), load(plain)
    assert from_tag.keys() == from_plain.keys()
    for key in from_tag:
        assert _shape(from_tag[key]) == _shape(from_plain[key]), key


def test_both_spellings_agree_on_reference_identity() -> None:
    for doc in ("p: !class:Box(size=3)\na: !ref:p\nb: !ref:p", "p: {_target_: Box, size: 3}\na: ${ref:p}\nb: ${ref:p}"):
        graph = load(doc)
        assert graph["a"] is graph["b"] is graph["p"]


def test_the_two_spellings_can_be_mixed_in_one_document() -> None:
    """Dual-support must survive a half-migrated file, which is the state every
    config passes through during the sweep."""
    graph = load("proto: !class:Box(size=3)\nnew: {_target_: Holder, box: {_ref_: proto}}")
    assert graph["new"].box is graph["proto"]


# --------------------------------------------------------------------------- #
# Round trip
# --------------------------------------------------------------------------- #


def test_a_reserved_key_document_round_trips_through_dump() -> None:
    """Mandated for every new feature: dump the configured graph and reload it."""
    graph = load("h: {_target_: Holder, seed: 5, box: {_target_: Box, size: 3}}")
    reloaded = load(dump(graph["h"]))
    assert isinstance(reloaded, Holder)
    assert reloaded.seed == 5
    assert isinstance(reloaded.box, Box)
    assert reloaded.box.size == 3
