"""hydraide — the preprocessor (architecture record 19, phase 1).

``hydraide.emit(source)`` = ``dump(load(source, until="settled"))``: passes 1–7 followed by the
serializer, producing ONE plain-YAML document in which every marker carries its
final kwargs, every contest is settled, shared markers are anchors and deferral is
``_partial_: true``. The engine is untouched in this phase — these tests pin the
tool's contract, not the engine's.
"""

import subprocess
import sys
import textwrap
import warnings
from pathlib import Path
from typing import Any

import pytest
import yaml

from confluid import configurable, load
from confluid.exceptions import ConfigurationError
from confluid.fluid import PartialClass


@configurable
class HModel:
    def __init__(self, hidden: int = 16, lr: float = 0.0) -> None:
        self.hidden, self.lr = hidden, lr


@configurable
class HStream:
    def __init__(self, ops: Any = None, lr: float = 0.0) -> None:
        self.ops, self.lr = ops, lr


@configurable
class HResize:
    def __init__(self, size: int = 1) -> None:
        self.size = size


@configurable
class HAdam:
    def __init__(self, params: Any = None, lr: float = 0.0) -> None:
        self.params, self.lr = params, lr


PLAIN_BASE = """\
model:
  _target_: HModel
  hidden: 32
lr: 0.1
train_set:
  _target_: HStream
  ops: ${ref:preprocess}
preprocess:
  - {_target_: HResize, size: 224}
optimizer:
  _target_: HAdam
  _partial_: true
"""

TAG_BASE = """\
model: !class:HModel(hidden=32)
lr: 0.1
train_set: !class:HStream
  ops: !ref:preprocess
preprocess:
  - !class:HResize(size=224)
optimizer: !partial:HAdam
"""

EXPERIMENT = """\
include: {base}
model.hidden: 64
torch_only:
  _scope_: {{framework: torch}}
  lr: 0.3
"""


def _write_pair(tmp_path: Path, base_text: str, base_name: str) -> Path:
    (tmp_path / base_name).write_text(base_text)
    exp = tmp_path / f"exp_{base_name}"
    exp.write_text(EXPERIMENT.format(base=base_name))
    return exp


# --------------------------------------------------------------------------- #
# The document hydraide emits
# --------------------------------------------------------------------------- #


def test_the_emitted_document_carries_every_settled_value(tmp_path: Path) -> None:
    """Include spliced, dotted override applied, scoped bare key broadcast — all
    VISIBLE in the output, nothing left implicit."""
    from confluid.hydraide import emit

    doc = yaml.safe_load(emit(_write_pair(tmp_path, PLAIN_BASE, "base.yaml"), scopes=["framework=torch"]))

    assert doc["model"] == {"_target_": "HModel", "hidden": 64, "lr": 0.3}
    assert doc["train_set"]["lr"] == 0.3
    assert doc["optimizer"] == {"_target_": "HAdam", "_partial_": True, "lr": 0.3}
    assert "torch_only" not in doc, "the scope wrapper is consumed, not emitted"
    assert "include" not in doc, "the include directive is consumed, not emitted"


def test_the_emitted_document_is_plain_yaml() -> None:
    """The whole point: `yaml.safe_load` reads it — no tags anywhere."""
    from confluid.hydraide import emit

    text = emit(TAG_BASE)
    assert "!class" not in text and "!partial" not in text and "!ref" not in text
    yaml.safe_load(text)  # must not raise


def test_both_spellings_emit_BYTE_IDENTICAL_output(tmp_path: Path) -> None:
    """The phase-1 invariant: the third witness of two-spellings-one-IR."""
    from confluid.hydraide import emit

    plain = emit(_write_pair(tmp_path, PLAIN_BASE, "base_plain.yaml"), scopes=["framework=torch"])
    tagged = emit(_write_pair(tmp_path, TAG_BASE, "base_tag.yaml"), scopes=["framework=torch"])
    assert plain == tagged


