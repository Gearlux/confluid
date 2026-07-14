from pathlib import Path

import pytest

from confluid import configurable, get_registry, load, load_config


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
    ``Instance(name, **mapping)`` raised ``got multiple values for argument 'target'``
    whenever a config carried a ``target:`` kwarg (e.g. dataflux ``ConfigureOp.target``).
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
    from confluid.fluid import Class, Reference

    config_file = tmp_path / "tags.yaml"
    config_file.write_text("model: !class:Model\n  layers: 10\nref: !ref:base_lr")

    data = load_config(config_file)
    # Tags produce Class/Reference objects
    assert isinstance(data["model"], Class)
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
    from confluid.fluid import Class

    config_file = tmp_path / "root_class.yaml"
    config_file.write_text("!class:Model\nlayers: 10\nactivation: relu\n")

    data = load_config(config_file)
    assert isinstance(data, Class)
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
    WITHOUT materializing — so the raw ``Class`` / ``Instance`` Fluids are visible."""
    from typing import cast

    import yaml

    from confluid.loader import ConfluidLoader

    return cast(dict, yaml.load(text, Loader=ConfluidLoader))


def test_class_form_bare_parses_to_deferred_class() -> None:
    """``!class:Model`` (no parens) parses to a deferred ``Class`` — never built."""
    from confluid.fluid import Class

    data = _parse_tags("m: !class:Model\n")
    assert isinstance(data["m"], Class)
    assert data["m"].kwargs == {}


def test_class_form_bare_with_body_keeps_deferred_with_kwargs() -> None:
    """A bare ``!class:Model`` plus a mapping body stays deferred but captures kwargs."""
    from confluid.fluid import Class

    data = _parse_tags("m: !class:Model\n  layers: 9\n")
    assert isinstance(data["m"], Class)
    assert data["m"].kwargs["layers"] == 9


def test_class_form_empty_parens_parses_to_instance() -> None:
    """``!class:Model()`` (empty parens) parses to an eager ``Instance``."""
    from confluid.fluid import Instance

    data = _parse_tags("m: !class:Model()\n")
    assert isinstance(data["m"], Instance)


def test_class_form_inline_kwargs_parses_to_instance() -> None:
    """``!class:Model(layers=7)`` parses to an eager ``Instance`` carrying coerced kwargs."""
    from confluid.fluid import Instance

    data = _parse_tags("m: !class:Model(layers=7)\n")
    assert isinstance(data["m"], Instance)
    # Inline values are coerced to native types at parse time (parse_value).
    assert data["m"].kwargs["layers"] == 7
    assert isinstance(data["m"].kwargs["layers"], int)


def test_class_form_bare_stays_deferred_after_load(_register_grammar_model: None) -> None:
    """``load()`` leaves a bare ``!class:`` deferred (the receiver flows it)."""
    from confluid.fluid import Class

    assert isinstance(load("m: !class:Model\n")["m"], Class)


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
    from confluid.fluid import Instance

    assert isinstance(built, Instance)
    assert built.kwargs["layers"] == 3  # block body wins on conflict
    assert built.kwargs["extra"] == 7  # inline-only key is preserved, not discarded


def test_lazy_tag_stays_deferred_with_any_grammar(_register_grammar_model: None) -> None:
    """``!lazy:`` always produces a deferred ``Lazy`` — parens or not, block or not."""
    from confluid.fluid import Lazy

    assert isinstance(load("m: !lazy:Model\n")["m"], Lazy)
    assert isinstance(load("m: !lazy:Model(layers=5)\n")["m"], Lazy)
    assert isinstance(load("m: !lazy:Model\n  layers: 5\n")["m"], Lazy)


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


def test_quoted_lazy_tag_is_not_recognized(_register_grammar_model: None) -> None:
    """The quote-the-tag trick is ``!class:`` / ``!ref:`` only — a quoted ``!lazy:``
    stays a plain string and is NEVER turned into a ``Lazy``."""
    value = load('m: "!lazy:Model(layers=5)"\n')["m"]
    assert value == "!lazy:Model(layers=5)"  # untouched string


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
    from confluid.fluid import Class

    assert isinstance(load("m: !class:Model\n", flow=False)["m"], Class)


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
