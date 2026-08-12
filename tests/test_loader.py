from pathlib import Path
from typing import Any, Dict, Optional

import pytest
import yaml

from confluid import ConfigurationError, configurable, get_registry, load, load_config
from confluid.fluid import Target
from confluid.loader import ConfluidLoader


def test_load_config_valid(tmp_path: Path) -> None:
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text("Model:\n  layers: 10")

    data = load_config(yaml_file)
    assert data["Model"]["layers"] == 10


def test_load_config_empty(tmp_path: Path) -> None:
    yaml_file = tmp_path / "empty.yaml"
    yaml_file.write_text("")

    data = load_config(yaml_file)
    assert data == {}


def test_load_config_not_found() -> None:
    with pytest.raises(FileNotFoundError):
        load_config("non_existent.yaml")


def test_kwarg_named_target_loads_without_marker_collision(tmp_path: Path) -> None:
    """A YAML kwarg literally named ``target`` must not collide with the marker ctors.

    The Fluid constructors' own first parameter is ``target`` — building a marker via
    ``Target(name, **mapping)`` raised ``got multiple values for argument 'target'``
    whenever a config carried a ``target:`` kwarg (e.g. recordstream ``ConfigureOp.target``).
    The loader assigns kwargs post-construction instead.
    """

    @configurable
    class _Configurish:
        def __init__(self, target: object = None, param: str = "") -> None:
            self.target = target
            self.param = param

    yaml_file = tmp_path / "target_kwarg.yaml"
    yaml_file.write_text(
        "wrapper: !class:_Configurish()\n"
        "  param: low_level\n"
        "  target: !class:_Configurish()\n"
        "    param: inner\n"
    )
    data = load(yaml_file)
    wrapper = data["wrapper"]
    assert isinstance(wrapper, _Configurish) and wrapper.param == "low_level"
    assert isinstance(wrapper.target, _Configurish) and wrapper.target.param == "inner"


def test_kwarg_named_target_survives_the_legacy_class_spelling() -> None:
    """The colon-free ``!class`` compat constructor is collision-proof too.

    It was the ONE tag constructor building its marker via
    ``Target(name, **kwargs)`` instead of ``_make_fluid``, so the legacy
    spelling raised ``got multiple values for argument 'target'`` on a config
    the modern ``!class:`` form loads fine.
    """
    data = yaml.load("x: !class Widget(target=inner, param=low)", Loader=ConfluidLoader)
    marker = data["x"]

    assert isinstance(marker, Target)
    assert marker.target == "Widget"
    assert marker.kwargs == {"target": "inner", "param": "low"}


def test_load_config_with_import() -> None:
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:

        # Use a standard module that is always available
        f.write("import: [os, sys]\n")
        path = f.name

    try:
        data = load_config(path)
        assert data == {}  # import is popped
    finally:
        import os

        os.unlink(path)


def test_load_with_custom_tags(tmp_path: Path) -> None:
    from confluid.fluid import Reference, Target

    config_file = tmp_path / "tags.yaml"
    config_file.write_text("model: !class:Model\n  layers: 10\nref: !ref:base_lr")

    data = load_config(config_file)
    # Tags produce Target/Reference objects
    assert isinstance(data["model"], Target)
    assert data["model"].target == "Model"
    assert data["model"].kwargs["layers"] == 10
    assert isinstance(data["ref"], Reference)
    assert data["ref"].target == "base_lr"


def test_load_config_root_level_class(tmp_path: Path) -> None:
    """Top-level `!class:` documents must round-trip via the path loader.

    The text loader (`confluid.loader.load(text)`) already handles a root
    Fluid (loader.py:183); the path loader must be symmetric so callers
    that point at a YAML file containing a single class doc don't blow
    up in `_process_imports` (which assumes a dict).
    """
    from confluid.fluid import Target

    config_file = tmp_path / "root_class.yaml"
    config_file.write_text("!class:Model\nlayers: 10\nactivation: relu\n")

    data = load_config(config_file)
    assert isinstance(data, Target)
    assert data.target == "Model"
    assert data.kwargs["layers"] == 10
    assert data.kwargs["activation"] == "relu"


