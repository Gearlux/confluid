"""``confluid.locate`` — where each class, include and import of a config text points, for go-to-definition.

Every test works in a sandbox: the CWD and the XDG tiers inside ``tmp_path`` (as ``test_search_paths.py`` does), and
a site folder of its own first on ``sys.path`` holding the packages a test writes — so a class is found in a source
file the test made, and the test can check that nothing was imported to find it.
"""

import importlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List

import pytest

import confluid.locate as locate_module
from confluid import configurable
from confluid.locate import Location, Mention, Resolution, locate, main, resolve_target, serve


@pytest.fixture(autouse=True)
def site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A sandboxed CWD and XDG lookup, and an empty site folder first on ``sys.path``; returns the site folder."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("XDG_CONFIG_DIRS", str(tmp_path / "xdg_sys"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    folder = tmp_path / "site"
    folder.mkdir()
    monkeypatch.syspath_prepend(str(folder))
    importlib.invalidate_caches()
    return folder


def _write(root: Path, files: Dict[str, str]) -> None:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    importlib.invalidate_caches()


def _only(mentions: List[Mention], kind: str) -> List[Mention]:
    return [m for m in mentions if m.kind == kind]


def _ship(site: Path, tmp_path: Path, files: Dict[str, str]) -> Path:
    """Install the distribution ``lp-dist`` as an editable checkout holding ``files``; returns the checkout."""
    checkout = tmp_path / "lp-dist-checkout"
    _write(checkout, files)
    info = site / "lp_dist-1.0.dist-info"
    _write(info, {"METADATA": "Metadata-Version: 2.1\nName: lp-dist\nVersion: 1.0\n"})
    (info / "direct_url.json").write_text(json.dumps({"url": checkout.as_uri(), "dir_info": {"editable": True}}))
    importlib.invalidate_caches()
    return checkout


# -- what the text names, and where --------------------------------------------------------------------------------


def test_a_class_tag_names_its_target_at_the_range_the_editor_underlines(tmp_path: Path) -> None:
    text = "model: !class:demo_pkg.models.Net(width=4)\n"
    (mention,) = locate(text, tmp_path / "a.yaml")
    assert (mention.kind, mention.text, mention.line) == ("class", "demo_pkg.models.Net", 0)
    assert text[mention.start : mention.stop] == "demo_pkg.models.Net"


def test_every_marker_tag_and_both_spellings_name_a_class(tmp_path: Path) -> None:
    text = (
        "a: !class:pkg.A {x: 1}\n"
        "b: !partial:pkg.B\n"
        "c: !lazy:pkg.C()\n"
        "d: {_target_: pkg.D, _partial_: true}\n"
        "e:\n"
        "  _target_: 'pkg.E'\n"
        "f: [!class:pkg.F {}]\n"
    )
    mentions = locate(text, tmp_path / "a.yaml")
    assert [(m.text, m.line) for m in mentions] == [
        ("pkg.A", 0),
        ("pkg.B", 1),
        ("pkg.C", 2),
        ("pkg.D", 3),
        ("pkg.E", 5),
        ("pkg.F", 6),
    ]
    lines = text.splitlines()
    assert all(lines[m.line][m.start : m.stop] == m.text for m in mentions)


def test_a_selector_stays_on_the_name_and_a_call_suffix_does_not(tmp_path: Path) -> None:
    text = "loss: !class:Focal@task=detection(gamma=2)\nplain: {_target_: Focal@role=loss}\n"
    assert [m.text for m in locate(text, tmp_path / "a.yaml")] == ["Focal@task=detection", "Focal@role=loss"]


def test_an_anchored_marker_is_found_where_its_tag_is_written(tmp_path: Path) -> None:
    text = "base: &net !class:pkg.Net {}\nagain: *net\n"
    mentions = locate(text, tmp_path / "a.yaml")
    assert [(m.text, m.line, text.splitlines()[0][m.start : m.stop]) for m in mentions] == [("pkg.Net", 0, "pkg.Net")]