def test_emit_is_IDEMPOTENT(tmp_path: Path) -> None:
    """emit(emit(x)) == emit(x) — the property `--check` rests on. Bare keys
    survive in the output and re-broadcast the same values on re-resolution."""
    from confluid.hydraide import emit

    once = emit(_write_pair(tmp_path, PLAIN_BASE, "base.yaml"), scopes=["framework=torch"])
    (tmp_path / "once.yaml").write_text(once)
    assert emit(tmp_path / "once.yaml") == once


def test_the_emitted_document_reloads_to_the_same_values(tmp_path: Path) -> None:
    from confluid.hydraide import emit

    text = emit(_write_pair(tmp_path, PLAIN_BASE, "base.yaml"), scopes=["framework=torch"])
    back = load(text)

    assert (back["model"].hidden, back["model"].lr) == (64, 0.3)
    assert back["train_set"].ops[0].size == 224
    assert isinstance(back["optimizer"], PartialClass)


# --------------------------------------------------------------------------- #
# Anchors — shared markers, named after where they live
# --------------------------------------------------------------------------- #


def test_a_shared_marker_is_emitted_as_a_NAMED_anchor(tmp_path: Path) -> None:
    """PyYAML would emit `&id001`; hydraide names the anchor after the shortest
    path the object has in the document, so the artefact reads."""
    from confluid.hydraide import emit

    text = emit(_write_pair(tmp_path, PLAIN_BASE, "base.yaml"))
    assert "&preprocess_0" in text and "*preprocess_0" in text
    assert "&id0" not in text


def test_identity_is_per_MARKER_not_per_container(tmp_path: Path) -> None:
    """Pinned as-is: `load(until="settled")` copies containers and keeps markers, so a list
    reached through `${ref:}` is emitted twice with its ELEMENT anchored, and the
    re-resolved document holds two lists sharing ONE marker.

    Asserted on the resolved tree rather than on built objects because `load()`
    builds only markers reachable through markers — a top-level list of markers
    stays unbuilt (BUGS-2026-08-13 F4, settled by phase 3's recursive
    instantiate, not here).
    """
    from confluid.hydraide import emit

    tree = load(emit(_write_pair(tmp_path, PLAIN_BASE, "base.yaml")), until="settled")
    assert tree["train_set"].kwargs["ops"] is not tree["preprocess"]
    assert tree["train_set"].kwargs["ops"][0] is tree["preprocess"][0]


def test_an_anchor_does_NOT_follow_an_include_overlay_tune(tmp_path: Path) -> None:
    """Pinned as-is, and it CORRECTS record 19's design note.

    An overlay that tunes an anchored node produces a tuned COPY at the key (P1
    copies deliberately); every alias site keeps the ORIGINAL marker. The
    2026-08-15 probe that "measured" the opposite named the consuming parameter
    `model` — the same as the top-level key — so the tuned marker reached it by
    bare-key BROADCAST, not through the anchor. With the parameter named `ops`
    the anchor's real behaviour shows: 32 at the alias, 99 at the key, two
    objects. `${ref:model}` is late-bound and DOES see 99.

    hydraide itself is unaffected — its output is already resolved and meets no
    further overlay — but this is why an anchor is not an authoring replacement
    for `${ref:}` under includes (phase 1b, TASKS.md), and it is pinned so the
    engine cannot silently change it either way without a decision.
    """
    from confluid.hydraide import emit

    (tmp_path / "base.yaml").write_text(
        "model: &m\n  _target_: HModel\n  hidden: 32\ntrainer:\n  _target_: HStream\n  ops: *m\n"
    )
    exp = tmp_path / "exp.yaml"
    exp.write_text("include: base.yaml\nmodel:\n  hidden: 99\n")

    doc = yaml.safe_load(emit(exp))
    assert doc["model"]["hidden"] == 99, "the key holds the tuned copy"
    assert doc["trainer"]["ops"]["hidden"] == 32, "the alias site kept the ORIGINAL"

    back = load(emit(exp))
    assert back["trainer"].ops is not back["model"]


# --------------------------------------------------------------------------- #
# Ruling 2 — tags are the preferred authoring form: no warning, `!partial:` exists
# --------------------------------------------------------------------------- #