# ---------------------------------------------------------------------------
# `!class:` eager-vs-deferred grammar (docs/tags.md)
# ---------------------------------------------------------------------------


@configurable
class _GrammarModel:
    """Tiny configurable target for the `!class:` form tests below."""

    def __init__(self, layers: int = 3) -> None:
        self.layers = layers


@pytest.fixture
def _register_grammar_model() -> None:
    """Register ``_GrammarModel`` under the short name the YAML snippets use.

    Mirrors the registry-cleanup discipline in the other test modules — the
    global registry is shared, so each form test re-registers its target.
    """
    get_registry().register_class(_GrammarModel, name="Model")


def _parse_tags(text: str) -> dict:
    """Parse a YAML string through ConfluidLoader (the tag-aware loader class),
    WITHOUT materializing — so the raw ``Target`` / ``Target`` Fluids are visible."""
    from typing import cast

    import yaml

    from confluid.loader import ConfluidLoader

    return cast(dict, yaml.load(text, Loader=ConfluidLoader))


def test_class_form_bare_parses_to_deferred_class() -> None:
    """``!class:Model`` (no parens) parses to a deferred ``Target`` — never built."""
    from confluid.fluid import Target

    data = _parse_tags("m: !class:Model\n")
    assert isinstance(data["m"], Target)
    assert data["m"].kwargs == {}


def test_class_form_bare_with_body_keeps_deferred_with_kwargs() -> None:
    """A bare ``!class:Model`` plus a mapping body stays deferred but captures kwargs."""
    from confluid.fluid import Target

    data = _parse_tags("m: !class:Model\n  layers: 9\n")
    assert isinstance(data["m"], Target)
    assert data["m"].kwargs["layers"] == 9


def test_class_form_empty_parens_parses_to_instance() -> None:
    """``!class:Model()`` (empty parens) parses to an eager ``Target``."""
    from confluid.fluid import Target

    data = _parse_tags("m: !class:Model()\n")
    assert isinstance(data["m"], Target)


def test_class_form_inline_kwargs_parses_to_instance() -> None:
    """``!class:Model(layers=7)`` parses to an eager ``Target`` carrying coerced kwargs."""
    from confluid.fluid import Target

    data = _parse_tags("m: !class:Model(layers=7)\n")
    assert isinstance(data["m"], Target)
    # Inline values are coerced to native types at parse time (parse_value).
    assert data["m"].kwargs["layers"] == 7
    assert isinstance(data["m"].kwargs["layers"], int)


def test_class_form_bare_is_built_by_load(_register_grammar_model: None) -> None:
    """A bare ``!class:`` is BUILT — the parens no longer change anything.

    Until 2026-08-11 the trailing ``()`` decided eager-vs-deferred and a bare
    ``!class:`` produced a stub whose construction depended on whether its parent
    was ``@configurable``. There are now exactly two modes and only ``_partial_``
    (``!lazy:``) selects between them.
    """
    bare = load("m: !class:Model\n")["m"]
    parens = load("m: !class:Model()\n")["m"]
    assert isinstance(bare, _GrammarModel) and isinstance(parens, _GrammarModel)
    assert bare.layers == parens.layers


def test_class_form_empty_parens_is_built_by_load(_register_grammar_model: None) -> None:
    """``load()`` eagerly materializes ``!class:Model()`` into a live instance."""
    built = load("m: !class:Model()\n")["m"]
    assert isinstance(built, _GrammarModel)
    assert built.layers == 3


def test_class_form_unquoted_inline_kwargs_are_coerced(_register_grammar_model: None) -> None:
    """Unquoted ``!class:Model(layers=7)`` coerces inline scalars to native types.

    The YAML-tag constructor runs each inline ``key=value`` through ``parse_value``,
    so ``"7"`` becomes ``int`` 7 — matching the quoted-string form. (A nested
    ``!ref:`` / ``${ENV}`` still can't appear unquoted; use the quoted form or a
    block body for those.)
    """
    built = load("m: !class:Model(layers=7)\n")["m"]
    assert isinstance(built, _GrammarModel)
    assert built.layers == 7
    assert isinstance(built.layers, int)


