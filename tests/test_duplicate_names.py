"""One name, several classes — coexistence, disambiguation, and the clobber warning.

The registry used to be a flat ``name -> class`` dict with no collision check, so a second
class registered under an existing name silently replaced the first: it vanished from
every picker, and which one survived depended on import order. Two shapes make that a
real constraint rather than a corner case — the same op implemented per framework (same
``category``, different ``group``) and a library publishing one name as both a loss and a
metric (same ``framework``, different ``role``).

These pin the whole contract: which key a name publishes under, how a lookup narrows, when
a duplicate is legal versus a mistake, and that everything a unique name did before is
unchanged.
"""

from types import SimpleNamespace
from typing import Any, Iterator, List, Tuple

import pytest

import confluid.registry as registry_module
from confluid import AmbiguousClassError, ConfigurationError, ConfluidError, configurable, get_registry, register


@pytest.fixture(autouse=True)
def setup_registry() -> None:
    get_registry().clear()


@pytest.fixture
def warnings() -> Iterator[List[str]]:
    """Collect ``confluid.registry`` warnings.

    loggair does not propagate into stdlib logging, so ``caplog`` stays EMPTY here and a
    negative assertion against it is a false green — the module logger is swapped instead.
    """
    collected: List[str] = []
    original = registry_module.logger
    registry_module.logger = SimpleNamespace(  # type: ignore[assignment]
        warning=lambda msg: collected.append(str(msg)),
        debug=lambda msg: None,
        info=lambda msg: None,
        trace=lambda msg: None,
    )
    try:
        yield collected
    finally:
        registry_module.logger = original  # type: ignore[assignment]


def _twins(axis: str, first: str, second: str, **shared: Any) -> Tuple[Any, Any]:
    """Two distinct classes both named ``Twin``, differing only on ``axis``."""

    def build(value: str) -> Any:
        @configurable(**shared, **{axis: value})
        class Twin:
            def __init__(self, n: int = 0) -> None:
                self.n = n
                self.which = value

        return Twin

    return build(first), build(second)


# ---------------------------------------------------------------------------
# storage + the public key
# ---------------------------------------------------------------------------


def test_a_unique_name_still_publishes_under_its_bare_name() -> None:
    """The regression guard for every existing exact-set assertion in the suite."""

    @configurable(category="op")
    class Solo:
        pass

    assert get_registry().list_classes() == {"Solo"}
    assert get_registry().list_classes(category="op") == {"Solo"}
    assert get_registry().get_class("Solo") is Solo


def test_classes_differing_by_a_tag_coexist_under_one_name() -> None:
    torch_twin, keras_twin = _twins("framework", "torch", "keras", task="t", role="loss")
    reg = get_registry()

    assert reg.get_class("Twin", framework="torch") is torch_twin
    assert reg.get_class("Twin", framework="keras") is keras_twin
    # Both are enumerable — the flat dict published only the winner.
    assert reg.list_classes() == {reg.key_for(torch_twin), reg.key_for(keras_twin)}
    assert len(reg.list_classes(task="t", role="loss")) == 2


def test_an_ambiguous_name_publishes_dotted_keys_that_look_up() -> None:
    """`list_classes()` -> `get_class(key)` must keep working for BOTH classes."""
    first, second = _twins("group", "a", "b", category="op")
    reg = get_registry()

    keys = reg.list_classes(category="op")
    assert all("." in key for key in keys), keys
    assert {reg.get_class(key) for key in keys} == {first, second}


def test_a_name_that_becomes_ambiguous_later_keeps_the_first_class_reachable() -> None:
    """The promotion case: the first class is already registered and stamped by then."""

    @configurable(category="op", group="a")
    class Twin:
        pass

    first = Twin
    reg = get_registry()
    assert reg.list_classes() == {"Twin"}
    assert reg.key_for(first) == "Twin"

    @configurable(category="op", group="b")
    class Twin:  # type: ignore[no-redef]  # noqa: F811 — the collision under test
        pass

    second = Twin
    assert reg.key_for(first) != "Twin" and "." in (reg.key_for(first) or "")
    assert reg.get_class(str(reg.key_for(first))) is first
    assert reg.get_class(str(reg.key_for(second))) is second
    # The reverse index still reaches both — it keys on entry keys, which never change,
    # so no rewrite was needed when the name was promoted.
    assert len(reg.list_classes(category="op")) == 2


