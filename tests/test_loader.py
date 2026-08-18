from pathlib import Path
from typing import Any, Dict, Optional

import pytest
import yaml

from confluid import ConfigurationError, configurable, get_registry, load
from confluid.fluid import Target
from confluid.loader import ConfluidLoader


def test_load_raw_from_file_valid(tmp_path: Path) -> None:
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text("Model:\n  layers: 10")

    data = load(yaml_file, until="raw")
    assert data["Model"]["layers"] == 10


def test_load_raw_from_file_empty(tmp_path: Path) -> None:
    yaml_file = tmp_path / "empty.yaml"
    yaml_file.write_text("")

    data = load(yaml_file, until="raw")
    assert data == {}


def test_load_raw_from_file_not_found() -> None:
    with pytest.raises(FileNotFoundError):
        load("non_existent.yaml", until="raw")


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


def test_load_raw_with_import() -> None:
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:

        # Use a standard module that is always available
        f.write("import: [os, sys]\n")
        path = f.name

    try:
        data = load(path, until="raw")
        assert data == {}  # import is popped
    finally:
        import os

        os.unlink(path)


def test_load_with_custom_tags(tmp_path: Path) -> None:
    from confluid.fluid import Reference, Target

    config_file = tmp_path / "tags.yaml"
    config_file.write_text("model: !class:Model\n  layers: 10\nref: !ref:base_lr")

    data = load(config_file, until="raw")
    # Tags produce Target/Reference objects
    assert isinstance(data["model"], Target)
    assert data["model"].target == "Model"
    assert data["model"].kwargs["layers"] == 10
    assert isinstance(data["ref"], Reference)
    assert data["ref"].target == "base_lr"


def test_load_raw_root_level_class(tmp_path: Path) -> None:
    """Top-level `!class:` documents must round-trip via the path loader.

    The text loader (`confluid.loader.load(text)`) already handles a root
    Fluid (loader.py:183); the path loader must be symmetric so callers
    that point at a YAML file containing a single class doc don't blow
    up in `_process_imports` (which assumes a dict).
    """
    from confluid.fluid import Target

    config_file = tmp_path / "root_class.yaml"
    config_file.write_text("!class:Model\nlayers: 10\nactivation: relu\n")

    data = load(config_file, until="raw")
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
    from confluid.fluid import PartialClass

    assert isinstance(load("m: !lazy:Model\n")["m"], PartialClass)
    assert isinstance(load("m: !lazy:Model(layers=5)\n")["m"], PartialClass)
    assert isinstance(load("m: !lazy:Model\n  layers: 5\n")["m"], PartialClass)


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
        ('"!class:Model(layers=5)"', "{_target_: Model, layers: 5}"),
        ('"!partial:Model(layers=5)"', "{_target_: Model, _partial_: true, layers: 5}"),
        ('"!lazy:Model(layers=5)"', "{_target_: Model, _partial_: true, layers: 5}"),
        ('"!ref:other"', "{_ref_: other}"),
        ('"!scope:debug"', "{_scope_: {debug: }}"),
        ('"!notscope:debug"', "{_notscope_: {debug: }}"),
    ],
)
def test_a_marker_written_as_a_quoted_STRING_is_refused(
    text: str, replacement: str, _register_grammar_model: None
) -> None:
    """A marker is a YAML tag or a reserved-key mapping — a quoted tag is a string, and a
    string starting with a marker prefix is refused, naming the two lines that work.

    Never let it through as text: a deferred optimizer written ``"!lazy:Adam(lr=0.01)"``
    once reached its constructor as that literal string with no diagnostic anywhere.
    """
    with pytest.raises(ConfigurationError) as excinfo:
        load(f"other: 7\nm: {text}\n")

    message = str(excinfo.value)
    assert text.strip('"') in message, "quote the offending text — a scalar carries no file:line"
    assert replacement in message, "and name the exact reserved-key line to write instead"
    assert text.strip('"').split("(")[0] in message, "and the tag to write unquoted"


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
    """The same refusal inside a marker's kwargs — the position a nested value goes in a block body."""
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
    data = load(cfg, until="raw")
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

    assert isinstance(load("m: !class:Model\n", until="document")["m"], Target)