def test_class_form_unquoted_inline_kwargs_coerce_float_bool_none() -> None:
    """Inline coercion covers floats, bools and null — not just ints.

    (Multi-arg unquoted tags must be space-free: YAML ends a tag at whitespace.)
    """
    data = _parse_tags("m: !class:Model(a=0.01,b=true,c=null)\n")
    assert data["m"].kwargs == {"a": 0.01, "b": True, "c": None}


def test_class_form_quoted_inline_kwargs_are_coerced(_register_grammar_model: None) -> None:
    """Quoted ``"!class:Model(layers=7)"`` is resolved through the resolver, which
    coerces inline scalars to the declared type (``parse_value``: ``"7"`` → ``7``)."""
    built = load('m: "!class:Model(layers=7)"\n')["m"]
    assert isinstance(built, _GrammarModel)
    assert built.layers == 7  # coerced str→int by the resolver path


def test_class_form_quoted_inline_ref_is_resolved(_register_grammar_model: None) -> None:
    """A nested ``!ref:`` works only in the QUOTED form (the Adam example in docs/tags.md).

    YAML forbids two tags on one node, so ``!class:Model(layers=!ref:n)`` cannot be
    written unquoted — the value must be a quoted string the resolver then parses.
    """
    built = load('n: 10\nm: "!class:Model(layers=!ref:n)"\n')["m"]
    assert built.layers == 10


def test_class_form_inline_kwargs_merge_with_body(_register_grammar_model: None) -> None:
    """Inline ``(k=v)`` kwargs MERGE with a mapping body (no longer discarded).

    An inline key absent from the body survives; on a key present in both, the
    block body wins (it sits later in document order — last-write-wins).
    """
    # Inline width=7 has no body entry → survives. Inline layers=99 is overridden
    # by the body's layers=3.
    built = _parse_tags("m: !class:Model(layers=99,extra=7)\n  layers: 3\n")["m"]
    from confluid.fluid import Target

    assert isinstance(built, Target)
    assert built.kwargs["layers"] == 3  # block body wins on conflict
    assert built.kwargs["extra"] == 7  # inline-only key is preserved, not discarded


def test_lazy_tag_stays_deferred_with_any_grammar(_register_grammar_model: None) -> None:
    """``!lazy:`` always produces a deferred ``Partial`` — parens or not, block or not."""
    from confluid.fluid import Partial

    assert isinstance(load("m: !lazy:Model\n")["m"], Partial)
    assert isinstance(load("m: !lazy:Model(layers=5)\n")["m"], Partial)
    assert isinstance(load("m: !lazy:Model\n  layers: 5\n")["m"], Partial)


def test_lazy_tag_inline_kwargs_are_coerced(_register_grammar_model: None) -> None:
    """``!lazy:`` coerces inline scalars and merges them with a block body, exactly
    like ``!class:`` — only the deferral differs."""
    inline = load("m: !lazy:Model(layers=5)\n")["m"]
    assert inline.kwargs["layers"] == 5  # coerced to int
    assert isinstance(inline.kwargs["layers"], int)
    block = load("m: !lazy:Model\n  layers: 5\n")["m"]
    assert block.kwargs["layers"] == 5  # native int
    merged = load("m: !lazy:Model(layers=99,extra=5)\n  layers: 3\n")["m"]
    assert merged.kwargs == {"layers": 3, "extra": 5}  # block wins; inline-only kept