def test_clear_empties_every_index() -> None:
    @configurable(category="op", group="g", task="t", role="r", framework="f")
    class Tagged:
        pass

    reg = get_registry()
    reg.clear()
    assert reg.list_classes() == set()
    assert reg.list_categories() == reg.list_groups() == reg.list_tasks() == set()
    assert reg.list_roles() == reg.list_frameworks() == set()
    assert reg.key_for(Tagged) is None


def test_registry_isolation_covers_every_index() -> None:
    """The conftest snapshot must name EVERY backing dict.

    Derived from the live object rather than restated: ``_by_framework`` was missing from
    the snapshot tuple from the day it was added, so a test that cleared the registry
    emptied the framework index for the rest of the session.
    """
    from conftest import _REGISTRY_INDEXES  # the suite's own conftest, not a package

    fresh = registry_module.ConfluidRegistry()
    backing = {name for name, value in vars(fresh).items() if isinstance(value, dict)}
    assert backing == set(_REGISTRY_INDEXES)


# ---------------------------------------------------------------------------
# lookup
# ---------------------------------------------------------------------------


def test_a_bare_lookup_of_an_ambiguous_name_raises_with_both_candidates() -> None:
    first, second = _twins("framework", "torch", "keras", task="t", role="loss")
    reg = get_registry()

    with pytest.raises(AmbiguousClassError) as excinfo:
        reg.get_class("Twin")

    message = str(excinfo.value)
    assert str(reg.key_for(first)) in message and str(reg.key_for(second)) in message
    # It names the axis the candidates actually DIFFER on — a hint pointing at a tag they
    # share would narrow nothing.
    assert "framework=" in message
    assert "!class:" in message  # and the config-side spelling


def test_ambiguous_error_is_a_configuration_error_and_a_value_error() -> None:
    """Catchable by existing `except ValueError:` code, distinct from 'unknown'."""
    from confluid import UnknownClassError

    assert issubclass(AmbiguousClassError, ConfigurationError)
    assert issubclass(AmbiguousClassError, ConfluidError)
    assert issubclass(AmbiguousClassError, ValueError)
    assert not issubclass(AmbiguousClassError, UnknownClassError)


def test_a_filter_that_matches_nothing_returns_none_rather_than_raising() -> None:
    _twins("framework", "torch", "keras", task="t", role="loss")
    assert get_registry().get_class("Twin", framework="mlx") is None
    assert get_registry().get_class("NeverRegistered") is None


def test_a_filter_that_still_leaves_two_candidates_raises() -> None:
    _twins("group", "a", "b", category="op", task="t")
    with pytest.raises(AmbiguousClassError):
        get_registry().get_class("Twin", task="t")


def test_the_selector_spelling_matches_the_keyword_filters() -> None:
    torch_twin, keras_twin = _twins("framework", "torch", "keras", task="t", role="loss")
    reg = get_registry()

    assert reg.get_class("Twin@framework=torch") is torch_twin
    assert reg.get_class("Twin@role=loss,framework=keras") is keras_twin
    # An explicit keyword wins over the selector on the same axis.
    assert reg.get_class("Twin@framework=torch", framework="keras") is keras_twin


@pytest.mark.parametrize("spec", ["Twin@nosuchaxis=1", "Twin@framework", "Twin@=torch", "Twin@framework="])
def test_a_malformed_selector_is_rejected(spec: str) -> None:
    """A typo'd axis must fail loudly — silently selecting nothing is worse."""
    with pytest.raises(ConfigurationError):
        get_registry().get_class(spec)


def test_lookup_by_class_object_is_identity_not_name() -> None:
    """`get_class(cls)` must return THAT class, even when its name is shared."""
    first, second = _twins("group", "a", "b", category="op")
    reg = get_registry()

    assert reg.get_class(first) is first
    assert reg.get_class(second) is second


def test_lookup_by_class_object_survives_a_name_override() -> None:
    @configurable(name="Renamed")
    class Original:
        pass

    reg = get_registry()
    assert reg.get_class("Renamed") is Original
    assert reg.get_class(Original) is Original
    assert reg.key_for(Original) == "Renamed"