def test_config_key_interpolation_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """``${key.path}`` embeds another config value; ``${ENV}`` stays env-based."""
    monkeypatch.setenv("CONFLUID_TEST_ROOT", "/store")
    doc = (
        "train:\n"
        "  dataset: RFUAV\n"
        "  version: v3\n"
        'data_dir: "${CONFLUID_TEST_ROOT}/${train.dataset}/${train.version}/data"\n'
    )
    result = load(doc, until="document")
    assert result["data_dir"] == "/store/RFUAV/v3/data"


def test_materialize_interpolates_config_keys_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The engine entry runs the same Resolver pass the loader runs.

    docs/interpolation.md promises interpolation from ``until="document"`` on;
    the engine entry substitutes too, and is idempotent on data ``load`` has
    already substituted.
    """

    monkeypatch.setenv("CONFLUID_TEST_ROOT", "/store")
    out = load({"run": {"name": "exp42"}, "output_dir": "${CONFLUID_TEST_ROOT}/runs/${run.name}"})
    assert out["output_dir"] == "/store/runs/exp42"


def test_materialize_resolves_against_a_separate_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """A distinct ``context=`` dict is the interpolation source, and is itself resolved."""

    monkeypatch.delenv("db", raising=False)
    out = load({"port_str": "port=${db.port}"}, context={"db": {"port": 5432}})
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
    plain = load('src: {_target_: _SpellSrc, input_dir: "${CONFLUID_TEST_ROOT}/files"}')["src"]
    root = load('!class:_SpellSrc()\ninput_dir: "${CONFLUID_TEST_ROOT}/files"')
    assert body.input_dir == plain.input_dir == root.input_dir == "/store/files"


def test_interpolation_burns_into_a_lazy_marker_without_building_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ``!lazy:`` kwarg substitutes at LOAD (burn-in) while construction stays deferred.

    Round-trip half of the pin: the dumped marker carries the SUBSTITUTED value,
    so a reload in a different environment reproduces this run — a slot that must
    stay late-bound uses ``!ref:`` instead.
    """
    from confluid import configurable, dump, load
    from confluid.fluid import PartialClass

    @configurable
    class _LazySrc:
        def __init__(self, input_dir: str = "") -> None:
            self.input_dir = input_dir

    monkeypatch.setenv("CONFLUID_TEST_ROOT", "/store")
    marker = load('opt: !lazy:_LazySrc()\n  input_dir: "${CONFLUID_TEST_ROOT}/opt"\n')["opt"]
    assert isinstance(marker, PartialClass)  # construction still deferred
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


# ---------------------------------------------------------------------------
# Duplicate mapping keys are REFUSED (BUGS-2026-08-13 P11)
#
# The YAML spec restricts a mapping's keys to be unique and lists "mapping keys
# may not be unique" among its loading failure points, but leaves the processor's
# response unspecified — PyYAML keeps the LAST value, silently. For confluid that
# meant `include: a.yaml` … `include: b.yaml` lost a whole FILE before the loader
# ever ran, and the survivor spliced at the FIRST occurrence's position (a
# collapsed duplicate keeps first-insertion order), inverting the documented
# "later line wins" rule. An ordinary duplicated key loses its first value the
# same way.
# ---------------------------------------------------------------------------


def test_two_include_directives_are_refused(tmp_path: Path) -> None:
    """The P11 case. Before this, a.yaml was silently never read."""
    (tmp_path / "a.yaml").write_text("from_a: 1\n")
    (tmp_path / "b.yaml").write_text("from_b: 2\n")
    main = tmp_path / "main.yaml"
    main.write_text("include: a.yaml\nx: 1\ninclude: b.yaml\n")

    with pytest.raises(ConfigurationError, match="duplicate key 'include'"):
        load(str(main))


def test_a_duplicated_ordinary_key_is_refused() -> None:
    """`include:` is only the case where the discarded value is a whole file."""
    with pytest.raises(ConfigurationError, match="duplicate key 'lr'"):
        load("lr: 0.1\nmodel: {_target_: collections.Counter}\nlr: 0.5")