@pytest.mark.parametrize(
    ("text", "replacement"),
    [
        ('"!lazy:Model(layers=5)"', "{_target_: Model, _partial_: true, layers: 5}"),
        ('"!clone:other"', "{_clone_: other}"),
        ('"!scope:debug"', "{_scope_: {debug: }}"),
        ('"!notscope:debug"', "{_notscope_: {debug: }}"),
    ],
)
def test_a_quoted_marker_the_string_path_cannot_honour_is_REFUSED(
    text: str, replacement: str, _register_grammar_model: None
) -> None:
    """The quote-the-tag trick is ``!class:`` / ``!ref:`` only — and now it SAYS so.

    Until 2026-08-12 a quoted ``!lazy:`` stayed a plain string: no marker, no
    error, no warning. A deferred optimizer written that way reached its
    constructor as the literal text ``!lazy:Adam(lr=0.01)`` and nothing said so —
    exactly the silent degradation the reserved-key format exists to end, which
    this path was quietly exempt from. This test previously ASSERTED the silence.
    """
    with pytest.raises(ConfigurationError) as excinfo:
        load(f"other: 7\nm: {text}\n")

    message = str(excinfo.value)
    assert text.strip('"') in message, "quote the offending text — a scalar carries no file:line"
    assert replacement in message, "and name the exact plain-YAML line to write instead"
    assert "0.4.0" in message, "name the release that removes the spelling"


def test_the_refusal_names_the_plain_yaml_line_to_write(_register_grammar_model: None) -> None:
    """An error that says "write it as plain YAML" must show WHICH plain YAML.

    The suffix is already parsed by the shared ``Target(...)`` grammar, so the
    replacement is derivable — and a message the reader can paste is the
    difference between a fix and a search.
    """
    with pytest.raises(ConfigurationError) as excinfo:
        load('m: "!lazy:Model(layers=5)"\n')

    assert "{_target_: Model, _partial_: true, layers: 5}" in str(excinfo.value)


def test_a_quoted_marker_inside_a_markers_own_kwargs_is_REFUSED(_register_grammar_model: None) -> None:
    """Even ``!class:`` / ``!ref:`` are honoured by NOTHING in that position.

    Measured against the pre-change engine: both reached the constructor as
    literal text. It is also the position ``docs/targets.md`` recommended the
    spelling for ("for a nested ``!ref:``, quote the tag"), so the one documented
    use case was the one that silently did nothing.
    """
    for inner in ('"!class:Model(layers=7)"', '"!ref:other"'):
        with pytest.raises(ConfigurationError, match="marker's own kwargs"):
            load(f"other: 7\nm:\n  _target_: Model\n  extra: {inner}\n")


def test_an_ordinary_value_starting_with_a_bang_is_untouched(_register_grammar_model: None) -> None:
    """The refusal matches the exact marker prefixes, never a bare leading ``!``.

    A config value may legitimately start with one — a shell negation, a CSS
    ``!important``, a message — and refusing those would break real documents to
    fix a spelling nobody uses.
    """
    out = load('a: "!important"\nb: "!not-a-marker:x"\nc: "!classroom: 3"\n')

    assert out == {"a": "!important", "b": "!not-a-marker:x", "c": "!classroom: 3"}