def test_registered_third_party_class_is_reachable_by_name_and_filter() -> None:
    class ThirdParty:
        pass

    register(ThirdParty, task="t", role="loss", framework="keras")
    reg = get_registry()
    assert reg.get_class("ThirdParty") is ThirdParty
    assert reg.get_class("ThirdParty", framework="keras") is ThirdParty
    assert reg.get_class("ThirdParty", framework="torch") is None


# ---------------------------------------------------------------------------
# the clobber rule
# ---------------------------------------------------------------------------


def test_re_registering_the_same_object_is_silent(warnings: List[str]) -> None:
    """A snapshot restore / re-decoration does this on every bootstrap."""

    @configurable(category="op", group="g")
    class Same:
        pass

    get_registry().register_class(Same, category="op")
    get_registry().register_class(Same, category="op")

    assert warnings == []
    assert len(get_registry().list_classes(category="op")) == 1
    assert getattr(Same, "__confluid_group__") == "g"  # the tagless re-register kept it


def test_same_name_same_tags_different_class_warns(warnings: List[str]) -> None:
    """The original bug: with no discriminating axis there is nothing to select on."""

    @configurable(category="op")
    class Twin:
        pass

    first = Twin

    @configurable(category="op")
    class Twin:  # type: ignore[no-redef]  # noqa: F811 — the collision under test
        pass

    assert len(warnings) == 1
    assert "already registered" in warnings[0]
    # Last-write-wins is preserved, so nothing that worked before breaks.
    assert get_registry().get_class("Twin") is Twin
    assert get_registry().key_for(first) is None


def test_differing_tags_coexist_without_a_warning(warnings: List[str]) -> None:
    _twins("framework", "torch", "keras", task="t", role="loss")
    assert warnings == []


def test_an_untagged_duplicate_counts_as_the_same_tags(warnings: List[str]) -> None:
    """Two untagged same-named classes have no axis to tell them apart — that is a clobber."""

    @configurable
    class Bare:
        pass

    @configurable
    class Bare:  # type: ignore[no-redef]  # noqa: F811
        pass

    assert len(warnings) == 1


def test_a_bare_reregister_after_a_name_override_is_idempotent(warnings: List[str]) -> None:
    """``name`` falls back to the mark the class already carries.

    Without the fallback a partial re-register (only ``category`` forwarded) re-derived
    the name from ``__name__`` and minted a SECOND entry under the original short name.
    """

    @configurable(name="Custom", category="op")
    class Original:
        pass

    get_registry().register_class(Original, category="op")

    assert get_registry().list_classes(category="op") == {"Custom"}
    assert warnings == []


def test_the_name_fallback_reads_the_class_s_own_mark_not_an_inherited_one() -> None:
    """A subclass must not inherit its parent's custom registration name."""

    @configurable(name="ParentName")
    class Parent:
        pass

    @configurable
    class Child(Parent):
        pass

    reg = get_registry()
    assert reg.get_class("ParentName") is Parent
    assert reg.get_class("Child") is Child


# ---------------------------------------------------------------------------
# resolve_class: strictness is the construction path's, not the probe path's
# ---------------------------------------------------------------------------


def test_resolve_class_is_non_raising_by_default() -> None:
    """Introspection callers already treat ``None`` as 'not introspectable'."""
    _twins("group", "a", "b", category="op")
    assert registry_module.resolve_class("Twin") is None


def test_resolve_class_strict_raises_on_ambiguity() -> None:
    _twins("group", "a", "b", category="op")
    with pytest.raises(AmbiguousClassError):
        registry_module.resolve_class("Twin", strict=True)


def test_resolve_class_filters_and_selectors_narrow_it() -> None:
    first, second = _twins("group", "a", "b", category="op")
    assert registry_module.resolve_class("Twin", group="b") is second
    assert registry_module.resolve_class("Twin@group=a") is first


def test_resolve_class_still_imports_a_dotted_path() -> None:
    """The importlib branch is unchanged — and a selector on it is ignored, not verified."""
    from collections import OrderedDict

    assert registry_module.resolve_class("collections.OrderedDict") is OrderedDict
    assert registry_module.resolve_class("collections.OrderedDict@framework=torch") is OrderedDict


