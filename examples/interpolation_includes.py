"""``${...}`` interpolation & the include tree — companion to ``docs/interpolation.md``.

Writes a two-file include tree to a temp directory, then shows env-var
interpolation (``${VAR}``), config-key interpolation (``${dotted.path}``, native
type preserved on a whole-string match), ``load(..., return_paths=True)`` returning
every contributing YAML file, and ``load(..., texts=…)`` reading files held in memory.
Note interpolation is applied at MATERIALIZATION (``load`` from ``until="document"`` on)
— ``load(until="raw")`` returns the raw parse.
"""

import os
import tempfile
from pathlib import Path

from confluid import ConfigFileNotFoundError, configurable, format_yaml_loc, load


@configurable
class ExampleSink:
    """A minimal configurable target for the marker-kwargs interpolation demo.

    Args:
        out_dir: Destination directory (interpolated at load time).
    """

    def __init__(self, out_dir: str = "") -> None:
        self.out_dir = out_dir


def main() -> None:
    os.environ["EXAMPLE_DATA_ROOT"] = "/store"

    with tempfile.TemporaryDirectory() as tmp:
        common = Path(tmp) / "common.yaml"
        common.write_text(
            """
train:
  dataset: RFUAV
  version: v3
  epochs: 20
"""
        )
        experiment = Path(tmp) / "experiment.yaml"
        experiment.write_text(
            """
include: common.yaml
# Mix env + config keys in one string; embedded matches substitute str(value).
# `${env:NAME}` is the explicit environment spelling; a dotted name is a config key:
data_dir: "${env:EXAMPLE_DATA_ROOT}/${train.dataset}/${train.version}/data"
# A whole-string match keeps the native type (int, not "20"):
epochs: "${train.epochs}"
# A miss falls back to the default (comma-separated after the name):
port: "${env:EXAMPLE_MISSING_PORT,8080}"
"""
        )

        # The include tree: entrypoint first, then each transitively include:-d file.
        raw, paths = load(experiment, until="raw", return_paths=True)
        assert "${env:EXAMPLE_DATA_ROOT}" in raw["data_dir"], 'until="raw" returns the RAW parse'

        # Interpolation happens at materialization — load() the same file.
        data = load(str(experiment))

        assert data["data_dir"] == "/store/RFUAV/v3/data", data["data_dir"]
        assert data["epochs"] == 20 and isinstance(data["epochs"], int), "whole-string match keeps the int"
        assert data["port"] == 8080, "missing name -> the :default applies (coerced)"
        assert [p.name for p in paths] == ["experiment.yaml", "common.yaml"], "entrypoint first, then includes"

        print(f"data_dir: {data['data_dir']}")
        print(f"epochs:   {data['epochs']} ({type(data['epochs']).__name__})")
        print(f"port:     {data['port']} (from :default)")
        print(f"include tree: {[p.name for p in paths]}")

        # --- ${...} reaches a marker's own kwarg block, and BURNS IN ---------
        # A placeholder inside a marker's mapping body substitutes in the same
        # load-time pass as any plain key; the marker then CARRIES the
        # substituted value (dump() emits it; a deferred `!partial:` slot flowed
        # later sees it). A slot that must stay late-bound uses `!ref:`.
        marked = Path(tmp) / "marked.yaml"
        marked.write_text(
            """
run:
  name: exp42
sink: !class:ExampleSink
  out_dir: "${env:EXAMPLE_DATA_ROOT}/${run.name}"
"""
        )
        cfg = load(str(marked))
        assert cfg["sink"].out_dir == "/store/exp42", cfg["sink"].out_dir
        print(f"marker kwarg: {cfg['sink'].out_dir}")

        held_texts(Path(tmp))


def held_texts(tmp: Path) -> None:
    """``load(..., texts=…)`` — files held in memory (an editor's unsaved files), never written."""
    graph = tmp / "graph"
    graph.mkdir()
    (graph / "flow.yaml").write_text("include: prep.steps.yaml\nsteps: 2\n")  # the only file on disk
    texts = {
        graph / "prep.steps.yaml": "include: shared.yaml\nsink: !class:ExampleSink\n  out_dir: out\n",
        graph / "shared.yaml": "mode: fast\n",
    }

    doc, paths = load(graph / "flow.yaml", until="settled", texts=texts, return_paths=True)
    print(f"held: mode={doc['mode']} steps={doc['steps']} sink at {format_yaml_loc(doc['sink'])}")
    print(f"held: include tree {[p.name for p in paths]}, folder {sorted(p.name for p in graph.iterdir())}")
    assert (doc["mode"], doc["steps"]) == ("fast", 2)
    assert format_yaml_loc(doc["sink"]) == f"{(graph / 'prep.steps.yaml').resolve()}:2:7", "the REAL path"
    assert [p.name for p in paths] == ["flow.yaml", "prep.steps.yaml", "shared.yaml"]
    assert sorted(p.name for p in graph.iterdir()) == ["flow.yaml"], "nothing was written"

    try:  # con: the same call without the held texts
        load(graph / "flow.yaml", until="settled")
        raise AssertionError("the include is not on disk — this must raise")
    except ConfigFileNotFoundError as exc:
        print(f"without texts: {type(exc).__name__}: {str(exc).split(' (searched')[0]}")


if __name__ == "__main__":
    main()