def test_import_key_warns_on_missing_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo'd ``import:`` module warns at load time (it used to fail silently
    and only surface much later as "Cannot resolve class"). Loading still
    succeeds — the module may be an optional dependency of a shared config."""
    from types import SimpleNamespace

    import confluid.loader as loader_module

    warnings_seen: list[str] = []
    monkeypatch.setattr(loader_module, "logger", SimpleNamespace(warning=lambda msg: warnings_seen.append(msg)))

    cfg = tmp_path / "cfg.yaml"
    cfg.write_text("import: [definitely_not_a_module_xyz]\nval: 1\n")
    data = load_config(cfg)
    assert data == {"val": 1}
    assert any("definitely_not_a_module_xyz" in msg for msg in warnings_seen)


def test_global_safe_loader_stays_clean() -> None:
    """Tags are registered on ConfluidLoader ONLY — plain ``yaml.safe_load``
    must still REJECT confluid tags. Guards against re-polluting the global
    ``yaml.SafeLoader``, which would hand Fluid markers to every other
    yaml-consuming library in the process."""
    import yaml

    import confluid  # noqa: F401 — confluid fully imported, constructors registered

    with pytest.raises(yaml.constructor.ConstructorError):
        yaml.safe_load("m: !class:Model\n")
    with pytest.raises(yaml.constructor.ConstructorError):
        yaml.safe_load("r: !ref:base\n")
    # ...while confluid's own entry point parses them fine.
    from confluid.fluid import Target

    assert isinstance(load("m: !class:Model\n", flow=False)["m"], Target)


def test_config_key_interpolation_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """``${key.path}`` embeds another config value; ``${ENV}`` stays env-based."""
    monkeypatch.setenv("CONFLUID_TEST_ROOT", "/store")
    doc = (
        "train:\n"
        "  dataset: RFUAV\n"
        "  version: v3\n"
        'data_dir: "${CONFLUID_TEST_ROOT}/${train.dataset}/${train.version}/data"\n'
    )
    result = load(doc, flow=False)
    assert result["data_dir"] == "/store/RFUAV/v3/data"


def test_materialize_interpolates_config_keys_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """``materialize()`` runs the same Resolver pass ``load()`` runs.

    docs/interpolation.md promises interpolation "at materialization" naming
    ``load()`` / ``materialize()`` / ``resolve()``; measured, ``materialize()``
    skipped it and the literal ``${...}`` rode into values silently while the
    other two (and ``configure()``) resolved. Idempotent on the load() path,
    which has already substituted.
    """
    from confluid import materialize

    monkeypatch.setenv("CONFLUID_TEST_ROOT", "/store")
    out = materialize({"run": {"name": "exp42"}, "output_dir": "${CONFLUID_TEST_ROOT}/runs/${run.name}"})
    assert out["output_dir"] == "/store/runs/exp42"


def test_materialize_resolves_against_a_separate_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """A distinct ``context=`` dict is the interpolation source, and is itself resolved."""
    from confluid import materialize

    monkeypatch.delenv("db", raising=False)
    out = materialize({"port_str": "port=${db.port}"}, context={"db": {"port": 5432}})
    assert out["port_str"] == "port=5432"


def test_interpolation_reaches_a_markers_kwarg_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """``${ENV}`` and ``${key.path}`` inside a tag's mapping body substitute at load.

    Measured before the fix: ``Resolver.resolve`` returned Fluids whole, so the
    literal ``${...}`` rode into the constructed object silently on EVERY path,
    while the quoted-string spelling of the same target interpolated — two
    spellings, two answers. Nested plain dicts inside the kwargs interpolate too.
    """
    from confluid import configurable, load

    @configurable
    class _InterpSrc:
        def __init__(self, input_dir: str = "", tag: str = "", extras: dict = None) -> None:  # type: ignore[assignment]
            self.input_dir, self.tag, self.extras = input_dir, tag, extras

    monkeypatch.setenv("CONFLUID_TEST_ROOT", "/store")
    cfg = load(
        "run:\n"
        "  name: exp42\n"
        "src: !class:_InterpSrc()\n"
        '  input_dir: "${CONFLUID_TEST_ROOT}/files"\n'
        '  tag: "${run.name}"\n'
        "  extras:\n"
        '    nested: "${run.name}-x"\n'
    )
    assert cfg["src"].input_dir == "/store/files"
    assert cfg["src"].tag == "exp42"
    assert cfg["src"].extras == {"nested": "exp42-x"}


def test_interpolation_matches_across_marker_spellings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mapping-body, quoted-string, and Fluid-ROOT spellings substitute identically."""
    from confluid import configurable, load

    @configurable
    class _SpellSrc:
        def __init__(self, input_dir: str = "") -> None:
            self.input_dir = input_dir

    monkeypatch.setenv("CONFLUID_TEST_ROOT", "/store")
    body = load('src: !class:_SpellSrc()\n  input_dir: "${CONFLUID_TEST_ROOT}/files"\n')["src"]
    quoted = load('src: "!class:_SpellSrc(input_dir=${CONFLUID_TEST_ROOT}/files)"')["src"]
    root = load('!class:_SpellSrc()\ninput_dir: "${CONFLUID_TEST_ROOT}/files"')
    assert body.input_dir == quoted.input_dir == root.input_dir == "/store/files"


