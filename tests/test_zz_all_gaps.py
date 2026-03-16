import os
from pathlib import Path
from typing import Any, Dict

import pytest
import yaml
from confluid import (
    Fluid,
    configurable,
    dump,
    flow,
    get_hierarchy,
    get_registry,
    load,
    load_config,
    materialize,
    resolve_scopes,
    solidify,
)
from confluid.configurator import Configurator
from confluid.resolver import Resolver


@pytest.fixture(autouse=True)
def setup_registry() -> Any:
    get_registry().clear()
    yield


# --- 1. fluid.py ---


def test_fluid_proxy_logic() -> None:
    # 10-11: target name fallback
    f = Fluid("X")
    assert "Fluid(X" in repr(f)

    # 14-15: repr target is type
    @configurable
    class T:
        def __init__(self, val: int = 0):
            self.val = val

    f_type = Fluid(T)
    assert "Fluid(T" in repr(f_type)

    # flow line 43-46: Fluid target is string and registered
    f2 = Fluid("T", val=5)
    instance = flow(f2)
    assert instance.__class__ == T
    assert instance.val == 5

    # flow line 50-54: handle string tag !class: or !ref:
    assert flow("!class:T").__class__ == T

    # flow line 57: fallback for primitives
    assert flow(42) == 42

    # line 42 miss in previous run: class not found in registry (ValueError)
    with pytest.raises(ValueError, match="not found in registry"):
        flow(Fluid("MissingClassXYZ"))

    # Idempotency line 34
    t = T()
    assert flow(t) is t


# --- 2. configurator.py ---


def test_configurator_coverage() -> None:
    c = Configurator()
    # 32: data is None
    c.configure(None, data=None)
    # 48: data not dict
    c.configure(None, data="x")  # type: ignore[arg-type]

    @configurable
    class M:
        def __init__(self, x: int = 1):
            self.x = x

        @property
        def r(self) -> int:
            return 1

    m = M()

    # 114: recursion protection (resolved_val is dict AND current_val is configurable)
    @configurable
    class Sub:
        def __init__(self, val: int = 1):
            self.val = val

    m.x = Sub()  # type: ignore[assignment]
    c.configure(m, data={"M": {"x": {"val": 10}}})
    assert m.x.val == 10  # type: ignore[attr-defined]

    # 176: broadcast (attr in config AND not dict)
    m2 = M()
    c.configure(m2, data={"x": 20})
    assert m2.x == 20

    # 220: property no setter
    c.configure(m2, data={"M": {"r": 2}})

    # 209-210: signature error (built-in or non-callable __init__)
    c._get_configurable_attributes(int)

    # 188: recursive navigation miss in _deep_get
    assert c._deep_get({"a": 1}, "a.b") is None


def test_configurator_broken_dir_exhaustion() -> None:
    c = Configurator()

    class Broken:
        def __dir__(self) -> Any:
            raise Exception("Fail")

    # dir() failure propagates out of _walk_and_configure
    with pytest.raises(Exception, match="Fail"):
        c._walk_and_configure(Broken(), {}, {}, "")

    # Iterable walking
    c._walk_and_configure((1, 2), {}, {}, "")
    c._walk_and_configure({"a": 1}, {}, {}, "")


# --- 3. decorators.py ---


def test_decorators_coverage() -> None:
    # 62-63
    @configurable(name="C")
    class X:
        pass

    assert get_registry().get_class("C") is X


# --- 4. dumper.py ---


def test_dumper_coverage() -> None:
    f = Fluid("M", x=1)
    assert "!class:M" in dump(f)
    assert "- 1" in dump([1, (2,)])
    assert "42" in dump(42)

    @configurable
    class Bad:
        def __dir__(self) -> Any:
            return ["b"]

        @property
        def b(self) -> Any:
            raise Exception("Fail")

    dump(Bad())


# --- 5. loader.py ---


