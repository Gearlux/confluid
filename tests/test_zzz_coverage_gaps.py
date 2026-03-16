import pytest
import os
import yaml
from pathlib import Path
from confluid import (
    configurable,
    get_registry,
    configure,
    load,
    load_config,
    materialize,
    resolve_scopes,
    solidify,
    dump,
    Fluid,
    flow,
    get_hierarchy,
    readonly_config,
    ignore_config
)
from confluid.resolver import Resolver
from confluid.configurator import Configurator

@pytest.fixture(autouse=True)
def setup_registry():
    get_registry().clear()
    yield

# --- 1. registry.py ---
def test_registry_gaps():
    r = get_registry()
    @configurable
    class A: pass
    
    # Line 22: get_class with non-string
    assert r.get_class(A) is A
    
    # Line 27-31: is_configurable
    assert r.is_configurable(A) is True
    assert r.is_configurable(A()) is True
    class B: pass
    B.__name__ = "A" # Name match fallback
    assert r.is_configurable(B) is True
    assert r.is_configurable(int) is False

# --- 2. resolver.py ---
def test_resolver_gaps():
    r = Resolver(context={"a": {"b": 1}, "r": "!ref:a"})
    # Line 24: resolve non-str
    assert r.resolve(42) == 42
    
    # Line 49, 53: !class string
    # "!" is a valid yaml string character
    assert r.resolve("!class:M()") == {"_confluid_class_": "M"}
    assert r.resolve("!class:M(x)") == {"_confluid_class_": "M"}
    
    # Line 109: lookup miss
    assert r._resolve_ref("miss", local_context={"x": 1}) == "!ref:miss"
    
    # Line 124, 130-132: navigate miss
    assert r._lookup_path("a.c", {"a": 1}) is None
    
    # Line 144, 152, 156, 158, 160, 164: interpolate
    assert r._interpolate("just text") == "just text"
    assert r._interpolate("${MISS:def}") == "def"
    assert r._interpolate("${MISS}") == "${MISS}"
    os.environ["ET"] = "v"
    assert r._interpolate("${ET}") == "v"
    del os.environ["ET"]

# --- 3. schema.py ---
def test_schema_gaps():
    from confluid.schema import _build_hierarchy_recursive, _parse_docstring
    # 27
    h = {}
    _build_hierarchy_recursive(None, "", h, set())
    # 52-75
    class NoInit: pass
    get_hierarchy(NoInit())
    # 84 (docstring sections logic)
    assert _parse_docstring("Args:\n  x: d") == {"x": "d"}
    assert get_hierarchy(int) == {}

# --- 4. scopes.py ---
def test_scopes_gaps():
    # 76
    res = resolve_scopes({"scope_aliases": {"a": ["b"]}}, ["a"])
    assert res == {}

# --- 5. solidify.py ---
def test_solidify_gaps():
    # 23
    assert solidify((1,)) == (1,)
    # 28-36
    assert solidify({"_confluid_class_": "Missing"}) == {"_confluid_class_": "Missing"}
    @configurable
    class S:
        def __init__(self, x=1): self.x=x
    assert solidify({"_confluid_class_": "S", "x": 10}).x == 10

# --- 6. configurator.py ---
def test_configurator_gaps():
    c = Configurator()
    
    @configurable
    class M:
        def __init__(self, x=1): self.x = x
        @property
        def r(self): return 1
    
    m = M()
    # 48
    c._walk_and_configure((1, 2), {}, {}, "")
    
    # 114: recursion protection
    @configurable
    class Sub:
        def __init__(self, val=1): self.val = val
    m.x = Sub()
    c.configure(m, data={"M": {"x": {"val": 10}}})
    
    # 176: broadcast
    m2 = M()
    c.configure(m2, data={"x": 20})
    
    # 220: property no setter
    c.configure(m2, data={"M": {"r": 2}})
    
    # 209-210: signature error
    c._get_configurable_attributes(int)
    
    # 188: recursive navigation
    assert c._deep_get({"a": 1}, "a.b") is None
    
    # 67-69: Broken dir()
    class Broken:
        def __dir__(self): raise Exception("Fail")
    c._walk_and_configure(Broken(), {}, {}, "")

# --- 7. decorators.py ---
def test_decorators_gaps():
    # 62-63
    @configurable(name="C")
    class X: pass
    assert get_registry().get_class("C") is X

# --- 8. dumper.py ---
def test_dumper_gaps():
    # 22-23
    f = Fluid("M", x=1)
    assert "!class:M" in dump(f)
    # 49-51
    assert "1" in dump([1, (2,)])
    # 53
    assert "42" in dump(42)
    # 89->85
    @configurable
    class Bad:
        def __dir__(self): return ["b"]
        @property
        def b(self): raise Exception("Fail")
    dump(Bad())

# --- 9. fluid.py ---
def test_fluid_gaps():
    # 39->45, 42, 49-57
    @configurable
    class T:
        def __init__(self, val=0): self.val = val
    assert flow("!class:T").__class__ == T
    with pytest.raises(ValueError):
        flow(Fluid("Miss"))

# --- 10. loader.py ---
def test_loader_gaps(tmp_path):
    from confluid.loader import _register_constructors, _process_imports
    _register_constructors()
    
    # 39, 45, 48-61
    assert yaml.safe_load("!class:Model") == {"_confluid_class_": "Model"}
    assert yaml.safe_load("!ref r") == {"_confluid_ref_": "r"}
    assert yaml.safe_load("!class Model(x=1)") == {"_confluid_class_": "Model", "x": "1"}
    assert yaml.safe_load("!class Model") == {"_confluid_class_": "Model"}
    
    # 89-90, 94
    with pytest.raises(FileNotFoundError):
        load_config("missing_123.yaml")
        
    # 110, 114-115
    _process_imports({"import": None})
    _process_imports({"import": ["os"]})
    
    # 140, 145
    inc = tmp_path / "inc.yaml"
    inc.write_text("v: 1")
    main = tmp_path / "main.yaml"
    main.write_text("include: [ 123 ]")
    load_config(main)
    main.write_text(f"include: [ {inc.name} ]")
    
    # 168, 200
    assert load(42) == 42
    
    # 224
    assert materialize({"_confluid_ref_": "v"}, context={"v": 10}) == 10
    
    # 218-219
    @configurable
    class G:
        def __init__(self, x=1): self.x=x
    assert materialize({"_confluid_class_": "G"}, context={"G": 42}).x == 1