def test_columns_are_counted_as_the_editor_counts_them(tmp_path: Path) -> None:
    # "𝔼" is one character but two UTF-16 code units: the editor's column after it is one more.
    text = "n: {label: '𝔼', m: !class:pkg.Net {}}\n"
    (mention,) = locate(text, tmp_path / "a.yaml")
    character_column = text.index("pkg.Net")
    assert (mention.start, mention.stop) == (character_column + 1, character_column + 1 + len("pkg.Net"))


def test_a_text_yaml_cannot_read_still_gives_the_tags_before_the_error(tmp_path: Path) -> None:
    text = "a: !class:pkg.A\nb: !class:pkg.B\nc: [unclosed\n"
    assert [m.text for m in locate(text, tmp_path / "a.yaml")] == ["pkg.A", "pkg.B"]


def test_a_text_that_breaks_while_scanning_still_gives_the_tags_before_the_break(tmp_path: Path) -> None:
    text = "a: !class:pkg.A\nb: '\n"  # an unterminated quote: the scanner itself fails
    assert [m.text for m in locate(text, tmp_path / "a.yaml")] == ["pkg.A"]


def test_a_spelling_that_names_no_target_is_skipped(tmp_path: Path) -> None:
    text = (
        "a: !class:%70kg.X\n"  # PyYAML decodes the escape: the tag no longer reads as written
        "b: !class:(x=1)\n"
        "c: {_target_: '(x)'}\n"
        'd: {_target_: "pkg.\n    X"}\n'
        "e: !other:pkg.X\n"
        "include: [{nested: 1}, [a.yaml]]\n"
        "z: !class:pkg.Z\n"
    )
    assert [m.text for m in locate(text, tmp_path / "a.yaml")] == ["pkg.Z"]
    broken = "a: !other:x\nb: !class:(y)\nc: !class:%70kg.Y\nd: !class:pkg.Fine\ne: [\n"
    assert [m.text for m in locate(broken, tmp_path / "a.yaml")] == ["pkg.Fine"]


def test_text_with_nothing_to_point_at_gives_nothing(tmp_path: Path) -> None:
    assert locate("a: 1\nb: [x, y]\nc: !ref:a\n", tmp_path / "a.yaml") == []
    assert locate("", tmp_path / "a.yaml") == []


# -- classes found in their source, nothing imported ----------------------------------------------------------------


def test_a_dotted_class_is_found_in_its_source_without_importing_it(site: Path, tmp_path: Path) -> None:
    _write(
        site,
        {
            "lp_alpha/__init__.py": "raise RuntimeError('importing the package is not allowed')\n",
            "lp_alpha/models.py": "import os\n\n\n@decorator\nclass Net:\n    pass\n",
        },
    )
    (mention,) = locate("m: !class:lp_alpha.models.Net\n", tmp_path / "a.yaml")
    assert mention.target == Location(str((site / "lp_alpha/models.py").resolve()), 4)
    assert not mention.deferred
    assert "lp_alpha" not in sys.modules


def test_functions_assignments_and_nested_classes_are_definitions(site: Path, tmp_path: Path) -> None:
    _write(
        site,
        {
            "lp_beta/__init__.py": "",
            "lp_beta/mod.py": (
                "class Outer:\n"
                "    class Inner:\n"
                "        pass\n"
                "def build():\n"
                "    pass\n"
                "async def build_async():\n"
                "    pass\n"
                "Alias = Outer\n"
                "Typed: type = Outer\n"
                "class Model:\n"
                "    pass\n"
                "Model = register(Model)\n"
            ),
        },
    )
    text = "".join(
        f"{key}: !class:lp_beta.mod.{name}\n"
        for key, name in [("a", "Outer.Inner"), ("b", "build"), ("c", "build_async"), ("d", "Alias")]
        + [("e", "Typed"), ("f", "Model"), ("g", "Outer.Missing"), ("h", "Alias.Inner")]
    )
    lines = [m.target.line if m.target else None for m in locate(text, tmp_path / "a.yaml")]
    # Outer.Inner, build, build_async, Alias, Typed, Model (the class, not its rewrap), Outer.Missing, Alias.Inner
    assert lines == [1, 3, 5, 7, 8, 9, None, None]


