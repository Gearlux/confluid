"""The reserved-key format — plain YAML, no tags.

Every construct is pinned twice: once for the behaviour itself, and once as a
PARITY test asserting the tag spelling and the reserved-key spelling produce the
same result. The parity pins are what make the two front-ends safe to keep side
by side during the migration — a divergence is a test failure, not a surprise in
someone's training run.
"""

import warnings
from collections import Counter
from pathlib import Path
from typing import Any, Optional

import pytest
import yaml

from confluid import configurable, dump, flow, load, load_config
from confluid.exceptions import ConfigurationError, ScopeError
from confluid.fluid import Partial, Target
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
    variant:
      _scope_: {mode: big}
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
    made the third ``Target``-stub state unnecessary."""
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


# --------------------------------------------------------------------------- #
# Clone is REMOVED (user ruling 2026-08-15) — every spelling refuses loudly
#
# `_clone_` / `${clone:}` / the `!clone:` tag had zero users workspace-wide (one
# comment in confluid.example.yaml). Records 9 and 10 kept it as an "escape
# hatch"; the ruling supersedes them. Independence now has ONE spelling: write
# the marker twice — which is also what a preprocessor emits.
# --------------------------------------------------------------------------- #


def test_the_clone_reserved_key_is_refused_loudly() -> None:
    """A mapping carrying `_clone_` must not silently load as data with a
    literal `_clone_` key — that is exactly the degradation the format forbids."""
    with pytest.raises(ConfigurationError, match="_clone_") as exc:
        load("proto: {_target_: Box, size: 3}\nc: {_clone_: proto}")
    assert "<unicode string>:2" in str(exc.value), "the refusal must be located"


def test_the_clone_placeholder_is_refused_loudly() -> None:
    with pytest.raises(ConfigurationError, match="clone"):
        load("proto: {_target_: Box, size: 3}\nc: ${clone:proto}")


def test_the_clone_tag_no_longer_parses() -> None:
    """The deprecated tag spelling loses its constructor with the feature."""
    with pytest.raises((yaml.YAMLError, ConfigurationError)):
        load("proto: {_target_: Box, size: 3}\nc: !clone:proto")


def test_Clone_is_not_importable_from_the_public_surface() -> None:
    import confluid

    assert not hasattr(confluid, "Clone")
    assert "Clone" not in confluid.__all__


def test_the_reference_spellings_are_untouched_by_the_removal() -> None:
    """The con case: `_ref_` / `${ref:}` keep sharing identity exactly as before."""
    graph = load("proto: {_target_: Box, size: 3}\na: {_ref_: proto}\nb: ${ref:proto}")
    assert graph["a"] is graph["b"] is graph["proto"]


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
  _notscope_: {model: }
  model: {_target_: Box, label: mlp}
cnn_block:
  _scope_: {model: cnn}
  model: {_target_: Box, label: cnn}
"""


def test_notscope_supplies_the_default() -> None:
    assert load(SCOPED)["model"].label == "mlp"


def test_scope_activation_swaps_the_block() -> None:
    assert load(SCOPED, scopes=["model=cnn"])["model"].label == "cnn"


def test_several_dimensions_are_ANDed() -> None:
    """One block conditional on two dimensions — what previously needed a block
    nested inside another block."""
    doc = "b:\n  _scope_: {model: cnn, size: big}\n  model: {_target_: Box, label: both}"
    assert "model" not in load(doc, scopes=["model=cnn"])
    assert load(doc, scopes=["model=cnn", "size=big"])["model"].label == "both"


def test_boolean_scope() -> None:
    doc = "b:\n  _scope_: {debug: }\n  level: DEBUG"
    assert "level" not in load(doc)
    assert load(doc, scopes=["debug"])["level"] == "DEBUG"


def test_a_list_whose_first_item_is_a_scope_marker_is_a_block() -> None:
    """A mapping body splices its KEYS; a list body splices its ITEMS. Both start
    with ``_scope_:`` — only the container differs. This is the only way to write
    a conditional list ITEM, because a YAML node cannot be both kinds at once."""
    doc = "ops:\n  - first\n  - - _scope_: {extra: on_}\n    - a\n    - b\n  - last"
    assert load(doc)["ops"] == ["first", "last"]
    assert load(doc, scopes=["extra=on_"])["ops"] == ["first", "a", "b", "last"]