def test_a_tagged_document_no_longer_warns() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # any FutureWarning becomes a failure
        load("m: !class:HModel(hidden=3)")


def test_partial_tag_parses_to_a_Partial() -> None:
    marker = load("opt: !partial:HAdam(lr=0.5)", until="document")["opt"]
    assert isinstance(marker, PartialClass)
    assert marker.kwargs == {"lr": 0.5}


def test_lazy_tag_still_parses_as_the_alias() -> None:
    assert isinstance(load("opt: !lazy:HAdam", until="document")["opt"], PartialClass)


def test_partial_and_lazy_are_the_same_marker() -> None:
    a = load("opt: !partial:HAdam(lr=0.5)", until="document")["opt"]
    b = load("opt: !lazy:HAdam(lr=0.5)", until="document")["opt"]
    assert (type(a), a.target, a.kwargs) == (type(b), b.target, b.kwargs)


# --------------------------------------------------------------------------- #
# Con cases — what phase 1 must NOT change
# --------------------------------------------------------------------------- #


def test_a_malformed_tag_still_raises_located() -> None:
    with pytest.raises(ConfigurationError):
        load("m: !class:HModel(hidden=1, lr=2)")  # the space: the classic tag mangling


def test_a_removed_clone_key_is_still_refused_by_the_tool() -> None:
    from confluid.hydraide import emit

    with pytest.raises(ConfigurationError, match="_clone_"):
        emit("p: {_target_: HModel}\nc: {_clone_: p}\n")


def test_the_engine_is_untouched_load_still_builds() -> None:
    """Phase 1 adds a tool; `load()` builds exactly as before."""
    graph = load(textwrap.dedent(TAG_BASE))
    assert isinstance(graph["model"], HModel) and graph["model"].hidden == 32
    assert isinstance(graph["optimizer"], PartialClass)


def test_reference_kwargs_are_folded_into_the_referent_in_the_emitted_document() -> None:
    """What `emit` writes is the settled document: the referent carries the tuned
    kwargs, the alias is a bare anchor — and it reloads to the same values."""
    from confluid import configurable, load
    from confluid.hydraide import emit

    @configurable
    class _Knob:
        def __init__(self, v: int = 1, k: int = 0) -> None:
            self.v = v
            self.k = k

    text = "proto: !class:_Knob {v: 1}\nuse: {_ref_: proto, k: 5}\n"
    out = emit(text)
    assert "k: 5" in out and "_ref_" not in out
    reloaded = load(out)
    assert reloaded["use"] is reloaded["proto"]
    assert (reloaded["proto"].v, reloaded["proto"].k) == (1, 5)
    assert emit(out) == out


def test_a_dotted_kwarg_position_survives_the_emit_round_trip() -> None:
    """BC4 option B: the position lives in a marker stamp plain YAML cannot
    carry, so `dump()` re-emits the dotted line after the keys it beat — the
    emitted artefact replays to the same answer, like a class block does."""
    from confluid import configurable, load
    from confluid.hydraide import emit

    @configurable
    class _Motor:
        def __init__(self, power: int = 0) -> None:
            self.power = power

    text = "t: !class:_Motor\n  power: 1\npower: 99\nt.power: 50\n"
    assert load(text)["t"].power == 50
    out = emit(text)
    assert load(out)["t"].power == 50, f"the emitted artefact replays differently:\n{out}"
    assert out.index("power: 99") < out.index("t.power"), "the dotted line sits after the key it beat"
    assert emit(out) == out, "emit is idempotent"


# ---------------------------------------------------------------------------
# CD13 / CD14 / CD15 (BUGS-2026-08-19) — the emitted artefact is self-contained,
# a typo'd class refuses at settle, and anchors are unique.
# ---------------------------------------------------------------------------