def test_a_dollar_selector_without_a_context_matches_nothing() -> None:
    """Off the construction path there is no document, so ``$key`` cannot be answered."""
    _twins("framework", "torch", "keras", task="t", role="loss")
    assert registry_module.resolve_class("Twin@framework=$framework") is None


def test_a_dollar_selector_resolves_against_a_supplied_context() -> None:
    torch_twin, _ = _twins("framework", "torch", "keras", task="t", role="loss")
    resolved = registry_module.resolve_class("Twin@framework=$framework", context={"framework": "torch"})
    assert resolved is torch_twin


def test_an_unresolvable_dollar_selector_says_which_key_is_missing() -> None:
    _twins("framework", "torch", "keras", task="t", role="loss")
    with pytest.raises(ConfigurationError, match="framework"):
        registry_module.resolve_class("Twin@framework=$missing", context={"other": 1}, strict=True)


def test_parse_target_spec_splits_name_from_selector() -> None:
    assert registry_module.parse_target_spec("Foo") == ("Foo", {})
    assert registry_module.parse_target_spec("Foo@role=loss") == ("Foo", {"role": "loss"})
    assert registry_module.parse_target_spec("pkg.mod.Foo@role=loss,framework=keras") == (
        "pkg.mod.Foo",
        {"role": "loss", "framework": "keras"},
    )


def test_is_configurable_still_answers_for_a_duplicated_name() -> None:
    first, second = _twins("group", "a", "b", category="op")
    reg = get_registry()
    assert reg.is_configurable(first) and reg.is_configurable(second)


def test_key_for_returns_none_for_an_unregistered_class() -> None:
    class Stranger:
        pass

    assert get_registry().key_for(Stranger) is None


def test_two_classes_sharing_a_qualname_get_distinct_keys() -> None:
    """A dotted key must stay a unique handle even when the spelling repeats.

    Two classes defined in the same function scope share ``module.qualname``, so the
    canonical key has to disambiguate them itself or one would shadow the other.
    """

    def build(group: str) -> Any:
        @configurable(category="op", group=group)
        class Local:
            pass

        return Local

    first, second = build("a"), build("b")
    reg = get_registry()
    keys = {reg.key_for(first), reg.key_for(second)}
    assert len(keys) == 2
    assert reg.get_class(str(reg.key_for(first))) is first
    assert reg.get_class(str(reg.key_for(second))) is second


def test_a_retag_moves_the_class_between_index_buckets() -> None:
    """A re-register that CHANGES a tag must not leave the class under the old value."""

    @configurable(category="op")
    class Moving:
        pass

    reg = get_registry()
    assert reg.list_classes(category="op") == {"Moving"}
    reg.register_class(Moving, category="sink")
    assert reg.list_classes(category="op") == set()
    assert reg.list_classes(category="sink") == {"Moving"}


def test_get_class_accepts_a_dotted_key_directly() -> None:
    @configurable(category="op")
    class Solo:
        pass

    reg = get_registry()
    # An UNAMBIGUOUS class publishes under its bare name, but its canonical key must still
    # resolve — a config that spells the dotted path should never stop working just because
    # the name happens to be unique today.
    key = registry_module._entry_key(Solo)
    assert "<" not in key and "." in key  # `<locals>` stripped: the key rides a YAML tag
    assert reg.get_class(key) is Solo
    assert reg.key_for(Solo) == "Solo"


def test_selector_axes_are_the_list_classes_filters() -> None:
    """One vocabulary: a selector cannot name an axis a filter does not have."""
    import inspect

    params = inspect.signature(get_registry().list_classes).parameters
    assert tuple(params) == registry_module.SELECTOR_AXES


def test_registered_name_is_reported_in_the_ambiguity_message_for_named_registrations() -> None:
    """A ``name=``-registered class is still described by its dotted key, which is unique."""

    class A:
        pass

    class B:
        pass

    register(A, name="Shared", framework="torch")
    register(B, name="Shared", framework="keras")
    reg = get_registry()
    assert reg.get_class("Shared", framework="torch") is A
    assert reg.get_class("Shared", framework="keras") is B
    with pytest.raises(AmbiguousClassError):
        reg.get_class("Shared")