def test_interpolation_burns_into_a_lazy_marker_without_building_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ``!lazy:`` kwarg substitutes at LOAD (burn-in) while construction stays deferred.

    Round-trip half of the pin: the dumped marker carries the SUBSTITUTED value,
    so a reload in a different environment reproduces this run — a slot that must
    stay late-bound uses ``!ref:`` instead.
    """
    from confluid import configurable, dump, load
    from confluid.fluid import Partial

    @configurable
    class _LazySrc:
        def __init__(self, input_dir: str = "") -> None:
            self.input_dir = input_dir

    monkeypatch.setenv("CONFLUID_TEST_ROOT", "/store")
    marker = load('opt: !lazy:_LazySrc()\n  input_dir: "${CONFLUID_TEST_ROOT}/opt"\n')["opt"]
    assert isinstance(marker, Partial)  # construction still deferred
    assert marker.kwargs["input_dir"] == "/store/opt"
    dumped = dump({"opt": marker})
    assert "/store/opt" in dumped and "${" not in dumped
    monkeypatch.setenv("CONFLUID_TEST_ROOT", "/elsewhere")
    reloaded = load(dumped)["opt"]
    assert reloaded.kwargs["input_dir"] == "/store/opt"  # burned in — reload reproduces


# --------------------------------------------------------------------------------------
# A YAML mapping keeps the key TYPES it was written with.
# --------------------------------------------------------------------------------------


def test_non_string_mapping_keys_survive_load(tmp_path: Path) -> None:
    """A mapping keyed by int/float reaches its consumer keyed by int/float.

    ``_process_includes_recursive`` rebuilt every dict in the document with ``str(k)``,
    so a class-id table written ``{1: drone}`` arrived as ``{'1': 'drone'}`` and a lookup
    by the int id missed. The raw parse had it right the whole time — only this walk broke
    it, and it had done so since 2026-03-13. (A consumer is free to normalize keys itself;
    what is fixed here is confluid silently deciding for it.)
    """
    cfg = tmp_path / "keys.yaml"
    cfg.write_text("table:\n  1: one\n  2.5: mid\n  plain: str\n")

    table = load(str(cfg))["table"]

    assert table == {1: "one", 2.5: "mid", "plain": "str"}
    assert sorted(type(k).__name__ for k in table) == ["float", "int", "str"]


def test_non_string_keys_survive_inside_a_markers_kwargs(tmp_path: Path) -> None:
    """The same, for a mapping handed to a target as a constructor argument."""

    @configurable
    class Table:
        def __init__(self, names: Optional[Dict[Any, str]] = None) -> None:
            self.names = names

    cfg = tmp_path / "marker.yaml"
    cfg.write_text("node:\n  _target_: Table\n  names:\n    1: one\n    2: two\n")

    assert load(str(cfg))["node"].names == {1: "one", 2: "two"}


def test_a_dotted_STRING_key_still_expands_beside_non_string_keys(tmp_path: Path) -> None:
    """The guard must not cost the dotted-path grammar its job.

    ``expand_dotted_mapping`` asks ``"." in k``, which is why the loader used to stringify
    everything up front. It now skips non-str keys instead — so a dotted key must still
    expand in a document that also carries a non-str-keyed table.

    Expansion is TOP-LEVEL only (a nested ``inner.value:`` stays literal); that is
    pre-existing behaviour, and this pins the level where the two rules actually meet.
    """
    cfg = tmp_path / "mixed.yaml"
    cfg.write_text("inner.value: 7\ntable:\n  1: an int key\n")

    doc = load(str(cfg))

    assert doc["inner"] == {"value": 7}
    assert doc["table"] == {1: "an int key"}