def test_emit_reemits_import_and_the_artefact_reloads_in_a_fresh_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CD13 — pass 2 consumes `import:`; without re-emitting it the artefact's
    classes were registered only as a side effect of the emitting process, and a
    fresh `load()` failed with UnknownClassError."""
    from confluid.hydraide import emit

    monkeypatch.chdir(tmp_path)
    (tmp_path / "mod_cd13.py").write_text(
        "from confluid import configurable\n"
        "@configurable\n"
        "class CD13Model:\n"
        "    def __init__(self, hidden: int = 8):\n"
        "        self.hidden = hidden\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    (tmp_path / "src.yaml").write_text("import: mod_cd13\nmodel: !class:CD13Model\n  hidden: 32\n")

    out = emit("src.yaml")
    assert out.splitlines()[0] == "import: mod_cd13"
    (tmp_path / "artefact.yaml").write_text(out)
    probe = (
        "import sys; sys.path.insert(0, r'" + str(tmp_path) + "')\n"
        "from confluid import load\n"
        "r = load(r'" + str(tmp_path / "artefact.yaml") + "')\n"
        "print('reloaded', type(r['model']).__name__, r['model'].hidden)\n"
    )
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert "reloaded CD13Model 32" in result.stdout, result.stdout + result.stderr
    assert emit(str(tmp_path / "artefact.yaml")) == out, "emit stays idempotent with the import line"


def test_emit_refuses_an_unknown_class_with_its_location(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CD14 — a typo'd class name sailed through settle with the accept-everything
    list (it absorbed every bare key) and `check` blessed it with exit 0."""
    from confluid.exceptions import UnknownClassError
    from confluid.hydraide import emit

    monkeypatch.chdir(tmp_path)
    (tmp_path / "unknown.yaml").write_text("x: !class:NoSuchClass\nlr: 0.1\n")
    with pytest.raises(UnknownClassError, match=r"NoSuchClass at .*unknown\.yaml:1:4"):
        emit("unknown.yaml")
    with pytest.raises(UnknownClassError, match=r"unknown\.yaml:1:4"):
        load(str(tmp_path / "unknown.yaml"), until="settled")


def test_two_paths_folding_to_one_anchor_name_stay_unique(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CD15 — `m[0]` folds to `m_0` beside a key literally named `m_0`: the emitted
    document carried a DUPLICATE anchor and no parser could read it back."""
    from confluid.hydraide import emit

    monkeypatch.chdir(tmp_path)
    (tmp_path / "mod_cd15.py").write_text(
        "from confluid import configurable\n"
        "@configurable\n"
        "class CD15Box:\n"
        "    def __init__(self, size: int = 0):\n"
        "        self.size = size\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    (tmp_path / "anchors.yaml").write_text(
        "import: mod_cd15\n"
        "m:\n  - !class:CD15Box {size: 1}\nm_0: !class:CD15Box {size: 2}\n"
        "use_a: ${ref:m[0]}\nuse_b: ${ref:m_0}\n"
    )
    out = emit("anchors.yaml")
    anchors = [word for line in out.splitlines() for word in line.split() if word.startswith("&")]
    assert len(anchors) == len(set(anchors)), f"duplicate anchor in:\n{out}"
    (tmp_path / "anchors_out.yaml").write_text(out)
    reloaded = load(str(tmp_path / "anchors_out.yaml"))
    assert reloaded["use_a"] is reloaded["m"][0]
    assert reloaded["use_b"] is reloaded["m_0"]


def test_a_dollar_escape_survives_emit_and_reloads_to_the_same_objects(monkeypatch: pytest.MonkeyPatch) -> None:
    """PA14 — the emitted document carries `$$` (document form); emit is idempotent and the
    artefact reloads to the same strings."""
    from confluid import load
    from confluid.hydraide import emit

    monkeypatch.setenv("RUN_USER", "gert")
    src = "j: !class:collections.Counter\n  command: echo $$RUN_USER\n  opts: {$$k: 1, lit: '$$$$ money'}\n"
    emitted = emit(src)
    assert "command: echo $$RUN_USER" in emitted and "$$k: 1" in emitted and "$$$$ money" in emitted
    assert emit(emitted) == emitted
    assert load(emitted)["j"] == load(src)["j"]