def test_a_one_item_list_body_is_the_scalar_case() -> None:
    """The old scalar body needs no spelling of its own — it is a list of one."""
    doc = "ops:\n  - first\n  - - _scope_: {v: }\n    - 42"
    assert load(doc)["ops"] == ["first"]
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
        ("x: {_partial_: true, lr: 1}", "_partial_ needs _target_ in the same mapping"),
        # BUGS-2026-08-13 P10 — the modifier is read by the _target_ branch alone,
        # so every other discriminator used to strip and discard it silently.
        ("p: {_target_: Box}\nx: {_ref_: p, _partial_: true}", "_partial_ only modifies _target_"),
        ("x:\n  _scope_: {mode: fast}\n  _partial_: true\n  a: 1", "_partial_ only modifies _target_"),
        ("x:\n  _notscope_: {mode: }\n  _partial_: true\n  a: 1", "_partial_ only modifies _target_"),
        ("x: {_target_: 42}", "_target_ must be a non-empty string"),
        ("x: {_target_: ''}", "_target_ must be a non-empty string"),
        ("x: {_target_: Box, _partial_: sometimes}", "_partial_ must be true or false"),
        ("x: {_ref_: 42}", "_ref_ must be a non-empty string"),
        ("x: {_scope_: ''}", "_scope_ must be a non-empty mapping"),
        ("x: {_scope_: not-a-mapping}", "_scope_ must be a non-empty mapping"),
        ("x: {_scope_: {extra: yes}}", "YAML boolean"),
    ],
)
def test_malformed_markers_raise(doc: str, match: str) -> None:
    with pytest.raises(ConfigurationError, match=match):
        load(doc)


# --------------------------------------------------------------------------- #
# `_partial_` beside a non-`_target_` discriminator (BUGS-2026-08-13 P10)
#
# `_partial_` is a MODIFIER, read by the `_target_` branch alone. Every other
# discriminator returns from `_reserved_to_marker` before that read, so the key
# was stripped from the body and thrown away — no error, no warning, and the
# marker flowed EAGERLY. What made it indefensible is that the lone-modifier
# error advertised the four spellings that do not work.
# --------------------------------------------------------------------------- #


def test_the_lone_modifier_error_names_only_the_key_that_works() -> None:
    """The message used to list `_ref_` / `_scope_` / `_notscope_` (and the since-removed `_clone_`) as
    valid partners for `_partial_` — the exact four the code then ignored."""
    with pytest.raises(ConfigurationError) as exc:
        load("x: {_partial_: true, lr: 1}")

    message = str(exc.value)
    assert "_partial_ needs _target_ in the same mapping" in message
    for advertised in ("_ref_", "_scope_", "_notscope_"):
        assert advertised not in message, f"the message still advertises {advertised}"


@pytest.mark.parametrize(
    "doc, hint",
    [
        ("p: {_target_: Box}\nx: {_ref_: p, _partial_: true}", "put it on the node _ref_ points at"),
        ("x:\n  _scope_: {mode: fast}\n  _partial_: true\n  a: 1", "put it on the marker inside the _scope_ block"),
    ],
)
def test_the_refusal_names_the_spelling_that_works(doc: str, hint: str) -> None:
    """A refusal that does not say what to write instead just relocates the problem."""
    with pytest.raises(ConfigurationError) as exc:
        load(doc)
    assert hint in str(exc.value)


def test_the_refusal_carries_the_yaml_location() -> None:
    """Every error raised while processing a document names its file:line:col."""
    with pytest.raises(ConfigurationError, match=r"<unicode string>:2:4"):
        load("p: {_target_: Box}\nx: {_ref_: p, _partial_: true}")


def test_a_block_that_never_activates_still_raises() -> None:
    """The refusal is a PARSE-time check, so it does not depend on activation.

    An unactivated block is dropped without its contents being examined, but the
    block node itself is still constructed — otherwise the same document would be
    valid or invalid depending on the run's `--scope` flags, which is the worst
    possible place to hide a malformed marker.
    """
    with pytest.raises(ConfigurationError, match="_partial_ only modifies _target_"):
        load("x:\n  _scope_: {mode: fast}\n  _partial_: true\n  a: 1")