def test_a_package_re_export_is_followed_to_the_module_that_defines_the_class(site: Path, tmp_path: Path) -> None:
    _write(
        site,
        {
            "lp_gamma/__init__.py": (
                "from .core import Engine\nfrom .extra import *\nfrom lp_gamma.alias import A as B\n"
            ),
            "lp_gamma/core/__init__.py": "from .engine import Engine\n",
            "lp_gamma/core/engine.py": "\n\nclass Engine:\n    pass\n",
            "lp_gamma/extra.py": "class Starred:\n    pass\n",
            "lp_gamma/alias.py": "class A:\n    pass\n",
        },
    )
    text = "a: !class:lp_gamma.Engine\nb: !class:lp_gamma.Starred\nc: !class:lp_gamma.B\n"
    targets = [m.target for m in locate(text, tmp_path / "a.yaml")]
    assert targets == [
        Location(str((site / "lp_gamma/core/engine.py").resolve()), 2),
        Location(str((site / "lp_gamma/extra.py").resolve()), 0),
        Location(str((site / "lp_gamma/alias.py").resolve()), 0),
    ]


def test_a_lazy_export_table_is_followed_when_its_module_defines_the_class(site: Path, tmp_path: Path) -> None:
    _write(
        site,
        {
            "lp_delta/__init__.py": (
                "_NAMES = {'Sink': ('lp_delta.sink', ''), 'Ghost': 'lp_delta.sink', 'Other': ('lp_delta.empty',)}\n"
                "__getattr__, __dir__ = lazy_exports(__name__, _NAMES)\n"
            ),
            "lp_delta/sink.py": "class Sink:\n    pass\n",
            "lp_delta/empty.py": "",
            "lp_nolazy/__init__.py": "_NAMES = {'Sink': 'lp_delta.sink'}\n",  # no __getattr__: the table is just data
            "lp_fn/__init__.py": "_N = {'Sink': 'lp_delta.sink'}\n\ndef __getattr__(name):\n    pass\n",
        },
    )
    text = "a: !class:lp_delta.Sink\nb: !class:lp_delta.Ghost\nc: !class:lp_delta.Other\n"
    text += "d: !class:lp_nolazy.Sink\ne: !class:lp_fn.Sink\n"
    found = {m.text: m.target for m in locate(text, tmp_path / "a.yaml")}
    sink = Location(str((site / "lp_delta/sink.py").resolve()), 0)
    assert found == {
        "lp_delta.Sink": sink,
        "lp_delta.Ghost": None,  # the table names a module that does not define it
        "lp_delta.Other": None,
        "lp_nolazy.Sink": None,
        "lp_fn.Sink": sink,
    }


def test_a_package_two_checkouts_extend_is_searched_in_both(site: Path, tmp_path: Path) -> None:
    other = tmp_path / "other_checkout"
    _write(site, {"lp_shared/__init__.py": "__path__ = __import__('pkgutil').extend_path(__path__, __name__)\n"})
    _write(other, {"lp_shared/__init__.py": "", "lp_shared/child/__init__.py": "", "lp_shared/child/m.py": "X = 1\n"})
    sys.path.append(str(other))
    try:
        (mention,) = locate("x: !class:lp_shared.child.m.X\n", tmp_path / "a.yaml")
    finally:
        sys.path.remove(str(other))
    assert mention.target == Location(str((other / "lp_shared/child/m.py").resolve()), 0)