def test_the_dict_returned_by_list_classes_is_a_plain_set_of_strings() -> None:
    """Consumers do set arithmetic on the result (`preferred | type_compatible`)."""
    _twins("group", "a", "b", category="op")

    @configurable(category="op")
    class Solo:
        pass

    keys = get_registry().list_classes(category="op")
    assert isinstance(keys, set) and all(isinstance(k, str) for k in keys)
    assert "Solo" in keys and len(keys) == 3


# ---------------------------------------------------------------------------
# the config side: selectors in a tag, and the $key form
# ---------------------------------------------------------------------------
# A dotted path always disambiguates, but it is not always a spelling anyone should have
# to write (`keras.src.metrics.hinge_metrics.Hinge` is importable and unreadable). The
# selector rides an unquoted YAML tag because `@ = / , $` are all legal tag-suffix
# characters — `${...}` is NOT, which is why the document-key form is the unbraced `$key`.


def _framework_twins() -> Tuple[Any, Any]:
    def build(fw: str) -> Any:
        @configurable(task="t", role="loss", framework=fw)
        class Twin:
            def __init__(self, n: int = 0) -> None:
                self.n = n
                self.which = fw

        return Twin

    return build("torch"), build("keras")


def _load(text: str, tmp_path: Any, **kwargs: Any) -> Any:
    import confluid

    path = tmp_path / "c.yaml"
    path.write_text(text)
    return confluid.load(str(path), **kwargs)


def test_a_selector_in_a_tag_picks_the_right_class(tmp_path: Any) -> None:
    torch_twin, keras_twin = _framework_twins()
    cfg = _load("a: !class:Twin@framework=torch()\nb: !class:Twin@framework=keras()\n", tmp_path)

    assert isinstance(cfg["a"], torch_twin) and isinstance(cfg["b"], keras_twin)


def test_a_selector_composes_with_inline_kwargs(tmp_path: Any) -> None:
    """The widened tag grammar must still split the ``(k=v)`` call part."""
    _framework_twins()
    cfg = _load("a: !class:Twin@framework=keras(n=7)\n", tmp_path)

    assert cfg["a"].which == "keras" and cfg["a"].n == 7


def test_a_selector_works_on_lazy(tmp_path: Any) -> None:
    from confluid import flow

    _framework_twins()
    cfg = _load("a: !lazy:Twin@framework=torch\n", tmp_path)

    assert flow(cfg["a"]).which == "torch"


def test_a_dollar_selector_reads_a_top_level_key(tmp_path: Any) -> None:
    """Say the axis ONCE, then let every ambiguous target follow it."""
    _framework_twins()
    cfg = _load("framework: keras\na: !class:Twin@framework=$framework()\n", tmp_path)

    assert cfg["a"].which == "keras"


def test_a_dollar_selector_reads_a_dotted_path(tmp_path: Any) -> None:
    _framework_twins()
    cfg = _load("run:\n  framework: torch\na: !class:Twin@framework=$run.framework()\n", tmp_path)

    assert cfg["a"].which == "torch"


def test_a_dollar_selector_works_inside_another_markers_kwargs(tmp_path: Any) -> None:
    """The case load-time ``${...}`` interpolation cannot reach.

    ``Resolver.resolve`` returns a Fluid whole without walking its kwargs, so a quoted
    ``"!class:X@fw=${.framework}"`` nested inside another ``!class:`` block is never
    interpolated. The ``$`` form is resolved by the ENGINE at construction time instead,
    where the document is reachable at any depth — which is the shape flat configs use.
    """

    @configurable
    class Holder:
        def __init__(self, loss: Any = None) -> None:
            self.loss = loss

    _framework_twins()
    cfg = _load(
        "framework: keras\nh: !class:Holder()\n  loss: !class:Twin@framework=$framework()\n",
        tmp_path,
    )

    assert cfg["h"].loss.which == "keras"


def test_a_scope_block_can_drive_the_selector(tmp_path: Any) -> None:
    """Scope splicing runs before flow, so a block that declares its own dimension as an
    ordinary key makes every ``$`` selector in the document follow the活 verb."""
    _framework_twins()
    text = (
        "torch_block: !scope:framework=torch\n"
        "  framework: torch\n"
        "keras_block: !scope:framework=keras\n"
        "  framework: keras\n"
        "a: !class:Twin@framework=$framework()\n"
    )

    assert _load(text, tmp_path, scopes=["framework=torch"])["a"].which == "torch"
    assert _load(text, tmp_path, scopes=["framework=keras"])["a"].which == "keras"