def test_partial_beside_target_is_untouched() -> None:
    """The control: the one pairing that always worked, and must keep working."""
    marker = load("opt: {_target_: Opt, _partial_: true, lr: 0.5}", flow=False)["opt"]
    assert isinstance(marker, Partial)
    assert marker.kwargs["lr"] == 0.5


def test_the_working_spelling_for_a_deferred_referent() -> None:
    """What the refusal points at: deferral belongs on the node being CONSTRUCTED.

    Nothing is unexpressible — this is the spelling the error message names.
    """
    graph = load("proto: {_target_: Opt, _partial_: true, lr: 0.5}\nopt: {_ref_: proto}", flow=False)
    assert isinstance(graph["opt"], Partial)
    assert graph["opt"].kwargs["lr"] == 0.5


def test_a_scope_block_without_the_modifier_is_untouched() -> None:
    """The con case: the refusal must not fire on an ordinary scope block."""
    doc = "x:\n  _scope_: {mode: fast}\n  a: 1\n"
    assert load(doc, scopes=["mode=fast"], flow=False) == {"a": 1}
    assert load(doc, flow=False) == {}


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
# A reserved key delivered by a MERGE KEY (BUGS-2026-08-13 P2)
#
# ``<<:`` is core YAML 1.1 and the standard "many variants of one node" idiom,
# so the plain-format promise ("ordinary YAML that yaml.safe_load reads") has to
# cover it. The conversion itself always did: ``construct_mapping(deep=True)``
# resolves the merge, which is why a node carrying ONE literal reserved key
# picked up the anchor's ``_target_`` fine. Only the node-key GATE was blind —
# it read the literal scalar keys, and a merge key's literal key is ``<<``.
# --------------------------------------------------------------------------- #


def test_a_merge_key_delivering_target_converts_like_the_literal_spelling() -> None:
    """The P2 case: `<<: *b` where the anchor carries `_target_`.

    Before the fix this produced an inert ``dict`` carrying a literal
    ``_target_`` key, which ``flow()`` did not rescue either — it came out of
    the pipeline as ``{'_target_': 'collections.Counter'}``.
    """
    merged = load("base: &b\n  _target_: collections.Counter\nderived:\n  <<: *b\n", flow=False)
    literal = load("base:\n  _target_: collections.Counter\nderived:\n  _target_: collections.Counter\n", flow=False)

    assert isinstance(merged["derived"], Target)
    assert type(merged["derived"]) is type(literal["derived"])
    assert merged["derived"].target == literal["derived"].target
    assert flow(merged["derived"]) == flow(literal["derived"])


def test_a_merged_marker_takes_the_nodes_own_kwarg_override() -> None:
    """Merge-key precedence is unchanged: the node's own key beats the anchor's."""
    graph = load("base: &b\n  _target_: Box\n  size: 1\nderived:\n  <<: *b\n  size: 2\n")
    assert graph["derived"].size == 2


def test_a_merge_of_several_anchors_converts() -> None:
    """``<<: [*a, *b]`` — YAML allows a LIST of anchors; the gate must see through each."""
    graph = load(
        "one: &a\n  _target_: Box\ntwo: &c\n  size: 9\nderived:\n  <<: [*a, *c]\n",
    )
    assert graph["derived"].size == 9


def test_a_chained_merge_key_converts() -> None:
    """The anchor is itself built from a merge key — the walk has to recurse."""
    graph = load(
        "root: &r\n  _target_: collections.Counter\nmid: &m\n  <<: *r\nderived:\n  <<: *m\n",
        flow=False,
    )
    assert isinstance(graph["derived"], Target)


def test_a_merge_key_delivering_a_reserved_key_other_than_target_converts() -> None:
    """The gate tests the whole RESERVED_KEYS set, not ``_target_`` alone."""
    graph = load("proto: {_target_: collections.Counter}\nanchor: &a\n  _ref_: proto\nderived:\n  <<: *a\n")
    assert isinstance(graph["derived"], Counter)


def test_a_merge_key_plus_a_literal_reserved_key_still_converts() -> None:
    """The half that already worked, pinned so the gate change cannot regress it.

    One literal reserved key on the node was enough to get past the old gate,
    and the anchor's ``_target_`` was then picked up correctly — the measurement
    that proved the conversion machinery itself was never the problem.
    """
    graph = load("base: &b\n  _target_: collections.Counter\nderived:\n  <<: *b\n  _partial_: true\n", flow=False)
    assert isinstance(graph["derived"], Partial)
    assert graph["derived"].target == "collections.Counter"