def test_the_refusal_names_both_lines_and_the_file(tmp_path: Path) -> None:
    """A duplicate is a two-location problem: the reader needs both to fix it."""
    cfg = tmp_path / "dup.yaml"
    cfg.write_text("include: a.yaml\nx: 1\ninclude: b.yaml\n")

    with pytest.raises(ConfigurationError) as exc:
        load(str(cfg))

    message = str(exc.value)
    assert "dup.yaml:3:1" in message, message  # the second occurrence
    assert "line 1" in message, message  # the first one it collides with


def test_a_duplicate_inside_a_nested_mapping_is_refused() -> None:
    with pytest.raises(ConfigurationError, match="duplicate key 'size'"):
        load("outer:\n  size: 1\n  other: 2\n  size: 3\n")


def test_a_duplicate_inside_a_markers_own_kwargs_is_refused() -> None:
    with pytest.raises(ConfigurationError, match="duplicate key 'size'"):
        load("m:\n  _target_: collections.Counter\n  size: 1\n  size: 2\n")


def test_a_duplicate_inside_a_scope_block_is_refused() -> None:
    with pytest.raises(ConfigurationError, match="duplicate key 'size'"):
        load("wrap:\n  _scope_: {mode: fast}\n  size: 1\n  size: 2\n")


def test_the_valid_multi_file_spelling_still_works(tmp_path: Path) -> None:
    """One key, a sequence value — the spelling the refusal leaves you with.

    It also honours position, which the duplicate-key form got wrong: `x` is
    written ABOVE the include, so b.yaml's `x` wins.
    """
    (tmp_path / "a.yaml").write_text("from_a: 1\n")
    (tmp_path / "b.yaml").write_text("from_b: 2\nx: 999\n")
    main = tmp_path / "main.yaml"
    main.write_text("x: 1\ninclude: [a.yaml, b.yaml]\n")

    assert load(str(main)) == {"from_a": 1, "from_b": 2, "x": 999}


def test_a_merge_key_overridden_by_a_local_key_is_NOT_a_duplicate() -> None:
    """The false-positive guard that matters most.

    `<<:` merging a key the node also writes literally is ordinary override
    semantics — the whole point of the idiom — not a duplicate key.
    """
    doc = "base: &b\n  size: 1\n  label: base\nderived:\n  <<: *b\n  size: 2\n"
    assert load(doc)["derived"] == {"size": 2, "label": "base"}


def test_the_same_key_in_DIFFERENT_mappings_is_fine() -> None:
    """Uniqueness is per-mapping, not per-document."""
    assert load("a: {size: 1}\nb: {size: 2}\n") == {"a": {"size": 1}, "b": {"size": 2}}


def test_the_same_key_across_LIST_ITEMS_is_fine() -> None:
    assert load("items:\n  - size: 1\n  - size: 2\n")["items"] == [{"size": 1}, {"size": 2}]


def test_an_include_and_a_local_key_of_the_same_name_across_files_is_fine(tmp_path: Path) -> None:
    """An included file re-stating a key is a MERGE, not a duplicate — that is
    the entire point of overlays."""
    (tmp_path / "base.yaml").write_text("lr: 0.1\nseed: 7\n")
    main = tmp_path / "main.yaml"
    main.write_text("include: base.yaml\nlr: 0.5\n")

    assert load(str(main)) == {"lr": 0.5, "seed": 7}


def test_same_text_different_TAG_keys_are_not_duplicates() -> None:
    """Identity is (tag, value), not text.

    `1:` is an int key and `"1":` a str key; PyYAML keeps both, so refusing them
    would reject a legal document. Same for `yes:` (a YAML boolean) beside `"yes":`.
    """
    assert load('table:\n  1: one\n  "1": two\n')["table"] == {1: "one", "1": "two"}
    assert load('flags:\n  yes: a\n  "yes": b\n')["flags"] == {True: "a", "yes": "b"}


def test_the_refusal_binds_the_TAG_spelling_too() -> None:
    """A behaviour reachable from only one spelling is a bug in that spelling.

    The four tag constructors each built their mapping inline, so a check added
    to the plain path alone would have left the deprecated spelling silently
    losing a duplicated key. They now share `_str_keyed_mapping`.
    """
    with pytest.raises(ConfigurationError, match="duplicate key 'size'"):
        load("m: !class:Box\n  size: 1\n  size: 2\n")

    with pytest.raises(ConfigurationError, match="duplicate key 'size'"):
        load("w: !scope:mode=fast\n  size: 1\n  size: 2\n")