def test_a_dollar_selector_needs_the_document_to_still_be_active(tmp_path: Any) -> None:
    """The boundary: ``$key`` reads the document being materialized.

    A marker deliberately kept deferred and flowed LATER by domain code (``flow(self.loss)``
    inside a trainer's ``run()``) is outside that window — the document is long gone. The
    error names the key rather than failing obscurely, and the fix is to write the value
    literally (``@framework=keras``) or to flow inside ``confluid.active_context(doc)``.
    """
    import confluid
    from confluid import flow

    _framework_twins()
    cfg = _load("framework: keras\nl: !lazy:Twin@framework=$framework\n", tmp_path)

    with pytest.raises(ConfigurationError, match="framework"):
        flow(cfg["l"])

    with confluid.active_context(cfg):
        assert flow(cfg["l"]).which == "keras"


def test_a_bare_ambiguous_tag_raises_from_load(tmp_path: Any) -> None:
    _framework_twins()
    with pytest.raises(AmbiguousClassError):
        _load("a: !class:Twin()\n", tmp_path)


def test_a_selector_naming_no_registered_class_says_the_resolved_value(tmp_path: Any) -> None:
    """The error must report what ``$framework`` HELD, not the raw spelling."""
    from confluid import UnknownClassError

    _framework_twins()
    with pytest.raises(UnknownClassError, match="framework='mlx'"):
        _load("framework: mlx\na: !class:Twin@framework=$framework()\n", tmp_path)


def test_an_unknown_selector_axis_is_rejected_at_load(tmp_path: Any) -> None:
    _framework_twins()
    with pytest.raises(ConfigurationError, match="fraemwork"):
        _load("a: !class:Twin@fraemwork=torch()\n", tmp_path)


def test_a_dotted_target_still_matches_its_bare_name_block(tmp_path: Any) -> None:
    """Recommending dotted paths made this pre-existing bug load-bearing.

    ``!class:pkg.mod.Widget`` set the block-match name to the dotted string, so a
    ``Widget:`` block silently did not apply and the value stayed at its default.
    """

    @configurable
    class Widget:
        def __init__(self, lr: float = 0.0) -> None:
            self.lr = lr

    dotted = get_registry().key_for(Widget)  # the canonical key, `<locals>`-free
    assert _load(f"w: !class:{dotted}()\nWidget:\n  lr: 0.5\n", tmp_path)["w"].lr == 0.5
    assert _load("w: !class:Widget()\nWidget:\n  lr: 0.5\n", tmp_path)["w"].lr == 0.5


def test_a_selector_target_also_matches_its_bare_name_block(tmp_path: Any) -> None:
    _framework_twins()
    cfg = _load("a: !class:Twin@framework=torch()\nTwin:\n  n: 4\n", tmp_path)

    assert cfg["a"].which == "torch" and cfg["a"].n == 4


# ---------------------------------------------------------------------------
# dump round-trip (Serialization Symmetry)
# ---------------------------------------------------------------------------


def test_dump_emits_a_key_that_reloads_the_same_class(tmp_path: Any) -> None:
    """A shared name must dump as the DOTTED key, or reload picks the wrong twin."""
    import confluid

    _torch_twin, keras_twin = _framework_twins()
    text = confluid.dump(keras_twin(n=3))

    assert "_target_:" in text
    reloaded = confluid.load(text)
    assert isinstance(reloaded, keras_twin) and reloaded.n == 3


def test_dump_of_a_unique_name_still_emits_the_bare_name() -> None:
    """Guards every existing dump/load round-trip in the workspace."""
    import confluid

    @configurable
    class Solo:
        def __init__(self, n: int = 0) -> None:
            self.n = n

    assert "_target_: Solo" in confluid.dump(Solo(n=1))