def test_a_quoted_merge_key_is_ordinary_data() -> None:
    """False-positive guard: only PyYAML's MERGE tag is a merge key.

    A quoted ``"<<"`` is an ordinary string key — stock ``safe_load`` keeps it as
    data, so the gate must not treat its value as a node to look inside.
    """
    doc = 'base: &b\n  _target_: collections.Counter\nderived:\n  "<<": plain\n'
    assert load(doc, flow=False)["derived"] == {"<<": "plain"}
    assert yaml.safe_load(doc)["derived"] == {"<<": "plain"}


def test_an_ordinary_merge_key_is_untouched() -> None:
    """The con case: a merge key carrying no reserved key stays plain data.

    This is the fast path the gate exists to protect — the result must still
    match stock ``safe_load`` exactly.
    """
    doc = "defaults: &d\n  batch_size: 32\n  workers: 4\ntrain:\n  <<: *d\n  workers: 8\n"
    assert load(doc, flow=False)["train"] == {"batch_size": 32, "workers": 8}
    assert load(doc, flow=False)["train"] == yaml.safe_load(doc)["train"]


def test_a_self_referential_merge_key_terminates() -> None:
    """A node whose merge key aliases ITSELF composes fine in PyYAML, so the
    gate's walk needs a cycle guard or it recurses forever."""
    assert load("a: &x\n  <<: *x\n  k: 1\n", flow=False)["a"] == {"k": 1}


# --------------------------------------------------------------------------- #
# Parity with the tag spelling — the migration safety net
# --------------------------------------------------------------------------- #


def _shape(value: Any) -> Any:
    """A structural, identity-free view of a loaded value, for cross-load comparison.

    Two separate ``load()`` calls necessarily produce distinct objects, so the
    graphs are compared by TYPE and attribute shape rather than by identity.
    """
    if isinstance(value, (Target, Partial)):
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
            "seed: 7\nh: !class:Holder()\n  box: !class:Box()",
            "seed: 7\nh: {_target_: Holder, box: {_target_: Box}}",
        ),
    ],
    ids=["inline-kwargs", "block-body", "lazy", "ref-identity", "broadcast"],
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


# --------------------------------------------------------------------------------------
# The tag spelling is DEPRECATED — and says so
# --------------------------------------------------------------------------------------


def _load_capturing(text: str, tmp_path: Path, name: str = "legacy.yaml") -> list:
    """Load a config from a real FILE, returning the warnings it emitted.

    A file rather than a string because the notice names the document, and naming it
    is the whole point — a user with several configs needs to know WHICH to convert.
    """
    from confluid.loader import _TAG_SPELLING_WARNED

    path = tmp_path / name
    path.write_text(text)
    _TAG_SPELLING_WARNED.discard(str(path))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        load_config(str(path))
    return list(caught)


def test_a_tagged_document_announces_the_deprecation(tmp_path: Path) -> None:
    """The user must be TOLD, or the removal in 0.4.0 arrives as a surprise.

    A deprecation nobody sees is not a deprecation: this project's own alias round
    found consumers still on names deprecated for months, because nothing said so at
    runtime. ``FutureWarning`` rather than ``DeprecationWarning`` because Python shows
    it by DEFAULT — the audience is whoever wrote the YAML, not library code.
    """
    caught = _load_capturing("model: !class:Model\n  layers: 3\n", tmp_path)

    assert len(caught) == 1
    warning = caught[0]
    assert warning.category is FutureWarning
    message = str(warning.message)
    assert "legacy.yaml" in message, "must name the file to convert"
    assert "confluid-migrate" in message, "must name the tool that fixes it"
    assert "0.4.0" in message, "must name the release that removes it"