def test_loader_coverage(tmp_path: Path) -> None:
    from confluid.loader import _process_imports, _register_constructors

    _register_constructors()
    # 39: ScalarNode class tag
    assert yaml.safe_load("!class:Model") == {"_confluid_class_": "Model"}
    # 45: ref_compat
    assert yaml.safe_load("!ref r") == {"_confluid_ref_": "r"}
    # 48-61: class compat variants
    assert yaml.safe_load("!class Model(x=1)") == {
        "_confluid_class_": "Model",
        "x": "1",
    }
    assert yaml.safe_load("!class Model") == {"_confluid_class_": "Model"}

    # 89-90, 94: smart fallback
    with pytest.raises(FileNotFoundError):
        load_config("missing_xyz.yaml")

    # 110, 114-115: process imports
    _process_imports({"import": None})
    _process_imports({"import": ["os", "non_existent_xxx"]})

    # 140, 145: includes
    inc = tmp_path / "inc.yaml"
    inc.write_text("v: 1")
    main = tmp_path / "main.yaml"
    main.write_text("include: [ 123 ]")  # non-string inc_path
    load_config(main)
    main.write_text(f"include: [ {inc.name} ]")
    assert load_config(main)["v"] == 1

    # 168, 200: load fallbacks
    assert load(42) == 42

    # 224: flow_recursive ref
    assert materialize({"_confluid_ref_": "v"}, context={"v": 10}) == 10

    # 218-219: global_settings not dict
    @configurable
    class G:
        def __init__(self, x: int = 1):
            self.x = x

    assert materialize({"_confluid_class_": "G"}, context={"G": 42}).x == 1


# --- 6. parser.py ---


def test_parser_coverage() -> None:
    from confluid.parser import parse_value

    assert parse_value("true") is True
    assert parse_value("false") is False
    assert parse_value("null") is None
    assert parse_value("none") is None
    assert parse_value(":") == ":"


# --- 7. registry.py ---


def test_registry_coverage() -> None:
    r = get_registry()

    @configurable
    class D:
        pass

    r.register_class(D)  # 22: duplicate
    assert "D" in r.list_classes()  # 27-31
    obj: Dict[str, Any] = {}
    r.register_object(obj, "o")
    assert r.get_object("o") is obj

    # coverage for is_configurable with non-str name
    class E:
        pass

    E.__name__ = "D"
    assert r.is_configurable(E) is True
    assert r.is_configurable(int) is False


# --- 8. resolver.py ---


def test_resolver_coverage() -> None:
    r = Resolver(context={"a": {"b": 1}, "r": "!ref:a"})
    # 24: non-str
    assert r.resolve(None) is None
    # 31: recursion
    assert r.resolve("!ref:r") == {"b": 1}
    # 49, 53: !class string
    assert r.resolve("!class:M()") == {"_confluid_class_": "M"}
    assert r.resolve("!class:M(x)") == {"_confluid_class_": "M"}
    # 103-104, 109: lookup miss
    assert r._resolve_ref("m", local_context={"x": 1}) == "!ref:m"
    # 121, 124, 130-132: navigate miss
    assert r._lookup_path("a.c", {"a": 1}) is None
    # 144, 152, 156, 158, 160, 164: interpolate
    assert r._interpolate("just str") == "just str"
    assert r._interpolate("${MISSING:d}") == "d"
    assert r._interpolate("${MISSING}") == "${MISSING}"
    os.environ["ET"] = "v"
    assert r.resolve("${ET}") == "v"


# --- 9. schema.py ---


def test_schema_coverage() -> None:
    from confluid.schema import _build_hierarchy_recursive, _parse_docstring

    # 27
    h: Dict[str, Any] = {}
    _build_hierarchy_recursive(None, "", h, set())

    # 52-75
    class NoInit:
        pass

    get_hierarchy(NoInit())
    # 96-98
    assert _parse_docstring("Args:\n  x: d") == {"x": "d"}


# --- 10. scopes.py ---


def test_scopes_coverage() -> None:
    # 76
    res = resolve_scopes({"scope_aliases": {"a": ["b"]}}, ["a"])
    assert res == {}


# --- 11. solidify.py ---


def test_solidify_coverage() -> None:
    # 23
    assert solidify((1,)) == (1,)
    # 28-36
    assert solidify({"_confluid_class_": "Missing"}) == {"_confluid_class_": "Missing"}

    @configurable
    class S:
        def __init__(self, x: int = 1):
            self.x = x

    assert solidify({"_confluid_class_": "S", "x": 10}).x == 10