def test_a_plain_top_level_module_is_found(site: Path, tmp_path: Path) -> None:
    _write(site, {"lp_single.py": "def make():\n    pass\n"})
    found = [m.target for m in locate("x: !class:lp_single.make\ny: !class:lp_single.sub.make\n", tmp_path / "a")]
    assert found == [Location(str((site / "lp_single.py").resolve()), 0), None]  # a module has no submodule


def test_re_exports_are_followed_four_hops_and_a_missing_source_is_passed_over(site: Path, tmp_path: Path) -> None:
    chain = {f"lp_hop{i}.py": f"from lp_hop{i + 1} import Far\n" for i in range(6)}
    _write(site, {**chain, "lp_hop6.py": "class Far:\n    pass\n"})
    _write(site, {"lp_gone.py": "from lp_nowhere import Lost\nfrom lp_hop4 import Far as Lost\n"})
    text = "a: !class:lp_hop2.Far\nb: !class:lp_hop1.Far\nc: !class:lp_gone.Lost\n"
    targets = [m.target for m in locate(text, tmp_path / "a.yaml")]
    far = Location(str((site / "lp_hop6.py").resolve()), 0)
    assert targets == [far, None, far]  # lp_hop1 is five re-exports away


def test_a_registry_name_or_an_unreadable_source_is_deferred(site: Path, tmp_path: Path) -> None:
    _write(site, {"lp_eps/__init__.py": "", "lp_eps/broken.py": "class (:\n", "lp_eps/fine.py": "Y = 1\n"})
    text = (
        "a: !class:Trainer\n"
        "b: !class:lp_eps.broken.X\n"
        "c: !class:lp_eps.fine.Missing\n"
        "d: !class:lp_nowhere.X\n"
        "e: !class:lp_eps.nowhere.X\n"
        "f: !class:lp_eps.X\n"
    )
    mentions = locate(text, tmp_path / "a.yaml")
    assert [(m.target, m.deferred) for m in mentions] == [(None, True)] * 6