def test_a_string_loaded_document_is_not_told_to_run_an_uncopyable_command() -> None:
    """The remediation must be a command the reader can actually run — or no command.

    PyYAML names a string-loaded document ``<unicode string>``, and the notice
    interpolated that straight into its fix: ``confluid-migrate <unicode string>``.
    A message whose entire job is "here is what to do next" ended in an instruction
    that cannot be copied, pasted or acted on — and the project's own
    ``examples/performance.py`` (which builds its config as a string) printed it on
    every run. Naming the pseudo-file is fine; offering it as an argument is not.
    """
    from confluid.loader import _TAG_SPELLING_WARNED

    _TAG_SPELLING_WARNED.discard("<unicode string>")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        load("model: !class:Model\n  layers: 3\n", flow=False)

    message = str(caught[0].message)
    assert "confluid-migrate <unicode string>" not in message, "the command must not name a pseudo-file"
    assert "loaded from a string" in message, "say WHY no file is named"
    assert "_target_" in message, "still has to say what to write instead"
    assert "0.4.0" in message, "must name the release that removes it"


def test_the_notice_is_ONCE_PER_DOCUMENT_not_once_per_tag(tmp_path: Path) -> None:
    """A 400-line tagged config must not emit 400 warnings.

    Per-tag would bury the message it is trying to deliver; once-per-PROCESS would
    name the user's first config and stay silent about every other one.
    """
    caught = _load_capturing("a: !class:Model\nb: !lazy:SGD\nc: !ref:a\n", tmp_path, name="many.yaml")

    assert len(caught) == 1, f"one document, one notice — got {len(caught)}"


def test_the_reserved_key_format_is_silent(tmp_path: Path) -> None:
    """The migrated spelling is the destination, so it must warn about nothing."""
    caught = _load_capturing("model:\n  _target_: Model\n  layers: 3\n", tmp_path, name="plain.yaml")

    assert caught == []


# --------------------------------------------------------------------------- #
# A reserved key inside a DOTTED key is refused (BUGS-2026-08-13 P16)
#
# Conversion is parse-time; `expand_dotted_keys` runs post-parse. So `_target_`
# arriving by the dotted route is too late to make its node a marker, and the
# document keeps a literal `_target_` key as ordinary data that `flow()` does not
# rescue either. Every other key works dotted — including a kwarg merging INTO an
# already-nested marker — which is what makes the failure so easy to miss.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "doc, shape",
    [
        ("model._target_: Box", "top-level, reserved segment last"),
        ("outer:\n  inner._target_: Box", "nested — expansion is top-level only, so it stayed literal"),
        ("_target_.foo: Box", "reserved segment first"),
        ("a._target_.b: Box", "reserved segment in the middle"),
        ("m:\n  _target_: Box\n  sub._target_: Box", "inside a marker's kwargs"),
        ("items:\n  - model._target_: Box", "inside a list item"),
        ("proto:\n  _target_: Box\nalias._ref_: proto", "a dotted _ref_"),
    ],
)
def test_a_dotted_reserved_key_is_refused(doc: str, shape: str) -> None:
    """Each of these produced an inert value with a literal reserved key in it."""
    with pytest.raises(ConfigurationError, match="reserved key"):
        load(doc, flow=False)


def test_the_refusal_names_the_nested_spelling_and_the_location() -> None:
    with pytest.raises(ConfigurationError) as exc:
        load("model._target_: Box")

    message = str(exc.value)
    assert "model._target_" in message
    assert "_target_" in message and "nested" in message
    assert "<unicode string>:1:1" in message, message


def test_the_dotted_refusal_binds_the_TAG_spelling_too() -> None:
    with pytest.raises(ConfigurationError, match="reserved key"):
        load("m: !class:Box\n  sub._target_: Box\n")


# --- con cases: every dotted spelling that already worked stays working ------


def test_an_ordinary_dotted_key_still_expands() -> None:
    assert load("model.size: 3\nmodel.label: dotted", flow=False)["model"] == {"size": 3, "label": "dotted"}


def test_a_dotted_kwarg_still_merges_into_an_existing_marker() -> None:
    """The measurement that located the defect: the dotted spelling works for
    every key EXCEPT the one that decides the node is a marker."""
    graph = load("model:\n  _target_: Box\nmodel.size: 3")
    assert isinstance(graph["model"], Box)
    assert graph["model"].size == 3


def test_a_non_reserved_underscore_wrapped_key_is_untouched() -> None:
    """The false-positive guard: only the six reserved names are special."""
    assert load("model._custom_: 3", flow=False)["model"] == {"_custom_": 3}


def test_a_plain_reserved_key_is_untouched() -> None:
    assert isinstance(load("model:\n  _target_: Box", flow=False)["model"], Target)