def test_a_dumped_bare_name_survives_the_class_moving_module() -> None:
    """The evidence behind "dump() keeps the public key" (docs/architecture.md §4).

    Emitting the dotted key unconditionally would make a written config immune to a
    future namesake — the obvious-looking fix. It is not, because both spellings are
    handles into a live registry rather than import paths, and they differ only in
    WHICH change breaks them. A class moving module is an ordinary refactor; an
    unrelated package claiming its name is not. The bare name survives the first and
    a stale dotted key does not, which is what decided it.
    """
    from confluid import UnknownClassError, load

    registry = get_registry()

    class Moved:
        def __init__(self, size: int = 1) -> None:
            self.size = size

    registry.register_class(Moved, name="MovedWidget")
    Moved.__module__ = "somewhere.new"  # the refactor
    registry.register_class(Moved, name="MovedWidget")

    assert load("w: !class:MovedWidget()\n  size: 3\n")["w"].size == 3

    with pytest.raises(UnknownClassError):
        load("w: !class:oldpkg.legacy.MovedWidget()\n")


def test_every_published_key_is_a_legal_yaml_tag() -> None:
    """`list_classes()` must never advertise a key `!class:` cannot parse.

    `_entry_key` strips `<locals>` specifically so keys survive a YAML tag, but
    `<lambda>` reached it unhandled: registering two lambdas under one name published
    `__main__.<lambda>`, which `list_classes` returned and the loader rejected with a
    `ScannerError` — `<` is not a legal tag character.

    Registering an anonymous callable is legitimate (a one-line metric, a builder), and
    it works until the NAME becomes ambiguous — at which point the public key switches
    to the dotted form and becomes unusable. So the fix is to make the key tag-legal,
    not to refuse the registration.
    """
    from confluid import load

    registry = get_registry()
    registry.register_class(lambda: "A", name="AnonMetric", role="metric", framework="torch")
    registry.register_class(lambda: "B", name="AnonMetric", role="metric", framework="keras")

    keys = sorted(registry.list_classes(role="metric"))

    assert not any("<" in k or ">" in k for k in keys), keys
    assert [load(f"o: !class:{k}()")["o"] for k in keys] == ["A", "B"]


def test_a_locals_scope_is_dropped_while_a_lambda_name_is_unwrapped() -> None:
    """The two bracketed shapes need opposite treatment, so both are pinned.

    `<locals>` NAMES A SCOPE — dropping it leaves `outer.Inner`, which still identifies
    the class. `<lambda>` IS the name — dropping it would leave a bare trailing dot,
    identical for every lambda in the module, so it is unwrapped instead and same-module
    collisions fall to `_claim_key`'s `~N` suffix (`~` being a legal tag character).
    """
    from confluid.registry import _entry_key

    def outer() -> type:
        class Inner:
            pass

        return Inner

    assert _entry_key(outer()).endswith("outer.Inner")
    assert _entry_key(lambda: None).endswith(".lambda")


def test_same_qualname_classes_do_not_share_an_accept_list() -> None:
    """The introspection caches key on IDENTITY, not on ``module.QualName``.

    Two classes defined in ONE scope share that dotted name — which is why
    ``registry._claim_key`` suffixes ``~N`` — and until 2026-08-11 the five
    per-pass introspection caches keyed on the raw name, so the second class was
    served the FIRST one's accept-list. The measured consequence was silent: the
    second class built on its defaults and had the other's key ``setattr``-ed onto
    it as a post-init attribute. Real triggers are class factories, plugin loaders
    building classes in a loop, and parametrised fixtures.
    """
    from confluid import configurable, load
    from confluid.broadcast import _get_acceptable_keys, clear_pass_caches

    def make_both() -> tuple:
        @configurable(name="FirstWidget")
        class Widget:
            def __init__(self, alpha: int = 1) -> None:
                self.alpha = alpha

        first = Widget

        @configurable(name="SecondWidget")
        class Widget:  # type: ignore[no-redef]  # noqa: F811 — same __qualname__ IS the case under test
            def __init__(self, beta: int = 2) -> None:
                self.beta = beta

        return first, Widget

    first, second = make_both()
    assert first.__qualname__ == second.__qualname__  # the collision this test exists for

    clear_pass_caches()
    assert _get_acceptable_keys(first) == frozenset({"alpha"})
    assert _get_acceptable_keys(second) == frozenset({"beta"})

    clear_pass_caches()
    result = load("alpha: 41\nbeta: 42\na: !class:FirstWidget()\nb: !class:SecondWidget()\n")

    assert result["a"].alpha == 41
    assert result["b"].beta == 42
    assert not hasattr(result["b"], "alpha")  # the foreign key must never land