def test_finding_a_class_survives_a_finder_that_raises(
    site: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Raising:
        @staticmethod
        def find_spec(name: str, path: object = None) -> None:
            raise RuntimeError("this finder is broken")

    _write(site, {"lp_zeta.py": "class Z:\n    pass\n"})
    monkeypatch.setattr(sys, "meta_path", [Raising(), object(), *sys.meta_path])
    (mention,) = locate("z: !class:lp_zeta.Z\n", tmp_path / "a.yaml")
    assert mention.target == Location(str((site / "lp_zeta.py").resolve()), 0)


# -- includes and imports ------------------------------------------------------------------------------------------


def test_an_include_is_found_beside_the_file_first(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    _write(project, {"pyproject.toml": "", "config/sub/base.yaml": "a: 1\n", "config/sub/other.yaml": "b: 2\n"})
    text = "include:\n  - base.yaml\n  - other.yaml\n  - missing.yaml\nx: 1\n"
    mentions = locate(text, project / "config/sub/top.yaml")
    assert [(m.text, m.target) for m in mentions] == [
        ("base.yaml", Location(str((project / "config/sub/base.yaml").resolve()), 0)),
        ("other.yaml", Location(str((project / "config/sub/other.yaml").resolve()), 0)),
        ("missing.yaml", None),
    ]
    assert [(m.line, text.splitlines()[m.line][m.start : m.stop]) for m in mentions][0] == (1, "base.yaml")
    assert not any(m.deferred for m in mentions)


def test_an_include_is_found_in_the_project_and_its_config_folder(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    _write(project, {"pyproject.toml": "", "shared.yaml": "a: 1\n", "config/defaults.yaml": "b: 1\n"})
    _write(project, {"experiments/deep/run.yaml": ""})
    text = "include: [shared.yaml, defaults.yaml]\n"
    targets = [m.target for m in locate(text, project / "experiments/deep/run.yaml")]
    assert targets == [
        Location(str((project / "shared.yaml").resolve()), 0),
        Location(str((project / "config/defaults.yaml").resolve()), 0),
    ]


def test_an_include_outside_any_project_is_looked_up_from_its_own_folder(tmp_path: Path) -> None:
    _write(tmp_path / "loose", {"config/b.yaml": ""})
    (mention,) = locate("include: b.yaml\n", tmp_path / "loose/a.yaml")
    assert mention.target == Location(str((tmp_path / "loose/config/b.yaml").resolve()), 0)


def test_an_include_found_in_the_xdg_folder(tmp_path: Path) -> None:
    _write(tmp_path / "xdg", {"common.yaml": ""})
    (mention,) = locate("include: common.yaml\n", tmp_path / "work/a.yaml")
    assert mention.target == Location(str((tmp_path / "xdg/common.yaml").resolve()), 0)


def test_an_include_inside_a_scope_block_or_a_quoted_one_is_found(tmp_path: Path) -> None:
    _write(tmp_path / "p", {"fast.yaml": "", "q.yaml": ""})
    text = 'quick: !scope:quick\n  include: fast.yaml\ndeep:\n  _scope_: deep\n  include: "q.yaml"\n'
    mentions = locate(text, tmp_path / "p/a.yaml")
    assert [(m.text, m.target is not None) for m in mentions] == [("fast.yaml", True), ("q.yaml", True)]
    assert text.splitlines()[4][mentions[1].start : mentions[1].stop] == "q.yaml"


def test_an_interpolated_an_empty_or_a_multi_line_include_points_nowhere(tmp_path: Path) -> None:
    text = "include: ${env:CONFIGS}/a.yaml\nother:\n  include: ''\nthird:\n  include: \"a\n    b.yaml\"\n"
    assert [(m.text, m.target) for m in locate(text, tmp_path / "a.yaml")] == [("${env:CONFIGS}/a.yaml", None)]


def test_a_package_include_is_the_file_its_distribution_ships(site: Path, tmp_path: Path) -> None:
    checkout = _ship(site, tmp_path, {"config/render.yaml": "a: 1\n"})
    text = "include:\n  - pkg://lp-dist/config/render.yaml\n  - pkg://lp-dist/config/gone.yaml\n  - pkg://\n"
    found = [(m.text, m.target) for m in locate(text, tmp_path / "a.yaml")]
    assert found == [
        ("pkg://lp-dist/config/render.yaml", Location(str((checkout / "config/render.yaml").resolve()), 0)),
        ("pkg://lp-dist/config/gone.yaml", None),
        ("pkg://", None),
    ]


def test_a_package_reference_is_a_link_whatever_key_holds_it(site: Path, tmp_path: Path) -> None:
    checkout = _ship(site, tmp_path, {"config/render.yaml": "a: 1\n"})
    text = (
        "steps:\n"
        "  - config: pkg://lp-dist/config/render.yaml\n"
        '  - source: "pkg://lp-dist/config/render.yaml"\n'
        "  - pkg://lp-dist/config/gone.yaml\n"
        "  - path: pkg://lp-dist/config/${env:NAME}.yaml\n"
        "include: pkg://lp-dist/config/render.yaml\n"
    )
    mentions = locate(text, tmp_path / "a.yaml")
    render = Location(str((checkout / "config/render.yaml").resolve()), 0)
    assert [(m.kind, m.text, m.target) for m in mentions] == [
        ("include", "pkg://lp-dist/config/render.yaml", render),
        ("include", "pkg://lp-dist/config/render.yaml", render),
        ("include", "pkg://lp-dist/config/gone.yaml", None),
        ("include", "pkg://lp-dist/config/${env:NAME}.yaml", None),
        ("include", "pkg://lp-dist/config/render.yaml", render),
    ]
    rows = text.splitlines()
    assert [rows[m.line][m.start : m.stop] for m in mentions] == [m.text for m in mentions]
    assert not any(m.deferred for m in mentions)


def test_a_package_reference_that_is_not_the_whole_value_is_no_link(tmp_path: Path) -> None:
    text = (
        "hint: recordstream run pkg://lp-dist/config/render.yaml\n"
        "# include: pkg://lp-dist/config/render.yaml\n"
        '"pkg://lp-dist/config/render.yaml": 1\n'
        "tagged: !custom pkg://lp-dist/config/render.yaml\n"
        'split: "pkg://lp-dist/\n  config/render.yaml"\n'
        "after: ' pkg://lp-dist/config/render.yaml'\n"
    )
    assert locate(text, tmp_path / "a.yaml") == []


def test_a_config_path_is_found_where_the_run_it_starts_reads_it(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    files = {"pyproject.toml": "", "config/runs/a.yaml": "", "config/deep/beside.yaml": "", "own/b.YML": ""}
    _write(project, files)
    text = (
        "steps:\n"
        "  - config: config/runs/a.yaml\n"
        "  - config: runs/a.yaml\n"
        '  - config: "own/b.YML"\n'
        "  - config: config/runs/gone.yaml\n"
        "  - config: beside.yaml\n"
        "  - config: ${env:RUNS}/a.yaml\n"
        "include: beside.yaml\n"
    )
    mentions = locate(text, project / "config/deep/top.yaml")
    run_a = Location(str((project / "config/runs/a.yaml").resolve()), 0)
    beside = Location(str((project / "config/deep/beside.yaml").resolve()), 0)
    assert [(m.kind, m.text, m.target) for m in mentions] == [
        ("include", "config/runs/a.yaml", run_a),
        ("include", "runs/a.yaml", run_a),  # the project's config/ tier
        ("include", "own/b.YML", Location(str((project / "own/b.YML").resolve()), 0)),
        ("include", "config/runs/gone.yaml", None),
        ("include", "beside.yaml", None),  # a run reads its config from the project folder, never beside this file
        ("include", "${env:RUNS}/a.yaml", None),  # interpolated
        ("include", "beside.yaml", beside),  # an include: is looked up beside the file first
    ]
    rows = text.splitlines()
    assert [rows[m.line][m.start : m.stop] for m in mentions][2] == "own/b.YML"


def test_a_config_value_that_names_no_file_is_no_mention(tmp_path: Path) -> None:
    text = (
        "a: {config: debug}\n"
        "b: {config: [x.yaml]}\n"
        "c: {config: {path: x.yaml}}\n"
        "d:\n  config:\n"
        "e: {config: ''}\n"
        "f: {config: 3}\n"
        "g: {config: !custom x.yaml}\n"
        "h: {config: x.txt}\n"
    )
    assert locate(text, tmp_path / "a.yaml") == []


def test_an_import_names_its_module_file(site: Path, tmp_path: Path) -> None:
    _write(site, {"lp_imp/__init__.py": "", "lp_imp/ops.py": "", "lp_imp/sub/__init__.py": ""})
    text = "import: [lp_imp.ops, lp_imp.sub, lp_imp, lp_imp.nothing, lp_nothing]\nnested:\n  import: lp_imp.ops\n"
    found = [(m.text, m.target.path if m.target else None) for m in locate(text, tmp_path / "a.yaml")]
    assert found == [
        ("lp_imp.ops", str((site / "lp_imp/ops.py").resolve())),
        ("lp_imp.sub", str((site / "lp_imp/sub/__init__.py").resolve())),
        ("lp_imp", str((site / "lp_imp/__init__.py").resolve())),
        ("lp_imp.nothing", None),
        ("lp_nothing", None),
        ("lp_imp.ops", str((site / "lp_imp/ops.py").resolve())),
    ]
    assert all(m.kind == "import" and not m.deferred for m in locate(text, tmp_path / "a.yaml"))


def test_a_namespace_package_without_init_names_no_module_file(site: Path, tmp_path: Path) -> None:
    _write(site, {"lp_ns/inner/leaf.py": ""})
    assert [m.target for m in locate("import: [lp_ns, lp_ns.inner.leaf]\n", tmp_path / "a")] == [
        None,
        Location(str((site / "lp_ns/inner/leaf.py").resolve()), 0),
    ]


# -- the slow path: the registry -----------------------------------------------------------------------------------


@configurable(name="LocateProbeTarget")
class _Probe:
    pass


@configurable(name="LocateTwin", framework="torch")
class _TwinA:
    pass


@configurable(name="LocateTwin", framework="keras")
class _TwinB:
    pass


@pytest.fixture
def entry_points(monkeypatch: pytest.MonkeyPatch) -> List[int]:
    """The registry bootstrap, recorded instead of importing every installed entry point; returns the call list."""
    calls: List[int] = []
    monkeypatch.setattr(locate_module, "_REGISTRY_LOADED", False)

    def load_configurables() -> Dict[str, object]:
        calls.append(1)
        return {}

    monkeypatch.setattr(locate_module, "load_configurables", load_configurables)
    return calls


def test_a_registry_name_resolves_to_its_class_and_the_entry_points_load_once(entry_points: List[int]) -> None:
    first = resolve_target("LocateProbeTarget")
    assert first == Resolution(Location(str(Path(__file__).resolve()), _line_of("class _Probe:")))
    assert resolve_target("LocateProbeTarget").target == first.target
    assert entry_points == [1]


def test_resolving_imports_the_documents_modules_first(site: Path, entry_points: List[int]) -> None:
    _write(
        site,
        {
            "lp_reg.py": (
                "from confluid import configurable\n\n@configurable(name='LocateFromImport')\nclass R:\n    pass\n"
            )
        },
    )
    found = resolve_target("LocateFromImport", ["lp_missing_module", "lp_reg"])
    assert found.target == Location(str((site / "lp_reg.py").resolve()), 3)  # the `class` line, below the decorator


def test_an_ambiguous_an_unknown_or_a_sourceless_name_says_why(entry_points: List[int]) -> None:
    ambiguous = resolve_target("LocateTwin")
    assert ambiguous.target is None and "'LocateTwin' is registered by 2 classes" in str(ambiguous.reason)
    assert resolve_target("LocateTwin@framework=keras").target == Location(
        str(Path(__file__).resolve()), _line_of("class _TwinB:")
    )
    unknown = resolve_target("LocateNoSuchName")
    assert unknown == Resolution(None, "no class or function named 'LocateNoSuchName' is registered or importable")
    builtin = resolve_target("builtins.len")
    assert builtin.target is None and "has no source file" in str(builtin.reason)


def test_a_dotted_name_the_source_reading_missed_resolves_by_import(site: Path, entry_points: List[int]) -> None:
    _write(site, {"lp_dyn.py": "def _make():\n    class Made:\n        pass\n    return Made\n\nMade2 = _make()\n"})
    _write(site, {"lp_dyn2.py": "import lp_dyn\nglobals()['Late'] = lp_dyn.Made2\n"})
    (mention,) = locate("x: !class:lp_dyn2.Late\n", site / "a.yaml")
    assert mention.deferred
    assert resolve_target("lp_dyn2.Late").target == Location(str((site / "lp_dyn.py").resolve()), 1)


def _line_of(source: str) -> int:
    return Path(__file__).read_text().splitlines().index(source)


# -- the protocol and the command ----------------------------------------------------------------------------------


def _session(*requests: object) -> List[dict]:
    lines = [r if isinstance(r, str) else json.dumps(r) for r in requests]
    out = io.StringIO()
    serve(io.StringIO("\n".join(lines) + "\n"), out)
    return [json.loads(line) for line in out.getvalue().splitlines()]


def test_serve_answers_each_request_on_its_own_line(entry_points: List[int], tmp_path: Path) -> None:
    replies = _session(
        {"id": 1, "method": "locate", "params": {"path": str(tmp_path / "a.yaml"), "text": "m: !class:Thing\n"}},
        "",
        {"id": 2, "method": "resolve", "params": {"name": "LocateProbeTarget", "imports": []}},
        {"id": 3, "method": "resolve", "params": {"name": "LocateNoSuchName"}},
    )
    assert replies[0] == {
        "id": 1,
        "result": [
            {"kind": "class", "text": "Thing", "line": 0, "start": 10, "stop": 15, "target": None, "deferred": True}
        ],
    }
    assert replies[1]["result"]["target"]["path"] == str(Path(__file__).resolve())
    assert replies[2]["result"]["target"] is None and replies[2]["result"]["reason"]


def test_serve_answers_a_bad_request_with_an_error_and_goes_on(tmp_path: Path) -> None:
    replies = _session(
        "not json",
        "[1, 2]",
        {"id": 7, "method": "bogus"},
        {"id": 8, "method": "locate", "params": {"text": "a: 1"}},
        {"id": 9, "method": "locate", "params": {"path": str(tmp_path / "a.yaml"), "text": "a: 1"}},
    )
    assert [r["id"] for r in replies] == [None, None, 7, 8, 9]
    assert replies[0]["error"].startswith("JSONDecodeError")
    assert replies[1]["error"] == "ValueError: a request is a JSON object"
    assert replies[2]["error"] == "ValueError: unknown method 'bogus' — use 'locate' or 'resolve'"
    assert replies[3]["error"] == "KeyError: 'path'"
    assert replies[4] == {"id": 9, "result": []}


def test_the_served_process_keeps_stdout_for_the_protocol(site: Path, tmp_path: Path) -> None:
    _write(site, {"lp_noisy.py": "import os\nprint('printed')\nos.write(1, b'written\\n')\n"})
    requests = [
        {"id": 1, "method": "resolve", "params": {"name": "lp_noisy.nothing", "imports": ["lp_noisy"]}},
        {"id": 2, "method": "locate", "params": {"path": str(tmp_path / "a.yaml"), "text": "a: !class:x.Y\n"}},
    ]
    # `python -c` puts its working folder (the site) on sys.path; the entry points count as loaded, so the child
    # imports only the noisy module — which prints, and writes to file descriptor 1 directly.
    program = "import sys, confluid.locate as l; l._REGISTRY_LOADED = True; sys.exit(l.main(['--serve']))"
    done = subprocess.run(
        [sys.executable, "-c", program],
        input="".join(json.dumps(r) + "\n" for r in requests),
        capture_output=True,
        text=True,
        env={**os.environ, "LOGGAIR_DIR": str(tmp_path / "logs")},
        timeout=60,
        cwd=site,
    )
    assert done.returncode == 0, done.stderr
    replies = [json.loads(line) for line in done.stdout.splitlines()]
    assert [r["id"] for r in replies] == [1, 2]
    assert replies[0]["result"]["target"] is None
    assert "printed" in done.stderr and "written" in done.stderr


def test_the_command_prints_a_files_mentions_as_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = tmp_path / "a.yaml"
    config.write_text("m: !class:Thing\n")
    assert main([str(config)]) == 0
    assert json.loads(capsys.readouterr().out) == [
        {"kind": "class", "text": "Thing", "line": 0, "start": 10, "stop": 15, "target": None, "deferred": True}
    ]


def test_the_command_refuses_a_missing_file_or_no_argument(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as missing:
        main([str(tmp_path / "nope.yaml")])
    assert missing.value.code == 2 and "no such file" in capsys.readouterr().err
    with pytest.raises(SystemExit) as bare:
        main([])
    assert bare.value.code == 2 and "give a FILE, or --serve" in capsys.readouterr().err


def test_the_command_serves_in_process(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    recorded = SimpleNamespace(calls=[])
    monkeypatch.setattr(locate_module, "serve", lambda stdin, stdout: recorded.calls.append((stdin, stdout)))
    assert main(["--serve"]) == 0
    assert len(recorded.calls) == 1 and recorded.calls[0][0] is sys.stdin
